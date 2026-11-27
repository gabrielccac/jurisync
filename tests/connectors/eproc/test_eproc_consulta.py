import asyncio
import json
import logging
import unittest
from unittest.mock import patch

from mcp.client.client import Client

from jurisync.connectors.eproc import client, consulta, server
from jurisync.core.errors import InputError

BASE = "https://eproc1g.trf6.jus.br/eproc/"
PANEL = '<a href="controlador.php?acao=processo_consultar&amp;hash=page">Consultar Processos</a>'
LOOKUP = "controlador_ajax.php?acao_ajax={action}&amp;hash=lookup"
# tipoPesquisa values differ from any portal on purpose: they must be read from the form, never assumed.
OPTIONS = [
    ("n1", "Número de Processo, Chave", "processos_consulta_por_numprocesso"),
    ("n2", "Nome da Parte", "processos_consulta_por_nome_parte"),
    ("c3", "CPF/CNPJ", "processos_consulta_por_documento_identificacao"),
    ("o4", "OAB", "processos_consulta_por_oab"),
    ("p5", "Originário / Relacionado", "processos_consulta_por_originario"),
]
PAGE = """
<form id="frmProcessoListaAjax" method="post">
<input type="hidden" name="hdnInfraTipoPagina" value="1">
<button type="submit" name="sbmConsultar" value="Consultar">Consultar</button>
<input type="hidden" name="strIdForm" value="frmProcessoListaAjax">
<input type="hidden" name="fnValidacao[]" value="gerenciadorTelaConsulta">
<input type="hidden" name="acao" value="processo_consultar">
<select id="selTipoPesquisa" name="tipoPesquisa">{options}</select>
<input type="text" name="numNrProcesso"><input type="text" name="strNomeParte"><input type="hidden" name="numIdPessoa">
<input type="text" name="strDocParte" maxlength="18"><input type="text" name="numNumOab">
<input type="text" name="numProcessoOriginario">
<input type="checkbox" name="chkNomeParte" value="true"><input type="checkbox" name="chkExibirBaixados">
<div id="divAreaResultadosAjax"></div>
</form>
<div id="divCaptchaWrapper"><div id="divInfraCaptcha"></div></div>
"""


def page(options=OPTIONS):
    rendered = "".join(
        f'<option value="{value}" data-acaoConsultaAssinada="{LOOKUP.format(action=action)}">{label}</option>'
        for value, label, action in options
    )
    return PAGE.replace("{options}", rendered)


FIRST = "5000001-00.2026.4.04.7200"
SECOND = "5000002-00.2026.4.04.7200"


def answer(number, index=0, pages=2, total=2):
    pager = "".join(f'<option value="{i}"{" selected" if i == index else ""}>{i + 1}</option>' for i in range(pages))
    return f"""
<table class="infraTable"><caption>Lista de Processos ({total} registros - {index + 1} a {index + 1}):</caption>
<tr><th>Número do Processo</th><th>Classe</th><th>Autor</th><th>Réu</th><th>Órgão Julgador</th>
<th>Data Autuação</th><th>Relator</th></tr>
<tr class="infraTrClara"><td><a href="controlador.php?acao=processo_selecionar&amp;hash=private">{number}</a></td>
<td>Procedimento Comum</td><td>Fulana de Tal (111.222.333-44)<br>Beltrano Fictício</td><td>Empresa Fictícia Ltda</td>
<td>1ª Vara Fictícia</td><td>30/07/2025 09:18:54</td><td>Des. Inventado</td></tr></table>
<select id="selInfraPaginacaoSuperior">{pager}</select>
<input type="hidden" name="hdnInfraPaginaAtual" value="{index}">
<input type="hidden" name="hdnInfraHashCriterios" value="crit">
"""


class Response:
    status_code = 200

    def __init__(self, body):
        self.content = body.encode("iso-8859-1")


class Session:
    def __init__(self, *bodies):
        self.bodies = iter(bodies)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, list(kwargs.get("data") or [])))
        return Response(next(self.bodies))

    def close(self):
        pass


class SearchTermTests(unittest.TestCase):
    def test_exactly_one_criterion_in_the_portals_format(self):
        self.assertEqual(consulta.search_term(party_name="  Fulana   de Tal "), ("partyName", "Fulana de Tal"))
        self.assertEqual(consulta.search_term(tax_id="11122233344"), ("taxId", "111.222.333-44"))
        self.assertEqual(consulta.search_term(tax_id="12.345.678/0001-00"), ("taxId", "12.345.678/0001-00"))
        self.assertEqual(consulta.search_term(oab="sc 12.345"), ("oab", "SC012345"))
        self.assertEqual(consulta.search_term(oab="OAB/RS 123456"), ("oab", "RS123456"))
        self.assertEqual(consulta.search_term(oab="1234/PR"), ("oab", "PR001234"))
        self.assertEqual(consulta.search_term(origin_number=FIRST), ("originNumber", "50000010020264047200"))
        for arguments in ({}, {"party_name": "Fulana de Tal", "oab": "SC1"}, {"tax_id": "  "}):
            with self.assertRaisesRegex(InputError, "exactly one"):
                consulta.search_term(**arguments)

    def test_invalid_values_are_refused_without_echoing_them(self):
        for arguments in (
            {"party_name": "Fulana"},
            {"tax_id": "111.222.333-4"},
            {"tax_id": "111222333-44x"},
            {"oab": "XX123"},
            {"oab": "SC1234567"},
            {"origin_number": "abc"},
        ):
            with self.assertRaises(InputError) as caught:
                consulta.search_term(**arguments)
            value = next(iter(arguments.values()))
            self.assertNotIn(value, str(caught.exception))


class SearchRequestTests(unittest.TestCase):
    def test_the_form_is_posted_to_the_lookup_of_the_chosen_type(self):
        url, fields = consulta.search_request(page(), BASE, "taxId", "111.222.333-44", include_closed=True)
        sent = dict(fields)
        self.assertIn("acao_ajax=processos_consulta_por_documento_identificacao", url)
        self.assertEqual(sent["tipoPesquisa"], "c3")
        self.assertEqual(sent["strDocParte"], "111.222.333-44")
        self.assertEqual(sent["chkExibirBaixados"], "on")
        self.assertEqual(sent["acao"], "processo_consultar")
        self.assertNotIn("sbmConsultar", sent)
        self.assertNotIn("chkNomeParte", sent)
        self.assertEqual(len(fields), len(sent))
        _, fields = consulta.search_request(page(), BASE, "oab", "SC012345")
        self.assertNotIn("chkExibirBaixados", dict(fields))
        self.assertEqual(dict(fields)["tipoPesquisa"], "o4")

    def test_a_search_type_the_portal_does_not_offer_is_a_clear_input_error(self):
        without = page([option for option in OPTIONS if option[1] != "OAB"])
        with self.assertRaisesRegex(InputError, "no search by oab"):
            consulta.search_request(without, BASE, "oab", "SC012345")
        no_box = page().replace('name="chkExibirBaixados"', 'name="outro"')
        with self.assertRaisesRegex(InputError, "baixados"):
            consulta.search_request(no_box, BASE, "partyName", "Fulana de Tal", include_closed=True)

    def test_the_lookups_are_allowed_only_where_the_search_names_them(self):
        self.assertEqual(client.SEARCH_AJAX_ACTIONS, {lookup for *_, lookup in consulta.CRITERIA.values()})
        self.assertNotIn(client.SEARCH_ACTION, client.READ_ACTIONS)
        self.assertFalse(client.SEARCH_AJAX_ACTIONS & client.AJAX_ACTIONS)
        lookup = BASE + "controlador_ajax.php?acao_ajax=processos_consulta_por_oab&hash=x"
        client.require_route(lookup, client.SEARCH_AJAX_ACTIONS, ajax=True, portal="trf6-1g")
        with self.assertRaises(RuntimeError):
            client.require_route(lookup, ajax=True, portal="trf6-1g")

    def test_write_actions_stay_blocked(self):
        for action in (
            "processo_movimentar",
            "processo_peticionar",
            "processo_distribuir",
            "intimacao_pauta_confirmar",
            "processo_cadastrar",
            "prazo_abrir",
        ):
            for ajax in (False, True):
                key = "acao_ajax" if ajax else "acao"
                route = BASE + ("controlador_ajax.php" if ajax else "controlador.php") + f"?{key}={action}&hash=x"
                with self.assertRaises(RuntimeError):
                    client.send(Session(), "POST", route, ajax=ajax, portal="trf6-1g")
        # A search type whose option points elsewhere is refused before anything is posted.
        hijacked = page([("o4", "OAB", "processo_movimentar")])
        session = Session(hijacked)
        with self.assertRaises(RuntimeError):
            consulta.search_processes(
                ("oab", "SC012345"), session=session, panel_html=PANEL, panel_url=BASE, portal="trf6-1g"
            )
        self.assertEqual([method for method, *_ in session.calls], ["GET"])


class SearchResultTests(unittest.TestCase):
    def test_the_answer_is_read_by_its_headers(self):
        result = consulta.parse_search_results(answer(FIRST, pages=1, total=1))
        self.assertEqual(result["diagnostics"], [])
        item = result["items"][0]
        self.assertEqual(item["processNumber"], FIRST)
        self.assertEqual(item["caseClass"], "Procedimento Comum")
        self.assertEqual(item["court"], "1ª Vara Fictícia")
        self.assertEqual(item["filedAt"], "2025-07-30T09:18:54")
        self.assertEqual(item["filedAtText"], "30/07/2025 09:18:54")  # the portal's text, for one cycle
        self.assertEqual(item["extra"], {"Relator": "Des. Inventado"})
        self.assertEqual(
            item["parties"],
            [
                {"pole": "active", "name": "Fulana de Tal (111.***.***-44)"},
                {"pole": "active", "name": "Beltrano Fictício"},
                {"pole": "passive", "name": "Empresa Fictícia Ltda"},
            ],
        )
        self.assertTrue(result["pagination"]["complete"])
        wrapped = consulta.parse_search_results(json.dumps({"status": "ok", "html": answer(FIRST, pages=1, total=1)}))
        self.assertEqual(wrapped["items"], result["items"])

    def test_captcha_empty_and_other_answers_are_told_apart(self):
        self.assertIn(
            "captcha_required", consulta.parse_search_results("<div>Digite o captcha</div>")["diagnostics"][0]
        )
        self.assertIn("captcha", consulta.parse_search_results('{"erro": "Captcha inválido"}')["diagnostics"][0])
        empty = consulta.parse_search_results("<div>Nenhum registro encontrado.</div>")
        self.assertEqual((empty["items"], empty["diagnostics"], empty["pagination"]["complete"]), ([], [], True))
        self.assertEqual(consulta.parse_search_results("<p>?</p>")["diagnostics"], ["unsupported_search_response"])
        people = "<table><tr><th>Nome</th><th>Documento</th></tr><tr><td>Fulana de Tal</td><td>-</td></tr></table>"
        self.assertIn("search_answer_without_processes", consulta.parse_search_results(people)["diagnostics"][0])

    def test_search_reads_pages_like_the_pager_and_answers_with_the_envelope(self):
        session = Session(page(), answer(FIRST, 0), answer(SECOND, 1))
        with self.assertLogs(level=logging.DEBUG) as logs:
            logging.getLogger("jurisync.test").debug("search starts")
            result = consulta.search_processes(
                ("partyName", "Fulana de Tal"),
                session=session,
                panel_html=PANEL,
                panel_url=BASE,
                portal="trf6-1g",
                max_pages=3,
            )
        self.assertTrue(result["success"])
        self.assertEqual([method for method, *_ in session.calls], ["GET", "POST", "POST"])
        self.assertIn("acao=processo_consultar", session.calls[0][1])
        self.assertIn("acao_ajax=processos_consulta_por_nome_parte", session.calls[1][1])
        second = dict(session.calls[2][2])
        self.assertEqual(second["hdnInfraPaginaAtual"], "1")
        self.assertEqual(second["hdnInfraHashCriterios"], "crit")
        self.assertEqual(second["strNomeParte"], "Fulana de Tal")
        self.assertEqual([item["processNumber"] for item in result["processes"]], [FIRST, SECOND])
        self.assertEqual((result["returned"], result["total"], result["next"]), (2, 2, None))
        self.assertEqual(result["scope"], "court_search")
        self.assertNotIn("hash", str(result))
        self.assertNotIn("Fulana de Tal", "\n".join(logs.output))

        session = Session(page(), answer(FIRST, 0, pages=4, total=4))
        result = consulta.search_processes(
            ("oab", "SC012345"), session=session, panel_html=PANEL, panel_url=BASE, portal="trf6-1g"
        )
        self.assertEqual((result["returned"], result["total"], result["next"]), (1, 4, {"maxPages": 4}))
        self.assertFalse(result["pagination"]["complete"])
        self.assertEqual(result["notes"], ["stopped at maxPages=1 of 4"])

        # Another page number coming back is not mixed in.
        session = Session(page(), answer(FIRST, 0), answer(SECOND, 0))
        result = consulta.search_processes(
            ("oab", "SC012345"), session=session, panel_html=PANEL, panel_url=BASE, portal="trf6-1g", max_pages=2
        )
        self.assertEqual(result["returned"], 1)
        self.assertEqual(result["notes"], ["search_page_2_failed"])

    def test_tool_checks_the_criterion_before_login_and_says_it_is_court_wide(self):
        async def run():
            async with Client(server.create_server(["trf6-1g"])) as mcp:
                tools = {tool.name: tool for tool in (await mcp.list_tools()).tools}
                two = await mcp.call_tool(
                    "eproc_search_processes", {"portal": "trf6-1g", "oab": "SC1", "taxId": "11122233344"}
                )
                return tools["eproc_search_processes"], two

        with patch.object(server.sessions, "_login") as login:
            tool, two = asyncio.run(run())
        login.assert_not_called()
        self.assertTrue(two.is_error)
        self.assertIn("exactly one", str(two.content))
        self.assertTrue(tool.annotations.read_only_hint)
        self.assertIn("court-wide", tool.description)
        self.assertIn("eproc_list_processes", tool.description)
        server.sessions.close()

    def test_tool_returns_the_search(self):
        async def run():
            async with Client(server.create_server(["trf6-1g"])) as mcp:
                return await mcp.call_tool("eproc_search_processes", {"portal": "trf6-1g", "taxId": "111.222.333-44"})

        session = Session(page(), answer(FIRST, 0, pages=1, total=1))
        server.sessions.close()
        try:
            with patch.object(server.sessions, "_login", return_value=(session, PANEL, BASE)):
                result = asyncio.run(run())
        finally:
            server.sessions.close()
        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content["processes"][0]["processNumber"], FIRST)
        self.assertEqual(dict(session.calls[1][2])["strDocParte"], "111.222.333-44")


if __name__ == "__main__":
    unittest.main()
