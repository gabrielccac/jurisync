"""Offline tests for how the draft filing survives a slow or flaky PJe: retries, document reconciliation, resume."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from curl_cffi.requests.exceptions import RequestException

from jurisync.connectors.pje.auth import certificate
from jurisync.connectors.pje.filing import DraftClient, StepTimeline
from jurisync.connectors.pje.filing.constants import PORTAL
from jurisync.connectors.pje.filing.documents import MAX_ROUNDS, documents_diff
from jurisync.connectors.pje.filing.retry import DELAYS, DocumentsNeedReview, is_transient, retrying
from jurisync.connectors.pje.filing.status import _check_pje, next_step

TYPE = "Outros Documentos"


def page(body="", state="vs"):
    return f'<html><body><input name="javax.faces.ViewState" value="{state}">{body}</body></html>'


class RetryTests(unittest.TestCase):
    def test_a_transient_error_is_repeated_with_growing_waits_then_succeeds(self):
        calls, waits = [], []

        def flaky():
            calls.append(1)
            if len(calls) < 3:
                raise RuntimeError("Timeout: Failed to perform, curl: (28) Operation timed out")
            return "ok"

        self.assertEqual(retrying(flaky, sleep=waits.append), "ok")
        self.assertEqual((len(calls), waits), (3, list(DELAYS)))

    def test_exhausted_retries_raise_the_last_error_and_stop(self):
        calls = []

        def down():
            calls.append(1)
            raise RuntimeError("PJe returned HTTP 503 outside the expected draft page")

        with self.assertRaisesRegex(RuntimeError, "503"):
            retrying(down, sleep=lambda seconds: None)
        self.assertEqual(len(calls), len(DELAYS) + 1)

    def test_errors_a_repeat_cannot_fix_are_raised_at_once(self):
        for error in (ValueError("bad pdf"), RuntimeError("PJe option 'X' is missing or ambiguous")):
            calls = []

            def fails(error=error, calls=calls):
                calls.append(1)
                raise error

            with self.subTest(error=str(error)), self.assertRaises(type(error)):
                retrying(fails, sleep=lambda seconds: None)
            self.assertEqual(len(calls), 1)

    def test_what_counts_as_transient(self):
        self.assertTrue(is_transient(RequestException("Operation timed out")))
        self.assertTrue(is_transient(TimeoutError()))
        self.assertTrue(is_transient(RuntimeError("PJe redirected the draft action to errorUnexpected.seam")))
        self.assertFalse(is_transient(DocumentsNeedReview([{"kind": "duplicate", "name": "a", "found": 2}])))
        self.assertFalse(is_transient(RuntimeError("Draft has a.pdf with a different document type")))


class FakeSession:
    """Answers get/post in order; an entry that is an Exception is raised instead."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = 0

    def _next(self):
        self.calls += 1
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    get = post = lambda self, *args, **kwargs: self._next()


def reply(body="<html></html>"):
    return SimpleNamespace(
        status_code=200, url=f"https://{PORTAL.host}/pje/x", content=body.encode(), text=body, headers={}
    )


class TransportRetryTests(unittest.TestCase):
    def client(self, *answers):
        client = DraftClient(FakeSession(*answers))
        client.sleep = mock.Mock()
        return client

    def test_a_get_that_times_out_is_repeated(self):
        client = self.client(RequestException("Operation timed out"), reply("<p>ok</p>"))
        self.assertIn("ok", client.get("https://x"))
        client.sleep.assert_called_once_with(DELAYS[0])
        self.assertEqual(client.retries, 1)

    def test_a_write_postback_is_never_repeated_but_a_read_is(self):
        write = self.client(RequestException("Operation timed out"), reply())
        with self.assertRaises(RequestException):
            write.post("https://x", {})
        self.assertEqual(write.session.calls, 1)
        read = self.client(RequestException("Operation timed out"), reply("<p>list</p>"))
        self.assertIn("list", read.post("https://x", {}, retry=True))
        self.assertEqual(read.session.calls, 2)


class DiffTests(unittest.TestCase):
    def expected(self, tmp, *names, type=TYPE):
        return [{"path": str(Path(tmp, name)), "type": type} for name in names]

    def test_each_document_lands_in_exactly_one_bucket(self):
        listed = [
            {"name": "ok.pdf", "type": TYPE},
            {"name": "wrong.pdf", "type": "Laudo Médico"},
            {"name": "twice.pdf", "type": TYPE},
            {"name": "twice.pdf", "type": TYPE},
            {"name": "other.pdf", "type": TYPE},
        ]
        diff = documents_diff(listed, self.expected("d", "ok.pdf", "wrong.pdf", "twice.pdf", "gone.pdf"))
        self.assertEqual(diff["ok"], ["ok.pdf"])
        self.assertEqual(diff["missing"], ["gone.pdf"])
        self.assertEqual(
            diff["wrongType"], [{"kind": "wrongType", "name": "wrong.pdf", "expected": TYPE, "found": "Laudo Médico"}]
        )
        self.assertEqual([(d["name"], d["found"]) for d in diff["duplicates"]], [("twice.pdf", 2)])
        self.assertEqual(diff["unexpected"], ["other.pdf"])
        self.assertFalse(diff["verified"])

    def test_a_clean_draft_is_verified_and_pje_name_mojibake_still_matches(self):
        listed = [{"name": "3-PROVAS DA ATIVIDADE RURAL.pdf", "type": TYPE}, {"name": "cafÃ©.pdf", "type": TYPE}]
        diff = documents_diff(listed, self.expected("d", "3-PROVAS DA ATIVIDADE RURAL.pdf", "café.pdf"))
        self.assertTrue(diff["verified"])
        self.assertEqual(diff["missing"] + diff["wrongType"] + diff["duplicates"], [])


class StatusTests(unittest.TestCase):
    def test_pje_check_reports_what_the_draft_holds_and_why_it_is_not_verified(self):
        client = DraftClient(None)
        client._pending_row = lambda draft_id, name: {"draftUrl": "u"}
        client.review_documents = lambda url: [
            {"name": "main.pdf", "type": "Petição inicial"},
            {"name": "laudo.pdf", "type": TYPE},
        ]
        payload = {
            "author": {"name": "X"},
            "documents": {
                "main": {"path": "main.pdf", "type": "Petição inicial"},
                "supporting": [{"path": "laudo.pdf", "type": "Laudo Médico"}, {"path": "rest.pdf", "type": TYPE}],
            },
        }
        check = _check_pje(client, {"draftId": "1"}, payload)
        docs = check["documents"]
        self.assertFalse(check["verified"])
        self.assertEqual(docs["missing"], ["rest.pdf"])
        self.assertEqual(docs["wrongType"][0]["found"], TYPE)
        self.assertEqual(
            docs["onDraft"], [{"name": "main.pdf", "type": "Petição inicial"}, {"name": "laudo.pdf", "type": TYPE}]
        )

    def test_next_step_tells_resume_from_a_manual_fix(self):
        resume = next_step("failed", {"conclusion": "incomplete", "pje": {"documents": {"missing": ["a"]}}})
        self.assertIn("resume", resume)
        manual = next_step("failed", {"conclusion": "incomplete", "pje": {"documents": {"wrongType": [{"name": "a"}]}}})
        self.assertIn("needsHuman", manual)
        self.assertIsNone(next_step("complete", {"conclusion": "verified"}))
        self.assertIn("wait", next_step("running", {"conclusion": "not_checked"}))


class ReconcileTests(unittest.TestCase):
    """add_supporting_documents against a draft whose list we control: PJe is the only source of truth."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def items(self, *names):
        for name in names:
            Path(self.tmp.name, name).write_bytes(b"%PDF-1")
        return [{"path": str(Path(self.tmp.name, name)), "type": TYPE} for name in names]

    def client(self, on_batch, listed=None):
        """`on_batch(todo_names, listed)` mutates the fake draft list and returns the row failures."""
        client = DraftClient(None)
        client.sleep = mock.Mock()
        client.draft = list(listed or [])
        client.batches = []

        def upload(url, items, timeline):
            names = [Path(i["path"]).name for i in items]
            client.batches.append(names)
            return on_batch(names, client.draft)

        client._upload_batch = upload
        client.review_documents = lambda url: list(client.draft)
        return client

    def test_a_row_that_fails_is_sent_again_and_the_run_converges(self):
        def batch(names, draft):
            saved = names[:1] if len(client.batches) == 1 else names  # the first round loses all but one row
            draft.extend({"name": n, "type": TYPE} for n in saved)
            return [(n, "RuntimeError: row did not render") for n in names[1:]] if len(client.batches) == 1 else []

        client = self.client(batch)
        states = []
        result = client.add_supporting_documents("u", self.items("a.pdf", "b.pdf", "c.pdf"), progress=states.append)
        self.assertEqual(client.batches, [["a.pdf", "b.pdf", "c.pdf"], ["b.pdf", "c.pdf"]])
        self.assertTrue(all(not r["alreadyAdded"] for r in result))
        self.assertEqual(states[-1]["a.pdf"], {"state": "verified", "attempts": 1})
        self.assertEqual(states[-1]["b.pdf"]["attempts"], 2)
        self.assertEqual(states[0]["b.pdf"]["state"], "missing")
        client.sleep.assert_called_once_with(DELAYS[0])

    def test_a_batch_that_cannot_start_costs_one_round_not_the_run(self):
        def batch(names, draft):
            if len(client.batches) == 1:
                raise RuntimeError("PJe did not render the supporting-document upload controls")
            draft.extend({"name": n, "type": TYPE} for n in names)
            return []

        client = self.client(batch)
        client.add_supporting_documents("u", self.items("a.pdf", "b.pdf"))
        self.assertEqual(len(client.batches), 2)

    def test_a_rerun_on_a_complete_draft_uploads_nothing(self):
        items = self.items("a.pdf", "b.pdf")
        client = self.client(lambda names, draft: [], listed=[{"name": n, "type": TYPE} for n in ("a.pdf", "b.pdf")])
        result = client.add_supporting_documents("u", items)
        self.assertEqual(client.batches, [])
        self.assertTrue(all(r["alreadyAdded"] for r in result))

    def test_only_what_the_list_lacks_is_sent(self):
        def batch(names, draft):
            draft.extend({"name": n, "type": TYPE} for n in names)
            return []

        client = self.client(batch, listed=[{"name": "a.pdf", "type": TYPE}])
        client.add_supporting_documents("u", self.items("a.pdf", "b.pdf"))
        self.assertEqual(client.batches, [["b.pdf"]])

    def test_a_row_that_never_saves_fails_after_the_rounds_and_names_the_error(self):
        client = self.client(lambda names, draft: [(n, "RuntimeError: PJe did not accept the bytes") for n in names])
        with self.assertRaisesRegex(RuntimeError, r"a\.pdf.*did not accept the bytes"):
            client.add_supporting_documents("u", self.items("a.pdf"))
        self.assertEqual(len(client.batches), MAX_ROUNDS)

    def test_a_wrong_type_or_a_duplicate_stops_the_run_with_each_problem_named(self):
        listed = [
            {"name": "a.pdf", "type": "Laudo Médico"},
            {"name": "b.pdf", "type": TYPE},
            {"name": "b.pdf", "type": TYPE},
        ]
        client = self.client(lambda names, draft: [], listed=listed)
        with self.assertRaises(DocumentsNeedReview) as caught:
            client.add_supporting_documents("u", self.items("a.pdf", "b.pdf"))
        kinds = {p["name"]: p["kind"] for p in caught.exception.problems}
        self.assertEqual(kinds, {"a.pdf": "wrongType", "b.pdf": "duplicate"})
        self.assertIn("'Laudo Médico'", str(caught.exception))
        self.assertEqual(client.batches, [])


class FakePje(DraftClient):
    """A TRF5 document panel that, like the real one, answers each row action with a PARTIAL page.

    Registration returns every row; every later answer carries only the ViewState and the touched row's own links, so
    a client that looks for the next row's type select in the last answer cannot find it.
    """

    def __init__(self, names, fail_bytes=()):
        super().__init__(None)
        self.sleep = lambda seconds: None
        self.names = names
        self.fail_bytes = set(fail_bytes)
        self.saved = []
        self.listed = [{"name": "main.pdf", "type": "Petição inicial"}]
        self.rows = {}
        self.pending = {}
        self.partial_answers = []
        self.step = 0

    def next_state(self):
        self.step += 1
        return f"vs{self.step}"

    def documents_panel(self, draft_url):
        body = (
            '<form id="formularioUpload"><input name="cid" value="42">'
            "<script id=\"acaoAjaxAdicionarProcessoDocumento\">{'containerId':'rows'}</script></form>"
            '<div id="divNomeArquivo"><span class="outNomeArquivo">main.pdf</span></div>'
        )
        return page(body, "vs0")

    def review_documents(self, draft_url):
        return list(self.listed)

    def ajax(self, url, state, fields, container, action, *, retry=False):
        if "jsonProcessoDocumento" in action:
            rows = ""
            registered = json.loads(json.loads(action["jsonProcessoDocumento"])["array"])
            for index, name in enumerate(entry["nome"] for entry in registered):
                prefix = f"fPP:{index}"
                self.rows[prefix] = name
                rows += (
                    f'<div data-dz-name="{name}"><select name="{prefix}:tipoDoc" onchange="A4J.AJAX.Submit(\'f\',event,'
                    f"{{'similarityGroupingId':'SEL{index}','containerId':'rows','parameters':{{'SEL{index}':'SEL{index}'}}}})\">"
                    '<option value="org.jboss.seam.ui.NoSelectionConverter.noSelectionValue">-</option>'
                    f'<option value="7">{TYPE}</option></select>'
                    f'<input name="{prefix}:descDoc" value=""><input name="{prefix}:numeroDoc" value=""></div>'
                )
            return page(rows, self.next_state())
        key = next(iter(k for k in action if k[:3] in ("SEL", "UPD", "SAV")))
        index = key[3:]
        prefix = f"fPP:{index}"
        if key.startswith("SEL"):
            links = (
                f'<a id="{prefix}:commandLinkAtualizarComboTipoDocumento" onclick="A4J.AJAX.Submit(\'f\',event,'
                f"{{'similarityGroupingId':'UPD{index}','containerId':'rows'}})\"></a>"
                f'<a id="{prefix}:commandLinkGravar" onclick="A4J.AJAX.Submit(\'f\',event,'
                f"{{'similarityGroupingId':'SAV{index}','containerId':'rows'}})\"></a>"
            )
            self.partial_answers.append(links)
            return page(links, self.next_state())
        if key.startswith("SAV"):
            name = self.rows[prefix]
            self.pending.pop(prefix, None)
            self.listed.append({"name": name, "type": TYPE})
            self.saved.append(name)
        self.partial_answers.append("")
        return page("", self.next_state())  # an update or a save answers with nothing but the ViewState

    def multipart(self, url, data, field, path, *, resource=False):
        ok = path.name not in self.fail_bytes
        return SimpleNamespace(json=lambda: {"requisicao": ok, "arquivos": [{"upload": ok}]})


class BatchAgainstPartialPagesTests(unittest.TestCase):
    """The failure of 20 Oct 2026: the last row's type select was looked up in the previous row's partial answer."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def items(self, *names):
        paths = []
        for name in names:
            Path(self.tmp.name, name).write_bytes(b"%PDF-1")
            paths.append({"path": str(Path(self.tmp.name, name)), "type": TYPE})
        return paths

    def test_every_row_is_saved_although_each_answer_lacks_the_next_rows_select(self):
        names = [f"{n}.pdf" for n in "abcd"]
        client = FakePje(names)
        failures = client._upload_batch("u", self.items(*names), StepTimeline(None, client, None))
        self.assertEqual(failures, [])
        self.assertEqual(sorted(client.saved), sorted(names))
        # The point of the fake: not one answer after the registration carried a type select.
        self.assertTrue(client.partial_answers)
        self.assertFalse(any("<select" in answer for answer in client.partial_answers))

    def test_a_row_whose_bytes_are_refused_is_reported_and_the_others_still_go(self):
        names = [f"{n}.pdf" for n in "abc"]
        client = FakePje(names, fail_bytes={"b.pdf"})
        failures = client._upload_batch("u", self.items(*names), StepTimeline(None, client, None))
        self.assertEqual([name for name, _ in failures], ["b.pdf"])
        self.assertIn("did not accept the supporting PDF bytes", failures[0][1])
        self.assertEqual(sorted(client.saved), ["a.pdf", "c.pdf"])

    def test_the_refused_row_is_sent_again_by_the_next_round_and_the_draft_verifies(self):
        names = [f"{n}.pdf" for n in "abc"]
        client = FakePje(names, fail_bytes={"b.pdf"})
        original = client.multipart
        calls = []

        def multipart(url, data, field, path, *, resource=False):
            calls.append(path.name)
            if path.name == "b.pdf" and calls.count("b.pdf") > 1:
                client.fail_bytes.clear()
            return original(url, data, field, path, resource=resource)

        client.multipart = multipart
        states = []
        result = client.add_supporting_documents("u", self.items(*names), progress=states.append)
        self.assertEqual(calls, ["a.pdf", "b.pdf", "c.pdf", "b.pdf"])
        self.assertEqual(states[-1]["b.pdf"], {"state": "verified", "attempts": 2})
        self.assertTrue(documents_diff(client.listed, self.items(*names))["verified"])
        self.assertEqual([r["name"] for r in result], names)


class CheckpointTests(unittest.TestCase):
    def test_fill_records_each_documents_state_in_the_checkpoint(self):
        client = DraftClient(None)
        client.add_subjects = lambda url, subjects: [{"alreadyAdded": True}]
        client.party_panel = lambda url: "P"
        client.add_author = lambda *a, **k: {"alreadyAdded": True}
        client._pending_row = lambda draft_id, name: {"draftUrl": "u"}

        def add_documents(url, documents, timeline=None, progress=None):
            progress({"a.pdf": {"state": "missing", "attempts": 1, "error": "RuntimeError: x"}})
            return {}

        client.add_documents = add_documents
        saved = []
        payload = {
            "subject": {"search": "s", "code": "1", "name": "S"},
            "authorCpf": "12345678909",
            "author": {"name": "X"},
            "documents": {"main": {"path": "m.pdf", "type": "T"}},
        }
        checkpoint = {"draftUrl": "d", "draftId": "1"}
        with mock.patch("jurisync.connectors.pje.filing.fill.phase_fingerprints") as prints:
            prints.return_value = {"subjects": "s", "parties": "p", "documents": "d"}
            client.fill_draft(checkpoint, payload, save=lambda data: saved.append(dict(data)))
        self.assertEqual(checkpoint["documentStates"]["a.pdf"]["state"], "missing")
        self.assertTrue(any("documentStates" in snapshot for snapshot in saved))


class LoginRetryTests(unittest.TestCase):
    def run_login(self, outcomes, account="otp"):
        attempts, waits = [], []

        def fake_login(*args):
            attempts.append(1)
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        with (
            mock.patch.object(certificate, "_twofa", return_value=account),
            mock.patch.object(certificate.native_signer, "load_a1", return_value=(1, 2, 3)),
            mock.patch.object(certificate.native_signer, "a1_path", return_value="a1.p12"),
            mock.patch.object(certificate, "_login", side_effect=fake_login),
            mock.patch.object(certificate.requests, "Session", return_value="fresh"),
        ):
            try:
                result = certificate.cert_login("trf5-1g", pin="x", sleep=waits.append, clock=lambda: 100.0)
            except RuntimeError as error:
                result = error
        return result, len(attempts), waits

    def test_a_login_that_stops_short_is_repeated_once_in_the_next_totp_window(self):
        result, attempts, waits = self.run_login(
            [RuntimeError("trf5-1g login did not reach an authenticated PJe page"), "session"]
        )
        self.assertEqual((result, attempts), ("session", 2))
        self.assertEqual(waits, [certificate._seconds_to_next_window(100.0)])
        self.assertEqual(waits, [21.0])

    def test_a_second_failure_and_other_errors_are_not_repeated_again(self):
        stop = "trf5-1g login did not reach an authenticated PJe page"
        result, attempts, _ = self.run_login([RuntimeError(stop), RuntimeError(stop)])
        self.assertEqual(attempts, 2)
        self.assertIsInstance(result, RuntimeError)
        result, attempts, waits = self.run_login([RuntimeError("PJe is unavailable (HTTP 503); try again later")])
        self.assertEqual((attempts, waits), (1, []))

    def test_a_login_without_totp_is_not_repeated(self):
        _, attempts, _ = self.run_login(
            [RuntimeError("SSO did not advance after the certificate callback")], account=None
        )
        self.assertEqual(attempts, 1)


class ServerResumeTests(unittest.TestCase):
    def setUp(self):
        from jurisync.connectors.pje import server

        self.server = server
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"JURISYNC_FILING_DIR": self.tmp.name, "PJE_ALLOW_DRAFT_WRITES": "true"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(server.fill_jobs.clear)
        self.addCleanup(server.fill_errors.clear)
        run = Path(self.tmp.name, "run1")
        run.mkdir()
        (run / "payload.json").write_text(json.dumps({"portal": "trf5-1g"}))
        (run / "checkpoint.json").write_text(json.dumps({"draftId": "1", "draftUrl": "u"}))

    def test_resume_continues_the_same_draft_in_the_background_as_a_fill(self):
        threads = []

        class Job:
            def __init__(self, target, args, name, daemon):
                threads.append((target, args))

            def start(self):
                pass

            def is_alive(self):
                return False

        with (
            mock.patch.object(self.server.filing, "validate_fill_payload"),
            mock.patch.object(self.server.threading, "Thread", Job),
        ):
            answer = self.server._start_fill("run1", "resume")
        self.assertEqual((answer["state"], answer["stage"]), ("started", "resume"))
        self.assertEqual(threads, [(self.server._fill_job, ("run1", "fill"))])

    def test_resume_without_a_draft_is_refused(self):
        Path(self.tmp.name, "run1", "checkpoint.json").unlink()
        with (
            mock.patch.object(self.server.filing, "validate_fill_payload"),
            self.assertRaisesRegex(Exception, "no draft yet"),
        ):
            self.server._start_fill("run1", "resume")

    def test_a_background_fill_uses_the_pooled_session_and_keeps_a_login_failure_visible(self):
        seen = {}

        class Pool:
            def session(self, key):
                class Context:
                    def __enter__(self):
                        if seen.get("fail"):
                            raise RuntimeError("trf5-1g login did not reach an authenticated PJe page")
                        return "pooled-session"

                    def __exit__(self, *exc):
                        return False

                return Context()

        with (
            mock.patch.object(self.server, "sessions", Pool()),
            mock.patch.object(self.server.filing, "run_stage", side_effect=lambda *a, **k: seen.update(k)) as run,
        ):
            self.server._fill_job("run1", "fill")
            self.assertEqual(run.call_args.kwargs["session"], "pooled-session")
            self.assertNotIn("run1", self.server.fill_errors)
            seen["fail"] = True
            self.server._fill_job("run1", "fill")
        self.assertIn("did not reach an authenticated", self.server.fill_errors["run1"])
        with mock.patch.object(self.server, "_draft_stage", return_value={"result": {"state": "failed"}}):
            status = self.server.draft_status("run1")
        self.assertIn("authenticated", status["result"]["lastStartError"])


if __name__ == "__main__":
    unittest.main()
