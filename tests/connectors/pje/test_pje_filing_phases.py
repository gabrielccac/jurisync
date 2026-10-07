import tempfile
import unittest
from pathlib import Path

from jurisync.connectors.pje.filing import DraftClient, StepTimeline
from jurisync.connectors.pje.filing.checkpoint import documents_fingerprint, fingerprint, write_checkpoint

PAYLOAD = {
    "subject": {"search": "s", "code": "1", "name": "S"},
    "authorCpf": "12345678909",
    "author": {"name": "EXAMPLE"},
    "attorney": {"cpf": "1", "name": "A", "oabNumber": "1", "oabState": "PE"},
    "passive": {"cnpj": "1", "search": "x"},
    "characteristics": {"claimValue": "1,00", "legalAid": True, "injunction": False},
}


class Recorder(DraftClient):
    """A client whose PJe operations are canned; `calls` records what the orchestration asked for."""

    def __init__(self, present=True):
        super().__init__(None)
        self.calls = []
        self.present = present

    def _answer(self, name, *args, **extra):
        self.calls.append((name, *args))
        return {"alreadyAdded": self.present, **extra}

    def add_subjects(self, draft_url, subjects):
        return [self._answer("subjects")]

    def party_panel(self, draft_url):
        self.calls.append(("party_panel",))
        return "PANEL"

    def add_author(self, draft_url, cpf, name, address=None, panel=None):
        return self._answer("author", panel)

    def add_attorney(self, draft_url, attorney, panel=None):
        return self._answer("attorney", panel)

    def add_passive_body(self, draft_url, body, panel=None):
        return self._answer("passive", panel)

    def save_characteristics(self, draft_url, characteristics):
        return self._answer("characteristics")

    def add_documents(self, draft_url, documents, timeline=None):
        return self._answer("documents")

    def _pending_row(self, draft_id, author_name):
        self.calls.append(("pending",))
        return {"draftId": draft_id, "draftUrl": "review-url"}

    def names(self):
        return [call[0] for call in self.calls]


def checkpoint():
    return {"draftUrl": "draft", "draftId": "1"}


class PhaseTests(unittest.TestCase):
    def test_fresh_draft_runs_every_phase_and_records_each_in_the_checkpoint(self):
        client, saved = Recorder(present=False), []
        point = checkpoint()
        result = client.fill_draft(point, PAYLOAD, save=lambda data: saved.append(set(data["completed"])))
        self.assertEqual(
            client.names(), ["subjects", "party_panel", "author", "attorney", "passive", "characteristics", "pending"]
        )
        self.assertEqual(set(point["completed"]), {"subjects", "parties", "characteristics"})
        self.assertEqual(saved, [{"subjects"}, {"subjects", "parties"}, {"subjects", "parties", "characteristics"}])
        self.assertEqual(result["pending"], {"draftId": "1", "inNaoProtocolados": True})

    def test_snapshot_is_reused_while_nothing_is_added_and_refreshed_after_a_change(self):
        client = Recorder(present=True)
        client.fill_draft(checkpoint(), PAYLOAD)
        panels = {call[0]: call[1] for call in client.calls if call[0] in ("author", "attorney", "passive")}
        self.assertEqual(panels, {"author": "PANEL", "attorney": "PANEL", "passive": "PANEL"})
        self.assertEqual(client.names().count("party_panel"), 1)
        changed = Recorder(present=False)
        changed.fill_draft(checkpoint(), PAYLOAD)
        later = {call[0]: call[1] for call in changed.calls if call[0] in ("author", "attorney", "passive")}
        self.assertEqual(later, {"author": "PANEL", "attorney": None, "passive": None})

    def test_an_address_that_was_only_verified_keeps_the_party_snapshot(self):
        payload = {**PAYLOAD, "author": {"name": "EXAMPLE", "address": {"cep": "56800-000", "numero": "15"}}}
        for changed, expected in ((False, "PANEL"), (True, None)):
            client = Recorder(present=True)
            client.add_author = lambda *args, changed=changed, calls=client.calls, **kw: (
                calls.append(("author", None)) or {"alreadyAdded": True, "address": {"changed": changed}}
            )
            client.fill_draft(checkpoint(), payload)
            attorney = next(call for call in client.calls if call[0] == "attorney")
            self.assertEqual(attorney[1], expected, changed)

    def test_rerun_skips_completed_phases_and_only_checks_the_draft_exists(self):
        first = Recorder()
        point = checkpoint()
        first.fill_draft(point, PAYLOAD)
        again = Recorder()
        timeline = StepTimeline(None, again, "fill")
        result = again.fill_draft(point, PAYLOAD, timeline)
        self.assertEqual(again.names(), ["pending"])
        self.assertEqual(result["parties"], {"skipped": True})
        self.assertEqual(
            [step["status"] for step in timeline.steps if step["step"] != "find draft in Não Protocolados"],
            ["skipped"] * 3,
        )

    def test_changed_payload_reruns_only_its_phase(self):
        point = checkpoint()
        Recorder().fill_draft(point, PAYLOAD)
        changed = {**PAYLOAD, "characteristics": {**PAYLOAD["characteristics"], "claimValue": "2,00"}}
        client = Recorder()
        client.fill_draft(point, changed)
        self.assertEqual(client.names(), ["characteristics", "pending"])

    def test_verify_all_ignores_the_checkpoint(self):
        point = checkpoint()
        Recorder().fill_draft(point, PAYLOAD)
        client = Recorder()
        client.fill_draft(point, PAYLOAD, verify_all=True)
        self.assertIn("author", client.names())
        self.assertIn("characteristics", client.names())

    def test_documents_fingerprint_follows_content_not_location(self):
        with tempfile.TemporaryDirectory() as tmp:
            one, two = Path(tmp, "a", "x.pdf"), Path(tmp, "b", "x.pdf")
            for path in (one, two):
                path.parent.mkdir()
                path.write_bytes(b"%PDF-1 same")
            self.assertEqual(
                documents_fingerprint({"main": {"path": str(one), "type": "T"}}),
                documents_fingerprint({"main": {"path": str(two), "type": "T"}}),
            )
            two.write_bytes(b"%PDF-1 other")
            self.assertNotEqual(
                documents_fingerprint({"main": {"path": str(one), "type": "T"}}),
                documents_fingerprint({"main": {"path": str(two), "type": "T"}}),
            )
        self.assertEqual(fingerprint({"b": 1, "a": 2}), fingerprint({"a": 2, "b": 1}))

    def test_checkpoint_is_replaced_atomically_with_owner_only_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "checkpoint.json")
            write_checkpoint(path, {"draftId": "1"})
            write_checkpoint(path, {"draftId": "2"})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(Path(tmp).iterdir()), [path])


class DocumentBatchTests(unittest.TestCase):
    def documents(self, tmp, count=2):
        paths = []
        for index in range(count):
            path = Path(tmp, f"{index}-DOC.pdf")
            path.write_bytes(b"%PDF-1")
            paths.append(path)
        return {
            "main": {"path": str(paths[0]), "type": "Petição inicial"},
            "supporting": [{"path": str(path), "type": "Outros Documentos"} for path in paths[1:]],
        }

    def client(self, listings, uploaded):
        client = DraftClient(None)
        reads = []
        batches = []

        def review(draft_url):
            reads.append(1)
            return listings[min(len(reads), len(listings)) - 1]

        client.review_documents = review
        client.add_main_document = lambda url, doc, existing=None: {"alreadyAdded": "main" not in uploaded}
        client._upload_batch = lambda url, items, timeline: batches.append([Path(i["path"]).name for i in items])
        return client, reads, batches

    def test_everything_present_costs_one_read_and_no_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            docs = self.documents(tmp)
            listing = [
                {"name": "0-DOC.pdf", "type": "Petição inicial"},
                {"name": "1-DOC.pdf", "type": "Outros Documentos"},
            ]
            client, reads, batches = self.client([listing], uploaded=())
            client.add_documents("url", docs)
        self.assertEqual(len(reads), 1)
        self.assertEqual(batches, [])

    def test_missing_supporting_files_go_in_one_batch_then_are_verified_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            docs = self.documents(tmp, count=3)
            listing = [{"name": "0-DOC.pdf", "type": "Petição inicial"}]
            complete = [
                {"name": "0-DOC.pdf", "type": "Petição inicial"},
                {"name": "1-DOC.pdf", "type": "Outros Documentos"},
                {"name": "2-DOC.pdf", "type": "Outros Documentos"},
            ]
            client, reads, batches = self.client([listing, complete], uploaded=())
            client.add_documents("url", docs)
        self.assertEqual(batches, [["1-DOC.pdf", "2-DOC.pdf"]])
        self.assertEqual(len(reads), 2)

    def test_a_missing_file_after_the_batch_is_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            docs = self.documents(tmp)
            client, _, _ = self.client([[], [{"name": "0-DOC.pdf", "type": "Petição inicial"}]], uploaded=())
            with self.assertRaisesRegex(RuntimeError, "1-DOC.pdf"):
                client.add_documents("url", docs)

    def test_a_single_file_is_a_batch_of_one_and_is_verified_after_upload(self):
        with tempfile.TemporaryDirectory() as tmp:
            docs = self.documents(tmp)
            item = docs["supporting"][0]
            listing = [{"name": "0-DOC.pdf", "type": "Petição inicial"}]
            complete = [*listing, {"name": "1-DOC.pdf", "type": "Outros Documentos"}]
            client, reads, batches = self.client([listing, complete], uploaded=())
            result = client.add_supporting_document("url", item)
        self.assertEqual(batches, [["1-DOC.pdf"]])
        self.assertEqual(len(reads), 2)
        self.assertEqual(result, {"alreadyAdded": False, "name": "1-DOC.pdf", "type": "Outros Documentos"})

    def test_a_file_already_on_the_draft_is_not_uploaded_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            docs = self.documents(tmp)
            item = docs["supporting"][0]
            listing = [
                {"name": "0-DOC.pdf", "type": "Petição inicial"},
                {"name": "1-DOC.pdf", "type": "Outros Documentos"},
            ]
            client, reads, batches = self.client([listing], uploaded=())
            result = client.add_supporting_document("url", item)
            self.assertTrue(result["alreadyAdded"])
            self.assertEqual((batches, len(reads)), ([], 1))
            wrong = {**item, "type": "Laudo Médico"}
            with self.assertRaisesRegex(RuntimeError, "different document type"):
                client.add_supporting_document("url", wrong)

    def test_row_name_pairs_each_type_select_with_its_file(self):
        from bs4 import BeautifulSoup

        html = """<div><div data-dz-name="0-A.pdf"><select name="f:0:tipoDoc"></select></div>
        <div data-dz-name="1-B.pdf"><select name="f:1:tipoDoc"></select></div></div>"""
        soup = BeautifulSoup(html, "html.parser")
        names = [DraftClient._row_name(select) for select in soup.find_all("select")]
        self.assertEqual(names, ["0-A.pdf", "1-B.pdf"])


class SubjectBatchTests(unittest.TestCase):
    def test_page_is_read_once_until_a_subject_is_added(self):
        client = DraftClient(None)
        reads, seen = [], []
        client.get = lambda url: reads.append(url) or f"page{len(reads)}"

        def add(url, subject, page=None):
            seen.append(page)
            return {"alreadyAdded": subject["code"] != "2"}

        client.add_subject = add
        client.add_subjects("u", [{"code": "1"}, {"code": "2"}, {"code": "3"}])
        self.assertEqual(seen, ["page1", "page1", "page2"])
        self.assertEqual(len(reads), 2)


if __name__ == "__main__":
    unittest.main()
