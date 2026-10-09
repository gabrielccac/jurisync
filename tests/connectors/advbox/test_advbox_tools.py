"""Offline tests for the task finder, compact reports, attach-by-reference and dry-run creation."""

import os
import tempfile
import unittest
from unittest import mock

from jurisync.connectors.advbox import server
from jurisync.connectors.advbox.client import AdvboxClient
from jurisync.connectors.advbox.parsers import (
    build_edit_body,
    compact_lawsuit_report,
    parse_activity_row,
    parse_task_form,
)
from jurisync.connectors.advbox.session import AdvboxSession
from jurisync.core.documents_dir import save_document

REPORT = {
    "processNumber": "0007535-13.2026.4.05.8303",
    "sections": {
        "Intimações": [{"Data": f"0{n}/10/2026", "Intimação": "x" * 500} for n in range(1, 5)],
        "Histórico de tarefas": [
            {"Data": "08/10/2026", "Descrição": "ANALISAR PROPOSTA DE ACORDO Prazo: 5 dias.", "Responsável": "EMILLY"},
            {
                "Data": "06/10/2026",
                "Descrição": "ABRIR INTIMAÇÃO — DJEN 747498033, processo 0007535",
                "Responsável": "KAÍKY",
            },
            {"Data": "01/10/2026", "Descrição": "ABRIR INTIMAÇÃO", "Responsável": "KAÍKY"},
        ],
    },
}


class ActivityRowTest(unittest.TestCase):
    def test_post_id_comes_from_the_title_link_when_the_row_has_no_data_attributes(self):
        row = [
            '<a href="posts/status/1" data-field="completed"></a>',
            "",
            "",
            "",
            '<a href="posts/285220270/edit" class="link" title="comentario">ABRIR INTIMAÇÃO</a>',
            '<a href="customers/9/edit" class="link">CLIENTE</a>',
            "<span>08/10/2026</span>",
            "",
            "",
        ]
        activity = parse_activity_row(row)
        self.assertEqual(activity["postId"], "285220270")
        self.assertIsNone(activity["lawsuitId"])


class CompactReportTest(unittest.TestCase):
    def test_defaults_return_the_report_untouched(self):
        self.assertIs(compact_lawsuit_report(REPORT), REPORT)

    def test_task_search_finds_a_djen_id_and_keeps_totals(self):
        result = compact_lawsuit_report(REPORT, task_search="djen 747498033")
        self.assertEqual([t["Data"] for t in result["sections"]["Histórico de tarefas"]], ["06/10/2026"])
        self.assertEqual(result["compact"]["tasksMatched"], 1)
        self.assertEqual(result["compact"]["noticesTotal"], 4)

    def test_caps_and_shortening_never_mutate_the_source(self):
        result = compact_lawsuit_report(REPORT, max_tasks=2, max_notices=1, max_text=40)
        self.assertEqual(len(result["sections"]["Histórico de tarefas"]), 2)
        self.assertEqual(len(result["sections"]["Intimações"]), 1)
        self.assertTrue(result["sections"]["Intimações"][0]["Intimação"].endswith("…"))
        self.assertLessEqual(len(result["sections"]["Intimações"][0]["Intimação"]), 41)
        self.assertEqual(len(REPORT["sections"]["Intimações"][0]["Intimação"]), 500)
        self.assertEqual(result["compact"]["tasksMatched"], 3)
        self.assertEqual(result["compact"]["tasksReturned"], 2)


class FindTasksTest(unittest.TestCase):
    def client(self):
        client = AdvboxClient(session=mock.Mock(spec=AdvboxSession))
        client.list_members = mock.Mock(return_value={"users": [{"id": 1}, {"id": 2}]})
        client.list_activities = mock.Mock(
            return_value={
                "total": 1,
                "records": [
                    {
                        "postId": "9",
                        "task": "ANALISAR PROPOSTA DE ACORDO: x",
                        "detail": "x",
                        "start": "08/10/2026",
                        "deadline": "15/10/2026",
                        "customer": {"id": "1", "name": "CLIENTE"},
                        "responsible": [{"id": "2", "name": "EMILLY"}],
                    }
                ],
            }
        )
        return client

    def test_every_member_is_asked_when_no_responsible_is_given(self):
        client = self.client()
        result = client.find_tasks("PROPOSTA")
        self.assertEqual(client.list_activities.call_args.kwargs["responsibleIds"], [1, 2])
        self.assertEqual(client.list_activities.call_args.kwargs["search"], "PROPOSTA")
        self.assertEqual(result["tasks"][0]["postId"], "9")
        self.assertEqual(result["tasks"][0]["prazo"], "15/10/2026")
        self.assertNotIn("estado", result["tasks"][0])

    def test_explicit_responsible_skips_the_member_lookup(self):
        client = self.client()
        client.find_tasks("PROPOSTA", responsible=[49187])
        client.list_members.assert_not_called()


class UploadFolderTest(unittest.TestCase):
    def test_folder_is_read_from_the_task_form_once(self):
        session = mock.Mock(spec=AdvboxSession)
        session.using.side_effect = lambda handler: handler(session)
        session.get_html.return_value = "<script>var up = 'files/temp/216759';</script>"
        client = AdvboxClient(session=session)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ADVBOX_UPLOAD_FOLDER", None)
            self.assertEqual(client.upload_folder(), "files/temp/216759")
            self.assertEqual(client.upload_folder(), "files/temp/216759")
        session.get_html.assert_called_once_with("/posts/create")

    def test_configured_folder_wins(self):
        client = AdvboxClient(session=mock.Mock(spec=AdvboxSession))
        with mock.patch.dict(os.environ, {"ADVBOX_UPLOAD_FOLDER": "files/temp/5"}):
            self.assertEqual(client.upload_folder(), "files/temp/5")


class CreateTaskToolTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        patcher = mock.patch.dict(os.environ, {"JURISYNC_DOCUMENTS_DIR": self.directory.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    ARGS = dict(lawsuitId="1", tasksId="2", guests=[216759], date="09/10/2026")

    def test_saved_files_are_read_by_name_with_their_type(self):
        save_document("a_1.pdf", b"%PDF-1.7 body")
        files = server._saved_files(["a_1.pdf"])
        self.assertEqual(files, [{"filename": "a_1.pdf", "bytes": b"%PDF-1.7 body", "contentType": "application/pdf"}])

    def test_unsupported_oversized_and_missing_files_are_refused(self):
        save_document("page_1.html", b"<html>")
        save_document("big_1.pdf", b"x" * 20)
        with self.assertRaises(ValueError):
            server._saved_files(["page_1.html"])
        with mock.patch.object(server, "MAX_ATTACHMENT_BYTES", 10), self.assertRaises(ValueError):
            server._saved_files(["big_1.pdf"])
        with self.assertRaises(FileNotFoundError):
            server._saved_files(["nope_1.pdf"])

    def test_dry_run_previews_without_the_write_gate_or_any_request(self):
        save_document("a_1.pdf", b"%PDF-1.7 body")
        with (
            mock.patch.dict(os.environ, {"ADVBOX_ALLOW_WRITES": "false"}),
            mock.patch.object(server.client, "preview_task", return_value={"dryRun": True}) as preview,
            mock.patch.object(server.client, "create_task") as create,
        ):
            result = server.create_task(**self.ARGS, attachmentFiles=["a_1.pdf"], dryRun=True)
        self.assertEqual(result, {"dryRun": True})
        create.assert_not_called()
        self.assertEqual(preview.call_args.args[1][0]["filename"], "a_1.pdf")

    def test_real_creation_still_needs_the_write_gate(self):
        with mock.patch.dict(os.environ, {"ADVBOX_ALLOW_WRITES": "false"}):
            with self.assertRaises(Exception) as caught:
                server.create_task(**self.ARGS)
        self.assertIn("ADVBOX_ALLOW_WRITES", str(caught.exception))

    def test_more_than_five_attachments_are_refused(self):
        with self.assertRaises(Exception) as caught:
            server.create_task(
                **self.ARGS,
                attachmentFiles=[f"f{n}_1.pdf" for n in range(5)],
                attachments=[server.Attachment(filename="x.pdf", contentBase64="eA==")],
                dryRun=True,
            )
        self.assertIn("At most 5", str(caught.exception))


def edit_form(editing="285556378", files=(("a_1.pdf", "111"),), comments="Linha 1 &amp; mais\nlinha 2", checked=""):
    items = "".join(
        f'<li><a href="posts-file/1/{name}" class="file-name">{name}</a>'
        f'<a href="javascript: void(0);" class="delete-file material-icons" title="Remover" data-file="{name}" '
        f'data-item="{code}">delete</a></li>'
        for name, code in files
    )
    return f"""
    <select class="processes" name="lawsuits_id"><option value="18175173" selected="selected">CLIENTE, PROCESSO</option></select>
    <select id="response-mention" data-guests="216759,49187" name="user"><option value="" selected="selected"></option></select>
    <select data-task="572240" data-step="3" class="response-tasks" name="tasks_id"><option value="" selected="selected"></option></select>
    <input name="has_partner" type="hidden" value="0"><input name="workflow_id" type="hidden" value="0">
    <input name="workflow_sequence" type="hidden" value="1"><input name="workflow_limit" type="hidden" value="0">
    <input class="date" name="date" type="text" value="08/10/2026"><input class="time" name="hour" type="text">
    <input class="date" name="date_deadline" type="text" value="15/10/2026"><input name="date_end" type="text">
    <input name="hour_end" type="text"><input name="local" type="text">
    <textarea class="c" name="comments">{comments}</textarea>
    <input name="important" type="checkbox" value="1" {checked}><input name="urgent" type="checkbox" value="1">
    <select name="recurrence"><option value="" selected="selected"></option><option value="daily">Diária</option></select>
    <select name="repeat_on_week"><option value="all" selected="selected">No dia exato</option></select>
    <ul class="modal-upload-files list-thumbs">{items}</ul>
    <input name="create_token" type="hidden" value="tok123"><input name="editing" type="hidden" value="{editing}">
    """


class TaskFormTest(unittest.TestCase):
    def test_form_values_are_read_like_the_browser_posts_them(self):
        form = parse_task_form(edit_form(checked="checked"))
        self.assertEqual(
            (form["editing"], form["createToken"], form["lawsuitId"], form["tasksId"]),
            ("285556378", "tok123", "18175173", "572240"),
        )
        self.assertEqual(form["guests"], [216759, 49187])
        self.assertEqual((form["date"], form["hour"], form["deadline"]), ("08/10/2026", "", "15/10/2026"))
        self.assertEqual(form["comments"], "Linha 1 & mais\nlinha 2")
        self.assertEqual(form["files"], [{"name": "a_1.pdf", "code": "111"}])
        self.assertTrue(form["flags"]["important"])
        self.assertFalse(form["flags"]["urgent"])
        self.assertEqual((form["recurrence"], form["repeatOnWeek"]), ("", "all"))

    def test_unrecognised_page_is_refused(self):
        with self.assertRaises(ValueError):
            parse_task_form("<html>login</html>")

    def test_unchanged_edit_reproduces_the_form_in_the_browsers_order(self):
        body = build_edit_body(parse_task_form(edit_form()))
        self.assertEqual(
            list(body),
            [
                "lawsuits_id",
                "user",
                "squad",
                "guests[]",
                "has_partner",
                "tasks_id",
                "workflow_id",
                "workflow_sequence",
                "workflow_limit",
                "workflow",
                "date",
                "hour",
                "date_deadline",
                "date_end",
                "hour_end",
                "local",
                "comments",
                "recurrence",
                "repeat_on_week",
                "create",
                "ai_suggestion",
                "create_token",
                "editing",
                "v2",
            ],
        )
        self.assertEqual(
            (body["editing"], body["create_token"], body["guests[]"]), ("285556378", "tok123", ["216759", "49187"])
        )
        self.assertNotIn("important", body)
        self.assertNotIn("file[]", body)

    def test_changes_are_applied_and_the_rest_is_preserved(self):
        form = parse_task_form(edit_form(checked="checked"))
        body = build_edit_body(
            form,
            append_comments="Mais uma.",
            deadline="",
            hour="09:30",
            guests=[49187],
            account_id=216759,
            file_codes=["9"],
        )
        self.assertEqual(body["comments"], "Linha 1 & mais\nlinha 2\n\nMais uma.")
        self.assertEqual((body["date_deadline"], body["hour"], body["date"]), ("", "09:30", "08/10/2026"))
        self.assertEqual(body["guests[]"], ["49187", "216759"])  # the integration account is kept as a guest
        self.assertEqual((body["important"], body["file[]"]), ("1", ["9"]))
        replaced = build_edit_body(form, comments="Novo texto")
        self.assertEqual(replaced["comments"], "Novo texto")

    def test_invalid_values_are_refused(self):
        form = parse_task_form(edit_form())
        for kwargs in ({"date": "2026-10-09"}, {"deadline": "9/10/26"}, {"hour": "9h"}, {"date": ""}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                build_edit_body(form, **kwargs)


class UpdateTaskTest(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {"ADVBOX_ALLOW_WRITES": "true", "ADVBOX_UPLOAD_FOLDER": "files/temp/216759"})
        env.start()
        self.addCleanup(env.stop)
        self.session = mock.Mock(spec=AdvboxSession)
        self.session.using.side_effect = lambda handler: handler(self.session)
        self.client = AdvboxClient(session=self.session)
        self.client.list_activities = mock.Mock(return_value={"records": [{"postId": "285556378"}]})

    def test_stale_completed_or_deleted_tasks_are_refused(self):
        self.client.list_activities.return_value = {"records": [{"postId": "1"}]}
        self.serve(edit_form())
        with self.assertRaises(ValueError) as caught:
            self.client.update_task("285556378", {})
        self.assertIn("not among the pending tasks", str(caught.exception))
        self.session.post.assert_not_called()

    def test_pending_lookup_pages_through_full_pages_only(self):
        full = {"records": [{"postId": str(n)} for n in range(100)]}
        self.client.list_activities.side_effect = [full, {"records": [{"postId": "285556378"}]}]
        self.assertTrue(self.client.is_pending("285556378", [216759]))
        self.assertEqual(self.client.list_activities.call_count, 2)
        self.client.list_activities.side_effect = [{"records": [{"postId": "7"}]}]
        self.assertFalse(self.client.is_pending("285556378", [216759]))

    def serve(self, *forms):
        self.session.get_html.side_effect = list(forms)
        self.session.post.return_value = {
            "create": True,
            "message": 'Tarefa editada com sucesso. <a href="posts/777/edit">x</a>',
            "kanban": {"add": 777, "remove": "285556378"},
        }

    def test_update_posts_the_browser_body_and_reports_the_replacement(self):
        self.serve(edit_form())
        result = self.client.update_task("285556378", {"append_comments": "Mais."})
        path, body = self.session.post.call_args.args
        self.assertEqual(path, "/posts")
        self.assertEqual((body["editing"], body["create_token"]), ("285556378", "tok123"))
        self.assertTrue(body["comments"].endswith("Mais."))
        self.assertEqual((result["postId"], result["replacedPostId"]), ("777", "285556378"))
        self.assertEqual(result["message"], "Tarefa editada com sucesso. x")

    def test_new_files_are_staged_and_sent_as_codes(self):
        self.serve(edit_form())
        self.session.post_multipart.return_value = "555"
        self.client.update_task("285556378", {}, [{"filename": "n_1.pdf", "bytes": b"%PDF"}])
        self.assertEqual(self.session.post.call_args.args[1]["file[]"], ["555"])

    def test_removed_files_are_deleted_from_the_replacement_by_its_own_code(self):
        self.serve(edit_form(), edit_form(editing="777", files=(("a_1.pdf", "222"), ("b_1.pdf", "333"))))
        result = self.client.update_task("285556378", {}, remove_files=["a_1.pdf"])
        delete = self.session.post.call_args_list[-1]
        self.assertEqual(delete.args[0], "/s3")
        self.assertEqual(delete.args[1], {"id": "222", "type": "file", "customer": "", "_method": "DELETE"})
        self.assertEqual(result["removedFiles"], ["a_1.pdf"])

    def test_guards_refuse_before_anything_is_sent(self):
        cases = (
            (edit_form(editing="999"), {}, {}),
            (edit_form(), {"expect_comments": "outro texto"}, {}),
            (edit_form(), {"remove_files": ["nao_existe.pdf"]}, {}),
        )
        for form, kwargs, _ in cases:
            with self.subTest(kwargs=kwargs):
                self.serve(form)
                with self.assertRaises(ValueError):
                    self.client.update_task("285556378", {}, **kwargs)
                self.session.post.assert_not_called()
                self.session.post_multipart.assert_not_called()

    def test_preview_changes_nothing_and_hides_the_token(self):
        self.serve(edit_form())
        preview = self.client.preview_update("285556378", {"deadline": "20/10/2026"})
        self.assertTrue(preview["dryRun"])
        self.assertEqual(preview["body"]["create_token"], "<token>")
        self.assertEqual(preview["files"], ["a_1.pdf"])
        self.session.post.assert_not_called()

    def test_write_gate_applies(self):
        self.serve(edit_form())
        with mock.patch.dict(os.environ, {"ADVBOX_ALLOW_WRITES": "false"}), self.assertRaises(Exception):
            self.client.update_task("285556378", {})


if __name__ == "__main__":
    unittest.main()
