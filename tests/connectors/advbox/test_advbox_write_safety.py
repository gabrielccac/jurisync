"""Offline tests for the ADVBOX write guards: replacement, locking, staged files, duplicates and confirmations."""

import contextlib
import os
import tempfile
import threading
import time
import unittest
from datetime import date
from unittest import mock

from curl_cffi.requests.exceptions import Timeout

from jurisync.connectors.advbox import parsers, server
from jurisync.connectors.advbox.client import AdvboxClient, UnconfirmedWriteError, dedupe_marker
from jurisync.connectors.advbox.documents import AdvboxDocuments, is_download_url, saved_folder_name
from jurisync.connectors.advbox.session import AdvboxSession
from jurisync.core.documents_dir import resolve_saved_folder


def tool(name):
    """The function behind an ADVBOX tool, called directly."""
    return server.create_server()._tool_manager.get_tool(name).fn


FORM = {
    "editing": "100",
    "createToken": "tok",
    "lawsuitId": "9",
    "tasksId": "572240",
    "guests": ["216759"],
    "hasPartner": "0",
    "workflowId": "0",
    "workflowSequence": "1",
    "workflowLimit": "0",
    "date": "08/10/2026",
    "hour": "",
    "deadline": "15/10/2026",
    "dateEnd": "",
    "hourEnd": "",
    "local": "",
    "comments": "ABRIR INTIMAÇÃO\n[ref:747498033]",
    "recurrence": "",
    "repeatOnWeek": "all",
    "important": False,
    "urgent": False,
    "files": [{"name": "a_1.pdf", "code": "111"}],
}
CREATED = {"create": True, "message": "ok", "kanban": {"add": 777, "remove": "100"}}
OPTIONS = {"lawsuitId": "9", "tasksId": "2", "guests": [216759], "date": "09/10/2026", "comments": "ABRIR INTIMAÇÃO"}
FILES = [{"filename": "a.pdf", "bytes": b"%PDF"}, {"filename": "b.pdf", "bytes": b"%PDF"}]


class WriteCase(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {"ADVBOX_ALLOW_WRITES": "true", "ADVBOX_UPLOAD_FOLDER": "files/temp/216759"})
        env.start()
        self.addCleanup(env.stop)
        self.session = mock.Mock(spec=AdvboxSession)
        self.session.using.side_effect = lambda handler: handler(self.session)
        self.held = 0
        self.session.exclusive.side_effect = self.exclusive
        self.client = AdvboxClient(session=self.session)
        self.client.read_task_form = mock.Mock(return_value=dict(FORM))
        self.client.is_pending = mock.Mock(return_value=True)
        self.client.search_tasks = mock.Mock(return_value={"tasks": [], "hasMore": False})
        build = mock.patch("jurisync.connectors.advbox.tasks.build_edit_body", return_value={"editing": "100"})
        build.start()
        self.addCleanup(build.stop)

    @contextlib.contextmanager
    def exclusive(self):
        self.held += 1
        try:
            yield self.session
        finally:
            self.held -= 1

    def deletes(self):
        return [c.args[1]["id"] for c in self.session.post.call_args_list if c.args[0] == "/s3"]


class UpdateNeverLosesTheNewPostId(WriteCase):
    def test_a_failed_form_read_after_the_replacement_still_returns_the_new_post_id(self):
        self.client.read_task_form.side_effect = [dict(FORM), RuntimeError("ADVBOX GET /posts/create failed (500)")]
        self.session.post.return_value = CREATED
        result = self.client.update_task("100", {}, remove_files=["a_1.pdf"])
        self.assertEqual((result["postId"], result["replacedPostId"]), ("777", "100"))
        self.assertEqual(result["removedFiles"], [])
        self.assertEqual(result["removeErrors"][0]["name"], "a_1.pdf")
        self.assertIn("new postId 777", result["warnings"][0])

    def test_a_failed_file_removal_is_reported_per_file(self):
        self.client.read_task_form.side_effect = [
            {**FORM, "files": [{"name": "a_1.pdf", "code": "111"}, {"name": "b_1.pdf", "code": "112"}]},
            {
                **FORM,
                "editing": "777",
                "files": [{"name": "a_1.pdf", "code": "222"}, {"name": "b_1.pdf", "code": "333"}],
            },
        ]
        self.session.post.side_effect = [CREATED, Timeout("slow"), "ok"]
        result = self.client.update_task("100", {}, remove_files=["a_1.pdf", "b_1.pdf"])
        self.assertEqual(result["postId"], "777")
        self.assertEqual(result["removedFiles"], ["b_1.pdf"])
        self.assertEqual([e["name"] for e in result["removeErrors"]], ["a_1.pdf"])

    def test_clean_update_has_no_warnings(self):
        self.session.post.return_value = CREATED
        result = self.client.update_task("100", {})
        self.assertEqual((result["warnings"], result["removeErrors"]), ([], []))

    def test_unconfirmed_replacement_says_the_old_task_may_be_gone(self):
        self.session.post.side_effect = Timeout("no answer")
        with self.assertRaisesRegex(UnconfirmedWriteError, "MAY have been replaced"):
            self.client.update_task("100", {})
        self.assertEqual(self.session.post.call_count, 1)


class CompositeWritesHoldTheOfficeLock(WriteCase):
    def test_check_staging_and_post_run_inside_one_exclusive_section(self):
        seen = {}
        self.client.is_pending.side_effect = lambda *a, **k: seen.setdefault("check", self.held) or True
        self.session.post_multipart.side_effect = lambda *a, **k: str(seen.setdefault("stage", self.held) and 555)
        self.session.post.side_effect = lambda *a, **k: seen.setdefault("post", self.held) and CREATED
        self.client.update_task("100", {}, [FILES[0]])
        self.assertEqual(seen, {"check": 1, "stage": 1, "post": 1})
        self.assertEqual(self.held, 0)

    def test_create_complete_and_delete_hold_it_too(self):
        seen = []
        self.session.post.side_effect = lambda path, body: (
            seen.append(self.held) or (CREATED if path == "/posts" else '<span class="deleted">x</span>')
        )
        self.client.is_pending.side_effect = [True, False]
        self.client.create_task(OPTIONS)
        self.client.complete_task("100")
        self.client.delete_task("100")
        self.assertEqual(seen, [1, 1, 1])

    def test_the_session_lock_is_reentrant_and_excludes_other_threads(self):
        session = AdvboxSession()
        self.addCleanup(session.close)
        session.jar = {}
        order = []

        def other():
            with session.exclusive():
                order.append("other")

        with session.exclusive():
            self.assertEqual(session.using(lambda s: "nested"), "nested")  # same thread: no deadlock
            thread = threading.Thread(target=other)
            thread.start()
            time.sleep(0.05)
            order.append("first")
        thread.join(2)
        self.assertEqual(order, ["first", "other"])


class StagedFilesDoNotStayBehind(WriteCase):
    def test_a_failed_second_upload_removes_the_first(self):
        self.session.post_multipart.side_effect = ["501", RuntimeError("ADVBOX POST /s3 failed (500)")]
        with self.assertRaisesRegex(RuntimeError, r"upload of b\.pdf failed.*1 staged file\(s\) were removed"):
            self.client.stage_files(FILES)
        self.assertEqual(self.deletes(), ["501"])

    def test_a_refused_creation_removes_the_staged_files_and_reports_the_ones_left(self):
        self.session.post_multipart.side_effect = ["501", "502"]

        def post(path, body):
            if path == "/posts":
                raise RuntimeError("ADVBOX POST /posts failed (422): <b>data inválida</b>")
            if body["id"] == "502":
                raise RuntimeError("ADVBOX POST /s3 failed (500)")
            return ""

        self.session.post.side_effect = post
        with self.assertRaises(RuntimeError) as caught:
            self.client.create_task(OPTIONS, FILES)
        self.assertNotIsInstance(caught.exception, UnconfirmedWriteError)
        self.assertIn("No task was created", str(caught.exception))
        self.assertIn("codes left: 502", str(caught.exception))
        self.assertEqual(self.deletes(), ["501", "502"])

    def test_an_unconfirmed_creation_keeps_them_and_is_never_replayed(self):
        self.session.post_multipart.side_effect = ["501", "502"]
        for failure in (Timeout("no answer"), RuntimeError("ADVBOX POST /posts failed (502)")):
            with self.subTest(failure=failure):
                self.session.post.reset_mock()
                self.session.post_multipart.side_effect = ["501", "502"]
                self.session.post.side_effect = failure
                with self.assertRaises(UnconfirmedWriteError) as caught:
                    self.client.create_task(OPTIONS, FILES)
                message = str(caught.exception)
                self.assertIn("MAY have been created", message)
                self.assertIn("advbox_search_tasks", message)
                self.assertIn("501, 502", message)
                self.assertEqual(self.session.post.call_count, 1)  # no replay, no cleanup
        self.session.post.side_effect = None
        self.session.post.return_value = {"create": False, "message": "?"}
        with self.assertRaisesRegex(UnconfirmedWriteError, "MAY have been created"):
            self.client.create_task(OPTIONS)


class DuplicateCreation(WriteCase):
    def task(self, **changes):
        return {"postId": "55", "task": "ABRIR INTIMAÇÃO", "detail": "x\n[ref:747498033]", "lawsuitId": "9", **changes}

    def test_marker_rules(self):
        self.assertEqual(dedupe_marker(" 747498033 "), "[ref:747498033]")
        self.assertEqual(dedupe_marker(""), "")
        for bad in ("a]b", "a\nb", "x" * 81):
            with self.assertRaises(ValueError):
                dedupe_marker(bad)

    def test_an_existing_marked_task_is_returned_and_nothing_is_created(self):
        self.client.search_tasks.return_value = {"tasks": [self.task()], "hasMore": False}
        result = self.client.create_task(OPTIONS, FILES, dedupe_key="747498033")
        self.assertEqual((result["postId"], result["duplicate"], result["created"]), ("55", True, False))
        self.session.post.assert_not_called()
        self.session.post_multipart.assert_not_called()
        self.assertEqual(self.client.search_tasks.call_args.args[0], "747498033")

    def test_without_a_match_the_marker_is_the_last_line_of_the_comment(self):
        other_lawsuit = self.task(lawsuitId="8")
        other_key = self.task(detail="[ref:7474980339]")
        self.client.search_tasks.return_value = {"tasks": [other_lawsuit, other_key], "hasMore": False}
        self.client.read_task_form.return_value = {**FORM, "comments": "[ref:7474980339]"}
        self.session.post.return_value = CREATED
        result = self.client.create_task(OPTIONS, dedupe_key="747498033")
        body = self.session.post.call_args.args[1]
        self.assertEqual(body["comments"], "ABRIR INTIMAÇÃO\n[ref:747498033]")
        self.assertEqual((result["postId"], result["duplicate"]), ("777", False))

    def test_a_shortened_or_unattributed_row_is_confirmed_on_the_form(self):
        cut = self.task(detail="ABRIR INTIMAÇÃO 747498033…", lawsuitId=None)
        self.client.search_tasks.return_value = {"tasks": [cut], "hasMore": False}
        result = self.client.create_task(OPTIONS, dedupe_key="747498033")
        self.assertTrue(result["duplicate"])
        self.client.read_task_form.assert_called_once_with("55")
        self.client.read_task_form.return_value = {**FORM, "lawsuitId": "8"}  # marked, but another lawsuit
        self.session.post.return_value = CREATED
        self.assertFalse(self.client.create_task(OPTIONS, dedupe_key="747498033")["duplicate"])

    def test_no_key_means_no_lookup_and_an_untouched_comment(self):
        self.session.post.return_value = CREATED
        result = self.client.create_task(OPTIONS)
        self.client.search_tasks.assert_not_called()
        self.assertEqual(self.session.post.call_args.args[1]["comments"], "ABRIR INTIMAÇÃO")
        self.assertNotIn("duplicate", result)

    def test_dry_run_shows_the_marker_and_the_existing_task(self):
        self.client.search_tasks.return_value = {"tasks": [self.task()], "hasMore": False}
        preview = self.client.preview_task(OPTIONS, dedupe_key="747498033")
        self.assertTrue(preview["duplicate"])
        self.assertEqual(preview["existingTask"]["postId"], "55")
        self.assertTrue(preview["body"]["comments"].endswith("\n[ref:747498033]"))
        self.session.post.assert_not_called()

    def test_tool_passes_the_key(self):
        with mock.patch.object(server.client, "create_task", return_value={"postId": "1"}) as create:
            tool("advbox_create_task")(lawsuitId="9", tasksId="2", guests=[1], date="2026-10-09", dedupeKey="747498033")
        self.assertEqual(create.call_args.kwargs, {"dedupe_key": "747498033"})


class CompleteAndDelete(WriteCase):
    def test_dry_run_shows_the_task_and_sends_nothing_even_without_the_write_gate(self):
        with mock.patch.dict(os.environ, {"ADVBOX_ALLOW_WRITES": "false"}):
            complete = self.client.complete_task("100", dry_run=True)
            delete = self.client.delete_task("100", scope="all", dry_run=True)
        self.assertEqual((complete["action"], complete["pending"]), ("complete", True))
        self.assertEqual(complete["current"]["comments"], FORM["comments"])
        self.assertEqual((delete["action"], delete["scope"], delete["files"]), ("delete", "all", ["a_1.pdf"]))
        self.session.post.assert_not_called()

    def test_expect_comments_and_a_foreign_form_refuse_before_any_write(self):
        for operation in (self.client.complete_task, self.client.delete_task):
            with self.assertRaisesRegex(ValueError, "does not contain the expected text"):
                operation("100", expect_comments="outro texto")
            with self.assertRaisesRegex(ValueError, "form of task 100, not 101"):
                operation("101")
            operation("100", expect_comments="747498033", dry_run=True)
        self.session.post.assert_not_called()

    def test_completion_is_confirmed_by_the_pending_list(self):
        self.client.is_pending.side_effect = [True, False]
        self.session.post.return_value = ""
        self.assertEqual(self.client.complete_task("100")["completed"], True)
        self.client.is_pending.side_effect = [True, True]
        with self.assertRaisesRegex(UnconfirmedWriteError, "still listed as pending"):
            self.client.complete_task("100")
        self.client.is_pending.side_effect = [False]
        with self.assertRaisesRegex(ValueError, "not among the pending tasks"):
            self.client.complete_task("100")
        self.assertEqual(self.session.post.call_count, 2)

    def test_deletion_without_the_marker_raises_instead_of_deleted_false(self):
        self.session.post.return_value = '<span class="deleted">Excluída</span>'
        self.assertEqual(
            self.client.delete_task("100"), {"postId": "100", "deleted": True, "scope": "this", "marker": "Excluída"}
        )
        self.session.post.return_value = "<html>login</html>"
        with self.assertRaisesRegex(UnconfirmedWriteError, "not confirmed"):
            self.client.delete_task("100")

    def test_tools_are_marked_destructive_and_dry_run_skips_the_write_gate(self):
        import asyncio

        from mcp.client.client import Client

        async def tools():
            async with Client(server.create_server()) as client:
                return {tool.name: tool for tool in (await client.list_tools()).tools}

        listed = asyncio.run(tools())
        for name in ("advbox_complete_task", "advbox_delete_task", "advbox_update_task"):
            self.assertTrue(listed[name].annotations.destructive_hint, name)
            self.assertIn("dryRun", listed[name].input_schema["properties"])
            self.assertIn("expectComments", listed[name].input_schema["properties"])
        self.assertEqual(listed["advbox_complete_task"].input_schema["required"], ["postId"])
        self.assertIn("dedupeKey", listed["advbox_create_task"].input_schema["properties"])
        with (
            mock.patch.dict(os.environ, {"ADVBOX_ALLOW_WRITES": "false"}),
            mock.patch.object(server.client, "complete_task", return_value={"dryRun": True}) as complete,
            mock.patch.object(server.client, "delete_task", return_value={"dryRun": True}) as delete,
        ):
            tool("advbox_complete_task")("100", expectComments="x", dryRun=True)
            tool("advbox_delete_task")("100", dryRun=True)
            with self.assertRaises(Exception) as caught:
                tool("advbox_delete_task")("100")
        self.assertIn("ADVBOX_ALLOW_WRITES", str(caught.exception))
        self.assertEqual(complete.call_args.kwargs, {"expect_comments": "x", "dry_run": True})
        self.assertEqual(delete.call_count, 1)


class DocumentsFollowTheCurrentSession(unittest.TestCase):
    def test_session_is_resolved_on_every_call(self):
        holder = mock.Mock()
        documents = AdvboxDocuments(holder)
        holder.session = first = mock.Mock()
        first.using.return_value = "first"
        self.assertEqual(documents.list_customer_files("1"), "first")
        holder.session = second = mock.Mock()  # the office's client was replaced after a credential change
        second.using.return_value = "second"
        self.assertEqual(documents.list_customer_files("1"), "second")
        self.assertIs(server.documents._client, server.client)

    def test_download_urls_with_parent_segments_are_refused(self):
        base = "https://app.advbox.com.br"
        for good in (
            f"{base}/files/1/a.pdf",
            f"{base}/posts-file/1/Procuração v1.2.pdf",
            f"{base}/file/download?customer=1&path=A",
        ):
            self.assertTrue(is_download_url(good), good)
        for bad in (
            f"{base}/files/../admin/export",
            f"{base}/files/%2e%2e/admin",
            f"{base}/files/%2E%2E%2Fadmin",
            f"{base}/files/a/%252e%252e/b",
            f"{base}/posts-file/1/..%2f..%2fx",
            f"{base}/file/download?customer=1&path=..%2F2",
            "https://evil.example/files/a.pdf",
        ):
            self.assertFalse(is_download_url(bad), bad)


class SaveFolderNames(unittest.TestCase):
    def test_names_the_shared_folder_refuses_are_adjusted(self):
        self.assertEqual(saved_folder_name("Procuração (1).pdf"), "Procuração (1).pdf")
        self.assertEqual(saved_folder_name("_rascunho.pdf"), "rascunho.pdf")
        self.assertEqual(saved_folder_name(".env"), "arquivo.env")
        self.assertEqual(saved_folder_name("RG + CPF [frente] #2.pdf"), "RG _ CPF _frente_ _2.pdf")
        self.assertEqual(saved_folder_name("###.pdf"), "arquivo.pdf")
        long = saved_folder_name("a" * 300 + ".pdf")
        self.assertEqual((len(long), long[-4:]), (181, ".pdf"))

    def test_save_folder_keeps_walking_and_reports_the_original_name(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        session = mock.Mock()
        session.using.side_effect = lambda handler: handler(session)
        documents = AdvboxDocuments(mock.Mock(session=session))
        names = ["RG + CPF.pdf", "Laudo.pdf", "_x.pdf"]
        tree = {
            "": {"files": [], "folders": [{"path": "A"}]},
            "A": {
                "files": [{"name": n, "url": f"https://app.advbox.com.br/files/{i}"} for i, n in enumerate(names)],
                "folders": [],
            },
        }
        with (
            mock.patch.dict(os.environ, {"JURISYNC_DOCUMENTS_DIR": folder.name}),
            mock.patch.object(documents, "list_customer_files", side_effect=lambda customer, path="": tree[path]),
            mock.patch.object(documents, "_fetch_bytes", return_value=b"x"),
        ):
            result = documents.save_folder("1", "A", "run1")
            saved = sorted(path.name for path in resolve_saved_folder("run1")[1])
        self.assertEqual(
            result["files"],
            [
                {"name": "RG _ CPF.pdf", "bytes": 1, "renamedFrom": "RG + CPF.pdf"},
                {"name": "Laudo.pdf", "bytes": 1},
                {"name": "x.pdf", "bytes": 1, "renamedFrom": "_x.pdf"},
            ],
        )
        self.assertEqual(saved, ["Laudo.pdf", "RG _ CPF.pdf", "x.pdf"])


class OverdueUsesTheOfficeDate(unittest.TestCase):
    def row(self, deadline):
        return ["", "", "", "", '<a href="posts/1/edit" title="d">T</a>', "", "08/10/2026", deadline, ""]

    def test_a_task_due_today_is_not_overdue(self):
        with mock.patch.object(parsers, "_today", return_value=date(2026, 11, 9)):
            self.assertFalse(parsers.parse_activity_row(self.row("09/11/2026"))["overdue"])
            self.assertTrue(parsers.parse_activity_row(self.row("08/11/2026"))["overdue"])
            self.assertFalse(parsers.parse_activity_row(self.row("10/11/2026"))["overdue"])
            self.assertFalse(parsers.parse_activity_row(self.row(""))["overdue"])

    def test_today_is_taken_in_sao_paulo(self):
        from datetime import UTC, datetime

        late_evening_utc = datetime(2026, 11, 10, 1, 30, tzinfo=UTC)  # still 09/11, 22:30, in São Paulo
        with mock.patch.object(parsers, "datetime") as clock:
            clock.now.side_effect = lambda zone=None: late_evening_utc.astimezone(zone)
            self.assertEqual(parsers._today(), date(2026, 11, 9))


class Response:
    def __init__(self, status, text="", headers=None):
        self.status_code, self.text, self.headers = status, text, headers or {}


class SessionCoolDown(unittest.TestCase):
    def setUp(self):
        self.session = AdvboxSession()
        self.addCleanup(self.session.close)
        self.session.jar = {}

    def test_csrf_token_retries_once_after_a_429(self):
        page = Response(200, '<meta name="csrf-token" content="abc123">')
        with (
            mock.patch.object(
                self.session, "_send", side_effect=[Response(429, "", {"retry-after": "0"}), page]
            ) as send,
            mock.patch.object(self.session, "_cool_down"),
        ):
            self.assertEqual(self.session.using(lambda s: s.csrf_token()), "abc123")
        self.assertEqual(send.call_count, 2)
        with mock.patch.object(self.session, "_send", return_value=Response(302, "", {"location": "/login"})):
            with self.assertRaisesRegex(RuntimeError, "session expired"):
                self.session.csrf_token(force=True)

    def test_the_cool_down_wait_releases_the_office_lock(self):
        self.session.cooldown_until = time.time() + 0.6
        started = threading.Event()

        def read(session):
            started.set()
            return session.get_html("/posts/create")

        with mock.patch.object(self.session, "_send", return_value=Response(200, "page")):
            worker = threading.Thread(target=lambda: self.session.using(lambda s: self.session.using(read)))
            worker.start()
            started.wait(2)
            time.sleep(0.1)
            free = self.session.lock.acquire(timeout=0.2)
            if free:
                self.session.lock.release()
            worker.join(3)
        self.assertTrue(free, "the lock stayed held during the cool-down")
        self.assertFalse(worker.is_alive())

    def test_inside_a_composite_write_the_lock_is_kept(self):
        self.session.cooldown_until = time.time() + 0.5
        started = threading.Event()

        def write():
            with self.session.exclusive():
                started.set()
                self.session.get_html("/posts/create")

        with mock.patch.object(self.session, "_send", return_value=Response(200, "page")):
            worker = threading.Thread(target=write)
            worker.start()
            started.wait(2)
            time.sleep(0.1)
            free = self.session.lock.acquire(timeout=0.1)
            if free:
                self.session.lock.release()
            worker.join(3)
        self.assertFalse(free)


if __name__ == "__main__":
    unittest.main()
