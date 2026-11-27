import asyncio
import unittest
from unittest.mock import patch

from mcp.client.client import Client

from jurisync.connectors.eproc import client, server
from jurisync.connectors.eproc.parsers import parse_lawyer_process_report

BASE = "https://eproc1g.trf6.jus.br/eproc/"
PANEL = '<a href="controlador.php?acao=relatorio_processo_procurador_listar&amp;hash=private">Relação de Processos</a>'
REPORT = """
<select name="selStatusProcesso"><option selected>Todos</option></select>
<select><option>Desconto não autorizado de contribuição sindical</option></select>
<input type="checkbox" name="chkMostrarBaixados">
<input name="hdnInfraNroItens" value="1"><input name="hdnInfraPaginaAtual" value="0">
<table class="infraTable"><caption>Lista de Processos (1 registro):</caption>
<tr><th></th><th>Número Processo</th><th>Classe</th><th>Autores Principais</th><th>Réu(s)</th>
<th>Localidade Judicial</th><th>Assunto</th><th>Último Evento</th><th>Data/Hora Ult. Evento</th>
<th>Data/Hora Autuação</th><th>Valor da Causa</th></tr>
<tr class="infraTrClara"><td></td>
<td><a href="controlador.php?acao=processo_selecionar&amp;hash=private">0000001-23.2026.4.06.3800</a> JUÍZO1</td>
<td>JEF Cível</td><td>Autora Um<br>Autor Dois<br><br></td><td>INSS<br><br></td><td>Belo Horizonte</td>
<td>Pessoa com deficiência</td><td>Ato ordinatório</td><td>13/10/2026 17:40:13</td>
<td>30/07/2025 09:18:54</td><td>R$ 21.354,97</td></tr></table>
"""


class Response:
    status_code = 200

    def __init__(self, body):
        self.content = body.encode("iso-8859-1")


class Session:
    def __init__(self, body=REPORT):
        self.body = body
        self.calls = []
        self.closed = False

    def request(self, method, url, **kwargs):
        self.calls.append((method, url))
        return Response(self.body)

    def close(self):
        self.closed = True


class ProcessReportTests(unittest.TestCase):
    def test_report_has_distinct_columns_parties_and_pagination(self):
        result = parse_lawyer_process_report(REPORT, BASE)
        item = result["items"][0]
        self.assertEqual(item["number"], "0000001-23.2026.4.06.3800")
        self.assertEqual(item["court"], "JUÍZO1")
        self.assertEqual(item["subject"], "Pessoa com deficiência")
        self.assertEqual(item["lastEventAt"], "13/10/2026 17:40:13")
        self.assertEqual(item["claimValue"], "R$ 21.354,97")
        self.assertEqual(
            item["parties"],
            [
                {"pole": "active", "name": "Autora Um"},
                {"pole": "active", "name": "Autor Dois"},
                {"pole": "passive", "name": "INSS"},
            ],
        )
        self.assertEqual(result["filters"], {"status": "Todos", "includeClosed": False})
        self.assertTrue(result["pagination"]["complete"])
        partial = parse_lawyer_process_report(REPORT.replace("(1 registro)", "(7 registros)"), BASE)
        self.assertFalse(partial["pagination"]["complete"])
        self.assertEqual(partial["pagination"]["totalItems"], 7)

    def test_a_report_longer_than_one_page_is_not_complete(self):
        """The caption names the whole list and the page range; the item counter holds only the page size."""
        first_page = REPORT.replace("(1 registro)", "(843 registros - 1 a 1)")
        result = parse_lawyer_process_report(first_page, BASE)
        self.assertEqual(result["pagination"]["totalItems"], 843)
        self.assertEqual(result["pagination"]["itemCount"], 1)
        self.assertFalse(result["pagination"]["complete"])

    def test_empty_unknown_and_malformed_reports_are_distinguished(self):
        empty = REPORT[: REPORT.index('<tr class="infraTrClara">')] + "</table>"
        empty = empty.replace("(1 registro)", "(0 registros)")
        result = parse_lawyer_process_report(empty, BASE)
        self.assertEqual(result["items"], [])
        self.assertTrue(result["pagination"]["complete"])
        self.assertEqual(result["diagnostics"], [])
        for body in ("<html>Unexpected</html>", REPORT.replace("<td>JEF Cível</td>", "")):
            self.assertTrue(parse_lawyer_process_report(body, BASE)["diagnostics"])

    def test_subject_options_do_not_mask_real_access_or_session_errors(self):
        self.assertIsNone(client.classify_body(REPORT))
        self.assertEqual(client.classify_body(REPORT + "<div>Acesso negado</div>"), "access_denied")
        self.assertEqual(client.classify_body('<input name="username">'), "session_expired")

    def test_client_reuses_session_and_redacts_signed_links(self):
        session = Session()
        with patch.object(client, "authenticate") as login:
            result = client.list_processes(session=session, panel_html=PANEL, panel_url=BASE, portal="trf6-1g")
        login.assert_not_called()
        self.assertTrue(result["success"])
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(session.calls[0][0], "GET")
        self.assertNotIn("hash", str(result))
        self.assertNotIn("url", str(result))
        self.assertFalse(session.closed)

    def test_client_closes_owned_session_even_on_unrecognized_response(self):
        session = Session("<html>Unexpected</html>")
        with patch.object(client, "authenticate", return_value=(session, PANEL, BASE)):
            result = client.list_processes(portal="trf6-1g")
        self.assertFalse(result["success"])
        self.assertTrue(session.closed)

    def test_missing_report_and_foreign_host_fail_before_request(self):
        for panel in (
            "<html>Panel</html>",
            PANEL.replace('href="controlador.php', 'href="https://other.example/controlador.php'),
        ):
            session = Session()
            with self.assertRaises(RuntimeError):
                client.list_processes(session=session, panel_html=panel, panel_url=BASE, portal="trf6-1g")
            self.assertFalse(session.calls)

    def test_report_pages_are_posted_like_the_pager_and_counted_truthfully(self):
        """ "(3 registros - 1 a 1)": later pages re-post the list form with hdnInfraPaginaAtual, criteria intact."""

        def page(number, index, caption="(3 registros - {0} a {0})", criteria="c1"):
            row = REPORT.replace("0000001-23.2026.4.06.3800", number).replace("(1 registro)", caption.format(index + 1))
            pager = "".join(
                f'<option value="{i}"{" selected" if i == index else ""}>{i + 1}</option>' for i in range(3)
            )
            return (
                '<form id="frmProcessoLista" method="post" '
                'action="controlador.php?acao=relatorio_processo_procurador_listar&amp;hash=form">'
                + row.replace('<input name="hdnInfraPaginaAtual" value="0">', "")
                + f'<select id="selInfraPaginacaoSuperior">{pager}</select>'
                + f'<input type="hidden" name="hdnInfraPaginaAtual" value="{index}">'
                + f'<input type="hidden" name="hdnInfraHashCriterios" value="{criteria}">'
                + '<select name="selStatusProcesso"><option value="">Todos</option><option value="B">B</option></select>'
                + '<select name="assuntoField[]" multiple><option value="1">um</option></select>'
                + '<input type="checkbox" name="chkInfraItem0" value="x"></form>'
            )

        numbers = ["0000001-23.2026.4.06.3800", "0000002-23.2026.4.06.3800", "0000003-23.2026.4.06.3800"]

        class Pages(Session):
            def __init__(self, bodies):
                super().__init__()
                self.bodies = iter(bodies)
                self.posted = []

            def request(self, method, url, **kwargs):
                self.calls.append((method, url))
                self.posted.append(kwargs.get("data"))
                return Response(next(self.bodies))

        session = Pages([page(n, i) for i, n in enumerate(numbers)])
        result = client.list_processes(session=session, panel_html=PANEL, panel_url=BASE, portal="trf6-1g", max_pages=5)
        self.assertTrue(result["success"])
        self.assertEqual([item["number"] for item in result["items"]], numbers)
        self.assertEqual(result["pagination"]["totalItems"], 3)
        self.assertEqual(result["pagination"]["totalPages"], 3)
        self.assertEqual(result["pagination"]["pagesRead"], 3)
        self.assertTrue(result["pagination"]["complete"])
        self.assertEqual([method for method, _ in session.calls], ["GET", "POST", "POST"])
        self.assertIn("hash=form", session.calls[1][1])
        sent = dict(session.posted[2])
        self.assertEqual(sent["hdnInfraPaginaAtual"], "2")
        self.assertEqual(sent["hdnInfraHashCriterios"], "c1")
        self.assertEqual(sent["selStatusProcesso"], "")  # a single select sends its first option, as a browser does
        self.assertNotIn("assuntoField[]", sent)  # a multiple select with nothing selected sends nothing
        self.assertNotIn("chkInfraItem0", sent)
        self.assertNotIn("hash", str(result))

        # maxPages stops early and says so; the list is not complete.
        session = Pages([page(n, i) for i, n in enumerate(numbers)])
        result = client.list_processes(session=session, panel_html=PANEL, panel_url=BASE, portal="trf6-1g", max_pages=2)
        self.assertEqual(result["pagination"]["itemCount"], 2)
        self.assertFalse(result["pagination"]["complete"])
        self.assertEqual(result["notes"], ["stopped at maxPages=2 of 3"])

        # A page that answers another page number (the portal reset the criteria) is not mixed in.
        session = Pages([page(numbers[0], 0), page(numbers[0], 0)])
        result = client.list_processes(session=session, panel_html=PANEL, panel_url=BASE, portal="trf6-1g", max_pages=3)
        self.assertEqual(result["pagination"]["itemCount"], 1)
        self.assertEqual(result["pagination"]["pagesRead"], 1)
        self.assertFalse(result["pagination"]["complete"])
        self.assertEqual(result["notes"], ["report_page_2_mismatch"])

        with self.assertRaises(ValueError):
            client.list_processes(session=Session(), panel_html=PANEL, panel_url=BASE, portal="trf6-1g", max_pages=0)

    def test_mcp_exposes_listing_and_reuses_session(self):
        session = Session()

        async def run():
            async with Client(server.create_server(["trf6-1g"])) as mcp:
                tools = {tool.name: tool for tool in (await mcp.list_tools()).tools}
                self.assertIn("eproc_list_processes", tools)
                for _ in range(2):
                    result = await mcp.call_tool("eproc_list_processes", {"portal": "trf6-1g"})
                    self.assertFalse(result.is_error)
                    self.assertEqual(len(result.structured_content["items"]), 1)

        server.sessions.close()
        try:
            with patch.object(server.sessions, "_login", return_value=(session, PANEL, BASE)) as login:
                asyncio.run(run())
                self.assertEqual(login.call_count, 1)
        finally:
            server.sessions.close()


if __name__ == "__main__":
    unittest.main()
