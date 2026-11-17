"""TJGO lawyer desk: intimação and process lists, and the ciência guard of document reads.

Fixtures follow the pages captured live on 2026-11-17, with invented numbers, names and ids.
"""

import asyncio
import json
import unittest
from datetime import date
from unittest import mock

from mcp.client.client import Client

from jurisync.connectors.projudi import base, parsers, server, tjgo, tjgo_desk
from jurisync.core.session_pool import SessionPool

TODAY = date(2026, 11, 17)
NUMBER = "0000001-23.2026.8.09.0001"  # the process the fake adapter serves
PENDING_NUMBER = "0000004-56.2026.8.09.0001"

DJE_ROW = """<tr><td>{id}</td><td><a href="BuscaProcesso?Id_Processo=999{id}">{process}</a></td>
<td>{movement}</td><td class="lista_data">{published}</td><td class="lista_data">{deadline}</td>
<td class="colunaMinima"><button class="imgIcons" type="button" title="Marcar Aguardando Peticionamento"
 onclick="location.href='DescartarPendenciaProcesso?PaginaAtual=7&amp;pendencia={id}&amp;CodigoPendencia=1'"></button></td>
<td align="center"><button class="imgIcons" type="button" title="Retirar da Página Inicial"
 onclick="location.href='DescartarPendenciaProcesso?PaginaAtual=6&amp;pendencia={id}&amp;CodigoPendencia=1'"></button></td>
</tr>"""

ROWS = [
    {
        "id": "700000001",
        "process": "1.23",
        "movement": "Decisão -> Outras Decisões (10/11/2026 10:00:00)",
        "published": "12/11/2026 23:59:59",
        "deadline": "03/12/2026 23:59:59",
    },
    {
        "id": "700000002",
        "process": "4.56",
        "movement": "Ato Ordinatório (17/11/2026 12:00:00)",
        "published": "19/11/2026 23:59:59",
        "deadline": "",
    },
]

PORTAL_EMPTY = "<em>N&atilde;o h&aacute; pend&ecirc;ncias</em>"


def home_page(rows=ROWS, quantity=None, portal=PORTAL_EMPTY):
    body = "".join(DJE_ROW.format(**row) for row in rows)
    dje = (
        f"""<fieldset><legend>Intimações/Citações Publicadas no Diário Eletrônico</legend>
<table class="Tabela"><thead><tr><th>N&uacute;m.</th><th>Processo</th><th>Movimenta&ccedil;&atilde;o</th>
<th>Data Publicação</th><th>Possível Data Limite</th><th>Marcar</th><th>Descartar</th></tr></thead>
<tbody>{body}</tbody><tfoot><tr><td colspan="7">Quantidade: {len(rows) if quantity is None else quantity}</td></tr>
</tfoot></table></fieldset>"""
        if rows
        else ""
    )
    return f"""<html><head><title>Processo Judicial</title></head><body>
<p>Boa Tarde, <span>ADVOGADA FICTICIA</span>! Seja bem vindo ao <span>Processo Judicial</span></p>
<fieldset class="fieldEdicaoEscuro"><legend>Processos Ativos/Audiências</legend>
<table class="Tabela"><thead><tr><th>Tipo</th><th>Qtde</th></tr></thead><tbody>
<tr><td>Juizado Especial Cível</td><td><a title='x' href="BuscaProcesso?PaginaAtual=2&Proprio=1&Id_ServentiaSubTipo=1&ProcessoStatusCodigo=0">2</a></td></tr>
<tr><td>UPJ Cível</td><td><a title='x' href="BuscaProcesso?PaginaAtual=2&Proprio=1&Id_ServentiaSubTipo=72&ProcessoStatusCodigo=0">1</a></td></tr>
</tbody></table></fieldset>
<fieldset class="fieldEdicaoEscuro"><legend>Pend&ecirc;ncias</legend>
{dje}
{portal}
</fieldset>
<fieldset class="fieldEdicaoEscuro"><legend>Processos Aguardando Peticionamento</legend></fieldset>
</body></html>"""


def read_json(*rows, meta=True):
    items = [{"desc1": 2 * len(rows), "id": "-50000"}, {"desc1": "0", "id": "-60000"}] if meta else []
    for identifier, process, published in rows:
        items.append(
            {
                "desc1": "999",
                "desc2": process,
                "desc3": "Despacho -> Mero Expediente (17/11/2026 09:00:00)",
                "desc4": "Intimação",
                "desc5": published,
                "desc6": "15/12/2026 23:59:59",
                "desc7": "",
                "desc8": "0",
                "id": identifier,
                "hash": "1",
                "desc9": "false",
            }
        )
    return json.dumps(items)


PROCESS_ROW = """<tr class="TabelaLinha1" onclick="submete('999')"><td align="center">{n}</td>
<td align="center"><input class="formEdicaoCheckBox" name="processos" type="checkbox" value="999"></td>
<td><i class="cor_prioridade_3"></i> {number}</td>
<td><div class='coluna100'><div class='coluna15'><b>Polo Ativo</b></div><div class='coluna80'>Autora Ficticia {n}</div></div>
<div class='coluna100'><div class='coluna15'><b>Polo Passivo</b></div><div class='coluna80'>Empresa Ficticia S.A.</div></div></td>
<td>04/03/2026 15:58:57</td><td align="center"><button name="formLocalizarimgEditar"></button></td></tr>"""


def process_list(numbers, total=None):
    rows = "".join(PROCESS_ROW.format(n=n, number=number) for n, number in enumerate(numbers, 1))
    return f"""<html><head><title>Busca de Processo</title></head><body><form>
<table id="Tabela" class="Tabela"><thead><tr><td>n.</td><td></td><td>Número</td><td>Partes</td><td>Distribuição</td>
<td>Selecionar</td></tr></thead><tbody id="tabListaProcesso">{rows}</tbody></table>
<div id="Paginacao"><b> Total de: {len(numbers) if total is None else total}</b></div></form></body></html>"""


EXPIRED = (
    "<html><head><title>Problema no pedido</title></head><body>Prezado(a) usuário(a) Atente-se à informação abaixo: "
    "Usuário inválido ou sessão expirada.</body></html>"
)


class ParseHomeTest(unittest.TestCase):
    def test_categories_diario_rows_and_an_empty_portal_block(self):
        home = tjgo_desk.parse_home(home_page(), now=TODAY)
        self.assertEqual(
            [(c["label"], c["subtype"], c["count"]) for c in home["categories"]],
            [("Juizado Especial Cível", "1", 2), ("UPJ Cível", "72", 1)],
        )
        first, second = home["dje"]["items"]
        self.assertEqual(home["dje"]["declared"], 2)
        self.assertEqual(
            tjgo_desk.public(first),
            {
                "id": "700000001",
                "process": "1.23",
                "movement": "Decisão -> Outras Decisões",
                "movementDate": "10/11/2026 10:00:00",
                "publication": "12/11/2026 23:59:59",
                "deadline": "03/12/2026 23:59:59",
                "awaitingPublication": False,
            },
        )
        self.assertTrue(second["awaitingPublication"])  # published on 19/11, after "today"
        self.assertEqual(home["portal"], {"items": [], "complete": True, "text": ""})

    def test_published_today_counts_as_published(self):
        home = tjgo_desk.parse_home(home_page(), now=date(2026, 11, 19))
        self.assertFalse(home["dje"]["items"][1]["awaitingPublication"])

    def test_portal_rows_that_name_a_process_are_pending(self):
        portal = """<fieldset><legend>Intimações</legend><table><thead><tr><th>Processo</th></tr></thead><tbody>
<tr><td><a href="BuscaProcesso?Id_Processo=1">0000004-56.2026.8.09.0001</a></td><td>Aguardando leitura</td></tr>
</tbody></table></fieldset>"""
        home = tjgo_desk.parse_home(home_page(portal=portal), now=TODAY)
        self.assertTrue(home["portal"]["complete"])
        self.assertEqual([i["process"] for i in home["portal"]["items"]], ["0000004-56.2026.8.09.0001"])
        self.assertEqual(home["portal"]["items"][0]["_key"], (4, 56))

    def test_portal_content_without_process_rows_is_not_read_in_full(self):
        for portal in ("<p>Intimações aguardando leitura: <a href='Pendencia?x=1'>3</a></p>", ""):
            with self.subTest(portal=portal):
                home = tjgo_desk.parse_home(home_page(portal=portal), now=TODAY)
                self.assertFalse(home["portal"]["complete"])
                self.assertEqual(home["portal"]["items"], [])

    def test_not_the_home_page_or_a_changed_layout_fails_loudly(self):
        with self.assertRaises(base.ProjudiParseError):
            tjgo_desk.parse_home(EXPIRED)
        with self.assertRaises(base.ProjudiParseError):
            tjgo_desk.parse_home(home_page().replace("Pend&ecirc;ncias</legend>", "Avisos</legend>"))
        broken = home_page().replace("<td>700000001</td>", "")
        with self.assertRaises(base.ProjudiParseError):
            tjgo_desk.parse_home(broken)


class ParseListsTest(unittest.TestCase):
    def test_read_page_skips_paging_metadata_and_flags_future_readings(self):
        rows, awaiting = tjgo_desk.parse_read_page(
            read_json(("1", "1.23", "17/11/2026 23:59:59"), ("2", "4.56", "18/11/2026 23:59:59")), "17", now=TODAY
        )
        self.assertEqual([r["id"] for r in rows], ["1", "2"])
        self.assertEqual([r["id"] for r in awaiting], ["2"])
        self.assertEqual(rows[0]["movement"], "Despacho -> Mero Expediente")

    def test_empty_answer_is_no_rows_and_other_shapes_fail(self):
        self.assertEqual(tjgo_desk.parse_read_page(b"", "14"), ([], []))
        for content in ("<html>erro</html>", '{"id": "1"}', '[{"id": "1", "desc2": "", "desc5": ""}]'):
            with self.subTest(content=content), self.assertRaises(base.ProjudiParseError):
                tjgo_desk.parse_read_page(content, "17")

    def test_process_list_rows_and_total(self):
        rows, total = tjgo_desk.parse_process_list(process_list(["0000001-23", "0000004-56"]))
        self.assertEqual(total, 2)
        self.assertEqual(
            rows[0],
            {
                "number": "0000001-23",
                "parties": [
                    {"role": "Polo Ativo", "name": "Autora Ficticia 1"},
                    {"role": "Polo Passivo", "name": "Empresa Ficticia S.A."},
                ],
                "distributedAt": "04/03/2026 15:58:57",
            },
        )
        not_found = "<html><title>Busca de Processo</title><script>var mensagemErro = 'Nenhum Processo foi localizado.';</script></html>"
        self.assertEqual(tjgo_desk.parse_process_list(not_found), ([], 0))
        with self.assertRaises(base.ProjudiParseError):
            tjgo_desk.parse_process_list("<html><title>Busca de Processo</title></html>")

    def test_process_list_without_the_selection_column_or_with_a_note(self):
        html = process_list(["0000001-23"]).replace(
            """<td align="center"><input class="formEdicaoCheckBox" name="processos" type="checkbox" value="999"></td>""",
            "",
        )
        rows, _ = tjgo_desk.parse_process_list(html)
        self.assertEqual((rows[0]["number"], rows[0]["distributedAt"]), ("0000001-23", "04/03/2026 15:58:57"))
        noted, _ = tjgo_desk.parse_process_list(process_list(["0000004-56 (Apenso)"]))
        self.assertEqual(noted[0]["number"], "0000004-56")
        with self.assertRaisesRegex(base.ProjudiParseError, "unexpected shape"):
            tjgo_desk.parse_process_list(process_list(["sem número"]))

    def test_process_keys_of_cnj_and_short_numbers(self):
        for value in ("0000004-56.2026.8.09.0001", "4.56", "0000004-56", "4-56"):
            with self.subTest(value=value):
                self.assertEqual(tjgo_desk.process_key(value), (4, 56))
        self.assertIsNone(tjgo_desk.process_key("sem número"))


def page(html, status=200):
    content = html.encode("utf-8") if isinstance(html, str) else html
    return mock.Mock(status_code=status, content=content, headers={})


class DeskSession:
    """Answers the desk's GETs by path; records every request."""

    def __init__(self, home=None, reads=None, lists=None):
        self.home = home if home is not None else home_page()
        self.reads = reads or {}
        self.lists = lists or {}
        self.paths = []

    def request(self, method, url, **kwargs):
        path = url.split("projudi.tjgo.jus.br/", 1)[1]
        self.paths.append(path)
        if path == tjgo_desk.HOME:
            return page(self.home)
        if path.startswith("PendenciaJson?"):
            flow = path.split("fluxo=", 1)[1].split("&", 1)[0]
            position = int(path.split("PosicaoPaginaAtual=", 1)[1])
            pages = self.reads.get(flow, [""])
            return page(pages[position] if position < len(pages) else "")
        if path.startswith("BuscaProcesso?") and "Proprio=1" in path:
            subtype = path.split("Id_ServentiaSubTipo=", 1)[1].split("&", 1)[0]
            return page(self.lists[subtype])
        raise AssertionError(f"unexpected request {method} {path}")


class TjgoGuardTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(tjgo_desk, "today", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.adapter = tjgo.TjgoAdapter()

    def test_diario_intimation_not_yet_published_is_pending_for_its_process_only(self):
        session = DeskSession()
        state = self.adapter.pending_ciencia(session, "tjgo-1g", PENDING_NUMBER)
        self.assertIsNone(state["unchecked"])
        self.assertEqual([i["id"] for i in state["items"]], ["700000002"])
        published = self.adapter.pending_ciencia(session, "tjgo-1g", NUMBER)
        self.assertEqual(published, {"items": [], "unchecked": None})
        # Only the home page and the two "lidas" lists, filtered from tomorrow on, were requested.
        self.assertTrue(all(p == tjgo_desk.HOME or p.startswith("PendenciaJson?") for p in session.paths))
        self.assertIn("dataInicialFim=18%2F11%2F2026", session.paths[1])
        self.assertFalse(any("Descartar" in p or p.startswith("Pendencia?") for p in session.paths))

    def test_intimation_removed_from_home_is_still_found_in_the_read_list(self):
        session = DeskSession(
            home=home_page(rows=ROWS[:1]), reads={"17": [read_json(("700000009", "1.23", "18/11/2026 23:59:59"))]}
        )
        state = self.adapter.pending_ciencia(session, "tjgo-1g", NUMBER)
        self.assertEqual([i["id"] for i in state["items"]], ["700000009"])

    def test_read_list_is_paged_to_its_end(self):
        full = read_json(*[(str(n), "9.99", "20/11/2026 23:59:59") for n in range(tjgo_desk.PAGE_SIZE)])
        last = read_json(("700000010", "1.23", "20/11/2026 23:59:59"))
        session = DeskSession(home=home_page(rows=[]), reads={"17": [full, last]})
        state = self.adapter.pending_ciencia(session, "tjgo-1g", NUMBER)
        self.assertEqual([i["id"] for i in state["items"]], ["700000010"])
        endless = DeskSession(home=home_page(rows=[]), reads={"17": [full] * 50})
        self.assertIn(
            "could not be read in full", self.adapter.pending_ciencia(endless, "tjgo-1g", NUMBER)["unchecked"]
        )

    def test_unreadable_lists_leave_the_ciencia_unchecked(self):
        unknown = DeskSession(home=home_page(portal="<p>Intimações: <a href='Pendencia?x=1'>2</a></p>"))
        self.assertIn("layout", self.adapter.pending_ciencia(unknown, "tjgo-1g", NUMBER)["unchecked"])
        broken = DeskSession(reads={"14": ["<html>?</html>"]})
        self.assertIn("Diário", self.adapter.pending_ciencia(broken, "tjgo-1g", NUMBER)["unchecked"])
        changed = DeskSession(home=home_page().replace("Pend&ecirc;ncias</legend>", "Avisos</legend>"))
        self.assertIn("Pendências", self.adapter.pending_ciencia(changed, "tjgo-1g", NUMBER)["unchecked"])

    def test_expired_session_is_not_mistaken_for_an_empty_list(self):
        with self.assertRaises(base.ProjudiSessionExpired):
            self.adapter.pending_ciencia(DeskSession(home=EXPIRED), "tjgo-1g", NUMBER)

    def test_refresh_needs_the_lawyer_home(self):
        session = mock.Mock()
        session.get.return_value = page(home_page())
        self.assertIs(self.adapter.refresh("tjgo-1g", session), session)
        self.assertTrue(session.get.call_args.args[0].endswith(tjgo_desk.HOME))
        for html in (EXPIRED, '<html><input name="Usuario"><input name="Senha"></html>'):
            session.get.return_value = page(html)
            self.assertIsNone(self.adapter.refresh("tjgo-1g", session))

    def test_lists_of_intimations_and_processes(self):
        session = DeskSession(
            reads={"17": [read_json(("700000009", "1.23", "18/11/2026 23:59:59"))]},
            lists={"1": process_list(["0000001-23", "0000004-56"]), "72": process_list(["0000007-89"], total=2)},
        )
        listed = self.adapter.list_intimations(session, "tjgo-1g")
        self.assertEqual((listed["diarioCount"], listed["diarioDeclared"], listed["complete"]), (2, 2, True))
        self.assertEqual([i["id"] for i in listed["awaitingPublicationNotOnHome"]], ["700000009"])
        self.assertNotIn("_key", json.dumps(listed))
        processes = self.adapter.list_processes(session, "tjgo-1g")
        self.assertFalse(processes["complete"])
        self.assertEqual(
            [(g["subtype"], g["total"], g["complete"]) for g in processes["categories"]],
            [("1", 2, True), ("72", 2, False)],
        )
        self.assertTrue(all("QuantidadeRegistrosPagina=500" in p for p in session.paths if "Proprio=1" in p))

    def test_a_type_with_one_process_answers_its_page(self):
        from tests.connectors.projudi.test_projudi import PROCESS_HTML

        session = DeskSession(lists={"1": process_list(["0000004-56"]), "72": PROCESS_HTML})
        processes = self.adapter.list_processes(session, "tjgo-1g")
        self.assertTrue(processes["complete"])
        single = processes["categories"][1]
        self.assertEqual((single["total"], single["processes"][0]["number"]), (1, NUMBER))
        self.assertEqual(single["processes"][0]["parties"][0]["name"], "Parte Autora Ficticia")

    def test_short_number_opens_the_process_with_the_same_pair(self):
        self.assertEqual(self.adapter.normalize_number("4.56", "tjgo-1g"), "0000004-56")
        self.assertEqual(self.adapter.normalize_number(NUMBER, "tjgo-1g"), NUMBER)
        with self.assertRaises(ValueError):
            self.adapter.normalize_number("0000001-23.2026.8.16.0001", "tjgo-1g")
        from tests.connectors.projudi.test_projudi import PROCESS_HTML

        session = mock.Mock()
        session.request.return_value = page(PROCESS_HTML)
        self.assertEqual(self.adapter.open_process(session, "tjgo-1g", "0000001-23")["number"], NUMBER)
        self.assertEqual(session.request.call_args.kwargs["data"]["ProcessoNumero"], "0000001-23")
        with self.assertRaisesRegex(ValueError, "different process"):
            self.adapter.open_process(session, "tjgo-1g", "0000001-24")


class GuardedAdapter(base.Adapter):
    """Fake adapter with a fixed ciência state; records whether the process or a file was opened."""

    flavor = "guarded"

    def __init__(self, state):
        from tests.connectors.projudi.test_projudi import INDEX, PROCESS_HTML, pdf_with_text

        self.state, self.index, self.page = state, json.loads(INDEX), PROCESS_HTML
        self.pdf = pdf_with_text("Decisao ficticia")
        self.opened = []

    def is_login_redirect(self, response):
        return False

    def pending_ciencia(self, session, portal, process_number):
        return self.state

    def open_process(self, session, portal, process_number):
        self.opened.append(("process", process_number))
        return parsers.parse_process(self.page)

    def fetch_index(self, session, portal):
        return parsers.parse_index(json.dumps(self.index))

    def download(self, session, portal, document_id, hash_value):
        self.opened.append(("file", document_id))
        return self.pdf, "application/pdf"


PENDING = {
    "items": [
        {"id": "700000002", "process": "4.56", "kind": "Intimação", "publication": "19/11/2026", "_key": (4, 56)}
    ],
    "unchecked": None,
}


class CienciaGuardTest(unittest.TestCase):
    def read(self, state, **kwargs):
        adapter = GuardedAdapter(state)
        try:
            return adapter, base.read_documents(adapter, object(), "tjgo-1g", NUMBER, ["100"], **kwargs)
        except base.CienciaPending as error:
            return adapter, error

    def test_pending_ciencia_refuses_before_anything_is_opened(self):
        adapter, error = self.read(PENDING)
        self.assertIsInstance(error, base.CienciaPending)
        self.assertNotIsInstance(error, base.CienciaUnchecked)
        self.assertIn("ciência not yet registered for: 700000002 Intimação 4.56", str(error))
        self.assertIn("acknowledgeCiencia=true", str(error))
        self.assertEqual(adapter.opened, [])

    def test_unchecked_ciencia_refuses_before_anything_is_opened(self):
        adapter, error = self.read({"items": [], "unchecked": "the home page's Pendências could not be read"})
        self.assertIsInstance(error, base.CienciaUnchecked)
        self.assertTrue(str(error).startswith("ciência not checked: the home page"))
        self.assertEqual(adapter.opened, [])

    def test_acknowledged_read_reports_what_it_may_have_done(self):
        adapter, (_, documents, ciencia) = self.read(PENDING, acknowledge_ciencia=True)
        self.assertEqual(documents[0]["extractionStatus"], "extracted")
        self.assertEqual(ciencia["ciencia"]["pendenteAoLer"][0]["id"], "700000002")
        self.assertNotIn("_key", json.dumps(ciencia))
        self.assertIn("aviso", ciencia["ciencia"])
        _, (_, _, unchecked) = self.read({"items": [], "unchecked": "x"}, acknowledge_ciencia=True)
        self.assertIs(unchecked["cienciaChecked"], False)

    def test_nothing_pending_reads_without_a_notice(self):
        adapter, (_, documents, ciencia) = self.read({"items": [], "unchecked": None})
        self.assertEqual((ciencia, documents[0]["extractionStatus"]), ({}, "extracted"))
        self.assertEqual(adapter.opened, [("process", NUMBER), ("file", "100")])

    def test_download_is_guarded_too(self):
        adapter = GuardedAdapter(PENDING)
        with self.assertRaises(base.CienciaPending):
            base.download_document(adapter, object(), "tjgo-1g", NUMBER, "100")
        self.assertEqual(adapter.opened, [])


class GuardedServerTest(unittest.TestCase):
    def setUp(self):
        self.adapter = GuardedAdapter(PENDING)
        for patcher in (
            mock.patch.dict(server.ADAPTERS, {"tjgo-servlet": self.adapter}),
            mock.patch.object(
                server,
                "sessions",
                SessionPool(login=lambda portal: object(), refresh=lambda portal, s: s, close=lambda s: None),
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def call(self, name, arguments):
        async def run():
            async with Client(server.create_server()) as client:
                return await client.call_tool(name, arguments)

        return asyncio.run(run())

    def test_refusal_is_a_tool_error_and_the_acknowledgement_reads(self):
        refused = self.call("projudi_read_documents", {"process_number": NUMBER, "document_ids": ["100"]})
        self.assertTrue(refused.is_error)
        self.assertIn("ciência not yet registered", refused.content[0].text)
        self.assertEqual(self.adapter.opened, [])
        read = self.call(
            "projudi_read_documents", {"process_number": NUMBER, "document_ids": ["100"], "acknowledgeCiencia": True}
        ).structured_content
        self.assertEqual(read["ciencia"]["pendenteAoLer"][0]["id"], "700000002")
        self.assertEqual(read["documents"][0]["extractionStatus"], "extracted")

    def test_lists_are_tool_errors_on_a_generation_without_them(self):
        with mock.patch.object(base.Adapter, "list_processes", side_effect=NotImplementedError("no list")):
            self.assertTrue(self.call("projudi_list_processes", {}).is_error)
        listed = self.call("projudi_list_intimations", {"portal": "tjam-1g"})
        self.assertTrue(listed.is_error)
