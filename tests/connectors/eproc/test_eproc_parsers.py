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
        self.assertEqual((process["id"], process["court"], process["caseClassCode"]), ("7", "SCUABI011", None))
        self.assertEqual(
            (parsed["eventCount"], parsed["documentCount"], parsed["events"][0]["documents"][0]["type"]),
            (1, 1, "pdf"),
        )

    def test_real_detail_enriches_cover_subjects_parties_and_document_query(self):
        html = """<fieldset id="fldCapa" data-classe="596"><span id="txtNumProcesso">5000001-23.2026.4.04.7207</span><span id="txtSituacao">MOVIMENTO</span></fieldset>
        <fieldset id="fldAssuntos"><table><tr><th>Código</th><th>Descrição</th><th>Principal</th></tr><tr><td>1</td><td>Assunto</td><td>Sim</td></tr></table></fieldset>
        <table id="tblPartesERepresentantes"><tr><th>AUTOR</th><th>RÉU</th></tr><tr><td><span data-parte="AUTOR">Alice</span><span id="spnCpfParteAutor0">123</span><span class="sr-only">Tipo de Usuário: ADVOGADO</span></td><td><span data-parte="REU">Bob</span></td></tr></table>
        <table id="tblEventos"><tr id="trEvento1"><td>1</td><td>01/01/2026</td><td><label class="infraEventoDescricao">Juntada</label></td><td><label class="infraEventoUsuario">user</label></td><td><a class="infraLinkDocumento" href="controlador.php?acao=acessar_documento&amp;doc=9&amp;evento=2&amp;key=k&amp;hash=h" aria-label="Visualizar documento A do tipo pdf" title="A"></a></td></tr></table>"""
        parsed = parse_process_detail(html, BASE)
        self.assertEqual(parsed["cover"]["status"], "MOVIMENTO")
        self.assertEqual(parsed["subjects"][0]["code"], "1")
        self.assertEqual(parsed["parties"][0]["representatives"], ["ADVOGADO"])
        self.assertEqual(parsed["documents"][0]["id"], "9")

    def test_cover_maps_court_specific_fields_and_keeps_unknown_ones_under_extra(self):
        html = """<fieldset id="fldCapa" data-classe="1"><span id="txtNumProcesso">5000001-23.2026.4.04.0000</span>
        <span id="txtOrgaoColegiado">TURMA FICTICIA</span><span id="txtNumRequis">000001</span>
        <span id="txtMagistradoAtuante">JUIZ FICTICIO</span><span id="txtCampoNovo">valor</span>
        <span id="txtLocalidade"></span></fieldset>"""
        self.assertEqual(
            parse_process_detail(html, BASE)["cover"],
            {
                "number": "5000001-23.2026.4.04.0000",
                "collegiateBody": "TURMA FICTICIA",
                "paymentRequestNumber": "000001",
                "actingJudge": "JUIZ FICTICIO",
                "OrgaoColegiado": "TURMA FICTICIA",
                "NumRequis": "000001",
                "MagistradoAtuante": "JUIZ FICTICIO",
                "caseClassCode": "1",
                "extra": {"CampoNovo": "valor"},
            },
        )

    def test_party_cpf_is_masked_and_cnpj_kept(self):
        html = """<table id="tblPartesERepresentantes"><tr><th>AUTOR</th><th>RÉU</th></tr><tr>
        <td><span data-parte="AUTOR">Parte Ficticia</span><span id="spnCpfParteAutor0">123.456.789-09</span></td>
        <td><span data-parte="REU">Empresa Ficticia</span><span id="spnCpfParteReu0">12.345.678/0001-90</span></td></tr>
        <tr><td><span data-parte="AUTOR">Outra Parte</span><span id="spnCpfParteAutor1">98765432100</span></td></tr></table>"""
        parties = parse_process_detail(html, BASE)["parties"]
        self.assertEqual([party["taxId"] for party in parties], ["123.***.***-09", "12.345.678/0001-90", "987******00"])

    def test_detail_discovers_event_filter_request_and_all_co_parties(self):
        html = """<script>url: 'controlador_ajax.php?acao_ajax=montar_tabela_eventos_filtrados&amp;num_processo=123&amp;hash=h'</script>
        <table id="tblOpcoesEventos"><tr><td><input type="checkbox" value="hdnDocs" checked></td><td><input type="checkbox" value="hdnExternal"></td></tr></table>
        <table id="tblPartesERepresentantes"><tr><th>AUTOR</th></tr><tr><td><span data-parte="AUTOR">Alice</span><span data-parte="AUTOR">Bob</span><span data-parte="AUTOR">Alice</span></td></tr></table>"""
        parsed = parse_process_detail(html, BASE)
        self.assertEqual(parsed["eventRequest"]["data"], {"hdnDocs": "true", "hdnExternal": "false"})
        self.assertIn("montar_tabela_eventos_filtrados", parsed["eventRequest"]["url"])
        self.assertEqual([part["name"] for part in parsed["parties"]], ["Alice", "Bob"])

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
        self.assertEqual(parsed["events"][0]["id"], "2")
        self.assertEqual(parsed["pagination"]["totalPages"], 3)
        self.assertFalse(parsed["pagination"]["complete"])

    def test_hearing_notice_queue_reads_session_columns(self):
        # Shape of TJSC 2G "Intimações de Pauta" (16/11/2026), names and numbers replaced.
        html = """<table class="infraTable"><caption>Lista de Intimações de Pauta (1 registro):</caption>
          <tr><th>Processo</th><th>Assunto</th><th>Evento</th><th>Data Sessão</th><th>Sequencial</th>
          <th>Composição prevista do Julgamento</th><th>Visualizado</th></tr>
          <tr class="infraTrClara"><td><div><a href="controlador.php?acao=processo_selecionar&amp;num_processo=1&amp;hash=h">0000001-23.2026.8.24.0001</a></div>
          <br/><b>Juízo: </b>GCIV0202<br/><a href="javascript:exibirSubFrm('controlador.php?acao=processo_lembrete_externo_cadastrar')">Cadastrar Lembrete</a>
          <br/><b>Colegiado: </b>2ª Câmara de Direito Civil<br/><b></b><br/>FULANO DE TAL<br/>(00000000000) - <b>APELANTE</b>
          <br/><b></b><br/>EMPRESA X LTDA<br/>(00000000000100) - <b>APELADO</b></td>
          <td>Assunto civil</td>
          <td>Intimação Eletrônica - Expedida/Certificada - Pauta<br/><b>Sessão Virtual</b><br/>(23/10/2026 18:56:23)</td>
          <td>12/11/2026 00:00:00<br/>a<br/>19/11/2026 23:59:00</td><td>129</td><td>DES. A (GCIV0204)</td>
          <td><button class="infraButton" onclick="confirma('1','2');">Visualizar</button></td></tr></table>"""
        parsed = parse_process_list_page(html, BASE)
        self.assertEqual(parsed["pagination"]["totalItems"], 1)
        self.assertTrue(parsed["pagination"]["complete"])
        item = parsed["items"][0]
        self.assertEqual(item["number"], "0000001-23.2026.8.24.0001")
        self.assertEqual((item["court"], item["collegiate"]), ("GCIV0202", "2ª Câmara de Direito Civil"))
        self.assertEqual(
            item["parties"],
            [{"role": "APELANTE", "name": "FULANO DE TAL"}, {"role": "APELADO", "name": "EMPRESA X LTDA"}],
        )
        self.assertEqual(item["subject"], "Assunto civil")
        self.assertEqual(item["deadlineEvent"], "Intimação Eletrônica - Expedida/Certificada - Pauta")
        self.assertEqual(item["requestedAt"], "23/10/2026 18:56:23")
        self.assertEqual((item["sessionStart"], item["sessionEnd"]), ("12/11/2026 00:00:00", "19/11/2026 23:59:00"))
        self.assertEqual(item["sessionSequence"], "129")
        self.assertEqual((item["deadlineStart"], item["deadlineEnd"]), ("", ""))

    def test_detail_title_skips_screen_reader_menu_heading(self):
        html = (
            '<h2 id="menu-title" class="sr-only">Menu lateral</h2><h1>Consulta Processual - Detalhes do Processo</h1>'
        )
        self.assertEqual(parse_process_detail(html, BASE)["title"], "Consulta Processual - Detalhes do Processo")

    def test_expired_queue_uses_seven_columns_and_caption_total(self):
        caption = "<caption>Lista de Citações e Intimações com Decurso de Prazo (1 registro):</caption>"
        html = f"""<input name="hdnInfraNroItens" value="0"><table class="infraTable">{caption}
          <tr><th>Processo</th><th>Classe</th><th>Assunto</th><th>Evento e Prazo</th>
          <th>Data envio requisição</th><th>Final Prazo</th><th>Conferido ?</th></tr>
          <tr class="infraTrClara"><td><a href="controlador.php?acao=processo_selecionar">0000001-23.2026.4.06.3800</a>
          Juízo: MGBHZCIV21T<span data-parte="AUTOR">Parte de teste</span></td><td>Classe</td><td>Assunto</td>
          <td>Intimação — 10 dias</td><td>21/09/2026 17:09:03</td><td>07/10/2026 23:59:59</td>
          <td><img alt="Conferir"></td></tr></table>"""
        result = parse_process_list_page(html, "https://eproc1g.trf6.jus.br/eproc/")
        self.assertEqual(len(result["items"]), 1)
        item = result["items"][0]
        self.assertEqual(
            (item["caseClass"], item["subject"], item["deadlineEvent"]), ("Classe", "Assunto", "Intimação — 10 dias")
        )
        self.assertEqual(
            (item["requestedAt"], item["deadlineStart"], item["deadlineEnd"]),
            ("21/09/2026 17:09:03", "", "07/10/2026 23:59:59"),
        )
        self.assertEqual(item["parties"], [{"role": "AUTOR", "name": "Parte de teste"}])
        self.assertEqual(result["pagination"]["totalItems"], 1)
        self.assertTrue(result["pagination"]["complete"])
        unknown = parse_process_list_page(html.replace(caption, ""), BASE)
        self.assertIsNone(unknown["pagination"]["totalItems"])
        self.assertFalse(unknown["pagination"]["complete"])


if __name__ == "__main__":
    unittest.main()
