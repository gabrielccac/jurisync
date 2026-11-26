"""Offline checks of consecutive reads on one process: the kept lookup and an expired session. No portal requests."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from jurisync import portals
from jurisync.connectors.pje import jsf, search, server
from jurisync.connectors.pje import process as client
from jurisync.connectors.pje.expedientes import TABLE_ID
from jurisync.core.session_pool import SessionExpired, SessionPool

NUMBER = "1020340-18.2025.4.01.3100"
CA = "ab" * 20
CONFIG = portals.pje("trf1-1g")
SEARCH_PAGE = """<form id="fPP"><input name="javax.faces.ViewState" value="s0"><input id="fPP:search" value="Pesquisar" onclick="A4J.AJAX.Submit('fPP',event,{'similarityGroupingId':'fPP:search'})"></form>"""
FOUND = (
    '<input name="javax.faces.ViewState" value="s1"><table id="fPP:processosTable"><tr><td></td>'
    f'<td><a id="select" onclick="idProcessoSelecionado:123">{NUMBER}</a></td>' + "<td></td>" * 7 + "</tr></table>"
)
SELECTED = f'<input name="javax.faces.ViewState" value="s2"><script>open("x?id=123&ca={CA}")</script>'
DETAIL = f'<div>{NUMBER}</div><form id="navbar"><input type="hidden" name="javax.faces.ViewState" value="d1"/></form>'
ATO = "/pje/Painel/painel_usuario/popup/visualizarExpediente.seam?idProcessoParteExpediente=100&x=1"
TAB = (
    f'<html><table><tbody id="{TABLE_ID}"><tr><td><div>Intimação (100) - Prioridade: Normal - ID do documento '
    "(900)</div><h6>AUTORA</h6><div>Diário Eletrônico (16/09/2026 19:56:06)</div>"
    "<div>O sistema registrou ciência em 18/09/2026 00:00:00</div>Prazo: 15 dias</td>"
    f'<td>09/10/2026 23:59:59</td><td><a href="{ATO}">Visualizar ato</a></td><td>SIM</td></tr></tbody></table></html>'
)


def answer(body, url):
    return SimpleNamespace(status_code=200, url=url, headers={}, content=body.encode(), text=body)


class Portal:
    """A trf1-1g session that answers the search, the detail page, its Expedientes tab and an act; it counts."""

    def __init__(self):
        self.requests = []
        self.tabs = []  # bodies the next Expedientes requests get, before the real tab

    def get(self, url, **_):
        self.requests.append(("GET", url))
        body = SEARCH_PAGE if url == CONFIG.search_url else DETAIL if search.DETAIL_PATH in url else "<p>Ato</p>"
        return answer(body, url)

    def post(self, url, data, **_):
        self.requests.append(("POST", url))
        if url != CONFIG.search_url:
            return answer(self.tabs.pop(0) if self.tabs else TAB, url)
        return answer(SELECTED if "idProcessoSelecionado" in data else FOUND, url)


def test_process_then_documents_then_act_search_the_process_once(monkeypatch):
    monkeypatch.setattr(client, "read_selected_documents", Mock(return_value=[]))
    session = Portal()
    process = client.fetch_process(NUMBER, "page", portal="trf1-1g", session=session)
    assert process["success"] and process["expedientes"]["total"] == 1
    assert len(session.requests) == 5  # search page, search, select, detail page, Expedientes tab

    del session.requests[:]
    read = client.read_documents(NUMBER, ["900"], portal="trf1-1g", session=session)
    assert read["ciencia"] == {"estado": "nenhuma"}
    assert session.requests == [("POST", session.requests[0][1])]  # only the Expedientes tab, read again

    del session.requests[:]
    act = client.fetch_process(NUMBER, "latest", portal="trf1-1g", session=session, ato_expediente="100")
    assert act["ato"]["texto"] == "Ato" and act["ciencia"] == {"estado": "nenhuma"}
    assert [method for method, _ in session.requests] == ["POST", "GET"]  # the tab, then the act

    other = Portal()  # another login keeps nothing of this one
    client.fetch_process(NUMBER, "page", portal="trf1-1g", session=other)
    assert len(other.requests) == 5


def test_a_kept_page_whose_tab_no_longer_answers_is_read_again(monkeypatch):
    session = Portal()
    client.fetch_process(NUMBER, "page", portal="trf1-1g", session=session)
    del session.requests[:]
    session.tabs.append("<html></html>")  # the kept page's ViewState was dropped by the portal
    result = client.fetch_process(NUMBER, "page", portal="trf1-1g", session=session)
    assert result["expedientes"]["complete"] is True
    assert [method for method, _ in session.requests] == ["POST", "GET", "POST"]

    monkeypatch.setattr(search, "MEMO_SECONDS", -1)
    del session.requests[:]
    client.fetch_process(NUMBER, "page", portal="trf1-1g", session=session)
    assert len(session.requests) == 5  # a lookup older than MEMO_SECONDS is searched again


def test_the_login_page_is_an_expired_session():
    url = CONFIG.search_url
    with pytest.raises(SessionExpired):
        jsf.check_page(answer('<form id="loginForm"></form>', url), url, "page")
    sso = SimpleNamespace(
        status_code=302, url=url, headers={"Location": "https://sso.cloud.pje.jus.br/auth/realms/pje"}
    )
    with pytest.raises(SessionExpired):
        jsf.check_page(sso, url, "page")
    elsewhere = SimpleNamespace(status_code=302, url=url, headers={"Location": "/pje/errorUnexpected.seam"})
    with pytest.raises(RuntimeError) as raised:
        jsf.check_page(elsewhere, url, "page")
    assert not isinstance(raised.value, SessionExpired)


def test_a_tool_logs_in_again_once_when_the_session_expired(monkeypatch):
    logins = []

    def login(portal):
        logins.append(portal)
        return Mock()

    monkeypatch.setattr(server, "sessions", SessionPool(login=login, refresh=lambda p, s: s, close=lambda s: None))
    calls = []

    def fetch(number, scope, *, portal, session):
        calls.append(session)
        if len(calls) == 1:
            raise SessionExpired("PJe search page indicates an expired session")
        return {"success": True, "timeline": []}

    monkeypatch.setattr(server, "fetch_process", fetch)
    assert server.create_server()._tool_manager.get_tool("pje_get_process").fn(NUMBER, "trf1-1g")["success"]
    assert len(logins) == 2 and calls[0] is not calls[1]


def test_an_expiry_while_documents_are_opened_is_not_repeated(monkeypatch):
    monkeypatch.setattr(client, "read_selected_documents", Mock(side_effect=SessionExpired("expired")))
    with pytest.raises(RuntimeError) as raised:
        client.read_documents(NUMBER, ["900"], portal="trf1-1g", session=Portal())
    assert not isinstance(raised.value, SessionExpired)
