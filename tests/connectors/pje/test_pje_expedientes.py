from datetime import date
from pathlib import Path

import pytest

from jurisync.connectors.pje import expedientes

ROW = """<tr><td id="r{n}a"><span><div>{tipo} ({exp}) - Prioridade: Normal - ID do documento
<a href="/pje/x">({doc}) <i></i></a></div><h6>{parte}</h6>{extra}<div>{meio} ({expedido})</div>
<div>{quem} registrou ciência em {ciencia}</div>Prazo: {prazo}<script>function a(){{ alert(1) }}</script></span></td>
<td><div class="text-center"><h6>{limite}</h6><h6></h6><h6><span>(para manifestação)</span></h6></div></td>
<td>Visualizar ato {resposta}</td><td>SIM</td></tr>"""


def page(*rows):
    return f'<html><table><tbody id="{expedientes.TABLE_ID}">{"".join(rows)}</tbody></table></html>'


def row(**overrides):
    values = {
        "n": 1, "tipo": "Intimação", "exp": "100", "doc": "900", "parte": "AUTORA TESTE", "extra": "",
        "meio": "Diário Eletrônico", "expedido": "16/09/2026 19:56:06", "quem": "O sistema",
        "ciencia": "18/09/2026 00:00:00", "prazo": "15 dias", "limite": "09/10/2026 23:59:59", "resposta": "",
    }  # fmt: skip
    return ROW.format(**{**values, **overrides})


def test_parse_row_with_deadline():
    [item] = expedientes.parse_expedientes(page(row()))
    assert item == {
        "expedienteId": "100",
        "tipo": "Intimação",
        "documentId": "900",
        "parte": "AUTORA TESTE",
        "meio": "Diário Eletrônico",
        "expedidoEm": "2026-09-16",
        "ciencia": {"por": "O sistema", "em": "2026-09-18"},
        "prazoDias": 15,
        "semPrazo": False,
        "limite": "2026-10-09",
        "limiteTipo": "manifestação",
        "respondido": False,
        "fechado": True,
        "atoPath": None,
        "respostaPath": None,
    }


def test_sem_prazo_and_response_flag():
    items = expedientes.parse_expedientes(
        page(row(prazo="0 sem prazo", limite="", exp="101"), row(exp="102", resposta="Resposta"))
    )
    assert items[0]["semPrazo"] is True and items[0]["prazoDias"] is None and items[0]["limite"] is None
    assert items[1]["respondido"] is True


def test_open_deadlines_excludes_past_answered_and_no_deadline():
    items = expedientes.parse_expedientes(
        page(
            row(exp="1", limite="09/10/2026 23:59:59"),
            row(exp="2", limite="08/10/2026 23:59:59"),
            row(exp="3", limite="20/10/2026 23:59:59", resposta="Resposta"),
            row(exp="4", prazo="0 sem prazo", limite=""),
        )
    )
    assert [i["expedienteId"] for i in expedientes.open_deadlines(items, date(2026, 10, 9))] == ["1"]


def test_missing_table_is_empty_list():
    assert expedientes.parse_expedientes("<html></html>") == []


def test_tab_request_matches_browser_shape():
    html = """<form id="navbar"><input name="navbar:cbCronologia" value="DESC"/>
    <input name="navbar:cbExpediente" value="false"/><input type="hidden" name="javax.faces.ViewState" value="j_id4"/></form>"""
    body = expedientes.tab_request(html)
    assert body["AJAXREQUEST"] == "_viewRoot"
    assert body[expedientes.TAB_CONTROL] == expedientes.TAB_CONTROL
    assert body["navbar"] == "navbar" and body["navbar:cbCronologia"] == "DESC"
    assert body["javax.faces.ViewState"] == "j_id4" and body["AJAX:EVENTS_COUNT"] == "1"


ATO_PAGE = """<html><body><script>var x=1;</script>Ícone de expedientes Intimação (28230491) Destinatário AUTORA TESTE
Prazo 1 dias Data limite para manifestação 28/04/2026 23:59:59 Fechado? SIM Ícone de anexos Documentos Anexos
157781029 - Intimação (Designar perícia social) - 1° Grau Juntado por SERVIDORA em 23/04/2026 16:41:41
ATO ORDINATÓRIO determinada a produção de PERÍCIA SOCIAL.</body></html>"""


def test_parse_ato_extracts_box_attachments_and_text():
    ato = expedientes.parse_ato(ATO_PAGE, text_limit=400)
    assert ato["expediente"] == {
        "expedienteId": "28230491",
        "destinatario": "AUTORA TESTE",
        "prazo": "1 dias",
        "limite": "2026-04-28",
        "fechado": True,
    }
    assert ato["anexos"] == [
        {
            "documentId": "157781029",
            "titulo": "Intimação (Designar perícia social) - 1° Grau",
            "juntadoPor": "SERVIDORA",
            "juntadoEm": "2026-04-23",
        }
    ]
    assert "PERÍCIA SOCIAL" in ato["texto"] and "var x" not in ato["texto"]


def test_ato_of_a_pending_communication_opens_only_with_permission():
    from unittest.mock import Mock, patch

    item = {
        "atoPath": "/pje/Painel/painel_usuario/popup/visualizarExpediente.seam?idProcessoParteExpediente=1&x=2",
        "ciencia": None,
        "expedienteId": "1",
    }
    session = Mock()
    session.get.return_value = Mock(status_code=200, content=b"<html>Ato</html>", headers={})
    with patch.object(expedientes.jsf, "get", return_value=session.get.return_value) as get:
        with pytest.raises(expedientes.PendingNotice):
            expedientes.fetch_ato(session, item, "https://pje1g.trf5.jus.br/pje/x")
        get.assert_not_called()
        ato = expedientes.fetch_ato(session, item, "https://pje1g.trf5.jus.br/pje/x", allow_pending=True)
        get.assert_called_once()
    assert "ciencia" not in ato
    given = expedientes.ato_ciencia(item, opened=True)
    assert given["estado"] == "dada" and given["intimacoes"][0]["expedienteId"] == "1"
    assert expedientes.ato_ciencia(item, opened=False)["estado"] == "pendente"
    assert expedientes.ato_ciencia({**item, "ciencia": {"em": "2026-10-06"}}, opened=True) == {"estado": "nenhuma"}
    with pytest.raises(ValueError, match="not in this process"):
        expedientes.ato_item([], "9")


def test_tab_is_complete_only_when_every_row_was_parsed_and_there_is_one_page():
    html = page(row(exp="1"), row(exp="2"))
    assert expedientes.tab_complete(html, expedientes.parse_expedientes(html))
    empty = page()
    assert expedientes.tab_complete(empty, expedientes.parse_expedientes(empty))
    # No table at all: an empty process and a tab that did not load look the same, so it is not vouched for.
    assert not expedientes.tab_complete("<html></html>", [])
    unparsed = page(row(exp="1"), "<tr><td>Outro formato de linha</td><td>x</td></tr>")
    assert not expedientes.tab_complete(unparsed, expedientes.parse_expedientes(unparsed))
    scroller = '<table class="rich-datascr"><tr><td class="rich-datascr-act">1</td><td class="rich-datascr-inact">2</td></tr></table>'
    paged = page(row(exp="1")).replace("</html>", scroller + "</html>")
    assert not expedientes.tab_complete(paged, expedientes.parse_expedientes(paged))
    single = page(row(exp="1")).replace(
        "</html>", scroller.replace('<td class="rich-datascr-inact">2</td>', "") + "</html>"
    )
    assert expedientes.tab_complete(single, expedientes.parse_expedientes(single))


def test_fetch_reports_a_paged_tab_as_incomplete():
    from unittest.mock import Mock, patch

    body = page(row(exp="1")).replace("</html>", '<td class="rich-datascr-inact">2</td></html>')
    detail = '<form id="navbar"><input type="hidden" name="javax.faces.ViewState" value="j_id4"/></form>'
    answer = Mock(status_code=200, content=body.encode(), headers={})
    with patch.object(expedientes.jsf, "post", return_value=answer):
        items, complete = expedientes.fetch_expedientes(Mock(), detail, "https://pje1g.trf5.jus.br/pje/x")
    assert [item["expedienteId"] for item in items] == ["1"] and complete is False


def test_pending_notice_covers_unlinked_communications_and_documents_of_the_same_entry():
    items = [
        {"expedienteId": "1", "documentId": "10", "ciencia": None},
        {"expedienteId": "2", "documentId": None, "ciencia": None},
        {"expedienteId": "3", "documentId": None, "ciencia": {"por": "x", "em": "2026-10-01"}},
    ]
    assert [i["expedienteId"] for i in expedientes.pending_notice(items, ["99"])] == ["2"]
    timeline = [
        {"movements": [{"documents": [{"id": "10"}, {"id": "11"}]}, {"documents": [{"id": "12"}]}]},
    ]
    linked = items[:1]
    assert [i["expedienteId"] for i in expedientes.pending_notice(linked, ["11"], timeline)] == ["1"]
    assert expedientes.pending_notice(linked, ["12"], timeline) == []
    # 13 is not in the loaded timeline: it cannot be told apart from an attachment of the pending act.
    assert [i["expedienteId"] for i in expedientes.pending_notice(linked, ["12", "13"], timeline)] == ["1"]
    assert "ciência not checked" in expedientes.unchecked_message("unavailable")
    assert issubclass(expedientes.NoticeUnchecked, expedientes.PendingNotice)


def test_pending_notice_matches_only_requested_documents_without_ciencia():
    items = [
        {"expedienteId": "1", "documentId": "10", "ciencia": None},
        {"expedienteId": "2", "documentId": "10", "ciencia": {"por": "x", "em": "2026-10-01"}},
        {"expedienteId": "3", "documentId": "11", "ciencia": None},
    ]
    assert [i["expedienteId"] for i in expedientes.pending_notice(items, ["10"])] == ["1"]


FIXTURES = Path(__file__).parent / "fixtures"


def test_tjpa_rows_without_priority_take_the_act_document_from_the_row_links():
    html = (FIXTURES / "expedientes_tjpa.html").read_text(encoding="utf-8")
    items = expedientes.parse_expedientes(html)
    assert expedientes.tab_complete(html, items)
    assert [(i["expedienteId"], i["tipo"], i["documentId"]) for i in items] == [
        ("30000003", "Citação", "180000003"),
        ("30000002", "Citação", "180000002"),
        ("30000001", "Intimação", "180000001"),
    ]
    noticed, pending, answered = items
    assert noticed["ciencia"] == {"por": "ADVOGADO FICTICIO DA SILVA", "em": "2026-07-27"}
    assert noticed["fechado"] is False and noticed["atoPath"].endswith("paramIdProcessoDocumento=180000003")
    assert noticed["parte"] == "EMPRESA AEREA TESTE S.A." and noticed["meio"] == "Expedição eletrônica"
    assert pending["ciencia"] is None and pending["atoPath"] is None and pending["fechado"] is True
    assert (pending["limite"], pending["limiteTipo"]) == ("2026-07-29", "ciência")
    assert answered["prazoDias"] == 30 and answered["limite"] == "2026-11-04" and answered["respondido"] is True
    # Only the act of the pending citação (and what shares its timeline entry) is held back; the rest reads.
    timeline = [
        {"movements": [{"documents": [{"id": "180000002"}, {"id": "180000004"}]}]},
        {"movements": [{"documents": [{"id": "100000001"}]}]},
    ]
    assert expedientes.pending_notice(items, ["100000001"], timeline) == []
    assert [i["expedienteId"] for i in expedientes.pending_notice(items, ["180000004"], timeline)] == ["30000002"]


def test_a_row_without_header_still_counts_for_the_ciencia_check():
    html = (FIXTURES / "expedientes_tjpb_sem_id.html").read_text(encoding="utf-8")
    items = expedientes.parse_expedientes(html)
    assert expedientes.tab_complete(html, items)
    regular, headless = items
    assert regular["expedienteId"] == "20000002" and regular["documentId"] == "150000002"
    assert headless["expedienteId"] is None and headless["documentId"] == "150000001"
    assert headless["tipo"] == "Citaçao" and headless["meio"] == "Sistema" and headless["ciencia"] is None
    timeline = [{"movements": [{"documents": [{"id": "150000001"}]}, {"documents": [{"id": "140000000"}]}]}]
    assert expedientes.pending_notice(items, ["140000000"], timeline) == []
    blocked = expedientes.pending_notice(items, ["150000001"], timeline)
    assert blocked == [headless]
    assert "sem id, documento 150000001" in expedientes.pending_message(blocked)


def test_a_row_with_neither_expediente_nor_document_id_keeps_the_tab_incomplete():
    html = (FIXTURES / "expedientes_tjpb_sem_id.html").read_text(encoding="utf-8")
    html = html.replace("validaDocumento.seam?idProcessoDocumento=150000001&amp;cid=9", "x")
    items = expedientes.parse_expedientes(html)
    assert [i["expedienteId"] for i in items] == ["20000002"]
    assert not expedientes.tab_complete(html, items)
