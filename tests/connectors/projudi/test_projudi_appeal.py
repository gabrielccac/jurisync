"""Projudi desk generation (TJPR/TJAM): a 2º grau number opens the recurso page, and projudi_get_process adds the capa
tabs and each movement's intimações. Markup as observed on TJPR on 2026-11-27 (same generation as TJAM); every name,
number and token is synthetic."""

import unittest

from jurisync.connectors.projudi import tjam
from tests.connectors.projudi.test_legacy_local import (
    APP,
    FILES_PAGE,
    FILES_URL,
    KEY,
    NUMBER,
    PARTIES_PAGE,
    POST_LOGON,
    PROCESS_PAGE,
    SEARCH_PAGE,
    SEARCH_RESULT,
    TJ,
    FakeSession,
    response,
    wired,
)

APPEAL = "0000003-45.2026.8.04.0000"
INTIMATIONS_URL = f"/projudi/movimentacao.do?_tj={TJ}a7"
APPEAL_MENU = POST_LOGON.replace(
    "</script>",
    f'add_menu("Processos 2º Grau", "/processo/buscaProcessosQualquerInstancia.do?_tj={TJ}2g", "userMainFrame", "");'
    "</script>",
)
APPEAL_SEARCH_PAGE = SEARCH_PAGE.replace('value="false"', 'value="true"').replace("numeroProcesso", "numeroRecurso")
APPEAL_RESULT = f"""<html><table class="resultTable"><tr><td><input type="checkbox" name="processos" value="9"></td>
<td><a href="/projudi/processo/recursal/recurso.do?_tj={TJ}rc" class="link"><em>{APPEAL} AI</em></a></td></tr>
</table></html>"""


def intimations_expander(source):
    return f"""<a href="javascript://nop/" class="{source}"><img></a><b>Intimações</b>
<script type="text/javascript">var aj_null = new AjaxJspTag.HtmlContent(
"{INTIMATIONS_URL}", {{
sourceClass: "{source}",
parameters: "tipoLayout=1",
doPost: true,
target: "divIntimacoesProcesso{source}"}});</script>"""


APPEAL_PAGE = f"""<html><legend>Recurso {APPEAL} AI</legend>
<table class="form"><tr><td class="label">Órgão Julgador:</td><td>1ª Câmara Cível</td></tr>
<tr><td class="label">Nível de Sigilo:</td><td>Público</td></tr></table>
<div id="tabprefix0"><fieldset><legend>Informações Gerais</legend><table class="form">
<tr><td class="label">Autuação:</td><td>01/10/2026</td></tr><tr><td class="label">Valor da Demanda:</td>
<td>R$ 1.000,00</td></tr><tr><td class="label">Data do Arquivamento:</td><td></td></tr></table></fieldset></div>
<div id="tabprefix1"><fieldset><legend>Agravante</legend><table class="resultTable"><thead><tr><th></th><th>Nome</th>
<th>RG</th><th>CPF/CNPJ</th><th>Advogados</th></tr></thead><tbody><tr><td></td><td>Parte Recorrente</td><td>1</td>
<td>000.000.000-00</td><td>OAB 1N-AM - ADVOGADA EXEMPLO</td></tr><tr><td colspan="5"><fieldset>
<legend>Endereço</legend><table class="resultTable"><thead><tr><th>Endereço</th></tr></thead><tbody><tr>
<td>Rua Exemplo</td></tr></tbody></table></fieldset></td></tr></tbody></table></fieldset></div>
<div id="tabprefix2"><table class="resultTable"><thead><tr><th></th><th>Seq.</th><th>Data</th><th>Evento</th>
<th>Movimentado Por</th></tr></thead><tbody><tr id="mov2Grau,JUIZ,,,,,"><td>
<a href="javascript://nop/" class="linkArquivosmovimentacoesRecurso1"><img></a>
<script type="text/javascript">var aj_null = new AjaxJspTag.HtmlContent(
"{FILES_URL}", {{
sourceClass: "linkArquivosmovimentacoesRecurso1",
target: "divArquivosMovimentacaoProcessomovimentacoesRecurso1"}});</script></td><td>1</td>
<td>05/11/2026 14:06:56</td><td>DECISÃO MONOCRÁTICA</td><td>Desembargador Exemplo
{intimations_expander("linkIntimacoesmovimentacoesRecurso1")}</td></tr></tbody></table></div>
<div id="tabprefix11"><table class="resultTable"><thead><tr><th>Pauta da Sessão</th><th>Data de Realização</th>
<th>Situação</th><th>Motivo</th><th>Observação</th></tr></thead><tbody><tr>
<td>26/10/2026 00:00 às 03/11/2026 23:59</td><td>04/11/2026 13:50</td><td>Julgamento Realizado</td><td></td><td></td>
</tr></tbody></table></div>
<div id="tabprefix12"><table class="resultTable"><thead><tr><th></th><th>Publicação</th><th>Tipo de Documento</th>
<th>Data da Geração</th><th>Situação</th></tr></thead><tbody><tr><td colspan="5">Nenhum registro encontrado</td></tr>
</tbody></table></div></html>"""
# The movement with sequence 2 gets an "Intimações" expander.
DETAILED_PROCESS_PAGE = PROCESS_PAGE.replace(
    "<td>Servidor Exemplo</td>",
    f"<td>Servidor Exemplo {intimations_expander('linkIntimacoesmovimentacoes0')}</td>",
)
MOVEMENT_INTIMATIONS = """<table class="resultTable"><thead><tr><th>Nome</th><th>Prazo</th><th>Urgente</th>
<th>Intimação Pessoal</th><th>Intimação Online</th><th>Data de Leitura</th><th>Data de Cumprimento</th>
<th>Data Decurso</th><th>Data de Renúncia de Prazo</th><th>Status</th><th>Leitor</th></tr></thead><tbody>
<tr class="even"><td>PARTE AUTORA</td><td>5 dias úteis</td><td>Não</td><td>Não</td><td>Sim
<img src="/projudi/img/themes/olive/mainPage/iInfoAdvogado.gif" title="Data de veiculação no DJEN: 07/10/2026"></td>
<td>08/10/2026 23:59</td><td>-</td><td>17/10/2026</td><td>-</td><td>LIDA, PRAZO VENCIDO</td>
<td>ADVOGADA EXEMPLO(Leitura automática em 08/10/2026 às 23:59)</td></tr></tbody></table>"""
DATA_TAB = """<html><div id="tabprefix0"><table class="form"><tr><td class="label">Data do Trânsito em Julgado:</td>
<td></td></tr><tr><td class="label">Situação:</td><td>Ativo</td></tr><tr><td class="label">Valor da Causa:</td>
<td>R$ 2.000,00</td></tr></table></div></html>"""
ADDITIONAL_TAB = """<html><div id="tabprefix1"><table class="form"><tr><td class="label">Trânsito em Julgado:</td>
<td>Sem Trânsito Cadastrado</td></tr></table></div></html>"""
SEARCH_ACTION = APP + "processo/buscaProcessosQualquerInstancia.do?actionType=pesquisar"
NOT_FOUND = "<html>Nenhum registro encontrado</html>"


class AppealAndDetailsTest(unittest.TestCase):
    def setUp(self):
        wired(self)

    def routes(self, first_result=SEARCH_RESULT, appeal_result=APPEAL_RESULT):
        tabs = {"tabPartes": PARTIES_PAGE, "tabDadosProcesso": DATA_TAB, "tabDadosAdicionais": ADDITIONAL_TAB}
        pages = {
            ("GET", APP + "usuario/postLogon.do?actionType=iniciar"): APPEAL_MENU,
            ("GET", APP + f"processo/buscaProcessosQualquerInstancia.do?_tj={TJ}"): SEARCH_PAGE,
            ("GET", APP + f"processo/buscaProcessosQualquerInstancia.do?_tj={TJ}2g"): APPEAL_SEARCH_PAGE,
            ("GET", APP + f"processo.do?_tj={TJ}cd"): DETAILED_PROCESS_PAGE,
            ("GET", APP + f"processo/recursal/recurso.do?_tj={TJ}rc"): APPEAL_PAGE,
            ("POST", APP + INTIMATIONS_URL.removeprefix("/projudi/")): MOVEMENT_INTIMATIONS,
            ("POST", APP + FILES_URL.removeprefix("/projudi/")): FILES_PAGE,
        }

        def routes(method, url, kwargs):
            data = dict(kwargs.get("data") or {})
            if (method, url) == ("POST", SEARCH_ACTION):
                return response(200, appeal_result if "numeroRecurso" in data else first_result)
            if (method, url) == ("POST", APP + "visualizacaoProcesso.do?actionType=visualizar&id=1"):
                return response(200, tabs[data["selectedIcon"]])
            if (method, url) in pages:
                return response(200, pages[(method, url)])
            raise AssertionError(f"unexpected {method} {url}")

        return routes

    def searches(self, session):
        return [dict(call[2]["data"]) for call in session.calls if call[1] == SEARCH_ACTION]

    def test_a_2nd_grau_number_opens_the_recurso_with_its_own_tables(self):
        session = FakeSession(self.routes())
        process = tjam.ADAPTER.open_process(session, KEY, APPEAL)
        self.assertEqual([s.get("numeroRecurso") for s in self.searches(session)], ["00000034520268040000"])
        self.assertEqual(process["instance"], "2")
        self.assertEqual(
            process["fields"],
            {
                "Órgão Julgador": "1ª Câmara Cível",
                "Nível de Sigilo": "Público",
                "Autuação": "01/10/2026",
                "Valor da Demanda": "R$ 1.000,00",
            },
        )
        self.assertEqual(
            process["parties"],
            [{"role": "Agravante", "name": "Parte Recorrente", "lawyers": "OAB 1N-AM - ADVOGADA EXEMPLO"}],
        )
        self.assertNotIn("000.000.000-00", str(process))
        self.assertEqual(process["sessoes"][0]["situacao"], "Julgamento Realizado")
        self.assertEqual((process["publicacoes"], process["intimacoes"]), ([], []))
        self.assertEqual([m["hasFiles"] for m in process["movements"]], [True])
        tjam.ADAPTER.process_details(session, KEY, process)
        self.assertEqual(process["movements"][0]["intimacoes"][0]["status"], "LIDA, PRAZO VENCIDO")
        self.assertFalse(any("visualizacaoProcesso" in call[1] for call in session.calls))  # every tab is on the page

    def test_a_number_one_search_does_not_find_is_looked_up_in_the_other(self):
        session = FakeSession(self.routes(first_result=NOT_FOUND))
        process = tjam.ADAPTER.open_process(session, KEY, APPEAL)
        self.assertEqual(process["instance"], "2")
        session = FakeSession(self.routes(appeal_result=NOT_FOUND))
        self.assertEqual(tjam.ADAPTER.open_process(session, KEY, NUMBER)["instance"], "1")
        self.assertEqual(len(self.searches(session)), 1)  # found at the 1º grau: the 2º grau is not searched
        session = FakeSession(self.routes(first_result=NOT_FOUND, appeal_result=NOT_FOUND))
        with self.assertRaisesRegex(ValueError, "not found"):
            tjam.ADAPTER.open_process(session, KEY, NUMBER)
        self.assertEqual(len(self.searches(session)), 2)

    def test_details_add_the_capa_tabs_and_each_movements_intimacoes_without_reading_one(self):
        session = FakeSession(self.routes())
        process = tjam.ADAPTER.open_process(session, KEY, NUMBER)
        self.assertEqual(process["instance"], "1")
        tjam.ADAPTER.process_details(session, KEY, process)
        self.assertEqual((process["fields"]["Valor da Causa"], process["fields"]["Situação"]), ("R$ 2.000,00", "Ativo"))
        self.assertNotIn("Data do Trânsito em Julgado", process["fields"])  # empty on the page
        self.assertEqual(process["additionalData"], {"Trânsito em Julgado": "Sem Trânsito Cadastrado"})
        self.assertEqual(
            process["movements"][0]["intimacoes"],
            [
                {
                    "destinatario": "PARTE AUTORA",
                    "prazo": "5 dias úteis",
                    "urgente": "Não",
                    "pessoal": "Não",
                    "online": "Sim",
                    "dataLeitura": "08/10/2026 23:59",
                    "dataCumprimento": "",
                    "dataDecurso": "17/10/2026",
                    "dataRenuncia": "",
                    "status": "LIDA, PRAZO VENCIDO",
                    "djen": "07/10/2026",
                    "leituraAutomatica": True,
                }
            ],
        )
        self.assertNotIn("intimacoes", process["movements"][1])
        self.assertNotIn("ADVOGADA EXEMPLO(", str(process))
        posted = [dict(call[2]["data"]) for call in session.calls if "movimentacao.do" in call[1]]
        self.assertEqual(posted, [{"tipoLayout": "1"}])
        self.assertFalse(any("intimacao.do" in call[1] or "arquivo.do" in call[1] for call in session.calls))
        quiet = FakeSession(self.routes())
        process = tjam.ADAPTER.open_process(quiet, KEY, NUMBER)
        tjam.ADAPTER.process_details(quiet, KEY, process, intimations=False)
        self.assertFalse(any("movimentacao.do" in call[1] for call in quiet.calls))


if __name__ == "__main__":
    unittest.main()
