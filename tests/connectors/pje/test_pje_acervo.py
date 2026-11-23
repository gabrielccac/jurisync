"""Offline checks of the Acervo fallback for a process search behind reCAPTCHA (TJRN 1G). No portal requests."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qsl

from jurisync import portals
from jurisync.connectors.pje import acervo, search

NUMBER = "0800001-11.2026.8.20.5135"
OTHER = "0800002-22.2026.8.20.5124"
CA = "ab" * 40
PANEL = "https://pje1g.tjrn.jus.br/pje/Painel/painel_usuario/advogado.seam"

SEARCH_PAGE = """<form id="fPP"><input name="javax.faces.ViewState" value="s0">
<script id="fPP:j_id77">executarPesquisaReCaptcha=function(){A4J.AJAX.Submit('fPP',null,{'similarityGroupingId':'fPP:j_id77','parameters':{'fPP:j_id77':'fPP:j_id77'}})};</script>
<input id="fPP:searchProcessos" value="Pesquisar" onclick="return executarReCaptcha();"></form>"""
CAPTCHA = """<input name="javax.faces.ViewState" value="s1"><dl class="rich-messages"><dt><span>A verifica&ccedil;&atilde;o de
captcha n&atilde;o est&aacute; correta.</span></dt></dl><table id="fPP:processosTable"></table>"""
PANEL_PAGE = '<form id="tabPanel:_form"><input name="javax.faces.ViewState" value="p0"></form>'


def node(key, label, count):
    control = f"formAbaAcervo:trAc:{key}::jNd"
    return (
        f"<a id=\"{control}\" onclick=\"A4J.AJAX.Submit('formAbaAcervo',event,{{'similarityGroupingId':'{control}',"
        f"'containerId':'j_id673','parameters':{{'{control}':'{control}','ajaxSingle':'{control}',"
        f"'selectedTab':'tabAcervo'}} ,'status':'_viewRoot:status'}} );return false;\">{label} "
        f'<span class="pull-right">{count}</span></a>'
    )


TAB = f'<input name="javax.faces.ViewState" value="p1"><form id="formAbaAcervo">{node(54, "Comarca A", 7)}{node(55, "Comarca B", 0)}{node(56, "Comarca C", 3)}</form>'
LISTING = """<input name="javax.faces.ViewState" value="p2"><form id="formAcervo">
<input name="formAcervo:numeroProcesso:numeroSequencial" type="text" value="">
<input name="formAcervo:numeroProcesso:numeroDigitoVerificador" type="text" value="">
<input name="formAcervo:numeroProcesso:Ano" type="text" value="">
<input name="formAcervo:numeroProcesso:ramoJustica" type="text" value="">
<input name="formAcervo:numeroProcesso:respectivoTribunal" type="text" value="">
<input name="formAcervo:numeroProcesso:NumeroOrgaoJustica" type="text" value="">
<input name="formAcervo:itCL" type="text" value="">
<select name="formAcervo:itPR"><option value="noSel" selected>Selecione</option><option value="27">X</option></select>
<input id="formAcervo:btPesqAc" name="formAcervo:btPesqAc" type="button" value="Pesquisar"
 onclick="A4J.AJAX.Submit('formAcervo',event,{'similarityGroupingId':'formAcervo:btPesqAc','containerId':'formAcervo:j_id1226','parameters':{'formAcervo:btPesqAc':'formAcervo:btPesqAc','selectedTab':'tabAcervo'} ,'status':'_viewRoot:status'} );return false;">
<input name="formAcervo" type="hidden" value="formAcervo"><input name="javax.faces.ViewState" type="hidden" value="p2">
</form>"""


def found(number, process_id):
    link = f"/pje/Processo/ConsultaProcesso/Detalhe/listProcessoCompletoAdvogado.seam?id={process_id}&amp;ca={CA}"
    return (
        f'<input name="javax.faces.ViewState" value="p3"><table id="formAcervo:tbProcessos"><tbody><tr>'
        f"<td><a title=\"Peticionar nos autos\" onclick=\"window.open('{link}&amp;aba=tbAnexar','blank_');\">P</a>"
        f"<a title=\"Autos Digitais\" onclick=\"window.open('{link}','blank_');\">{number}</a></td>"
        f"<td>ProceComCiv {number} PARTE A X PARTE B</td></tr></tbody></table>"
    )


EMPTY = '<input name="javax.faces.ViewState" value="p3"><table id="formAcervo:tbProcessos"><tbody></tbody></table>'


def response(body, url):
    return SimpleNamespace(
        status_code=200, url=url, headers={"Content-Type": "text/html"}, content=body.encode(), text=body
    )


class AcervoParsing(unittest.TestCase):
    def test_captcha_refusal_is_recognized(self):
        self.assertTrue(acervo.captcha_refused(CAPTCHA.replace("\n", " ")))
        self.assertFalse(acervo.captcha_refused('<table id="fPP:processosTable"></table>'))

    def test_nodes_carry_their_control_container_and_count(self):
        nodes = acervo.parse_nodes(TAB)
        self.assertEqual([n["control"] for n in nodes][0], "formAbaAcervo:trAc:54::jNd")
        self.assertEqual([(n["label"], n["count"], n["container"]) for n in nodes][1], ("Comarca B", 0, "j_id673"))

    def test_listing_fields_fill_the_cnj_number_and_keep_selects(self):
        fields = dict(acervo.listing_fields(LISTING, NUMBER))
        self.assertEqual(fields["formAcervo:numeroProcesso:numeroSequencial"], "0800001")
        self.assertEqual(fields["formAcervo:numeroProcesso:NumeroOrgaoJustica"], "5135")
        self.assertEqual(fields["formAcervo:itPR"], "noSel")
        self.assertNotIn("formAcervo:btPesqAc", fields)
        self.assertNotIn("javax.faces.ViewState", fields)
        self.assertEqual(acervo.search_container(LISTING), "formAcervo:j_id1226")

    def test_autos_link_takes_the_matching_row_only(self):
        self.assertEqual(acervo.autos_link(found(NUMBER, "4488028"), NUMBER), ("4488028", CA))
        self.assertIsNone(acervo.autos_link(found(OTHER, "9"), NUMBER))


class AcervoFallback(unittest.TestCase):
    def test_search_behind_recaptcha_opens_the_process_from_the_acervo(self):
        config = portals.pje("tjrn-1g")
        session = Mock()
        detail = f'<input name="javax.faces.ViewState" value="d"><div>{NUMBER}</div>'
        session.get.side_effect = [
            response(SEARCH_PAGE, config.search_url),
            response(PANEL_PAGE, PANEL),
            response(detail, config.url(search.DETAIL_PATH)),
        ]
        session.post.side_effect = [
            response(CAPTCHA, config.search_url),  # the search: reCAPTCHA refused
            response(TAB, PANEL),  # Acervo tab
            response(LISTING, PANEL),  # Comarca A (Comarca B has no process and is skipped)
            response(EMPTY, PANEL),  # not there
            response(LISTING, PANEL),  # Comarca C
            response(found(NUMBER, "4488028"), PANEL),
            response("<html></html>", config.url(search.DETAIL_PATH)),  # expedientes tab
        ]
        result = search.search_process(session, NUMBER, portal="tjrn-1g")
        self.assertTrue(result["success"])
        self.assertEqual(result["processo_id"], "4488028")
        self.assertEqual(result["search_results"]["via"], "acervo")
        detail_url = session.get.call_args_list[2].args[0]
        self.assertIn(f"listProcessoCompletoAdvogado.seam?id=4488028&ca={CA}", detail_url)
        posted = [dict(parse_qsl(call.kwargs["data"])) for call in session.post.call_args_list[1:6]]
        self.assertEqual(posted[0].get("tabAcervo"), "tabAcervo")
        self.assertEqual(posted[1].get("ajaxSingle"), "formAbaAcervo:trAc:54::jNd")
        self.assertEqual(posted[2].get("formAcervo:numeroProcesso:numeroSequencial"), "0800001")
        self.assertEqual(posted[3].get("ajaxSingle"), "formAbaAcervo:trAc:56::jNd")
        for body in posted:
            self.assertFalse(any("Gravar" in key or "Mover" in key or "NovaCaixa" in key for key in body))

    def test_process_missing_from_the_acervo_is_a_clear_failure(self):
        config = portals.pje("tjrn-1g")
        session = Mock()
        session.get.side_effect = [response(SEARCH_PAGE, config.search_url), response(PANEL_PAGE, PANEL)]
        session.post.side_effect = [
            response(CAPTCHA, config.search_url),
            response(TAB, PANEL),
            response(LISTING, PANEL),
            response(EMPTY, PANEL),
            response(LISTING, PANEL),
            response(EMPTY, PANEL),
        ]
        result = search.search_process(session, NUMBER, portal="tjrn-1g")
        self.assertFalse(result["success"])
        self.assertIn("CAPTCHA", result["error"])


# -- listing (sanitized from TJMG 1G: fake parties, numbers and ids) ----------------------------------------------

MG_PANEL = "https://pje.tjmg.jus.br/pje/Painel/painel_usuario/advogado.seam"
FIRST = "5000001-11.2026.8.13.0024"
SECOND = "5000002-22.2026.8.13.0024"
THIRD = "5000003-33.2026.8.13.0024"
FOURTH = "5000004-44.2026.8.13.0702"


def box(node_key, box_key, label, count):
    control = f"formAbaAcervo:trAc:{node_key}:{box_key}::cxItem"
    escaped = control.replace("-", "\\x2D")
    return (
        f'<table class="rich-tree-node"><tr><td class="rich-tree-node-text"><div class="col-md-10">'
        f"<a id=\"{control}\" onclick=\"A4J.AJAX.Submit('formAbaAcervo',event,{{'similarityGroupingId':'{escaped}',"
        f"'containerId':'j_id649','parameters':{{'{escaped}':'{escaped}','ajaxSingle':'{escaped}',"
        f"'selectedTab':'tabAcervo'}} ,'status':'_viewRoot:status'}} );return false;\">"
        f'<i class="fa fa-home" title="Caixa"></i> <span class="nomeTarefa">{label}</span></a></div>'
        f'<div class="col-md-2 itemContador"><span title="Número de itens {count}">{count}</span></div></td></tr></table>'
    )


def row(process_id, number, *, moved=True, priority=False):
    link = f"/pje/Processo/ConsultaProcesso/Detalhe/listProcessoCompletoAdvogado.seam?id={process_id}&amp;ca={CA}"
    prefix = f"formAcervo:tbProcessos:{process_id}"
    return (
        f'<tr class="rich-table-row"><td id="{prefix}:j_id1218"></td>'
        f'<td id="{prefix}:j_id1237"><a title="Peticionar nos autos" onclick="window.open(\'{link}&amp;aba=tbAnexar\',\'blank_\');">Peticionar</a></td>'
        f'<td class="rich-table-cell informacoesProcesso" id="{prefix}:j_id1240"><div class="vcenter"><div class="col-md-4">'
        f'<a class="numero-processo-acervo" title="Autos Digitais" onclick="window.open(\'{link}\',\'blank_\');">'
        f'<span class="text-bold">PJEC {number}</span></a> <div class="pje-clipboard"><i class="fa fa-clipboard"></i></div>'
        f'Indenização por Dano Moral</div><div class="col-md-8 informacoes-linha-acervo">'
        f'<div><span class="nome-parte">FULANA DE TESTE X EMPRESA EXEMPLO S/A</span></div>'
        f"<div>/1ª Unidade Jurisdicional Cível - 1º JD da Comarca de Teste"
        f"{'<span class="pull-right" title="Prioritário"><i></i></span>' if priority else ''}</div>"
        f"<div>Distribuído em 02/02/2026</div>"
        f"{'<div>Último movimento: 09/07/2026 20:10 - Arquivado Definitivamente</div>' if moved else ''}"
        f"</div></div></td></tr>"
    )


def scroller(page=None):
    pager = f'<table><tr><td class="rich-datascr-act">{page}</td></tr></table>' if page else ""
    return (
        '<div class="rich-datascr" id="formAcervo:tbProcessos:scPendentes">'
        "<script>new Richfaces.Datascroller('formAcervo:tbProcessos:scPendentes', function(event){"
        "A4J.AJAX.Submit('formAcervo',event,{'similarityGroupingId':'formAcervo:tbProcessos:scPendentes',"
        "'containerId':'j_id649','parameters':{'formAcervo:tbProcessos:scPendentes':event.memo.page,"
        "'ajaxSingle':'formAcervo:tbProcessos:scPendentes','selectedTab':'tabAcervo'} } ); return false;});</script>"
        f"{pager}</div>"
    )


def listing(rows, total, page=None):
    return (
        f'<input name="javax.faces.ViewState" value="b{total}"><form id="formAcervo">'
        '<input name="formAcervo:itCL" type="text" value=""><input name="formAcervo" type="hidden" value="formAcervo">'
        '<table id="formAcervo:tbProcessos"><tfoot><tr><td>'
        f'{scroller(page)}<span class="text-muted">{total} resultados encontrados</span></td></tr></tfoot>'
        '<tbody id="formAcervo:tbProcessos:tb">'
        f"{''.join(rows)}</tbody></table>"
        '<input name="formAcervo:tbProcessos:j_id1220:selecionarTodos" type="checkbox">'
        '<input name="formAcervo:tbProcessos:j_id1220" type="hidden" value="formAcervo:tbProcessos:j_id1220"></form>'
    )


MG_TAB = (
    '<input name="javax.faces.ViewState" value="p1"><form id="formAbaAcervo">'
    f"{node(1024, 'Comarca Teste - Juizado Especial', 3)}{node(1025, 'Comarca Vazia', 0)}"
    f"{node(1026, 'Comarca Sem Caixas', 1)}</form>"
)
MG_NODE = (
    '<input name="javax.faces.ViewState" value="p2"><form id="formAbaAcervo">'
    f"{box(1024, -1, 'Caixa de entrada', 0)}{box(1024, 175802, 'Arquivados', 3)}{box(999, 1, 'Outra', 4)}</form>"
)


class AcervoListing(unittest.TestCase):
    def test_boxes_of_the_opened_jurisdiction_only(self):
        boxes = acervo.parse_boxes(MG_NODE, {"control": "formAbaAcervo:trAc:1024::jNd"})
        self.assertEqual([(b["label"], b["count"]) for b in boxes], [("Caixa de entrada", 0), ("Arquivados", 3)])
        self.assertEqual(boxes[0]["control"], "formAbaAcervo:trAc:1024:-1::cxItem")
        self.assertEqual(boxes[0]["container"], "j_id649")

    def test_row_reads_what_the_listing_shows(self):
        page = acervo.parse_listing(
            listing([row("700000001", FIRST, priority=True), row("700000002", SECOND, moved=False)], 2)
        )
        first, second = page["rows"]
        self.assertEqual(
            first,
            {
                "processNumber": FIRST,
                "processId": "700000001",
                "classe": "PJEC",
                "assunto": "Indenização por Dano Moral",
                "partes": "FULANA DE TESTE X EMPRESA EXEMPLO S/A",
                "orgaoJulgador": "1ª Unidade Jurisdicional Cível - 1º JD da Comarca de Teste",
                "distribuidoEm": "2026-02-02",
                "ultimoMovimento": {"em": "2026-07-09", "descricao": "Arquivado Definitivamente"},
                "prioritario": True,
            },
        )
        self.assertIsNone(second["ultimoMovimento"])
        self.assertFalse(second["prioritario"])
        self.assertNotIn(CA, str(page))
        self.assertEqual((page["total"], page["scroller"]["control"]), (2, "formAcervo:tbProcessos:scPendentes"))

    def test_listing_fields_leave_out_the_row_selection_form(self):
        fields = dict(acervo.listing_fields(listing([], 0)))
        self.assertEqual(fields, {"formAcervo:itCL": "", "formAcervo": "formAcervo"})

    def mg_session(self, pages):
        session = Mock()
        session.get.side_effect = [response(PANEL_PAGE, MG_PANEL)]
        session.post.side_effect = [
            response(MG_TAB, MG_PANEL),
            response(MG_NODE, MG_PANEL),  # Comarca Teste: two boxes, Caixa de entrada empty and not opened
            *[response(page, MG_PANEL) for page in pages],
            # Comarca Sem Caixas (TJRN-like): only the search form; its Pesquisar lists everything
            response(LISTING, MG_PANEL),
            response(listing([row("700000004", FOURTH)], 1), MG_PANEL),
        ]
        return session

    def test_every_box_and_page_is_read_and_nothing_is_opened(self):
        session = self.mg_session(
            [
                listing([row("700000001", FIRST), row("700000002", SECOND)], 3, page=1),
                listing([row("700000003", THIRD)], 3, page=2),
            ]
        )
        result = acervo.list_processes(session, "tjmg-pje-1g", max_pages=5)
        self.assertTrue(result["complete"])
        self.assertEqual((result["total"], result["returned"]), (4, 4))
        self.assertEqual([i["processNumber"] for i in result["items"]], [FIRST, SECOND, THIRD, FOURTH])
        self.assertEqual(result["items"][0]["caixa"], "Arquivados")
        self.assertEqual(result["items"][0]["jurisdicao"], "Comarca Teste - Juizado Especial")
        self.assertEqual(
            [j["label"] for j in result["jurisdictions"]], ["Comarca Teste - Juizado Especial", "Comarca Sem Caixas"]
        )
        posted = [dict(parse_qsl(call.kwargs["data"])) for call in session.post.call_args_list]
        self.assertEqual(posted[2]["ajaxSingle"], "formAbaAcervo:trAc:1024:175802::cxItem")
        self.assertEqual(posted[3]["formAcervo:tbProcessos:scPendentes"], "2")
        self.assertEqual(posted[3]["AJAXREQUEST"], "j_id649")
        self.assertEqual(posted[5].get("formAcervo:btPesqAc"), "formAcervo:btPesqAc")
        for body in posted:
            self.assertFalse(any("Peticionar" in v or "listProcessoCompleto" in v for v in body.values()))
            self.assertFalse(any("Caixa" in key or "Mover" in key or "j_id1268" in key for key in body))
        self.assertEqual(session.get.call_count, 1)  # the panel only: no process detail page

    def test_pages_beyond_max_pages_leave_the_listing_incomplete(self):
        session = self.mg_session([listing([row("700000001", FIRST), row("700000002", SECOND)], 3, page=1)])
        result = acervo.list_processes(session, "tjmg-pje-1g", max_pages=1)
        self.assertFalse(result["complete"])
        self.assertFalse(result["jurisdictions"][0]["complete"])
        self.assertEqual(result["jurisdictions"][0]["boxes"][1]["returned"], 2)

    def test_regions_keep_matching_jurisdictions(self):
        session = Mock()
        session.get.side_effect = [response(PANEL_PAGE, MG_PANEL)]
        session.post.side_effect = [
            response(MG_TAB, MG_PANEL),
            response(LISTING, MG_PANEL),
            response(listing([row("700000004", FOURTH)], 1), MG_PANEL),
        ]
        result = acervo.list_processes(session, "tjmg-pje-1g", regions=["sem caixas"])
        self.assertEqual(result["total"], 1)
        self.assertEqual([i["processNumber"] for i in result["items"]], [FOURTH])

    def test_pje1x_has_no_acervo_listing(self):
        with self.assertRaisesRegex(ValueError, "PJe 1.x"):
            acervo.read_acervo("trf5-legacy", session=Mock())


class TencentGuard(unittest.TestCase):
    def test_tencent_search_page_is_guarded(self):
        page = '<input id="fPP:searchProcessos" onclick="return executarTencentCaptcha();;A4J.AJAX.Submit()">'
        self.assertTrue(acervo.captcha_guarded(page))


if __name__ == "__main__":
    unittest.main()
