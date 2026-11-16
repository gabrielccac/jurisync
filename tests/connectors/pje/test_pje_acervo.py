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
        self.assertIn("reCAPTCHA", result["error"])


if __name__ == "__main__":
    unittest.main()
