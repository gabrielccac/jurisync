import asyncio
import unittest
from unittest.mock import patch

from mcp.client.client import Client

from jurisync.connectors.eproc import client, filtros, server
from jurisync.connectors.eproc.parsers import report_filter_request
from jurisync.core.errors import InputError

BASE = "https://eproc1g.trf6.jus.br/eproc/"
PANEL = '<a href="controlador.php?acao=relatorio_processo_procurador_listar&amp;hash=private">Relação de Processos</a>'
# Option values differ from any portal on purpose: they must be read from the form, never assumed.
FORM = """
<form id="frmProcessoLista" method="post"
 action="controlador.php?acao=relatorio_processo_procurador_listar&amp;acao_origem=relatorio_processo_procurador_listar&amp;hash=form">
<input type="hidden" name="hdnInfraTipoPagina" value="1">
<select name="selVaraFederal"><option value="">Todos</option><option value="7">Vara Fictícia</option></select>
<select name="prazoField" id="prazoField">
<option value="t0" selected>Todos</option><option value="s1">Aguardando abertura</option>
<option value="v2">Vencido (Decurso de Prazo)</option><option value="a3">Aberto</option>
<option value="f4">Fechado</option><option value="p5">Sem Prazo (Aberto/Ag.Abertura)</option></select>
<input type="text" name="dataInicioField" placeholder="__/__/____">
<input type="text" name="dataFimField" placeholder="__/__/____">
<input type="text" name="dataIntimacaoField" placeholder="__/__/____">
<select name="selStatusProcesso"><option value="" selected>Todos</option><option value="B">BAIXADO</option></select>
<input type="text" name="txtApenso">
<input type="checkbox" name="chkMostrarBaixados" id="chkMostrarBaixados" value="S">
<input type="hidden" name="hdnExcel" value="0">
<input type="hidden" name="hdnInfraNroItens" value="1"><input type="hidden" name="hdnInfraPaginaAtual" value="3">
<input type="hidden" name="hdnInfraHashCriterios" value="c1">
<table class="infraTable"><caption>Lista de Processos (1 registro):</caption>
<tr><th></th><th>Número Processo</th><th>Classe</th><th>Autores Principais</th><th>Réu(s)</th>
<th>Localidade Judicial</th><th>Assunto</th><th>Último Evento</th><th>Data/Hora Ult. Evento</th>
<th>Data/Hora Autuação</th><th>Valor da Causa</th></tr>
<tr class="infraTrClara"><td><input type="checkbox" name="chkInfraItem0" value="x"></td>
<td><a href="controlador.php?acao=processo_selecionar&amp;hash=private">{number}</a> JUÍZO1</td>
<td>Procedimento Comum</td><td>Parte Autora Fictícia</td><td>Réu Fictício</td><td>Cidade Fictícia</td>
<td>Assunto Fictício</td><td>Ato ordinatório</td><td>13/10/2026 17:40:13</td>
<td>30/07/2025 09:18:54</td><td>R$ 1.000,00</td></tr></table>
</form>
"""
FIRST = "5000001-00.2026.4.06.3800"
FILTERED = "5000002-00.2026.4.06.3800"


class Response:
    status_code = 200

    def __init__(self, body):
        self.content = body.encode("iso-8859-1")


class Session:
    def __init__(self, *bodies):
        self.bodies = iter(bodies)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, dict(kwargs.get("data") or [])))
        return Response(next(self.bodies))

    def close(self):
        pass


def page(number=FIRST):
    return FORM.replace("{number}", number)


class ProcessNumberTests(unittest.TestCase):
    def result(self, numbers, **pagination):
        return {
            "success": True,
            "items": [{"number": number} for number in numbers],
            "pagination": {"totalItems": len(numbers), "complete": True, **pagination},
            "diagnostics": [],
        }

    def test_every_row_gets_process_number_and_the_filter_keeps_that_process(self):
        result, total = filtros.by_process(self.result([FIRST, FILTERED]))
        self.assertEqual([item["processNumber"] for item in result["items"]], [FIRST, FILTERED])
        self.assertEqual([item["number"] for item in result["items"]], [FIRST, FILTERED])  # old name, one cycle
        self.assertEqual(total, 2)
        wanted = filtros.process_digits(FILTERED)
        self.assertEqual(wanted, filtros.process_digits("50000020020264063800"))
        result, total = filtros.by_process(self.result([FIRST, FILTERED]), wanted)
        self.assertEqual(([item["processNumber"] for item in result["items"]], total), ([FILTERED], 1))
        self.assertIn("filtered here", result["notes"][0])
        result, total = filtros.by_process(self.result([FIRST], totalItems=120, complete=False), wanted)
        self.assertEqual((result["items"], total), ([], None))  # not read to the end: unknown
        self.assertIn("later page", result["notes"][0])
        for wrong in ("123", "5000001-00.2026.4.04.720X", ""):
            with self.assertRaises(InputError):
                filtros.process_digits(wrong)

    def test_tools_filter_by_process_number(self):
        queue = self.result([FIRST, FILTERED])

        async def run():
            async with Client(server.create_server(["trf6-1g"])) as mcp:
                listed = await mcp.call_tool(
                    "eproc_list_intimacoes", {"portal": "trf6-1g", "queue": "pending", "processNumber": FILTERED}
                )
                processes = await mcp.call_tool("eproc_list_processes", {"portal": "trf6-1g"})
                wrong = await mcp.call_tool("eproc_list_processes", {"portal": "trf6-1g", "processNumber": "12"})
                return listed.structured_content, processes.structured_content, wrong

        server.sessions.close()
        try:
            with (
                patch.object(server.sessions, "_login", return_value=(Session(page()), PANEL, BASE)),
                patch.object(server, "list_notices", return_value=queue),
            ):
                listed, processes, wrong = asyncio.run(run())
        finally:
            server.sessions.close()
        self.assertEqual([row["processNumber"] for row in listed["items"]], [FILTERED])
        self.assertEqual((listed["returned"], listed["total"]), (1, 1))
        self.assertEqual(processes["items"][0]["processNumber"], FIRST)
        self.assertEqual((processes["returned"], processes["total"], processes["next"]), (1, 1, None))
        self.assertTrue(wrong.is_error)


class IntimationDateTests(unittest.TestCase):
    def sent(self, html, start, end):
        _, fields = report_filter_request(html, BASE, date_from=start, date_to=end)
        return dict(fields), fields

    def test_dates_go_to_the_period_fields_as_the_portal_writes_them(self):
        self.assertEqual(filtros.intimation_dates("2026-11-01", "2026-11-30"), ("01/11/2026", "30/11/2026"))
        self.assertEqual(filtros.intimation_dates(None, "2026-11-30"), (None, "30/11/2026"))
        sent, fields = self.sent(page(), "01/11/2026", "30/11/2026")
        self.assertEqual((sent["dataInicioField"], sent["dataFimField"]), ("01/11/2026", "30/11/2026"))
        self.assertEqual(sent["dataIntimacaoField"], "")
        self.assertEqual(sent["hdnInfraPaginaAtual"], "0")
        self.assertEqual(sent["prazoField"], "t0")  # the other criteria keep the form's value
        self.assertEqual(len(fields), len(sent))

    def test_a_form_with_only_the_intimation_day_takes_one_day(self):
        only_day = page().replace('name="dataInicioField"', 'name="a"').replace('name="dataFimField"', 'name="b"')
        sent, _ = self.sent(only_day, "03/11/2026", "03/11/2026")
        self.assertEqual(sent["dataIntimacaoField"], "03/11/2026")
        with self.assertRaisesRegex(ValueError, "single intimation date"):
            self.sent(only_day, "03/11/2026", "04/11/2026")
        with self.assertRaisesRegex(ValueError, "no intimation date filter"):
            self.sent(only_day.replace('name="dataIntimacaoField"', 'name="c"'), "03/11/2026", None)

    def test_dates_are_validated_before_login(self):
        for wrong in ("03/11/2026", "2026-02-30", "2026-1-3", ""):
            with self.assertRaises(InputError):
                filtros.intimation_dates(wrong)
        with self.assertRaisesRegex(InputError, "after"):
            filtros.intimation_dates("2026-11-30", "2026-11-01")

        async def run():
            async with Client(server.create_server(["trf6-1g"])) as mcp:
                arguments = {"portal": "trf6-1g", "intimationDateFrom": "2026-11-30", "intimationDateTo": "2026-11-01"}
                return await mcp.call_tool("eproc_list_processes", arguments)

        with patch.object(server.sessions, "_login") as login:
            self.assertTrue(asyncio.run(run()).is_error)
        login.assert_not_called()
        server.sessions.close()

    def test_client_posts_the_dates_and_reads_the_answer(self):
        session = Session(page(), page(FILTERED))
        result = client.list_processes(
            session=session, panel_html=PANEL, panel_url=BASE, portal="trf6-1g", date_from="01/11/2026"
        )
        self.assertEqual([method for method, *_ in session.calls], ["GET", "POST"])
        self.assertEqual(session.calls[1][2]["dataInicioField"], "01/11/2026")
        self.assertEqual([item["number"] for item in result["items"]], [FILTERED])


if __name__ == "__main__":
    unittest.main()
