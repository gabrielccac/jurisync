"""The account's processes (consultation by the lawyer's OABs) on synthetic pages.

Every number, code, name and court here is invented; the page shapes follow what TJAL and TJMS serve.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from jurisync.connectors.esaj import account, base, common, parsers, search

HOST = "https://www2.tjal.jus.br"
FIXTURES = Path(__file__).parent / "fixtures"
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


# --- Second instance, Colégio Recursal and the incidents of a first-instance case (TJSP) ----------------------------

TJSP = "https://esaj.tjsp.jus.br"


def sp_number(i):
    return f"{i:07d}-00.2026.8.26.0001"


def test_the_second_instance_list_reads_its_own_row_names():
    items, total = parsers.process_listing((FIXTURES / "cposg_lista.html").read_text(encoding="utf-8"), "cposg")
    assert total == 2
    assert items[0] == {
        "processNumber": sp_number(1),
        "codigo": "RS0000000001",
        "foro": None,
        "parte": {"papel": "Apelante", "nome": "Banco Exemplo S/A"},
        "classe": "Apelação Cível",
        "assunto": "DIREITO DO CONSUMIDOR - Contratos de Consumo",
        "recebidoEm": "10/03/2026",
        "local": "99ª Câmara de Direito Privado",
    }


def parent_row(number, code):
    return row(number, code) + (
        f'<div><a id="incidentesRecursos_{code}" href="#" cdProcesso="{code}" cdForoProcesso="1" '
        'class="unj-link-collapse"><span>Incidentes e recursos</span></a></div>'
        f'<div style="display: none;" id="divFilhos{code}"></div>'
    )


class SaoPaulo:
    """A fake TJSP: first instance (one case with an incident), second instance and Colégio Recursal by OAB."""

    def __init__(self, oabs, first, second=(), recursal=()):
        self.oabs, self.first, self.second, self.recursal = oabs, first, list(second), list(recursal)
        self.seen = []

    def __call__(self, session, portal, method, path, **kwargs):
        params = kwargs.get("params") or {}
        self.seen.append((method, path, params, kwargs.get("headers")))
        assert method == "GET"
        if path == "esaj/api/auth/session":
            body = {"usuarioLogado": True, "oabs": [{"stringOab": oab, "regular": True} for oab in self.oabs]}
            return reply(json.dumps(body))
        if path == "cpopg/search.do":
            found = self.first.get(params["dadosConsulta.valorConsulta"], [])
            if not found:
                return reply(f"<html>{NOT_FOUND}</html>")
            rows = [parent_row(n, c) if c.endswith("P") else row(n, c) for n, c in found]
            return reply(listing(rows, len(found)), url=f"{TJSP}/cpopg/search.do")
        if path == "cpopg/obterProcessosFilhos.do":
            child = row(sp_number(9), "1A0009C").replace('id="divProcesso1A0009C"', "")
            return reply(child, url=f"{TJSP}/cpopg/obterProcessosFilhos.do")
        if path in ("cposg/search.do", "cposgcr/search.do"):
            assert params["cbPesquisa"] == "NUMOAB" and "dePesquisa" in params
            cases = self.second if path.startswith("cposg/") else self.recursal
            if not cases or params["dePesquisa"] != self.oabs[0]:
                return reply(f"<html>{NOT_FOUND}</html>")
            app = path.split("/")[0]
            return reply(listing([row(n, c, app=app) for n, c in cases], len(cases)), url=f"{TJSP}/{path}")
        raise AssertionError(f"unexpected request {method} {path}")


@pytest.fixture
def sao_paulo(monkeypatch):
    monkeypatch.setattr(account, "LIST_PAUSE", 0)

    def install(*args, **kwargs):
        fake = SaoPaulo(*args, **kwargs)
        monkeypatch.setattr(common, "request", fake)
        return fake

    return install


class Session:
    """A session object the per-login cache can live on."""


def test_tjsp_lists_every_instance_with_the_incidents_of_a_case(sao_paulo):
    fake = sao_paulo(
        ["000001SP", "000002GO"],
        {"000001SP": [(sp_number(1), "1A0001P"), (sp_number(2), "1A0002")]},
        second=[(sp_number(1), "RS0000000001")],
    )
    result = base.list_processes(Session(), "tjsp-esaj", 10)
    assert result["instancias"] == {"1g": "cpopg", "2g": "cposg", "colegio_recursal": "cposgcr"}
    by_instance = [(item["instancia"], item["processNumber"], item.get("pai")) for item in result["items"]]
    assert by_instance == [
        ("1g", sp_number(1), None),
        ("1g", sp_number(2), None),
        ("1g", sp_number(9), "1A0001P"),
        ("2g", sp_number(1), None),
    ]
    first = result["oabs"][0]
    assert (first["instancia"], first["total"], first["returned"], first["incidentes"]) == ("1g", 2, 2, 1)
    assert result["complete"] is True and len(result["oabs"]) == 6
    # The incidents load as the list page's own script asks: the parent's code and foro, with the page's token.
    _m, _p, params, headers = next(entry for entry in fake.seen if entry[1] == "cpopg/obterProcessosFilhos.do")
    assert (params["cdProcessoMaster"], params["cdForoProcesso"]) == ("1A0001P", "1")
    assert headers == {"X-CSRF-Token": "00000000-0000-0000-0000-000000000000"}


def test_an_oab_that_found_nothing_is_skipped_later_in_the_login_unless_refreshed(sao_paulo):
    session = Session()
    fake = sao_paulo(["000001SP", "000002GO"], {"000001SP": [(sp_number(2), "1A0002")]})
    base.list_processes(session, "tjsp-esaj", 10, ["1g"])
    searched = len(fake.seen)
    again = base.list_processes(session, "tjsp-esaj", 10, ["1g"])
    skipped = [o for o in again["oabs"] if o.get("skipped")]
    assert [o["oab"] for o in skipped] == ["000002GO"] and again["complete"] is True
    assert len(fake.seen) - searched == 2  # the session API and the one OAB with cases
    refreshed = base.list_processes(session, "tjsp-esaj", 10, ["1g"], refresh=True)
    assert not any(o.get("skipped") for o in refreshed["oabs"])


def test_an_instance_the_portal_does_not_have_is_refused():
    with pytest.raises(ValueError):
        base.list_processes(None, "tjal-esaj", 10, ["2g"])
