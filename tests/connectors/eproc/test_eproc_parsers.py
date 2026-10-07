import unittest

from jurisync.connectors.eproc.parsers import (
    parse_event_response,
    parse_panel,
    parse_process_detail,
    parse_process_list,
    parse_process_list_page,
)

BASE = "https://eproc.jfsc.jus.br/eprocV2/"


class EprocParserTests(unittest.TestCase):
    def test_panel_discovers_allowlisted_routes(self):
        panel = """<form id="formPesquisaRapida" action="controlador.php?acao=processo_pesquisa_rapida&amp;hash=x" method="post">
          <input name="txtNumProcessoPesquisaRapida"><input name="acao_retorno_pesquisa_rapida" value="painel_adv_listar">
          <button id="btnPesquisaRapidaSubmit" name="btnPesquisaRapidaSubmit"></button></form>
          <a href="controlador.php?acao=citacao_intimacao_prazo_aberto_listar&amp;hash=a">Prazos</a>
          <a href="controlador.php?acao=citacao_intimacao_pendente_listar&amp;hash=b">Pendentes</a>
          <a data-urlassinadacarregamento="controlador_ajax.php?acao_ajax=retornar_citacao_intimacao_estado&amp;estado=SC&amp;hash=c"></a>"""
        parsed = parse_panel(panel, BASE)
        self.assertEqual(parsed["quickSearch"]["numberField"], "txtNumProcessoPesquisaRapida")
        self.assertEqual(parsed["quickSearch"]["submitField"], "btnPesquisaRapidaSubmit")
        self.assertIsNone(parsed["quickSearch"]["submitValue"])
        self.assertEqual(parsed["quickSearch"]["returnField"], "acao_retorno_pesquisa_rapida")
        self.assertEqual(parsed["quickSearch"]["returnValue"], "painel_adv_listar")
        self.assertIn("hash=a", parsed["queues"]["open_deadlines"])
        self.assertIn("hash=b", parsed["queues"]["pending"])
        self.assertIn("retornar_citacao_intimacao_estado", parsed["stateEndpoint"])

    def test_list_and_detail_enriched_fields(self):
        listing = """<table class="infraTable"><tr class="infraTrClara"><td></td><td><input class="infraCheckbox" value="7"><a href="controlador.php?acao=processo_selecionar&num_processo=123">0000001-23.2026.4.04.7207</a><b>Juízo: </b>SCUABI011</td><td>Classe</td><td>Assunto</td><td>Evento</td><td>01/01</td><td>02/01</td><td>03/01</td></tr></table>"""
        detail = """<table id="tblEventos"><tr id="trEvento9" class="infraEventoImportante"><td>9</td><td>01/01/2026</td><td><label class="infraEventoDescricao">Publicado</label></td><td><label class="infraEventoUsuario">sistema</label></td><td><a class="infraLinkDocumento" data-doc="8" data-nome="A" data-mimetype="pdf" href="controlador.php?acao=acessar_documento&doc=8">A1</a></td></tr></table>"""
        process = parse_process_list(listing, BASE)[0]
        parsed = parse_process_detail(detail, BASE)
        self.assertEqual((process["id"], process["juizo"], process["classeCodigo"]), ("7", "SCUABI011", None))
        self.assertEqual(
            (parsed["eventCount"], parsed["documentCount"], parsed["eventos"][0]["documentos"][0]["tipo"]),
            (1, 1, "pdf"),
        )

    def test_real_detail_enriches_cover_subjects_parties_and_document_query(self):
        html = """<fieldset id="fldCapa" data-classe="596"><span id="txtNumProcesso">5000001-23.2026.4.04.7207</span><span id="txtSituacao">MOVIMENTO</span></fieldset>
        <fieldset id="fldAssuntos"><table><tr><th>Código</th><th>Descrição</th><th>Principal</th></tr><tr><td>1</td><td>Assunto</td><td>Sim</td></tr></table></fieldset>
        <table id="tblPartesERepresentantes"><tr><th>AUTOR</th><th>RÉU</th></tr><tr><td><span data-parte="AUTOR">Alice</span><span id="spnCpfParteAutor0">123</span><span class="sr-only">Tipo de Usuário: ADVOGADO</span></td><td><span data-parte="REU">Bob</span></td></tr></table>
        <table id="tblEventos"><tr id="trEvento1"><td>1</td><td>01/01/2026</td><td><label class="infraEventoDescricao">Juntada</label></td><td><label class="infraEventoUsuario">user</label></td><td><a class="infraLinkDocumento" href="controlador.php?acao=acessar_documento&amp;doc=9&amp;evento=2&amp;key=k&amp;hash=h" aria-label="Visualizar documento A do tipo pdf" title="A"></a></td></tr></table>"""
        parsed = parse_process_detail(html, BASE)
        self.assertEqual(parsed["capa"]["Situacao"], "MOVIMENTO")
        self.assertEqual(parsed["assuntos"][0]["codigo"], "1")
        self.assertEqual(parsed["partes"][0]["representantes"], ["ADVOGADO"])
        self.assertEqual(parsed["documentos"][0]["id"], "9")

    def test_detail_discovers_event_filter_request_and_all_co_parties(self):
        html = """<script>url: 'controlador_ajax.php?acao_ajax=montar_tabela_eventos_filtrados&amp;num_processo=123&amp;hash=h'</script>
        <table id="tblOpcoesEventos"><tr><td><input type="checkbox" value="hdnDocs" checked></td><td><input type="checkbox" value="hdnExternal"></td></tr></table>
        <table id="tblPartesERepresentantes"><tr><th>AUTOR</th></tr><tr><td><span data-parte="AUTOR">Alice</span><span data-parte="AUTOR">Bob</span><span data-parte="AUTOR">Alice</span></td></tr></table>"""
        parsed = parse_process_detail(html, BASE)
        self.assertEqual(parsed["eventRequest"]["data"], {"hdnDocs": "true", "hdnExternal": "false"})
        self.assertIn("montar_tabela_eventos_filtrados", parsed["eventRequest"]["url"])
        self.assertEqual([part["nome"] for part in parsed["partes"]], ["Alice", "Bob"])

    def test_list_page_complete_only_when_hidden_total_is_covered(self):
        row = '<tr class="infraTrClara"><td></td><td><a href="controlador.php?acao=processo_selecionar">1</a></td><td>C</td><td>A</td><td>E</td><td>1</td><td>2</td><td>3</td></tr>'
        incomplete = parse_process_list_page(
            f'<form><input name="hdnInfraNroItens" value="2"><input name="hdnInfraPaginaAtual" value="0"></form><table class="infraTable">{row}</table>',
            BASE,
        )
        complete = parse_process_list_page(
            f'<form><input name="hdnInfraNroItens" value="1"><input name="hdnInfraPaginaAtual" value="0"></form><table class="infraTable">{row}</table>',
            BASE,
        )
        self.assertFalse(incomplete["pagination"]["complete"])
        self.assertTrue(complete["pagination"]["complete"])

    def test_event_response_split_reports_observed_pagination_only(self):
        row = '<table id="tblEventosNovos"><tr id="trEvento2" class="infraEventoImportante"><td>2</td><td>02/01</td><td><label class="infraEventoDescricao">Prazo</label></td><td><label class="infraEventoUsuario">u</label></td><td>sem documento</td></tr></table>'
        parsed = parse_event_response("ignored±" + row + "±ignored±controlador_ajax.php?pagina=1±3", BASE)
        self.assertEqual(parsed["eventos"][0]["id"], "2")
        self.assertEqual(parsed["pagination"]["totalPages"], 3)
        self.assertFalse(parsed["pagination"]["complete"])


if __name__ == "__main__":
    unittest.main()
