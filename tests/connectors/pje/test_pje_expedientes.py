from datetime import date

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


def test_ato_is_not_opened_before_notice_is_registered():
    import pytest

    item = {
        "atoPath": "/pje/Painel/painel_usuario/popup/visualizarExpediente.seam?idProcessoParteExpediente=1&x=2",
        "ciencia": None,
    }
    with pytest.raises(ValueError, match="ciência not yet registered"):
        expedientes.fetch_ato(None, item, "https://pje1g.trf5.jus.br/pje/x")
    with pytest.raises(ValueError, match="not in this process"):
        expedientes.ato_item([], "9")


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
    assert ato["ciencia"]["pendenteAoLer"][0]["expedienteId"] == "1"


def test_pending_notice_matches_only_requested_documents_without_ciencia():
    items = [
        {"expedienteId": "1", "documentId": "10", "ciencia": None},
        {"expedienteId": "2", "documentId": "10", "ciencia": {"por": "x", "em": "2026-10-01"}},
        {"expedienteId": "3", "documentId": "11", "ciencia": None},
    ]
    assert [i["expedienteId"] for i in expedientes.pending_notice(items, ["10"])] == ["1"]
