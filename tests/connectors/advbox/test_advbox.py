import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from jurisync.connectors.advbox.client import AdvboxClient, build_activities_body, table_path
from jurisync.connectors.advbox.documents import download_filename, folder_zip_url, is_download_url, read_bounded
from jurisync.connectors.advbox.parsers import (
    build_create_body,
    normalize_table,
    parse_activity_details,
    parse_activity_row,
    parse_create_response,
    parse_csrf_token,
    parse_file_tree,
    parse_hidden_inputs,
    parse_lawsuit_report,
    parse_members,
    parse_workflow_steps,
    parse_workflows,
)
from jurisync.connectors.advbox.session import AdvboxSession, apply_set_cookies, is_expired_session
from jurisync.core.pdf import available_path


class WriteGuardTest(unittest.TestCase):
    def test_direct_mutation_calls_require_write_capability(self):
        client = AdvboxClient(session=mock.Mock(spec=AdvboxSession))
        session = AdvboxSession()
        try:
            with mock.patch.dict(os.environ, {"ADVBOX_ALLOW_WRITES": "false"}):
                for operation in (
                    lambda: client.create_task({}),
                    lambda: client.delete_task("1"),
                    lambda: client.complete_task("1"),
                    lambda: client.stage_files([{"filename": "file.pdf", "bytes": b"x"}]),
                    lambda: session.delete("/posts/1"),
                    lambda: session.post_multipart("/s3", {}),
                ):
                    with self.assertRaisesRegex(PermissionError, "writes are disabled"):
                        operation()
            client.session.post.assert_not_called()
        finally:
            session.close()

    def test_server_write_tools_refuse_before_any_request(self):
        from mcp.client.client import Client

        from jurisync.connectors.advbox import server

        async def call():
            async with Client(server.server) as client:
                return await client.call_tool("advbox_delete_task", {"postId": "1"})

        with (
            mock.patch.dict(os.environ, {"ADVBOX_ALLOW_WRITES": "false"}),
            mock.patch.object(server.client, "delete_task") as delete,
        ):
            result = asyncio.run(call())
        self.assertTrue(result.is_error)
        self.assertIn("writes are disabled", result.content[0].text)
        delete.assert_not_called()


class BoundedResponse:
    def __init__(self, chunks, length=None):
        self.chunks, self.headers = chunks, ({"content-length": str(length)} if length else {})

    def iter_content(self):
        yield from self.chunks


class RequestShapeTest(unittest.TestCase):
    def test_reader_enforces_byte_limit_while_streaming(self):
        with self.assertRaisesRegex(RuntimeError, "exceeds the 5-byte download limit"):
            read_bounded(BoundedResponse([b"\x01\x02\x03", b"\x04\x05\x06"]), "file", 5)
        with self.assertRaisesRegex(RuntimeError, "exceeds the 5-byte download limit"):
            read_bounded(BoundedResponse([], length=6), "file", 5)
        self.assertEqual(read_bounded(BoundedResponse([b"\x01\x02\x03"]), "file", 5), b"\x01\x02\x03")

    def test_list_paths_map_pagination_search_and_lawsuit_stages(self):
        customers = parse_qs(
            urlsplit(table_path("customers", page=2, pageSize=25, search="Silva")).query, keep_blank_values=True
        )
        self.assertEqual(customers["iDisplayStart"], ["25"])
        self.assertEqual(customers["iDisplayLength"], ["25"])
        self.assertEqual(customers["search"], ["Silva"])
        lawsuits = parse_qs(urlsplit(table_path("lawsuits")).query, keep_blank_values=True)
        self.assertEqual(lawsuits["steps_id[]"], ["9", "1", "6", "2", "3", "4", "5", "7"])
        self.assertEqual(lawsuits["date_from"], [""])

    def test_activities_body_mirrors_captured_request(self):
        body = parse_qs(
            build_activities_body(
                "tok3n",
                page=3,
                pageSize=20,
                search="pericia",
                completed="pendentes",
                responsibleIds=[100001],
                dateFrom="01/09/2026",
            ),
            keep_blank_values=True,
        )
        expected = {
            "_token": "tok3n",
            "iColumns": "9",
            "sColumns": ",important,urgent,,task,names_order,start,posts.date_deadline,users.name",
            "iDisplayStart": "40",
            "iDisplayLength": "20",
            "sSearch": "pericia",
            "completed": "pendentes",
            "page": "posts",
            "start_from": "01/09/2026",
            "bSortable_0": "false",
            "bSortable_1": "true",
        }
        for key, value in expected.items():
            self.assertEqual(body[key], [value], key)
        self.assertEqual(body["multiple_users_id[]"], ["100001"])
        self.assertEqual(body["priority[]"], ["1", "2", "3"])


class SessionTest(unittest.TestCase):
    def test_cookie_jar_merges_set_cookie_and_drops_deletions(self):
        jar = {}
        apply_set_cookies(
            jar, ["advbox_session=xyz; Path=/; HttpOnly", "XSRF-TOKEN=tok; Expires=Thu, 01 Jan 1970 00:00:00 GMT"]
        )
        self.assertEqual(jar["advbox_session"]["value"], "xyz")
        self.assertNotIn("XSRF-TOKEN", jar)
        apply_set_cookies(jar, ["advbox_session=deleted; expires=Thu, 01 Jan 1970 00:00:00 GMT"])
        self.assertNotIn("advbox_session", jar)

    def test_cached_session_expiry(self):
        now = time.time() * 1000
        self.assertTrue(is_expired_session(None, now))
        self.assertTrue(is_expired_session({"cookies": []}, now))
        self.assertFalse(is_expired_session({"cookies": [["advbox_session", "x"]]}, now))
        self.assertTrue(is_expired_session({"cookies": [["advbox_session", "x"]], "expiresAt": now + 10000}, now))
        self.assertFalse(is_expired_session({"cookies": [["advbox_session", "x"]], "expiresAt": now + 120000}, now))


class ParserTest(unittest.TestCase):
    def test_table_rows_become_named_records(self):
        result = normalize_table(
            {
                "iTotalRecords": 1,
                "iTotalDisplayRecords": 1,
                "iTotalPage": 1,
                "data": '[["<a href=\\"/lawsuits/42/edit\\">Maria &amp; João</a>", "Cível"]]',
            },
            ["parties", "type"],
        )
        self.assertEqual(
            result["records"], [{"parties": "Maria & João", "type": "Cível", "links": ["/lawsuits/42/edit"]}]
        )

    def test_hidden_fields_are_extracted_and_decoded(self):
        fields = parse_hidden_inputs(
            '<form><input type="hidden" name="_token" value="abc&amp;def">'
            '<input type="hidden" name="_device" value=""><input name="email"></form>'
        )
        self.assertEqual(fields, {"_token": "abc&def", "_device": ""})

    def test_activity_row(self):
        row = [
            '<span class="styles hidden">late</span> <a href="posts/status/167516185" data-field="completed" data-value="1" data-id="162717821" data-lawsuit="11176564" data-task="6886198" data-post="167516185"></a>',
            '<a data-field="important" data-value="1" data-id="162717821"></a>',
            '<a data-field="urgent" data-value="0" data-id="162717821"></a>',
            '<a data-field="viewed" data-value="0" data-id="162717821"></a>',
            '<a href="posts/167516185/edit" class="link" title="fazer novo beneficio">ENTRAR COM BENEFÍCIO: Fazer novo beneficio</a>',
            '<a href="customers/10463652/edit" class="link popup">CLIENTE EXEMPLO</a>',
            '<span class="red">08/12/2025 10:00</span>',
            '<span class="red" title="08/10/2025">11 meses atrás</span>',
            '<ul class=\'list-users\'><li><div class="user-profile"><img src="https://s3-sa-east-1.amazonaws.com/advbox/files/profiles/100001.jpg" onerror="this.src=\'images/user.png\'" class="profile" title="Pessoa Exemplo Um"></div></li><li><div class="user-profile"><img src="https://s3-sa-east-1.amazonaws.com/advbox/files/profiles/100002.jpg" class="profile" title="Pessoa Exemplo Dois"></div></li></ul>',
        ]
        activity = parse_activity_row(row)
        self.assertEqual(
            (activity["postId"], activity["activityId"], activity["lawsuitId"], activity["taskTemplateId"]),
            ("167516185", "162717821", "11176564", "6886198"),
        )
        self.assertEqual(activity["task"], "ENTRAR COM BENEFÍCIO: Fazer novo beneficio")
        self.assertEqual(activity["detail"], "fazer novo beneficio")
        self.assertEqual((activity["important"], activity["urgent"], activity["viewed"]), (True, False, False))
        self.assertEqual(activity["customer"], {"id": "10463652", "name": "CLIENTE EXEMPLO"})
        self.assertEqual(activity["start"], "08/12/2025 10:00")
        self.assertEqual(activity["deadline"], "08/10/2025")
        self.assertEqual(activity["deadlineLabel"], "11 meses atrás")
        self.assertTrue(activity["overdue"])
        self.assertEqual(
            activity["responsible"],
            [{"id": "100001", "name": "Pessoa Exemplo Um"}, {"id": "100002", "name": "Pessoa Exemplo Dois"}],
        )

    def test_csrf_token_either_attribute_order(self):
        self.assertEqual(parse_csrf_token('<meta name="csrf-token" content="abc123">'), "abc123")
        self.assertEqual(parse_csrf_token("<meta content='abc123' name='csrf-token'>"), "abc123")
        self.assertIsNone(parse_csrf_token("<html></html>"))

    def test_lawsuit_report(self):
        html = """<html><body>Página de impressão Imprimir VICTOR HUGO ADVOGADOS (87) 3831-7769 atendimento@vhadvogados.com
  Relatório emitido em 23/09/2026 19:08:18 &nbsp; Processo N&ordm; 0012250-98.2026.4.05.8303 - PREVIDENCIÁRIO Partes EDVONALDO BARBOSA DA SILVA
  <table class="info"><tr><td align="right">Fase</td><td>AGUARDANDO REALIZAÇÃO DE PERÍCIA MÉDICA</td></tr><tr><td align="right">Vara</td><td>18º</td></tr></table>
  <h3>Histórico de tarefas</h3><table class="data group"><thead><tr><th>Data</th><th>Hora</th><th>Descrição</th><th>Responsável</th></tr></thead>
  <tbody><tr><td>07/09/2026</td><td></td><td>PROTOCOLAR PREV - JUD</td><td>SAMUEL DA SILVA</td></tr></tbody></table></body></html>"""
        report = parse_lawsuit_report(html)
        self.assertEqual(report["office"], "VICTOR HUGO ADVOGADOS")
        self.assertEqual(report["reportIssuedAt"], "23/09/2026 19:08:18")
        self.assertEqual(report["processNumber"], "0012250-98.2026.4.05.8303")
        self.assertEqual(report["actionGroup"], "PREVIDENCIÁRIO")
        self.assertEqual(report["info"], {"Fase": "AGUARDANDO REALIZAÇÃO DE PERÍCIA MÉDICA", "Vara": "18º"})
        self.assertEqual(
            report["sections"]["Histórico de tarefas"],
            [
                {
                    "Data": "07/09/2026",
                    "Hora": "",
                    "Descrição": "PROTOCOLAR PREV - JUD",
                    "Responsável": "SAMUEL DA SILVA",
                }
            ],
        )

    def test_file_tree(self):
        html = """<ul class="files">
  <li class="queue sync completed"> <a href="javascript: void(0);" class="item folder open-folder hide-upload" data-path="Anexado em tarefas"> <i class="thumb material-icons">folder</i> <div class="info"> <p class="name">Anexado em tarefas</p> <p class="datetime">14 itens &bull; 17.1 MB</p> </div> </a> </li>
  <li class="queue sync completed"> <a href="files/10473994/0-PA_3489.pdf?f=38591366" class="item open-new-page" target="_blank"> <img src="images/file-manager-pdf.png" class="thumb"> <div class="info"> <p class="name">0-PA_3489.pdf</p> <input maxlength="140" data-type="file" data-item="38591366" name="name" type="text"> <p class="datetime"> 1 Set 26 &bull; 8.6 MB</p> </div> </a> </li>
  <li class="queue sync completed"> <a href="posts-file/134533798/4-DOCUMENTO_7873.pdf?f=19104953" class="item open-new-page" target="_blank"> <div class="info"> <p class="name">4-DOCUMENTO_7873.pdf</p> <input data-type="file" data-item="19104953" name="name" type="text"> <p class="datetime"> 8 Jul 25 &bull; 1.3 MB</p> </div> </a> </li>
  </ul>"""
        tree = parse_file_tree(html)
        self.assertEqual(
            tree["folders"],
            [{"path": "Anexado em tarefas", "name": "Anexado em tarefas", "itemCount": 14, "size": "17.1 MB"}],
        )
        first, second = tree["files"]
        self.assertEqual((first["name"], first["fileId"], first["sourcePostId"]), ("0-PA_3489.pdf", "38591366", None))
        self.assertEqual(first["url"], "https://app.advbox.com.br/files/10473994/0-PA_3489.pdf?f=38591366")
        self.assertEqual(
            first["downloadUrl"], "https://app.advbox.com.br/files/10473994/0-PA_3489.pdf?f=38591366&download=true"
        )
        self.assertEqual((first["uploadedAt"], first["size"]), ("1 Set 26", "8.6 MB"))
        self.assertEqual(second["sourcePostId"], "134533798")
        self.assertEqual(
            second["url"], "https://app.advbox.com.br/posts-file/134533798/4-DOCUMENTO_7873.pdf?f=19104953"
        )

    def test_activity_details(self):
        html = """<h1 class="task-title">Protocolar Prev - Jud<span class="reward">50pts</span></h1>
  <p class="column-label">Grupo de ação</p><p class="column-description">Previdenciário</p>
  <p class="column-label">Processo</p> <p class="column-description"> 0012250-98.2026.4.05.8303 <a href="javascript: void(0);" class="copy-clipboard" data-copy="0012250-98.2026.4.05.8303">content_copy</a> <a href="lawsuits/17519594/edit?post=279422924" class="url close popup small">Visualizar processo</a></p>"""
        details = parse_activity_details(html)
        self.assertEqual(
            (details["title"], details["points"], details["lawsuitId"], details["processNumber"]),
            ("Protocolar Prev - Jud", "50", "17519594", "0012250-98.2026.4.05.8303"),
        )
        self.assertEqual(details["fields"]["Grupo de ação"], "Previdenciário")
        self.assertIn("Visualizar processo", details["fields"]["Processo"])

    def test_members_usernames_and_groups(self):
        html = (
            '<select name="user"><option value=""></option><option value="200001">USUARIO EXEMPLO UM</option><option value="200002">USUARIO EXEMPLO DOIS</option></select>'
            '<div id="mentions_list" data-users="[{&quot;id&quot;:200001,&quot;name&quot;:&quot;Usuario Exemplo Um&quot;,&quot;username&quot;:&quot;usuarioexemplo&quot;}]"></div>'
            '<select name="squad"><option value="3328" data-members="200001,200002">CONTROLADORIA</option></select>'
        )
        members = parse_members(html)
        self.assertEqual(
            members["users"],
            [
                {"id": 200001, "name": "USUARIO EXEMPLO UM", "username": "usuarioexemplo"},
                {"id": 200002, "name": "USUARIO EXEMPLO DOIS", "username": ""},
            ],
        )
        self.assertEqual(members["groups"], [{"id": 3328, "name": "CONTROLADORIA", "memberIds": [200001, 200002]}])

    def test_workflows_and_steps(self):
        self.assertEqual(
            parse_workflows(
                '<select name="workflow"><option value=""></option><option value="17760">SS - PROTOCOLO JUD</option><option value="12051">&#039; - CUMPRIR ATO</option></select>'
            ),
            [{"id": 17760, "name": "SS - PROTOCOLO JUD"}, {"id": 12051, "name": "' - CUMPRIR ATO"}],
        )
        page = parse_workflow_steps(
            '<h1 class="title">Workflow Ss - Protocolo Jud <a>x</a></h1> <h2 class="subtitle">Tarefa <span id="workflow-seq">2</span> de 2</h2>'
            '<a data-task="9318097" data-user="132063">1. Processo Protocolado-jud</a>'
            '<a data-task="5795027" data-user="">2. Enviar Video</a>'
        )
        self.assertEqual((page["sequence"], page["total"]), (2, 2))
        self.assertEqual(
            page["steps"],
            [
                {"taskId": 9318097, "defaultUserId": 132063, "title": "Processo Protocolado-jud"},
                {"taskId": 5795027, "defaultUserId": None, "title": "Enviar Video"},
            ],
        )

    def test_create_body_and_response(self):
        with self.assertRaisesRegex(ValueError, "lawsuitId"):
            build_create_body(tasksId="1", guests=["200002"])
        with self.assertRaisesRegex(ValueError, "tasksId"):
            build_create_body(lawsuitId="1", guests=["200002"])
        with self.assertRaisesRegex(ValueError, "guest"):
            build_create_body(lawsuitId="1", tasksId="1", guests=[])
        body = build_create_body(
            lawsuitId="6725946",
            tasksId="5347991",
            guests=["200002"],
            workflowId="7081",
            date="28/09/2026",
            hour="16:00",
            comments="x",
        )
        self.assertEqual(
            (body["lawsuits_id"], body["guests[]"], body["user"], body["workflow_id"], body["create"]),
            ("6725946", ["200002"], "", "7081", "1"),
        )
        self.assertEqual(build_create_body(lawsuitId="1", tasksId="2", guests=["200002"])["workflow_id"], "0")
        parsed = parse_create_response('{"event":"message","create":true,"kanban":{"add":280921715,"remove":""}}')
        self.assertEqual((parsed["postId"], parsed["created"]), ("280921715", True))
        with self.assertRaisesRegex(ValueError, "no post ID"):
            parse_create_response('{"event":"message"}')


class DownloadTest(unittest.TestCase):
    def test_download_url_guard(self):
        self.assertTrue(is_download_url("https://app.advbox.com.br/files/10473994/x.pdf?f=1"))
        self.assertTrue(is_download_url("https://app.advbox.com.br/posts-file/134533798/x.pdf?f=1&download=true"))
        self.assertTrue(is_download_url("https://app.advbox.com.br/file/download?customer=10473994&path=00-PROTOCOLAR"))
        self.assertFalse(is_download_url("https://app.advbox.com.br/content/posts"))
        self.assertFalse(is_download_url("https://evil.example.com/files/x.pdf"))
        self.assertFalse(is_download_url("not-a-url"))

    def test_download_filename(self):
        self.assertEqual(download_filename('attachment; filename="LAUDO.pdf"', "fallback.pdf"), "LAUDO.pdf")
        self.assertEqual(
            download_filename("attachment; filename*=UTF-8''LAUDO%20M%C3%89DICO.pdf", "f"), "LAUDO MÉDICO.pdf"
        )
        self.assertEqual(download_filename(None, "../../etc/passwd"), "passwd")
        self.assertEqual(download_filename(None, ""), "advbox-download")
        self.assertEqual(download_filename('attachment; filename="DECIS\u00c3\u0083O.pdf"', "f"), "DECISÃO.pdf")
        self.assertEqual(download_filename('attachment; filename="100%.pdf"', "f"), "100%.pdf")

    def test_available_path_suffixes_without_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(available_path(directory, "x.pdf"), Path(directory) / "x.pdf")
            (Path(directory) / "x.pdf").write_text("a")
            self.assertEqual(available_path(directory, "x.pdf"), Path(directory) / "x-1.pdf")
            with self.assertRaises(ValueError):
                available_path(directory, "../x.pdf")

    def test_folder_zip_url(self):
        self.assertEqual(
            folder_zip_url("10473994", "00-PROTOCOLAR"),
            "https://app.advbox.com.br/file/download?customer=10473994&path=00-PROTOCOLAR",
        )
        self.assertEqual(
            folder_zip_url("1", "A/B C"), "https://app.advbox.com.br/file/download?customer=1&path=A%2FB%20C"
        )


if __name__ == "__main__":
    unittest.main()
