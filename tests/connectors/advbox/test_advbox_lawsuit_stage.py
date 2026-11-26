"""Offline tests for the lawsuit stage change (advbox_update_lawsuit_stage and advbox_create_task stageId).

The fixture is a sanitized, shortened copy of the lawsuit edit form (``GET /lawsuits/<id>/edit``) recorded on
Victor's office on 2026-11-23: names, ids and the token are placeholders, the field set and markup are the page's.
"""

import asyncio
import contextlib
import json
import os
import unittest
from unittest import mock

from curl_cffi.requests.exceptions import Timeout
from mcp.server.mcpserver.exceptions import ToolError

from jurisync.connectors.advbox import client as client_module
from jurisync.connectors.advbox import parsers, server
from jurisync.connectors.advbox.client import AdvboxClient, UnconfirmedWriteError
from jurisync.connectors.advbox.session import AdvboxSession

STEPS = (
    '<option value=""></option><option value="9">Marketing</option><option value="1"{n}>Negociação</option>'
    '<option value="3"{j}>Judicial</option><option value="8">Arquivamento</option>'
)


def lawsuit_page(step="1", stage="7090", lawsuit="555"):
    sub = {
        "1": '<option value="7090"{a}>1 - ATENDIMENTO/AN&Aacute;LISE DO CASO</option><option value="8155"{b}>2 - LEAD</option>',
        "3": '<option value="8144"{a}>AN&Aacute;LISE/PETI&Ccedil;&Atilde;O INICIAL</option><option value="7092"{b}>AJUIZADO</option>',
    }[step]
    first = {"1": "7090", "3": "8144"}[step]
    selected = ' selected="selected"'
    return f"""
<div class="popup-main"><form method="POST" action="https://app.advbox.com.br/lawsuits/{lawsuit}" accept-charset="UTF-8"
 class="lawsuit-data"><input name="_method" type="hidden" value="PUT"><input name="_token" type="hidden" value="TOKEN">
<select data-placeholder="Adicionar outra parte" class="autocomplete all no-trigger" name="customer"><option value="" selected="selected"></option></select>
<select class="no-trigger" data-id="100" data-name="CLIENTE" name="customer_type"><option value="1" selected="selected">Cliente</option><option value="3">Outro</option></select>
<select multiple name="customer_list[]"><option value="100" selected="selected">CLIENTE</option><option value="101" selected="selected">OUTRO</option></select>
<select multiple name="contrary_list[]"></select>
<textarea placeholder="Anota&ccedil;&otilde;es" class="autosize" rows="1" cols="" name="notes">
linha &amp; nota</textarea>
<select name="groups_id"><option value="">x</option><option value="98377" selected="selected">Atendimento</option></select>
<input placeholder="9999999-99.9999.9.99.9999" class="checkexists process_cnj" name="process_number" type="text">
<input placeholder="DD/MM/AAAA" class="date" name="registration" type="text" value="13/09/2024">
<input class="money no-blur" maxlength="15" name="fees_money" type="text" value="0,00">
<input name="disabled_field" type="text" value="x" disabled>
<select multiple name="account_list[]"><option value="77478">Parceiro</option></select>
<select data-allow-clear="false" name="users_id"><option value="1">A</option><option value="2" selected="selected">B</option></select>
<select data-placeholder="" class="list-stages no-trigger" data-allow-clear="false" name="steps_id">{STEPS.format(n=selected if step == "1" else "", j=selected if step == "3" else "")}</select>
<label><span>Fase</span></label>
<select data-placeholder="Selecione a etapa do processo" class="response-stages" data-allow-clear="true" name="stages_id"><option value=""></option>{sub.format(a=selected if stage == first else "", b="" if stage == first else selected)}</select>
<label><span>Etapa</span></label>
<input class="checkbox_icon" name="send_sms" type="checkbox" value="1">
<input class="checkbox_icon" name="send_email" type="checkbox" value="1">
<input type="submit" style="display: none;" />
</form>
<form method="POST" action="https://app.advbox.com.br/file/search" class="file-manager-search"><select class="changeFolder" name="customer"><option value="100" selected>C</option></select></form></div>
"""


SUB_STAGES = {
    "1": [
        {"id": 7090, "steps_id": 1, "stage": "1 - ATENDIMENTO/ANÁLISE DO CASO", "workflows_id": None, "is_active": 1},
        {"id": 8155, "steps_id": 1, "stage": "2 - LEAD", "workflows_id": None, "is_active": 1},
    ],
    "3": [
        {"id": 8144, "steps_id": 3, "stage": "ANÁLISE/PETIÇÃO INICIAL", "workflows_id": None, "is_active": 1},
        {"id": 7092, "steps_id": 3, "stage": "AJUIZADO", "workflows_id": 1410, "is_active": 1},
        {"id": 9999, "steps_id": 3, "stage": "OLD", "workflows_id": None, "is_active": 0},
    ],
}


class LawsuitFormParserTest(unittest.TestCase):
    def test_the_form_is_serialized_like_the_screen_sends_it(self):
        form = parsers.parse_lawsuit_form(lawsuit_page())
        self.assertEqual((form["lawsuitId"], form["stageId"], form["subStageId"]), ("555", "1", "7090"))
        self.assertEqual([option["id"] for option in form["stages"]], ["9", "1", "3", "8"])
        self.assertEqual(form["subStages"][0], {"id": "7090", "label": "1 - ATENDIMENTO/ANÁLISE DO CASO"})
        pairs = form["pairs"]
        self.assertEqual(pairs[0], ["_method", "PUT"])
        self.assertIn(["customer_list[]", "100"], pairs)
        self.assertIn(["customer_list[]", "101"], pairs)
        self.assertIn(["notes", "linha & nota"], pairs)  # the newline after <textarea> is dropped, entities decoded
        self.assertIn(["users_id", "2"], pairs)
        names = [name for name, _ in pairs]
        # Unchecked checkboxes, disabled fields, empty multi-selects, submit buttons and the second form stay out.
        for absent in ("send_sms", "send_email", "disabled_field", "contrary_list[]", "account_list[]"):
            self.assertNotIn(absent, names)
        self.assertEqual(names.count("customer"), 1)

    def test_a_single_select_without_a_selected_option_sends_its_first(self):
        html = lawsuit_page().replace(
            '<option value="2" selected="selected">B</option>', '<option value="2">B</option>'
        )
        self.assertIn(["users_id", "1"], parsers.parse_lawsuit_form(html)["pairs"])

    def test_the_body_changes_only_the_two_stage_fields_and_leaves_the_token_to_the_session(self):
        form = parsers.parse_lawsuit_form(lawsuit_page())
        body = parsers.build_lawsuit_stage_body(form, "3", "8144")
        self.assertNotIn("_token", body)
        self.assertEqual((body["steps_id"], body["stages_id"], body["_method"]), ("3", "8144", "PUT"))
        self.assertEqual(body["customer_list[]"], ["100", "101"])
        unchanged = {name: value for name, value in form["pairs"] if name not in ("_token", "steps_id", "stages_id")}
        for name, value in unchanged.items():
            if not name.endswith("[]"):
                self.assertEqual(body[name], value, name)

    def test_unrecognised_pages_are_refused(self):
        with self.assertRaises(ValueError):
            parsers.parse_lawsuit_form("<html>login</html>")
        with self.assertRaises(ValueError):
            parsers.parse_lawsuit_form(lawsuit_page().replace('value="PUT"', 'value="DELETE"'))
        with self.assertRaises(ValueError):
            parsers.parse_lawsuit_form(lawsuit_page().replace('name="stages_id"', 'name="other"'))

    def test_sub_stages_keep_active_entries_and_their_workflow(self):
        options = parsers.parse_sub_stages(json.dumps(SUB_STAGES["3"]))
        self.assertEqual([option["id"] for option in options], ["8144", "7092"])
        self.assertEqual(options[1]["workflowId"], 1410)
        with self.assertRaises(ValueError):
            parsers.parse_sub_stages('{"error": 1}')


class StageCase(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {"ADVBOX_ALLOW_WRITES": "true", "ADVBOX_UPLOAD_FOLDER": "files/temp/216759"})
        env.start()
        self.addCleanup(env.stop)
        self.session = mock.Mock(spec=AdvboxSession)
        self.session.using.side_effect = lambda handler: handler(self.session)
        self.session.exclusive.side_effect = lambda: contextlib.nullcontext(self.session)
        self.pages = [lawsuit_page("1", "7090")]
        self.session.get_html.side_effect = self.get_html
        self.saved = []
        self.session.post.side_effect = self.post
        self.client = AdvboxClient(session=self.session)

    def get_html(self, path):
        self.assertEqual(path, "/lawsuits/555/edit")
        page = self.pages[0] if len(self.pages) == 1 else self.pages.pop(0)
        if isinstance(page, Exception):
            raise page
        return page

    def post(self, path, body):
        if path.startswith("/stages/"):
            return json.dumps(SUB_STAGES[path.rsplit("/", 1)[1]])
        self.saved.append((path, body))
        return {"message": "Processo atualizado"}

    def saves(self):
        return [path for path, _ in self.saved]


class UpdateLawsuitStageTest(StageCase):
    def test_change_is_sent_confirmed_and_returns_previous_and_new(self):
        self.pages = [lawsuit_page("1", "7090"), lawsuit_page("3", "8144")]
        result = self.client.update_lawsuit_stage("555", "3")
        self.assertEqual(self.saves(), ["/lawsuits/555"])
        body = self.saved[0][1]
        self.assertEqual((body["steps_id"], body["stages_id"]), ("3", "8144"))
        self.assertEqual(
            result["previous"],
            {
                "stageId": "1",
                "stage": "Negociação",
                "subStageId": "7090",
                "subStage": "1 - ATENDIMENTO/ANÁLISE DO CASO",
            },
        )
        self.assertEqual(result["new"]["stageId"], "3")
        self.assertEqual(result["new"]["subStage"], "ANÁLISE/PETIÇÃO INICIAL")
        self.assertTrue(result["changed"] and result["subStageDefaulted"])
        self.assertEqual(result["confirmedBy"], "lawsuit form read again")
        self.assertEqual(result["warnings"], [])

    def test_unknown_stage_or_sub_stage_is_refused_before_anything_is_sent(self):
        with self.assertRaisesRegex(ValueError, "Unknown ADVBOX stage id '42'.*3 \\(Judicial\\)"):
            self.client.update_lawsuit_stage("555", "42")
        with self.assertRaisesRegex(ValueError, "Unknown sub-stage id '7090' for stage 3"):
            self.client.update_lawsuit_stage("555", "3", "7090")
        with self.assertRaisesRegex(ValueError, "Unknown sub-stage id '9999'"):  # inactive
            self.client.update_lawsuit_stage("555", "3", "9999")
        self.assertEqual(self.saved, [])

    def test_same_stage_and_sub_stage_sends_nothing(self):
        result = self.client.update_lawsuit_stage("555", "1")
        self.assertFalse(result["changed"])
        self.assertEqual(self.saved, [])

    def test_a_sub_stage_with_a_workflow_is_flagged(self):
        self.pages = [lawsuit_page("1", "7090"), lawsuit_page("3", "7092")]
        result = self.client.update_lawsuit_stage("555", "3", "7092")
        self.assertIn("workflow 1410", result["warnings"][0])

    def test_dry_run_sends_nothing_and_needs_no_write_gate(self):
        with mock.patch.dict(os.environ, {"ADVBOX_ALLOW_WRITES": ""}):
            result = self.client.update_lawsuit_stage("555", "3", "7092", dry_run=True)
        self.assertTrue(result["dryRun"])
        self.assertEqual(result["new"]["subStageId"], "7092")
        self.assertEqual(self.saved, [])

    def test_the_write_gate_applies(self):
        with mock.patch.dict(os.environ, {"ADVBOX_ALLOW_WRITES": ""}), self.assertRaises(PermissionError):
            self.client.update_lawsuit_stage("555", "3")
        self.session.get_html.assert_not_called()

    def test_a_form_that_does_not_show_the_new_stage_is_not_confirmed(self):
        self.pages = [lawsuit_page("1", "7090"), lawsuit_page("1", "7090")]
        with self.assertRaisesRegex(UnconfirmedWriteError, "NOT confirmed"):
            self.client.update_lawsuit_stage("555", "3")

    def test_a_lost_answer_is_settled_by_reading_the_form_again(self):
        self.pages = [lawsuit_page("1", "7090"), lawsuit_page("3", "8144")]

        def post(path, body):
            if path.startswith("/stages/"):
                return json.dumps(SUB_STAGES["3"])
            self.saved.append((path, body))
            raise Timeout("no answer")

        self.session.post.side_effect = post
        result = self.client.update_lawsuit_stage("555", "3")
        self.assertEqual(result["new"]["stageId"], "3")
        self.assertEqual(len(self.saved), 1)  # never replayed

    def test_a_refusal_says_the_stage_was_not_changed(self):
        def post(path, body):
            if path.startswith("/stages/"):
                return json.dumps(SUB_STAGES["3"])
            raise RuntimeError("ADVBOX POST /lawsuits/555 failed (422)")

        self.session.post.side_effect = post
        with self.assertRaisesRegex(RuntimeError, "was not changed"):
            self.client.update_lawsuit_stage("555", "3")

    def test_other_fields_read_back_differently_are_reported(self):
        after = lawsuit_page("3", "8144").replace('value="13/09/2024"', 'value="14/09/2024"')
        self.pages = [lawsuit_page("1", "7090"), after]
        result = self.client.update_lawsuit_stage("555", "3")
        self.assertIn("registration", result["warnings"][0])

    def test_a_burst_limit_413_is_waited_out(self):
        self.pages = [RuntimeError("ADVBOX GET /lawsuits/555/edit failed (413)"), lawsuit_page("1", "7090")]
        with mock.patch.object(client_module.time, "sleep") as sleep:
            form = self.client.lawsuit_form("555")
        self.assertEqual(form["stageId"], "1")
        sleep.assert_called_once_with(client_module.LAWSUIT_READ_WAIT)


class CreateTaskWithStageTest(StageCase):
    OPTIONS = {"lawsuitId": "555", "tasksId": "2", "guests": [216759], "date": "09/10/2026", "comments": "x"}

    def setUp(self):
        super().setUp()
        self.created = []
        self.client._create_task = mock.Mock(side_effect=self.create)

    def create(self, *args):
        self.created.append(args)
        return {"postId": "777", "created": True, "staged": []}

    def test_the_lawsuit_moves_after_the_task_is_created(self):
        self.pages = [lawsuit_page("1", "7090"), lawsuit_page("1", "7090"), lawsuit_page("3", "8144")]
        result = self.client.create_task(self.OPTIONS, stage_id="3")
        self.assertEqual(result["postId"], "777")
        self.assertEqual((result["stage"]["previous"]["stageId"], result["stage"]["new"]["stageId"]), ("1", "3"))
        self.assertEqual(result["warnings"], [])

    def test_an_unknown_stage_is_refused_before_the_task_is_written(self):
        with self.assertRaises(ValueError):
            self.client.create_task(self.OPTIONS, stage_id="42")
        self.assertEqual(self.created, [])

    def test_a_failed_move_after_the_task_exists_is_reported_not_raised(self):
        self.pages = [lawsuit_page("1", "7090"), lawsuit_page("1", "7090"), lawsuit_page("1", "7090")]
        result = self.client.create_task(self.OPTIONS, stage_id="3")
        self.assertEqual(result["postId"], "777")
        self.assertIn("NOT confirmed", result["stage"]["error"])
        self.assertIn("Do not create the task again", result["warnings"][0])

    def test_without_a_stage_nothing_about_the_lawsuit_is_read(self):
        self.client.create_task(self.OPTIONS)
        self.session.get_html.assert_not_called()


class ToolTest(unittest.TestCase):
    def call(self, name, arguments):
        return asyncio.run(server.create_server().call_tool(name, arguments))

    def test_tool_passes_ids_and_dry_run(self):
        with mock.patch.object(server, "client") as fake:
            fake.update_lawsuit_stage.return_value = {"dryRun": True}
            self.call(
                "advbox_update_lawsuit_stage",
                {"lawsuitId": "555", "stageId": "3", "subStageId": "7092", "dryRun": True},
            )
        fake.update_lawsuit_stage.assert_called_once_with("555", "3", "7092", dry_run=True)

    def test_non_numeric_ids_are_refused(self):
        for arguments in (
            {"lawsuitId": "555", "stageId": "Judicial"},
            {"lawsuitId": "555", "stageId": "3", "subStageId": "x"},
        ):
            with self.subTest(arguments=arguments), self.assertRaises(ToolError):
                self.call("advbox_update_lawsuit_stage", arguments)

    def test_create_task_forwards_the_stage_and_needs_a_stage_for_a_sub_stage(self):
        base = {"lawsuitId": "555", "tasksId": "2", "guests": [216759], "date": "2026-12-15", "dryRun": True}
        with mock.patch.object(server, "client") as fake:
            fake.preview_task.return_value = {"dryRun": True}
            self.call("advbox_create_task", {**base, "stageId": "3", "subStageId": "8144"})
            self.assertEqual(
                fake.preview_task.call_args.kwargs, {"dedupe_key": "", "stage_id": "3", "sub_stage_id": "8144"}
            )
            with self.assertRaisesRegex(ToolError, "subStageId needs stageId"):
                self.call("advbox_create_task", {**base, "subStageId": "8144"})


class CompletedTaskTest(unittest.TestCase):
    """Live 2026-11-23: ADVBOX answers 403 to the edit form of a completed task and refuses to delete it."""

    def test_delete_of_a_completed_task_says_so_and_sends_nothing(self):
        session = mock.Mock(spec=AdvboxSession)
        session.exclusive.side_effect = lambda: contextlib.nullcontext(session)
        client = AdvboxClient(session=session)
        client.read_task_form = mock.Mock(side_effect=RuntimeError("ADVBOX GET /posts/create failed (403)"))
        with mock.patch.dict(os.environ, {"ADVBOX_ALLOW_WRITES": "true"}):
            with self.assertRaisesRegex(ValueError, "probably completed.*reopen the task"):
                client.delete_task("303177366")
        session.post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
