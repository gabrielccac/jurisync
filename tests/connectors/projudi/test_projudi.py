import asyncio
import json
import os
import tempfile
import unittest
from unittest import mock

from mcp.client.client import Client
from mcp.server.mcpserver.exceptions import ToolError

from jurisync import portals
from jurisync.connectors.projudi import base, parsers, server, tjgo, tjgo_desk
from jurisync.core.documents import MAX_DOCUMENT_BYTES
from jurisync.core.http import UnsafeUrlError
from jurisync.core.session_pool import LoginRejected, SessionExpired, SessionPool

NUMBER = "0000001-23.2026.8.09.0001"
HASH = "a" * 40

PROCESS_HTML = """<html><head><title>Dados Processo</title></head><body>
<fieldset id="VisualizaDados"><legend>Autos</legend>
 <div class="aEsquerda"><div>Número</div><span><span class="bold" id="span_proc_numero"> 0000001-23.2026.8.09.0001</span></span> Área Cível</div>
 <fieldset id="VisualizaDados" class="VisualizaDados"><legend>Polo Ativo | Autor</legend>
  <fieldset class="VisualizaDados"><div>Nome</div><span title="Nome da Parte" class="nomes"> Parte Autora Ficticia</span></fieldset>
 </fieldset>
 <fieldset id="VisualizaDados" class="VisualizaDados"><legend>Polo Passivo | Réu</legend>
  <fieldset class="VisualizaDados"><div>Nome</div><span title="Nome da Parte"> Parte Ré Ficticia</span></fieldset>
 </fieldset>
 <fieldset id="VisualizaDados" class="VisualizaDados"><legend>Outras Informações</legend>
  <div>Serventia</div><span class="span1">Vara Ficticia</span><br/>
  <div>Classe</div><span>7 - Procedimento Comum</span><br/>
  <div>Assunto(s)</div><span>1 - Assunto Ficticio</span><br/>
  <div>Valor da Causa</div><span>1.000,00</span><br/>
  <div>Fase Processual</div><span>Conhecimento</span>
 </fieldset>
</fieldset>
<table id="TabelaArquivos">
 <tr><td colspan="9"></td></tr>
 <tr class="TabelaLinha1" movi_codigo="2"><td> 2</td><td><span class="filtro_tipo_movimentacao"> Decisão</span><br/> Despacho inicial</td><td>02/01/2026 10:00:00</td><td> JUIZ FICTICIO</td><td><a href="javascript:buscarArquivosMovimentacaoJSON('222', 'BuscaProcesso')">x</a></td><td></td></tr>
 <tr id="linha_222"><td></td></tr>
 <tr class="TabelaLinha1" movi_codigo="1"><td> 1</td><td><span class="filtro_tipo_movimentacao"> Peticão Enviada</span></td><td>01/01/2026 09:00:00</td><td> SISTEMA</td><td><a href="javascript:buscarArquivosMovimentacaoJSON('111', 'BuscaProcesso')">x</a></td><td></td></tr>
 <tr id="linha_111"><td></td></tr>
</table></body></html>"""

NOT_LOCATED_HTML = """<html><head><title>Busca de Processo</title></head><body><script>
var mensagemErro = 'Nenhum Processo foi localizado.';
</script></body></html>"""

INDEX = json.dumps(
    [
        {
            "titulo": "Peticão Enviada",
            "complemento": "",
            "arquivos": [
                {"id": "9", "id_movi_arq": "100", "hash": HASH, "nome": "inicial.pdf"},
                {"id": "10", "id_movi_arq": "101", "hash": HASH, "nome": "procuracao.pdf"},
            ],
        },
        {"titulo": "Decisão", "complemento": "x", "arquivos": None},
    ]
)

FILES = json.dumps(
    [
        {
            "id": "100",
            "nome_arquivo": "a.html",
            "arquivo_tipo": "Decisão",
            "usuario_assinador": "JUIZ",
            "AcessoUsuario": "3",
            "AcessoArquivo": "1",
            "hash": HASH,
        },
        {
            "id": "101",
            "nome_arquivo": "b.pdf",
            "arquivo_tipo": "Sigiloso",
            "usuario_assinador": "X",
            "AcessoUsuario": "1",
            "AcessoArquivo": "3",
            "hash": HASH,
        },
    ]
)


def pdf_with_text(text):
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 100] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>"
        ),
    ]
    stream = f"BT /F1 12 Tf 10 50 Td ({text}) Tj ET".encode()
    objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (len(objects) + 1, xref)


class FakeAdapter(base.Adapter):
    """In-memory adapter: serves a fixed process and documents."""

    flavor = "fake"

    def __init__(self, documents, index=None):
        self.documents = documents
        self.index = index if index is not None else json.loads(INDEX)
        self.downloads = []

    def is_login_redirect(self, response):
        return False

    def open_process(self, session, portal, process_number):
        return parsers.parse_process(PROCESS_HTML)

    def fetch_index(self, session, portal):
        return parsers.parse_index(json.dumps(self.index))

    def align_index(self, index, movements):
        return parsers.align_index(index, movements)

    def download(self, session, portal, document_id, hash_value):
        self.downloads.append(document_id)
        return self.documents[document_id]


class PortalRegistryTest(unittest.TestCase):
    def test_tjgo_tjpr_and_tjam_are_wired(self):
        self.assertEqual(portals.keys("projudi"), ("tjgo-1g", "tjpr-projudi-1g", "tjam-1g"))
        tjgo_portal = portals.projudi("tjgo-1g")
        self.assertEqual(
            (tjgo_portal.host, tjgo_portal.root, tjgo_portal.auth, tjgo_portal.region, tjgo_portal.justice),
            ("projudi.tjgo.jus.br", "/", "certificate_mtls", "09", "8"),
        )
        self.assertEqual(tjgo_portal.login_url, "https://projudi.tjgo.jus.br/LogOn")
        for key in ("tjrr-1g", "tjba-projudi-1g"):
            with self.subTest(key=key):
                self.assertFalse(portals.PORTALS[key].wired)
                with self.assertRaisesRegex(ValueError, "portal must be one of"):
                    portals.projudi(key)

    def test_projudi_portals_never_shadow_other_systems(self):
        self.assertEqual(portals.PORTALS["tjpr-1g"].system, "eproc")
        self.assertEqual(portals.PORTALS["tjpi-1g"].system, "pje")
        self.assertEqual(portals.PORTALS["tjpr-1g"].auth, "certificate_totp")
        self.assertEqual([p.key for p in portals.for_process("0000001-23.2026.8.16.0001")], ["tjpr-1g", "tjpr-2g"])


class NumberAndIdTest(unittest.TestCase):
    def test_number_is_normalized_and_must_belong_to_the_court(self):
        self.assertEqual(base.normalize_number("00000012320268090001", "tjgo-1g"), NUMBER)
        self.assertEqual(base.normalize_number(f" {NUMBER} ", "tjgo-1g"), NUMBER)
        for wrong in ("0000001-23.2026.8.16.0001", "0000001-23.2026.4.09.0001", "abc", ""):
            with self.subTest(number=wrong), self.assertRaisesRegex(ValueError, "CNJ number of TJGO"):
                base.normalize_number(wrong, "tjgo-1g")

    def test_at_most_five_unique_numeric_documents(self):
        base.validate_document_ids(["1", "2", "3", "4", "5"])
        for bad in ([], ["1"] * 2, [str(n) for n in range(6)], ["a"], [1], "1"):
            with self.subTest(ids=bad), self.assertRaises(ValueError):
                base.validate_document_ids(bad)


class ParserTest(unittest.TestCase):
    def test_process_page(self):
        process = parsers.parse_process(PROCESS_HTML.encode("iso-8859-1"))
        self.assertEqual((process["number"], process["area"], process["court"]), (NUMBER, "Cível", "Vara Ficticia"))
        self.assertEqual(process["phase"], "Conhecimento")
        self.assertEqual([p["name"] for p in process["parties"]], ["Parte Autora Ficticia", "Parte Ré Ficticia"])
        first = process["movements"][0]
        self.assertEqual(
            (first["sequence"], first["type"], first["description"], first["movementId"], first["hasFiles"]),
            ("2", "Decisão", "Despacho inicial", "222", True),
        )

    def test_process_page_fails_loudly(self):
        for html in ("<html><title>Consulta de Processos</title></html>", PROCESS_HTML.replace("TabelaArquivos", "x")):
            with self.subTest(html=html[:40]), self.assertRaises(base.ProjudiParseError):
                parsers.parse_process(html)

    def test_index_with_and_without_files(self):
        index = parsers.parse_index(INDEX.encode())
        self.assertEqual([len(entry["files"]) for entry in index], [2, 0])
        self.assertEqual(index[0]["files"][0], {"id": "100", "name": "inicial.pdf", "hash": HASH})
        self.assertEqual(parsers.parse_index(b"[]"), [])
        for bad in (b"<html>", b'{"a": 1}', b'[{"x": 1}]', b'[{"titulo": "a", "arquivos": [{"id_movi_arq": "1"}]}]'):
            with self.subTest(bad=bad), self.assertRaises(base.ProjudiParseError):
                parsers.parse_index(bad)

    def test_movement_files_report_access(self):
        files = parsers.parse_movement_files(FILES)
        self.assertEqual([f["released"] for f in files], [True, False])
        self.assertEqual(files[0]["type"], "Decisão")
        self.assertNotIn("hash", base.public_file(files[0]))

    def test_index_aligns_with_page_only_when_titles_match(self):
        movements = parsers.parse_process(PROCESS_HTML)["movements"]
        aligned = parsers.align_index(parsers.parse_index(INDEX), movements)
        self.assertEqual([(m["sequence"], len(m["files"])) for m in aligned], [("2", 0), ("1", 2)])
        other = parsers.parse_index(INDEX.replace("Decis\\u00e3o", "Outra"))
        self.assertIsNone(parsers.align_index(other, movements))
        self.assertIsNone(parsers.align_index(other[:1], movements))


class TjgoAdapterTest(unittest.TestCase):
    def test_download_url_is_built_from_validated_values(self):
        url = tjgo.ADAPTER.download_url("tjgo-1g", "123", HASH)
        self.assertEqual(
            url, f"https://projudi.tjgo.jus.br/BuscaProcesso?PaginaAtual=6&Id_MovimentacaoArquivo=123&hash={HASH}"
        )
        for args in (("12 3", HASH), ("123", "x&y=1"), ("", HASH)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                tjgo.ADAPTER.download_url("tjgo-1g", *args)

    def test_login_redirect_means_expired_session(self):
        def response(status, location=""):
            return mock.Mock(status_code=status, headers={"Location": location})

        self.assertTrue(tjgo.ADAPTER.is_login_redirect(response(302, "/LogOn?PaginaAtual=-200")))
        self.assertTrue(tjgo.ADAPTER.is_login_redirect(response(302, "https://projudi.tjgo.jus.br/LogOn")))
        self.assertFalse(tjgo.ADAPTER.is_login_redirect(response(302, "/Usuario")))
        self.assertFalse(tjgo.ADAPTER.is_login_redirect(response(200)))

    def test_login_start_never_follows_a_redirect_off_the_portal(self):
        """The first GET used to follow redirects on its own; now each hop is host-checked before it is requested."""
        session = mock.Mock()
        session.get.return_value = mock.Mock(
            status_code=302, url="https://projudi.tjgo.jus.br/", headers={"Location": "https://evil.invalid/steal"}
        )
        with self.assertRaises(UnsafeUrlError):
            tjgo.ADAPTER.login("tjgo-1g", session=session)
        self.assertEqual(session.get.call_count, 1)
        self.assertIs(session.get.call_args.kwargs["allow_redirects"], False)
        session.post.assert_not_called()

    def test_panel_is_told_from_the_login_page_by_its_fields_not_by_any_password_input(self):
        panel = mock.Mock(
            status_code=200,
            url="https://projudi.tjgo.jus.br/Usuario?x=1",
            text="<input type='password' name='senhaCertificado'>",
        )
        login = mock.Mock(
            status_code=200,
            url="https://projudi.tjgo.jus.br/Usuario",
            text='<input name="Usuario"><input name="Senha">',
        )
        self.assertTrue(tjgo.is_authenticated_panel(panel))
        self.assertFalse(tjgo.is_authenticated_panel(login))

    def test_expired_session_in_the_process_search(self):
        adapter = tjgo.TjgoAdapter()
        redirect = mock.Mock(status_code=302, headers={"Location": "/LogOn?PaginaAtual=-200"})
        session = mock.Mock()
        session.request.return_value = redirect
        with self.assertRaises(base.ProjudiSessionExpired):
            adapter.open_process(session, "tjgo-1g", NUMBER)
        login_page = mock.Mock(status_code=200, content=b'<html><input name="Usuario"></html>', headers={})
        session.request.return_value = login_page
        with self.assertRaises(base.ProjudiSessionExpired):
            adapter.open_process(session, "tjgo-1g", NUMBER)
        session.request.return_value = mock.Mock(status_code=200, content=b"<title>Consulta</title>", headers={})
        with self.assertRaisesRegex(ValueError, "not found"):
            adapter.open_process(session, "tjgo-1g", NUMBER)

    @staticmethod
    def _search_page(html):
        return mock.Mock(status_code=200, content=html.encode(), headers={})

    CLEARED = "<html><title>Busca de Processo</title></html>"

    def test_archived_process_opens_on_the_second_search(self):
        session = mock.Mock()
        cleared = self._search_page(self.CLEARED)
        session.request.side_effect = [
            cleared,
            self._search_page(NOT_LOCATED_HTML),
            cleared,
            self._search_page(PROCESS_HTML),
        ]
        process = tjgo.TjgoAdapter().open_process(session, "tjgo-1g", NUMBER)
        self.assertEqual(process["number"], NUMBER)
        clear, first, clear_again, second = (call.kwargs["data"] for call in session.request.call_args_list)
        self.assertEqual(clear, clear_again)
        self.assertNotIn("ProcessoStatusCodigo", first)
        self.assertEqual((second["ProcessoStatus"], second["ProcessoStatusCodigo"]), ("", "1"))

    def test_active_process_needs_one_search(self):
        session = mock.Mock()
        session.request.return_value = self._search_page(PROCESS_HTML)
        tjgo.TjgoAdapter().open_process(session, "tjgo-1g", NUMBER)
        self.assertEqual(session.request.call_count, 2)  # the form's "Limpar", then the search

    def test_every_search_starts_by_clearing_the_form_the_lists_left_behind(self):
        # 23/11/2026: after projudi_list_processes the session's search form kept the last serventia type
        # (Id_ServentiaSubTipo) and the search by number answered "Nenhum Processo foi localizado" until cleared.
        session = mock.Mock()
        session.request.return_value = self._search_page(PROCESS_HTML)
        tjgo.TjgoAdapter().open_process(session, "tjgo-1g", NUMBER)
        clear, search = session.request.call_args_list
        self.assertEqual((clear.args[0], clear.args[1].rsplit("/", 1)[1]), ("POST", "BuscaProcesso"))
        self.assertEqual(clear.kwargs["data"], {"PaginaAtual": "4"})
        self.assertEqual((search.kwargs["data"]["PaginaAtual"], search.kwargs["data"]["ProcessoNumero"]), ("2", NUMBER))

    def test_process_missing_from_both_searches_is_not_found(self):
        session = mock.Mock()
        session.request.return_value = self._search_page(NOT_LOCATED_HTML)
        with self.assertRaisesRegex(ValueError, "not found"):
            tjgo.TjgoAdapter().open_process(session, "tjgo-1g", NUMBER)
        # Two searches (each after clearing the form), then the home page's Diário rows.
        self.assertEqual(session.request.call_count, 5)
        self.assertTrue(session.request.call_args.args[1].endswith(tjgo_desk.HOME))

    def test_archived_search_still_checks_the_opened_number(self):
        session = mock.Mock()
        other = PROCESS_HTML.replace(NUMBER, "0000002-23.2026.8.09.0001")
        cleared = self._search_page(self.CLEARED)
        session.request.side_effect = [cleared, self._search_page(NOT_LOCATED_HTML), cleared, self._search_page(other)]
        with self.assertRaisesRegex(ValueError, "different process"):
            tjgo.TjgoAdapter().open_process(session, "tjgo-1g", NUMBER)

    def test_download_follows_only_the_tribunals_own_storage(self):
        config = portals.projudi("tjgo-1g")
        base.require_download_url(config, "https://projudi-2026-prd.s3.tjgo.jus.br/a/b.pdf?sig=1")
        for url in ("https://evil.example.com/a", "http://projudi.tjgo.jus.br/a", "https://tjgo.jus.br.evil.com/a"):
            with self.subTest(url=url), self.assertRaises(UnsafeUrlError):
                base.require_download_url(config, url)

    def test_download_hands_over_to_storage_and_reads_the_body(self):
        redirect = mock.Mock(status_code=302, headers={"Location": "https://bucket.s3.tjgo.jus.br/f.pdf?sig=1"})
        final = mock.Mock(status_code=200, headers={"Content-Type": "application/pdf"})
        final.iter_content.return_value = [b"%PDF-1.4 ", b"data"]
        session = mock.Mock()
        session.request.side_effect = [redirect, final]
        content, content_type = tjgo.ADAPTER.download(session, "tjgo-1g", "123", HASH)
        self.assertEqual((content, content_type), (b"%PDF-1.4 data", "application/pdf"))
        self.assertEqual(session.request.call_count, 2)

    def test_download_stops_at_the_size_limit_and_on_expiry(self):
        big = mock.Mock(status_code=200, headers={})
        big.iter_content.return_value = [b"x" * (MAX_DOCUMENT_BYTES // 2 + 1)] * 2
        session = mock.Mock()
        session.request.return_value = big
        self.assertEqual(tjgo.ADAPTER.download(session, "tjgo-1g", "123", HASH), (None, "too_large"))
        session.request.return_value = mock.Mock(status_code=302, headers={"Location": "/LogOn?PaginaAtual=-200"})
        with self.assertRaises(base.ProjudiSessionExpired):
            tjgo.ADAPTER.download(session, "tjgo-1g", "123", HASH)


class DocumentReadTest(unittest.TestCase):
    def setUp(self):
        self.html = (
            b"<html><body><p>Decisao do processo " + NUMBER.encode() + b" com texto suficiente.</p></body></html>"
        )
        self.adapter = FakeAdapter(
            {
                "100": (pdf_with_text("Peticao inicial ficticia"), "application/pdf"),
                "101": (b"<html><body>Acesso negado ao documento</body></html>", "text/html"),
            }
        )

    def read(self, ids):
        return base.read_documents(self.adapter, object(), "tjgo-1g", NUMBER, ids)

    def test_pdf_text_and_access_denied_are_reported_per_document(self):
        number, results, ciencia = self.read(["100", "101"])
        self.assertEqual(ciencia, {"estado": "nenhuma"})
        self.assertEqual(number, NUMBER)
        self.assertEqual([r["extractionStatus"] for r in results], ["extracted", "access_denied"])
        self.assertIn("Peticao inicial ficticia", results[0]["text"])
        self.assertEqual(results[0]["format"], "pdf")
        self.assertNotIn("hash", json.dumps(results))

    def test_html_text_is_extracted(self):
        self.adapter.documents["100"] = (self.html, "text/html;charset=iso-8859-1")
        _, results, _ = self.read(["100"])
        self.assertEqual((results[0]["extractionStatus"], results[0]["format"]), ("extracted", "html"))

    def test_session_expired_page_and_scanned_pdf_are_not_text(self):
        self.adapter.documents["100"] = (b'<html><title>Login</title><input type="password"></html>', "text/html")
        self.assertEqual(self.read(["100"])[1][0]["extractionStatus"], "session_expired")

    def test_unknown_id_and_over_limit_are_rejected_before_any_download(self):
        with self.assertRaisesRegex(ValueError, "not in this process"):
            self.read(["999"])
        with self.assertRaisesRegex(ValueError, "Select 1-5"):
            self.read([str(n) for n in range(6)])
        self.assertEqual(self.adapter.downloads, [])

    def test_oversized_document_is_reported(self):
        self.adapter.documents["100"] = (None, "too_large")
        self.assertEqual(self.read(["100"])[1][0]["extractionStatus"], "too_large")

    def test_empty_index_fails_instead_of_looking_like_no_documents(self):
        self.adapter.index = []
        with self.assertRaises(base.ProjudiParseError):
            self.read(["100"])

    def test_download_saves_one_document_in_the_shared_folder(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ, {"JURISYNC_DOCUMENTS_DIR": folder}):
            result = base.download_document(self.adapter, object(), "tjgo-1g", NUMBER, "100")
            self.assertTrue(result["saved"])
            self.assertEqual(result["savedAs"], "00000012320268090001_100.pdf")
            self.assertTrue(os.path.isfile(os.path.join(folder, result["savedAs"])))
            denied = base.download_document(self.adapter, object(), "tjgo-1g", NUMBER, "101")
            self.assertFalse(denied["saved"])
            self.assertEqual(denied["extractionStatus"], "access_denied")
        self.assertNotIn("hash", json.dumps(result))


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.adapter = FakeAdapter({})
        patch = mock.patch.dict(server.ADAPTERS, {"tjgo-servlet": self.adapter})
        patch.start()
        self.addCleanup(patch.stop)
        self.logins = []
        pool = SessionPool(
            login=lambda portal: self.logins.append(portal) or object(),
            refresh=lambda portal, s: s,
            close=lambda s: None,
        )
        patch_pool = mock.patch.object(server, "sessions", pool)
        patch_pool.start()
        self.addCleanup(patch_pool.stop)

    def call(self, name, arguments):
        async def run():
            async with Client(server.create_server()) as client:
                return await client.call_tool(name, arguments)

        return asyncio.run(run())

    def test_configured_portals_follow_each_adapter_login(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(server.configured_portals(), ())
        with mock.patch.dict(os.environ, {"TJAM_1G_USERNAME": "AM000", "TJAM_1G_PASSWORD": "x"}, clear=True):
            self.assertEqual(server.configured_portals(), ("tjam-1g",))
        for certificate in (
            {"PJE_A1_P12": "/a1.p12", "PJE_A1_PIN": "pin"},
            {"PROJUDI_A1_P12": "/projudi.p12", "PJE_A1_PIN": "pin"},
        ):
            with mock.patch.dict(os.environ, certificate, clear=True):
                self.assertEqual(server.configured_portals(), ("tjgo-1g",))
        with mock.patch.dict(os.environ, {"PJE_A1_P12": "/a1.p12"}, clear=True):
            self.assertEqual(server.configured_portals(), ())  # no PIN: the login cannot open the certificate

    def test_main_offers_and_logs_in_only_where_a_login_is_set(self):
        with (
            mock.patch.object(server, "load_env_file"),
            mock.patch.object(server, "serve"),
            mock.patch.object(server, "create_server") as create,
            mock.patch.object(server, "configured_portals", return_value=("tjam-1g",)),
        ):
            server.main()
        self.assertEqual(create.call_args.args, (("tjam-1g",),))
        self.assertEqual(server.sessions._only, ("tjam-1g",))

        async def tools():
            async with Client(server.create_server(["tjam-1g"])) as client:
                return {tool.name: tool for tool in (await client.list_tools()).tools}

        listed = asyncio.run(tools())
        lists = listed["projudi_list_intimacoes"].input_schema["properties"]["portal"]
        self.assertEqual((lists.get("default"), lists.get("enum", [lists.get("const")])), ("tjam-1g", ["tjam-1g"]))
        portal = listed["projudi_get_process"].input_schema["properties"]["portal"]
        self.assertEqual((portal.get("default"), portal.get("enum", [portal.get("const")])), ("tjam-1g", ["tjam-1g"]))

    def test_tools_take_a_portal_and_only_the_document_reads_are_not_read_only(self):
        async def tools():
            async with Client(server.create_server()) as client:
                return {tool.name: tool for tool in (await client.list_tools()).tools}

        listed = asyncio.run(tools())
        self.assertEqual(
            sorted(listed),
            [
                "projudi_download_document",
                "projudi_get_process",
                "projudi_list_documents",
                "projudi_list_intimacoes",
                "projudi_list_processes",
                "projudi_read_documents",
            ],
        )
        reads = {"projudi_download_document", "projudi_read_documents"}
        for tool in listed.values():
            with self.subTest(tool=tool.name):
                # A download writes a file and, like a read, may register a ciência once acknowledged.
                self.assertEqual(tool.annotations.read_only_hint, tool.name not in reads)
                # A ciência starts a deadline: destructive, the same rule in every portal.
                self.assertEqual(tool.annotations.destructive_hint, tool.name in reads)
                self.assertTrue(tool.title)
                portal = tool.input_schema["properties"]["portal"]
                self.assertEqual(portal.get("default"), "tjgo-1g")
                self.assertEqual(portal.get("enum", [portal.get("const")]), ["tjgo-1g", "tjpr-projudi-1g", "tjam-1g"])
                self.assertNotIn("portal", tool.input_schema.get("required", []))
                if tool.name in reads:
                    acknowledge = tool.input_schema["properties"]["acknowledgeCiencia"]
                    self.assertIsNone(acknowledge.get("default"))
                    self.assertNotIn("acknowledgeCiencia", tool.input_schema.get("required", []))

    def test_list_options_are_tjam_only_and_reach_its_adapter(self):
        refused = self.call("projudi_list_intimacoes", {"portal": "tjgo-1g", "lists": ["novas"]})
        self.assertTrue(refused.is_error)
        tjam_adapter = mock.Mock(flavor="legacy-local")
        tjam_adapter.list_processes.return_value = {"situation": "todos", "groups": [], "complete": True}
        with mock.patch.dict(server.ADAPTERS, {"legacy-local": tjam_adapter}):
            result = self.call(
                "projudi_list_processes",
                {"portal": "tjam-1g", "situation": "todos", "instances": ["1", "1"], "maxPages": 3},
            )
        self.assertEqual(result.structured_content["portal"], "tjam-1g")
        self.assertEqual(
            tjam_adapter.list_processes.call_args.kwargs, {"situation": "todos", "instances": ("1",), "max_pages": 3}
        )

    def test_get_process_hides_hashes_and_attaches_files_to_movements(self):
        result = self.call("projudi_get_process", {"process_number": NUMBER}).structured_content
        self.assertEqual((result["portal"], result["documentCount"]), ("tjgo-1g", 2))
        self.assertNotIn(HASH, json.dumps(result))
        self.assertNotIn("http", json.dumps({k: v for k, v in result.items() if k != "notice"}))
        self.assertEqual([len(m["files"]) for m in result["movements"]], [0, 2])
        self.assertNotIn("documentMovements", result)

    def test_unpaired_index_is_reported_on_its_own(self):
        self.adapter.index = [
            {"titulo": "Outra", "complemento": "", "arquivos": [{"id_movi_arq": "1", "hash": HASH, "nome": "a.pdf"}]}
        ] * 2
        result = self.call("projudi_get_process", {"process_number": NUMBER}).structured_content
        self.assertEqual(len(result["documentMovements"]), 2)
        self.assertNotIn(HASH, json.dumps(result))

    def test_unwired_portal_and_foreign_number_are_tool_errors(self):
        self.assertTrue(self.call("projudi_get_process", {"process_number": NUMBER, "portal": "tjam-1g"}).is_error)
        foreign = self.call("projudi_get_process", {"process_number": "0000001-23.2026.8.16.0001"})
        self.assertTrue(foreign.is_error)

    def test_list_documents_by_movement_checks_the_movement_belongs_to_the_process(self):
        with mock.patch.object(
            self.adapter, "movement_files", create=True, return_value=parsers.parse_movement_files(FILES)
        ):
            result = self.call(
                "projudi_list_documents", {"process_number": NUMBER, "movement_id": "111"}
            ).structured_content
            self.assertEqual([f["released"] for f in result["files"]], [True, False])
            self.assertNotIn(HASH, json.dumps(result))
            self.assertTrue(
                self.call("projudi_list_documents", {"process_number": NUMBER, "movement_id": "999"}).is_error
            )
        listed = self.call("projudi_list_documents", {"process_number": NUMBER}).structured_content
        self.assertEqual([len(m["files"]) for m in listed["movements"]], [2])

    def test_expired_session_logs_in_again_once(self):
        attempts = []

        def read(adapter, session):
            attempts.append(1)
            if len(attempts) == 1:
                raise base.ProjudiSessionExpired("expired")
            return "ok"

        self.assertEqual(server.call("tjgo-1g", read), "ok")
        self.assertEqual(self.logins, ["tjgo-1g", "tjgo-1g"])
        self.logins.clear()
        always = mock.Mock(side_effect=base.ProjudiSessionExpired("expired"))
        self.assertTrue(issubclass(base.ProjudiSessionExpired, SessionExpired))
        with self.assertRaises(ToolError):
            server.call("tjgo-1g", always)
        self.assertEqual(always.call_count, 2)


class UnwiredGenerationTest(unittest.TestCase):
    def test_tjgo_tjpr_and_tjam_have_adapters_and_the_other_generations_say_not_implemented(self):
        self.assertEqual(set(server.ADAPTERS), {"tjgo-servlet", "legacy-local", "cnj-servlet", "legacy-keycloak"})
        self.assertIs(server.adapter_for("tjgo-1g"), tjgo.ADAPTER)
        # TJBA has an adapter but stays unwired until its desk is proven live: no tool reaches it.
        for key in ("tjrr-1g", "tjba-projudi-1g"):
            with self.subTest(key=key):
                with self.assertRaisesRegex(
                    NotImplementedError, f"{key}.*not implemented yet; wired: tjgo-1g, tjpr-projudi-1g, tjam-1g"
                ):
                    server.adapter_for(key)
                with self.assertRaisesRegex(ToolError, "not implemented yet"):
                    server.call(key, lambda adapter, session: self.fail("an unwired portal must not log in"))
        with self.assertRaisesRegex(ValueError, "portal must be one of"):
            server.adapter_for("no-such-portal")

    def test_keycloak_courts_share_a_flavor_and_do_not_use_the_tjgo_routes(self):
        keycloak_courts = [p.key for p in portals.PORTALS.values() if p.flavor == "legacy-keycloak"]
        self.assertEqual(sorted(keycloak_courts), ["tjpr-projudi-1g", "tjrr-1g"])
        for key in keycloak_courts:
            self.assertNotEqual(portals.PORTALS[key].login_url, "https://projudi.tjgo.jus.br/LogOn")


if __name__ == "__main__":
    unittest.main()


# "Serventias Disponíveis" as TJGO served it on 2026-11-16 (markup kept; OABs and ids synthetic).
SERVENTIAS_HTML = """<div id="divCorpo" class="divCorpo"><div class="area"><h2>&raquo; Serventias Disponíveis</h2></div>
<fieldset class="fieldEdicaoEscuro"><legend class="formEdicaoLegenda"> Ordem dos Advogados do Brasil AL - AL</legend>
<label><a  href="Usuario?PaginaAtual=7&a1=1001&a2=2&a3=&a4=&a5=">
 Advogados - OAB/Matrícula: 11111-S-AL</a></label/><br /></fieldset>
<fieldset class="fieldEdicaoEscuro"><legend class="formEdicaoLegenda"> Ordem dos Advogados do Brasil GO - GO</legend>
<label><a  href="Usuario?PaginaAtual=7&a1=1002&a2=2&a3=&a4=&a5=">
 Advogados - OAB/Matrícula: 2222-N-GO</a></label/><br /></fieldset></div>
<input type="password" name="senhaCertificado"><input type="checkbox" name="memorizarSenha">"""


class ServentiaTest(unittest.TestCase):
    def config(self, profile=""):
        patcher = mock.patch.dict(os.environ, {"TJGO_1G_USER_PROFILE": profile})
        patcher.start()
        self.addCleanup(patcher.stop)
        return portals.projudi("tjgo-1g")

    def test_the_page_lists_each_serventia_with_its_section(self):
        self.assertEqual(
            tjgo.serventias(SERVENTIAS_HTML),
            [
                (
                    "Usuario?PaginaAtual=7&a1=1001&a2=2&a3=&a4=&a5=",
                    "Ordem dos Advogados do Brasil AL - AL / Advogados - OAB/Matrícula: 11111-S-AL",
                ),
                (
                    "Usuario?PaginaAtual=7&a1=1002&a2=2&a3=&a4=&a5=",
                    "Ordem dos Advogados do Brasil GO - GO / Advogados - OAB/Matrícula: 2222-N-GO",
                ),
            ],
        )
        self.assertEqual(tjgo.serventias(PROCESS_HTML), [])

    def test_the_profile_picks_by_oab_in_any_spelling_or_by_a_word(self):
        options = tjgo.serventias(SERVENTIAS_HTML)
        for profile in ("GO2222", "go 2.222", "GO-02222", "2222GO", "GO - GO"):
            with self.subTest(profile=profile):
                self.assertIn("a1=1002", tjgo.choose_serventia(self.config(profile), options))

    def test_without_a_profile_several_lawyer_serventias_stop_with_the_choices(self):
        options = tjgo.serventias(SERVENTIAS_HTML)
        with self.assertRaisesRegex(LoginRejected, "TJGO_1G_USER_PROFILE.*") as error:
            tjgo.choose_serventia(self.config(), options)
        self.assertIn("11111-S-AL", str(error.exception))
        self.assertIn("a1=1001", tjgo.choose_serventia(self.config(), options[:1]))
        with self.assertRaises(LoginRejected):
            tjgo.choose_serventia(self.config("SP9"), options)

    def test_login_follows_only_the_chosen_link_and_never_a_default_choice(self):
        self.config("GO2222")
        app = "https://projudi.tjgo.jus.br/"
        page = mock.Mock(
            status_code=200, url=app + "Usuario?PaginaAtual=9&hashFluxo=1", text=SERVENTIAS_HTML, headers={}
        )
        panel = mock.Mock(status_code=200, url=app + "Usuario?PaginaAtual=7&a1=1002", text="<p>Painel</p>", headers={})
        session = mock.Mock()
        session.cookies.jar = [mock.Mock(name="cookie")]
        session.cookies.jar[0].name = "JSESSIONID"
        start = mock.Mock(status_code=200, url=app, headers={})
        session.get.side_effect = [start, page, panel]
        session.post.return_value = mock.Mock(
            status_code=302, url=app + "LogOn", headers={"Location": "/certificado?hashFluxo=1"}
        )
        with mock.patch.object(base, "client_certificate") as cert:
            cert.return_value.__enter__.return_value = ("c", "k")
            self.assertIs(tjgo.ADAPTER.login("tjgo-1g", session=session), session)
        self.assertEqual(session.get.call_args.args[0], app + "Usuario?PaginaAtual=7&a1=1002&a2=2&a3=&a4=&a5=")
        self.assertEqual(session.post.call_count, 1)
        self.assertFalse(any("memorizar" in str(call).lower() for call in session.mock_calls))


class SessionGuardTest(unittest.TestCase):
    """27/11/2026: ``Usuario?PaginaAtual=7&LoginPronto=S`` (the serventia switch without ``a1``) and then a bare
    ``/Usuario`` ended a live TJGO session. Neither is ever sent."""

    APP = "https://projudi.tjgo.jus.br/"

    def test_the_two_requests_are_refused_before_anything_is_sent(self):
        session = mock.Mock()
        for path in ("Usuario", "Usuario?", "Usuario/", "Usuario?PaginaAtual=7&LoginPronto=S", "Usuario?PaginaAtual=7"):
            with self.subTest(path=path), self.assertRaises(tjgo.UnsafeNavigation):
                tjgo.ADAPTER.request(session, "tjgo-1g", "GET", path)
        session.request.assert_not_called()

    def test_the_home_and_a_chosen_serventia_pass(self):
        for url in (
            self.APP + "Usuario?PaginaAtual=-10",
            self.APP + "Usuario?PaginaAtual=7&a1=1002&a2=2",
            self.APP + "BuscaProcesso?PaginaAtual=4",
            self.APP + "UsuarioAlgo",
        ):
            with self.subTest(url=url):
                self.assertEqual(tjgo.require_safe_navigation(url), url)

    def test_a_serventia_link_without_a1_is_never_an_option(self):
        page = SERVENTIAS_HTML.replace(
            "Usuario?PaginaAtual=7&a1=1001&a2=2&a3=&a4=&a5=", "Usuario?PaginaAtual=7&LoginPronto=S"
        )
        self.assertEqual(
            [href for href, _ in tjgo.serventias(page)], ["Usuario?PaginaAtual=7&a1=1002&a2=2&a3=&a4=&a5="]
        )
        self.assertEqual(tjgo.serventias(SERVENTIAS_HTML.replace("a1=1001", "a1=").replace("a1=1002", "a1=")), [])
