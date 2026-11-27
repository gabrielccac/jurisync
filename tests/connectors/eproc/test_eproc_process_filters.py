"""eproc_list_processes filters: Listar baixados, Prazo and the latest-movement order, posted like the Buscar button."""

import unittest

from jurisync.connectors.eproc import client
from jurisync.connectors.eproc.parsers import parse_panel, report_filter_request

BASE = "https://eproc1g.tjmg.jus.br/eproc/"
PANEL_URL = BASE + "controlador.php?acao=painel_adv_listar&hash=p"
# The TJMG panel lists "Últimas movimentações" before "Relação de processos" (27/11/2026).
PANEL = (
    '<a href="controlador.php?acao=relatorio_processo_procurador_listar&amp;ord_ultimas_movimentacoes=S&amp;hash=m">'
    "Últimas movimentações</a>"
    '<a href="controlador.php?acao=relatorio_processo_procurador_listar&amp;hash=r">Relação de processos</a>'
)
ROW = (
    '<tr class="infraTrClara"><td></td><td><a href="controlador.php?acao=processo_selecionar&amp;hash=x">'
    "0000001-23.2026.8.13.0024</a> BHE 6 V.C. J</td><td>Classe</td><td>Autor</td><td>Réu</td><td>Belo Horizonte</td>"
    "<td>Assunto</td><td>Evento</td><td>13/10/2026 17:40:13</td><td>30/07/2025 09:18:54</td><td>R$ 1,00</td></tr>"
)
HEADER = (
    "<tr><th></th><th>Número Processo</th><th>Classe</th><th>Autores Principais</th><th>Réu(s)</th>"
    "<th>Localidade Judicial</th><th>Assunto</th><th>Último Evento</th><th>Data/Hora Ult. Evento</th>"
    "<th>Data/Hora Autuação</th><th>Valor da Causa</th></tr>"
)


def report(prazo="T", archived=False):
    options = "".join(
        f'<option value="{value}"{" selected" if value == prazo else ""}>{label}</option>'
        for value, label in (("T", "Todos"), ("S", "Aguardando abertura"), ("V", "Vencido"), ("A", "Aberto"))
    )
    checked = " checked" if archived else ""
    return (
        '<form id="frmProcessoLista" method="post" action="controlador.php?acao=relatorio_processo_procurador_listar'
        '&amp;acao_origem=relatorio_processo_procurador_listar&amp;hash=f">'
        f'<select name="prazoField">{options}</select><select name="selStatusProcesso"><option selected>Todos</option>'
        f'</select><input type="checkbox" name="chkMostrarBaixados" value="S"{checked}>'
        '<input type="hidden" name="hdnInfraNroItens" value="1"><input type="hidden" name="hdnInfraPaginaAtual" value="0">'
        '<input type="hidden" name="hdnExcel" value="0"><input type="hidden" name="hdnInfraHashCriterios" value="c">'
        f'<table class="infraTable"><caption>Lista de Processos (1 registro):</caption>{HEADER}{ROW}</table></form>'
    )


class Response:
    def __init__(self, body):
        self.status_code = 200
        self.content = body.encode("iso-8859-1")
        self.url = BASE
        self.headers = {}


class Portal:
    def __init__(self, filtered=None):
        self.filtered = filtered or report("A", archived=True)
        self.calls = []

    def request(self, method, url, data=None, **_kwargs):
        self.calls.append((method, url, data))
        return Response(self.filtered if method == "POST" else report())

    def close(self):
        pass


def listing(**filters):
    portal = Portal()
    result = client.list_processes(session=portal, panel_html=PANEL, panel_url=PANEL_URL, portal="tjmg-1g", **filters)
    return result, portal.calls


class ProcessFilterTest(unittest.TestCase):
    def test_default_reads_the_plain_list_not_the_latest_movement_link(self):
        self.assertIn("hash=r", parse_panel(PANEL, BASE)["actions"]["relatorio_processo_procurador_listar"])
        result, calls = listing()
        self.assertEqual([(method, url.split("hash=")[1]) for method, url, _ in calls], [("GET", "r")])
        self.assertEqual(result["filters"], {"includeClosed": False, "status": "Todos", "prazo": "Todos"})

    def test_filters_post_the_form_like_its_buscar_button(self):
        result, calls = listing(include_archived=True, prazo="aberto")
        self.assertEqual([method for method, _, _ in calls], ["GET", "POST"])
        fields = dict(calls[1][2])
        self.assertEqual(
            (fields["prazoField"], fields["chkMostrarBaixados"], fields["hdnInfraPaginaAtual"]), ("A", "S", "0")
        )
        self.assertEqual(fields["hdnInfraHashCriterios"], "c")
        self.assertEqual(result["filters"], {"includeClosed": True, "status": "Todos", "prazo": "Aberto"})
        self.assertEqual(len(result["items"]), 1)

    def test_archived_false_unchecks_and_latest_first_opens_the_ordered_link(self):
        url, fields = report_filter_request(report(archived=True), BASE, include_archived=False)
        self.assertNotIn("chkMostrarBaixados", dict(fields))
        self.assertIn("acao=relatorio_processo_procurador_listar", url)
        result, calls = listing(latest_first=True)
        self.assertIn("ord_ultimas_movimentacoes=S", calls[0][1])
        self.assertEqual(result["filters"]["order"], "latest_movement")

    def test_unknown_or_missing_filters_are_refused(self):
        with self.assertRaises(ValueError):
            listing(prazo="pendente")
        without = report().replace('<select name="prazoField">', '<select name="outro">')
        with self.assertRaises(ValueError):
            report_filter_request(without, BASE, prazo="aberto")


if __name__ == "__main__":
    unittest.main()
