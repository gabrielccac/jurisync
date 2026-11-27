"""The account's processes (consultation by the lawyer's OABs) on synthetic pages.

Every number, code, name and court here is invented; the page shapes follow what TJAL and TJMS serve.
"""

import json
from types import SimpleNamespace

import pytest

from jurisync.connectors.esaj import account, base, common, parsers, search

HOST = "https://www2.tjal.jus.br"
NOT_FOUND = (
    '<div id="mensagemRetorno"><li>Não existem informações disponíveis para os parâmetros informados.</li></div>'
)


def reply(text="", *, url=f"{HOST}/cpopg/search.do", status=200):
    return SimpleNamespace(
        status_code=status, url=url, text=text, content=text.encode(), json=lambda: json.loads(text), headers={}
    )


def row(number, code, name="Fulana de Tal", app="cpopg"):
    return f"""
    <div id="divProcesso{code}">
      <input type="hidden" name="_csrf" value="00000000-0000-0000-0000-000000000000">
      <div class="row home__lista-de-processos">
        <div class="nuProcesso col-md-3">Processo
          <a href="/{app}/show.do?processo.codigo={code}&processo.foro=1&conversationId=&cbPesquisa=NUMOAB&paginaConsulta=1"
             class="linkProcesso">{number}</a><span></span></div>
        <div class="col-md-3"><label class="unj-label tipoDeParticipacao">Requerente:</label>
          <div class="unj-base-alt nomeParte">{name}</div></div>
        <div class="col-md-4"><div class="classeProcesso">Procedimento Comum Cível</div>
          <div class="assuntoPrincipalProcesso">Indenização por Dano Moral</div></div>
        <div class="col-md-3"><label class="unj-label labelRecebidoEm">Recebido em:</label>
          <div class="dataLocalDistribuicaoProcesso">01/02/2026 - 1ª Vara Cível de Exemplo</div></div>
      </div>
    </div>"""


def listing(rows, total):
    return f"""<html><body><form id="formConsulta" action="/cpopg/search.do"></form>
    <div id="paginacaoSuperior"><span id="contadorDeProcessos">{total} Processos encontrados</span></div>
    <div id="listagemDeProcessos"><ul><li>{"</li><li>".join(rows)}</li></ul></div></body></html>"""


def number(i):
    return f"{i:07d}-00.2026.8.02.0001"


def test_the_listing_reads_every_field_the_row_shows():
    items, total = parsers.process_listing(listing([row(number(1), "1A0001")], 1), "cpopg")
    assert total == 1
    assert items == [
        {
            "processNumber": number(1),
            "codigo": "1A0001",
            "foro": "1",
            "parte": {"papel": "Requerente", "nome": "Fulana de Tal"},
            "classe": "Procedimento Comum Cível",
            "assunto": "Indenização por Dano Moral",
            "recebidoEm": "01/02/2026",
            "local": "1ª Vara Cível de Exemplo",
        }
    ]


def test_no_information_is_an_empty_list_and_an_unknown_page_is_an_error():
    assert parsers.process_listing(f"<html>{NOT_FOUND}</html>", "cpopg") == ([], 0)
    with pytest.raises(base.EsajParseError):
        parsers.process_listing("<html><form id='formConsulta'></form></html>", "cpopg")


def test_a_row_linking_another_application_is_ignored():
    items, _ = parsers.process_listing(listing([row(number(1), "1A0001", app="cposg")], 1), "cpopg")
    assert items == []


class Portal:
    """A fake e-SAJ with the account's OABs and the consultation by OAB, page by page."""

    def __init__(self, oabs, cases, *, page_size=25, session_status=200):
        self.oabs, self.cases, self.page_size, self.session_status = oabs, cases, page_size, session_status
        self.seen = []

    def __call__(self, session, portal, method, path, **kwargs):
        self.seen.append((method, path, kwargs.get("params")))
        assert method == "GET"
        if path == "esaj/api/auth/session":
            body = {"usuarioLogado": True, "oabs": [{"stringOab": oab, "regular": True} for oab in self.oabs]}
            return reply(json.dumps(body), status=self.session_status)
        if path in ("cpopg/search.do", "cpopg/trocarPagina.do"):
            params = kwargs["params"]
            assert params["cbPesquisa"] == "NUMOAB"
            assert (path == "cpopg/trocarPagina.do") == ("paginaConsulta" in params)
            found = self.cases.get(params["dadosConsulta.valorConsulta"], [])
            if not found:
                return reply(f"<html>{NOT_FOUND}</html>")
            page = int(params.get("paginaConsulta", 1))
            chunk = found[(page - 1) * self.page_size : page * self.page_size]
            return reply(listing([row(n, c) for n, c in chunk], len(found)))
        raise AssertionError(f"unexpected request {method} {path}")


@pytest.fixture
def portal(monkeypatch):
    monkeypatch.setattr(account, "LIST_PAUSE", 0)

    def install(*args, **kwargs):
        fake = Portal(*args, **kwargs)
        monkeypatch.setattr(common, "request", fake)
        return fake

    return install


def test_every_oab_is_searched_and_every_page_read(portal):
    cases = [(number(i), f"1A{i:04d}") for i in range(1, 31)]
    fake = portal(["000001MG", "000002SP"], {"000001MG": cases})
    result = base.list_processes(None, "tjal-esaj", 10)
    assert result["total"] == 30 and result["complete"] is True and result["consultation"] == "cpopg"
    assert [(o["oab"], o["total"], o["returned"], o["pages"], o["complete"]) for o in result["oabs"]] == [
        ("000001MG", 30, 30, 2, True),
        ("000002SP", 0, 0, 1, True),
    ]
    assert [path for _m, path, _p in fake.seen].count("cpopg/trocarPagina.do") == 1


def test_max_pages_stops_early_and_says_the_list_is_incomplete(portal):
    cases = [(number(i), f"1A{i:04d}") for i in range(1, 31)]
    portal(["000001MG"], {"000001MG": cases})
    result = base.list_processes(None, "tjal-esaj", 1)
    assert result["total"] == 25 and result["complete"] is False
    assert result["oabs"][0]["complete"] is False


def test_a_case_found_by_two_oabs_is_listed_once_with_both(portal):
    shared = [(number(1), "1A0001")]
    portal(["000001MG", "000002SP"], {"000001MG": shared, "000002SP": shared})
    result = base.list_processes(None, "tjal-esaj", 10)
    assert result["total"] == 1 and result["items"][0]["oabs"] == ["000001MG", "000002SP"]


def test_a_portal_without_the_account_api_is_not_supported(portal):
    portal([], {}, session_status=404)
    with pytest.raises(base.EsajBlocked) as error:
        base.list_processes(None, "tjal-esaj", 10)
    assert error.value.reason == "not_supported"


def test_a_single_match_answered_with_the_detail_page_is_one_item(monkeypatch):
    detail = reply(
        f'<span id="numeroProcesso">{number(7)}</span><span id="classeProcesso">Execução</span>'
        '<span id="varaProcesso">2ª Vara de Exemplo</span>',
        url=f"{HOST}/cpopg/show.do?processo.codigo=1A0007&processo.foro=2",
    )
    monkeypatch.setattr(search, "consult", lambda *a, **k: detail)
    items, total = base.oab_page(None, "tjal-esaj", base.adapter_for("tjal-esaj"), "000001MG", 1)
    assert total == 1
    assert items[0] == {
        "processNumber": number(7),
        "codigo": "1A0007",
        "foro": "2",
        "parte": None,
        "classe": "Execução",
        "assunto": None,
        "recebidoEm": None,
        "local": "2ª Vara de Exemplo",
    }


def test_a_captcha_portal_searches_with_a_minted_token(monkeypatch):
    sent = []
    monkeypatch.setattr(common, "request", lambda *a, **k: reply("<form></form>", url="https://esaj.tjms.jus.br/x"))
    monkeypatch.setattr(search, "token_fields", lambda *a: {"recaptcha_response_token": "t", "uuidCaptcha": "u"})

    def consult(session, portal, path, params=None):
        sent.append(params)
        return reply(listing([row(number(1).replace(".02.", ".12."), "1A0001", app="cpopg5")], 1))

    monkeypatch.setattr(search, "consult", consult)
    items, total = base.oab_page(None, "tjms-esaj", base.adapter_for("tjms-esaj"), "000001MG", 1)
    assert total == 1 and len(items) == 1
    assert len(sent) == 1 and sent[0]["uuidCaptcha"] == "u"


def test_max_pages_is_bounded():
    with pytest.raises(ValueError):
        base.list_processes(None, "tjal-esaj", 0)
