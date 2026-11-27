from unittest.mock import Mock, patch

import pytest

from jurisync.connectors.pje import audiencias

# The trf5-1g Audiência tab of 27/11/2026, with invented names.
TAB_TRF5 = """<table class="rich-table" id="processoConsultaAudienciaGridList"><thead><tr><th>Unidade</th><th>Sala</th>
<th>Tipo de Audi&ecirc;ncia</th><th>Data</th><th>Status da Audi&ecirc;ncia</th><th>Link da sala virtual</th></tr></thead>
<tbody><tr class="rich-table-row"><td><span><div class="col-sm-12">26ª Vara Federal CE</div></span></td>
<td><span><div class="col-sm-12">Fórum social - sala 4 - 26ª Vara</div></span></td>
<td><span><div class="col-sm-12">Instrução</div></span></td><td><span>22/01/26 15:00<br/> <b></b></span></td>
<td><span><div><a data-target="#modal1" data-toggle="modal" href="#" title="Audiência realizada">realizada <i></i></a>
<div class="modal fade" id="modal1"><div class="modal-body"><p><b>Realizador:</b> JUIZ TESTE</p>
<p><b>Houve acordo:</b> Não</p></div></div></div></span></td>
<td><span><a href="https://meet.example.jus.br/sala-1">Entrar</a></span></td></tr></tbody></table>
<span class="pull-right text-muted">1 resultados encontrados</span>"""
# The tjmg-pje-1g tab: four columns, "Data prevista" first.
TAB_TJMG = """<table class="rich-table" id="processoConsultaAudienciaGridList"><thead><tr><th>Data prevista</th>
<th>Tipo de Audi&ecirc;ncia</th><th>Sala</th><th>Status da Audi&ecirc;ncia</th></tr></thead><tbody>
<tr class="rich-table-row"><td><span><center>15/12/26 14:30<br/> <b></b></center></span></td>
<td><span><div class="col-sm-12">do art. 334 CPC (12624)</div></span></td><td><span><div>CEJUSC VIRTUAL</div></span></td>
<td><span>designada</span></td></tr></tbody></table><span class="pull-right text-muted">1 resultados encontrados</span>"""


def test_process_tab_reads_each_hearing_without_who_held_it():
    items, complete = audiencias.parse_tab(TAB_TRF5)
    assert complete is True
    assert items == [
        {
            "data": "2026-01-22",
            "hora": "15:00",
            "tipo": "Instrução",
            "orgaoJulgador": "26ª Vara Federal CE",
            "sala": "Fórum social - sala 4 - 26ª Vara",
            "situacao": "realizada",
            "acordo": False,
            "linkSalaVirtual": "https://meet.example.jus.br/sala-1",
        }
    ]
    assert "JUIZ" not in str(items)


def test_tjmg_tab_has_data_prevista_and_fewer_columns():
    items, complete = audiencias.parse_tab(TAB_TJMG)
    assert complete and items[0]["data"] == "2026-12-15" and items[0]["hora"] == "14:30"
    assert (items[0]["tipo"], items[0]["sala"], items[0]["situacao"]) == (
        "do art. 334 CPC (12624)",
        "CEJUSC VIRTUAL",
        "designada",
    )
    assert items[0]["orgaoJulgador"] is None and items[0]["linkSalaVirtual"] is None


def test_tab_missing_or_short_is_incomplete():
    assert audiencias.parse_tab("<html>Erro</html>") == ([], False)
    assert audiencias.parse_tab("<span>0 resultados encontrados</span>") == ([], True)
    assert audiencias.parse_tab(TAB_TJMG.replace("1 resultados", "2 resultados"))[1] is False


def test_fetch_tab_posts_the_tab_control_over_the_navbar_form():
    detail = '<form id="navbar"><input type="hidden" name="javax.faces.ViewState" value="j_id4"/></form>'
    assert not audiencias.has_tab(detail) and audiencias.has_tab(detail + audiencias.TAB_CONTROL)
    answer = Mock(status_code=200, content=TAB_TJMG.encode(), headers={})
    with patch.object(audiencias.jsf, "post", return_value=answer) as post:
        items, complete = audiencias.fetch_tab(Mock(), detail, "https://pje.tjmg.jus.br/pje/x")
    body = post.call_args.args[2]
    assert body[audiencias.TAB_CONTROL] == audiencias.TAB_CONTROL and body["javax.faces.ViewState"] == "j_id4"
    assert len(items) == 1 and complete


# The pauta page as trf5-1g renders it (27/11/2026), cut to the search form, one row and the pager.
SEARCH_FORM = """<form id="processoAudienciaSearchForm" action="/pje/ProcessoAudiencia/PautaAudiencia/listView.seam">
<input type="checkbox" name="processoAudienciaSearchForm:listaSituacoes" value="M"/>
<input type="text" name="processoAudienciaSearchForm:dtInicioDecoration:dtInicioFromFormInputDate" value="27/11/2026"/>
<input type="hidden" name="processoAudienciaSearchForm:dtInicioDecoration:dtInicioFromFormInputCurrentDate" value="11/2026"/>
<input type="text" name="processoAudienciaSearchForm:dtInicioDecoration:dtInicioToFormInputDate" value="27/11/2026"/>
<input type="button" id="processoAudienciaSearchForm:searchButton" name="processoAudienciaSearchForm:searchButton" value="Pesquisar"
onclick="showLoading(); ;A4J.AJAX.Submit('processoAudienciaSearchForm',event,{'similarityGroupingId':'processoAudienciaSearchForm:searchButton','eventsQueue':'','containerId':'processoAudienciaSearchForm:j_id166','parameters':{'processoAudienciaSearchForm:searchButton':'processoAudienciaSearchForm:searchButton','processoAudienciaSearchForm:j_id349':'1'} } );return false;"/>
<input type="hidden" name="processoAudienciaSearchForm" value="processoAudienciaSearchForm"/>
<input type="hidden" name="javax.faces.ViewState" value="j_id2"/></form>"""
PAUTA_ROW = """<tr class="rich-table-row"><td class="table-toolbar"><a href="/pje/Processo/ConsultaProcesso/Detalhe/listProcessoCompleto.seam?id={pid}&amp;ca=SECRET" title="Detalhe do processo"></a></td>
<td><div><span>{when}</span></div></td><td><div><span>{number}</span></div></td><td><div><span>7ª Vara Federal AL</span></div></td>
<td><div><span>AUTORA TESTE<br/> X<br/> INSTITUTO NACIONAL DO SEGURO SOCIAL - INSS</span></div></td>
<td><div><span>PROCEDIMENTO DO JUIZADO ESPECIAL CÍVEL (436)</span></div></td><td><div><span>Instrução</span></div></td>
<td><div><span>Subseção 7ª Vara Federal - TITULAR (Manhã)</span></div></td><td><div><span>Designada</span></div></td></tr>"""
PAUTA_TABLE = """<table class="rich-table" id="idProcessoAudiencia"><thead><tr><th><a href="/pje/ProcessoAudiencia/PautaAudiencia/report.seam" title="Gerar PDF"></a></th>
<th>Data/Hora</th><th>Processo</th><th>Órgão Julgador</th><th>Partes</th><th>Classe Judicial</th><th>Tipo</th><th>Sala</th><th>Situação</th></tr></thead>
<tbody>{rows}</tbody></table><span class="pull-right text-muted">{total} resultados encontrados</span>"""
PAGER = """<div id="j_id513"><form id="j_id514" name="j_id514" method="post" action="javascript:A4J.AJAX.SubmitForm('j_id514')">
<input class="rich-inslider-field-right rich-inslider-field" id="j_id514:j_id515Input" name="j_id514:j_id515" size="3" type="text" value="1" />
<script type="text/javascript">//<![CDATA[
new Richfaces.Slider("j_id514:j_id515",{'minValue':'1','maxValue':'2','sliderValue':'1','width':'250px','onchange':'A4J.AJAX.Submit(\\'j_id514\\',event,{\\'similarityGroupingId\\':\\'j_id514:j_id516\\',\\'actionUrl\\':\\'/pje/ProcessoAudiencia/PautaAudiencia/listView.seam\\',\\'eventsQueue\\':\\'\\',\\'containerId\\':\\'j_id351\\',\\'parameters\\':{\\'j_id514:j_id516\\':\\'j_id514:j_id516\\'} ,\\'status\\':\\'_viewRoot:status\\'} )','showArrows':true} )
//]]>
</script><input type="hidden" name="j_id514" value="j_id514" /><input type="hidden" name="autoScroll" value="" />
<input type="hidden" name="javax.faces.ViewState" id="javax.faces.ViewState" value="j_id3" /></form></div>"""
NUMBER_A = "0000001-00.2026.4.05.8002"
NUMBER_B = "0000002-00.2026.4.05.8002"


def _pauta(rows, total, pager=PAGER):
    return PAUTA_TABLE.format(rows="".join(rows), total=total) + pager


def test_parse_pauta_keeps_the_process_id_and_drops_the_access_key():
    items, total = audiencias.parse_pauta(
        _pauta([PAUTA_ROW.format(pid="31", when="02/09/2026 10:30", number=NUMBER_A)], 1)
    )
    assert total == 1
    assert items == [
        {
            "data": "2026-09-02",
            "hora": "10:30",
            "processNumber": NUMBER_A,
            "processId": "31",
            "classe": "PROCEDIMENTO DO JUIZADO ESPECIAL CÍVEL (436)",
            "partes": "AUTORA TESTE X INSTITUTO NACIONAL DO SEGURO SOCIAL - INSS",
            "orgaoJulgador": "7ª Vara Federal AL",
            "tipo": "Instrução",
            "sala": "Subseção 7ª Vara Federal - TITULAR (Manhã)",
            "situacao": "Designada",
        }
    ]
    assert "SECRET" not in str(items)


def test_search_request_fills_the_start_date_interval_with_the_button_parameters():
    body = audiencias.search_request(SEARCH_FORM, "2026-11-27", "2027-02-25")
    assert body["AJAXREQUEST"] == "processoAudienciaSearchForm:j_id166"
    assert body["processoAudienciaSearchForm:dtInicioDecoration:dtInicioFromFormInputDate"] == "27/11/2026"
    assert body["processoAudienciaSearchForm:dtInicioDecoration:dtInicioToFormInputDate"] == "25/02/2027"
    assert body[audiencias.PAUTA_BUTTON] == audiencias.PAUTA_BUTTON and body["javax.faces.ViewState"] == "j_id2"
    assert "processoAudienciaSearchForm:listaSituacoes" not in body  # unchecked: every situation


def test_requests_that_name_a_download_are_refused():
    download = SEARCH_FORM.replace("'processoAudienciaSearchForm:j_id349':'1'", "'baixarDocumentos':'1'")
    with pytest.raises(RuntimeError, match="refused"):
        audiencias.search_request(download, "2026-11-27", "2027-02-25")
    with pytest.raises(ValueError):
        audiencias.search_request(SEARCH_FORM, "27/11/2026", "2027-02-25")


def test_page_request_turns_the_slider():
    html = _pauta([], 16)
    assert audiencias.page_count(html) == 2
    body = audiencias.page_request(html, 2)
    assert body["AJAXREQUEST"] == "j_id351" and body["j_id514:j_id515"] == "2"
    assert body["j_id514:j_id516"] == "j_id514:j_id516" and body["javax.faces.ViewState"] == "j_id3"
    assert audiencias.page_request(html, 3) is None and audiencias.page_request(_pauta([], 1, pager=""), 2) is None


def test_read_pauta_gets_searches_and_reads_every_page():
    first = _pauta([PAUTA_ROW.format(pid="32", when="03/12/2026 09:00", number=NUMBER_B)], 2)
    second = _pauta([PAUTA_ROW.format(pid="31", when="01/12/2026 10:30", number=NUMBER_A)], 2)
    answers = [Mock(status_code=200, content=html.encode(), headers={}) for html in (first, second)]
    with (
        patch.object(
            audiencias.jsf, "get", return_value=Mock(status_code=200, content=SEARCH_FORM.encode(), headers={})
        ),
        patch.object(audiencias.jsf, "post", side_effect=answers) as post,
    ):
        result = audiencias.read_pauta(Mock(), "trf5-1g", "2026-11-27", "2027-02-25")
    assert post.call_count == 2
    assert (result["total"], result["returned"], result["pages"], result["complete"]) == (2, 2, 2, True)
    assert [item["processNumber"] for item in result["items"]] == [NUMBER_A, NUMBER_B]  # by date


def test_read_pauta_refuses_a_reversed_interval_and_panels_without_it():
    with pytest.raises(ValueError):
        audiencias.read_pauta(Mock(), "trf5-1g", "2027-01-01", "2026-01-01")
    with pytest.raises(ValueError, match="no Pauta"):
        audiencias.read_pauta(Mock(), "trt7-1g", "2026-01-01", "2026-02-01")
