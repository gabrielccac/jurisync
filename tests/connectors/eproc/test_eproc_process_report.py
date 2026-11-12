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
                {"role": "AUTOR", "name": "Autora Um"},
                {"role": "AUTOR", "name": "Autor Dois"},
                {"role": "REU", "name": "INSS"},
            ],
        )
        self.assertEqual(result["filters"], {"status": "Todos", "includeClosed": False})
        self.assertTrue(result["pagination"]["complete"])
        partial = parse_lawyer_process_report(REPORT.replace("(1 registro)", "(7 registros)"), BASE)
        self.assertFalse(partial["pagination"]["complete"])
        self.assertEqual(partial["pagination"]["totalItems"], 7)

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
