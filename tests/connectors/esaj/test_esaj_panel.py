"""The account panel (tarefas-adv), the paged movements and the intimação guard, on synthetic pages.

Every number, code, name and cursor here is invented; the page shapes follow what TJSP/TJAL/TJAM serve.
"""

import json
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from curl_cffi.requests import exceptions

from jurisync.connectors.esaj import base, parsers
from jurisync.core.http import UnsafeUrlError

NUMBER = "0000001-00.2026.8.26.0001"
DIGITS = "00000010020268260001"
OTHER = "00000020020268260001"
TJAL = "0000001-00.2026.8.02.0001"
HOST = "https://esaj.tjsp.jus.br"


def reply(value=None, *, text=None, url=f"{HOST}/x", status=200):
    body = text if text is not None else json.dumps(value)
    return SimpleNamespace(
        status_code=status, url=url, text=body, content=body.encode(), json=lambda: json.loads(body), headers={}
    )


def entry(instance, number=DIGITS, code="AB0000000001"):
    app = "cpopg" if instance == "PG" else "cposg"
    return {
        "cdProcesso": code,
        "nuProcesso": number,
        "instancia": instance,
        "urlCpo": f"{HOST}/{app}/show.do?processo.codigo={code}",
        "urlPasta": f"{HOST}/{app}/abrirPastaDigitalIntegracao.do?cdProcesso={code}&modoServico=false",
    }


def viewer(number=NUMBER):
    params = urlencode({"nuProcesso": "".join(c for c in number if c.isdigit()), "cdDocumento": "7"})
    tree = [{"data": {"cdDocumento": "7", "title": "Petição inicial"}, "children": [{"data": {"parametros": params}}]}]
    return "var requestScope = " + json.dumps(tree) + ";"


class Portal:
    """A fake e-SAJ: answers by path (and panel parameters) and records every request."""

    def __init__(self, *, intimations=(), by_number=(), by_code=None, petitions=None, search=None, timeout=False):
        self.intimations = list(intimations)
        self.by_number = list(by_number)
        self.by_code = by_code or {}
        self.petitions = petitions or {}
        self.search = search
        self.timeout = timeout
        self.seen = []

    def __call__(self, session, portal, method, path, **kwargs):
        self.seen.append((method, path, kwargs))
        params = kwargs.get("params")
        if path == "tarefas-adv/api/intimacoes":
            if self.timeout:
                raise exceptions.Timeout("Operation timed out")
            return reply(self.intimations)
        if path == "tarefas-adv/api/processos":
            pairs = params.items() if isinstance(params, dict) else params
            codes = [value for key, value in pairs if key == "cdsProcesso"]
            if codes:
                return reply([item for code in codes for item in self.by_code.get(code, [])])
            return reply(self.by_number)
        if path == "tarefas-adv/api/peticoes":
            return reply(self.petitions.get(params["situacao"], []))
        if path.startswith("cpopg/open.do"):
            return reply(text="<form></form>", url=f"{HOST}/cpopg/open.do")
        if path.startswith("cpopg/search.do"):
            return self.search
        if path.startswith(("/cpopg/abrirPastaDigitalIntegracao.do", "/cposg/abrirPastaDigitalIntegracao.do")):
            return reply(text=viewer(), url=f"{HOST}/pastadigital/abrirPastaProcessoDigital.do")
        raise AssertionError(f"unexpected request {method} {path}")

    def paths(self):
        return [path for _method, path, _kwargs in self.seen]


@pytest.fixture
def portal(monkeypatch):
    def install(**kwargs):
        fake = Portal(**kwargs)
        monkeypatch.setattr(base, "request", fake)
        return fake

    return install


# --- Bug: the panel lists both instances, in no fixed order -------------------------------------------------------


def test_the_first_instance_entry_wins_whatever_the_order():
    assert base.dashboard_entry([entry("SG"), entry("PG")], DIGITS)["instancia"] == "PG"
    assert base.dashboard_entry([entry("PG"), entry("SG")], DIGITS)["instancia"] == "PG"
    assert base.dashboard_entry([entry("SG")], DIGITS)["instancia"] == "SG"
    assert base.dashboard_entry([entry("PG", number=OTHER)], DIGITS) is None


def test_get_process_reads_the_first_instance_detail_when_the_panel_lists_the_second_first(portal, monkeypatch):
    fake = portal(by_number=[entry("SG"), entry("PG")])
    detail = reply(text=f'<span id="numeroProcesso">{NUMBER}</span>', url=f"{HOST}/cpopg/show.do")
    original = fake.__call__

    def route(session, portal_key, method, path, **kwargs):
        if path.startswith("/cpopg/show.do"):
            fake.seen.append((method, path, kwargs))
            return detail
        if path == "cpopg/carregarMovimentacoesAjax.do":
            fake.seen.append((method, path, kwargs))
            return reply(text="")
        return original(session, portal_key, method, path, **kwargs)

    monkeypatch.setattr(base, "request", route)
    result = base.load_process(None, "tjsp-esaj", NUMBER)
    assert result["processNumber"] == NUMBER and result["consultationSource"] == "dashboard"
    assert any(path.startswith("/cpopg/show.do") for path in fake.paths())


def test_a_second_instance_only_entry_falls_back_to_the_first_instance_consultation(portal, monkeypatch):
    portal(by_number=[entry("SG")])
    detail = reply(text=f'<span id="numeroProcesso">{NUMBER}</span>', url=f"{HOST}/cpopg/show.do?processo.codigo=X")
    monkeypatch.setattr(base, "consultation_search", lambda *a: (detail, "http"))
    monkeypatch.setattr(base, "paged_movements", lambda *a: ([], True))
    assert base.load_process(None, "tjsp-esaj", NUMBER)["consultationSource"] == "http"


# --- Bug: documents never opened on TJSP/TJAL/TJAM -------------------------------------------------------------------


def test_the_panel_resolves_the_process_by_its_code_when_the_number_finds_nothing(portal):
    fake = portal(by_code={"AB0000000001": [entry("PG")]})
    item = base.dashboard_process(None, "tjsp-esaj", NUMBER, code="AB0000000001", petitions=False)
    assert item["cdProcesso"] == "AB0000000001"
    assert fake.seen[1][2]["params"] == {"cdsProcesso": "AB0000000001"}
    assert "tarefas-adv/api/peticoes" not in fake.paths()


def test_a_code_from_the_listed_petitions_resolves_the_process(portal):
    petition = {"processo": {"cdProcesso": "AB0000000009", "nuProcesso": DIGITS}, "tipoPeticao": "PETICAO_INICIAL"}
    fake = portal(by_code={"AB0000000009": [entry("PG", code="AB0000000009")]}, petitions={"CONCLUIDA": [petition]})
    assert base.dashboard_process(None, "tjsp-esaj", NUMBER)["cdProcesso"] == "AB0000000009"
    assert fake.paths().count("tarefas-adv/api/peticoes") == 4


def test_a_code_that_is_not_a_plain_identifier_is_never_sent(portal):
    fake = portal()
    assert base.dashboard_process(None, "tjsp-esaj", NUMBER, code="../x", petitions=False) is None
    assert len(fake.seen) == 1


def test_documents_open_through_the_code_the_consultation_shows(portal, monkeypatch):
    fake = portal(by_code={"AB0000000001": [entry("SG"), entry("PG")]})
    monkeypatch.setattr(base, "consultation_code", lambda *a: ("AB0000000001", None))
    items = base.list_documents(None, "tjsp-esaj", NUMBER)["items"]
    assert items[0]["documentId"] == "7" and "parameters" not in items[0]
    assert fake.paths()[-1].startswith("/cpopg/abrirPastaDigitalIntegracao.do")


def test_a_second_instance_viewer_is_accepted_and_any_other_path_is_not(portal, monkeypatch):
    portal(by_number=[entry("SG")])
    assert base.document_inventory(None, "tjsp-esaj", NUMBER)[0]["documentId"] == "7"
    odd = entry("PG")
    odd["urlPasta"] = f"{HOST}/cpopg/outraRota.do?cdProcesso=AB0000000001"
    portal(by_number=[odd])
    with pytest.raises(base.EsajParseError):
        base.document_inventory(None, "tjsp-esaj", NUMBER)


def test_consultation_code_reads_the_detail_url_or_a_single_result_link(monkeypatch):
    detail = reply(text="", url=f"{HOST}/cpopg/show.do?processo.codigo=AB0000000001&processo.foro=1")
    monkeypatch.setattr(base, "consultation_search", lambda *a: (detail, "http"))
    assert base.consultation_code(None, "tjsp-esaj", NUMBER) == ("AB0000000001", detail)
    listing = reply(text='<a href="/cpopg/show.do?processo.codigo=AB0000000002">x</a>', url=f"{HOST}/cpopg/search.do")
    monkeypatch.setattr(base, "consultation_search", lambda *a: (listing, "http"))
    assert base.consultation_code(None, "tjsp-esaj", NUMBER) == ("AB0000000002", None)
    many = reply(text=listing.text + '<a href="/cpopg/show.do?processo.codigo=AB0000000003">y</a>', url=listing.url)
    monkeypatch.setattr(base, "consultation_search", lambda *a: (many, "http"))
    assert base.consultation_code(None, "tjsp-esaj", NUMBER) == (None, None)


PASSWORD_DETAIL = (
    '<a id="linkPasta" class="linkPasta btn" href="#liberarAutoPorSenha">Visualizar autos</a>'
    '<a class="linkPasta" href="#liberarAutoPorSenha&acessibilidade=true">Visualizar autos</a>'
)


def test_records_behind_the_process_password_are_reported_as_such(portal, monkeypatch):
    fake = portal()
    detail = reply(text=PASSWORD_DETAIL, url=f"{HOST}/cpopg/show.do?processo.codigo=AB0000000001")
    monkeypatch.setattr(base, "consultation_code", lambda *a: ("AB0000000001", detail))
    with pytest.raises(base.EsajBlocked) as error:
        base.document_inventory(None, "tjam-esaj", "0000001-00.2026.8.04.0001")
    assert error.value.reason == "password_required"
    assert not any("abrirPasta" in path for path in fake.paths())
    assert parsers.records_need_password(PASSWORD_DETAIL)
    assert not parsers.records_need_password('<a class="linkPasta" href="/cpopg/abrirPasta.do">Visualizar autos</a>')
    assert not parsers.records_need_password("<p>no link</p>")


def test_no_viewer_anywhere_keeps_the_old_answer(portal, monkeypatch):
    portal()

    def captcha(*a):
        raise base.EsajBlocked("Consultation requires CAPTCHA", "captcha_required")

    monkeypatch.setattr(base, "consultation_code", captcha)
    with pytest.raises(base.EsajBlocked) as error:
        base.document_inventory(None, "tjsp-esaj", NUMBER)
    assert error.value.reason == "flow_required"


# --- The intimação guard ---------------------------------------------------------------------------------------------


def intimation(code, ciencia=False):
    return {"id": "1", "titulo": "Intimação", "descricao": "Texto", "cdProcesso": code, "ciencia": ciencia}


def test_a_process_with_an_intimacao_without_ciencia_never_opens(portal):
    fake = portal(
        intimations=[intimation("AB0000000001"), intimation("AB0000000002", ciencia=True)],
        by_number=[entry("PG")],
        by_code={"AB0000000001": [entry("PG"), entry("SG", code="AB0000000005")]},
    )
    for read in (base.list_documents, lambda *a: base.document(*a, "7")):
        with pytest.raises(base.EsajBlocked) as error:
            read(None, "tjsp-esaj", NUMBER)
        assert error.value.reason == "intimacao_pending"
    assert not any("abrirPasta" in path or "pastadigital" in path for path in fake.paths())
    # The codes go to the panel together, as its own page sends them; only those without ciência.
    lookup = next(kwargs for _m, path, kwargs in fake.seen if kwargs.get("params") and path.endswith("processos"))
    assert lookup["params"] == [("cdsProcesso", "AB0000000001")]


def test_an_intimacao_of_another_process_does_not_close_this_one(portal):
    portal(
        intimations=[intimation("AB0000000009")],
        by_number=[entry("PG")],
        by_code={"AB0000000009": [entry("PG", number=OTHER, code="AB0000000009")]},
    )
    assert base.document_inventory(None, "tjsp-esaj", NUMBER)[0]["documentId"] == "7"


@pytest.mark.parametrize(
    "intimations",
    [
        [intimation("AB0000000009")],  # the panel does not resolve the code
        [intimation("")],  # an intimação without a code
        [intimation(None)],
    ],
)
def test_the_guard_fails_closed(portal, intimations):
    fake = portal(intimations=intimations, by_number=[entry("PG")])
    with pytest.raises(base.EsajBlocked) as error:
        base.document_inventory(None, "tjsp-esaj", NUMBER)
    assert error.value.reason == "intimacao_pending"
    assert not any("abrirPasta" in path for path in fake.paths())


def test_an_unreadable_intimacao_list_keeps_documents_closed(portal):
    fake = portal(by_number=[entry("PG")], timeout=True)
    with pytest.raises(base.EsajBlocked) as error:
        base.document_inventory(None, "tjsp-esaj", NUMBER)
    assert error.value.reason == "portal_timeout"
    assert not any("abrirPasta" in path for path in fake.paths())


# --- Bug: TJSP's intimações list timing out -----------------------------------------------------------------------


def test_the_intimacoes_list_waits_longer_and_reports_a_timeout(portal):
    fake = portal(intimations=[intimation("AB0000000001")])
    result = base.list_tasks(None, "tjsp-esaj", "intimacoes")
    assert result["items"][0]["cdProcesso"] == "AB0000000001"
    assert fake.seen[0][2]["timeout"] == base.INTIMATIONS_TIMEOUT > 30
    fake = portal(timeout=True)
    with pytest.raises(base.EsajBlocked) as error:
        base.list_tasks(None, "tjsp-esaj", "intimacoes")
    assert error.value.reason == "portal_timeout"
    assert [method for method, _p, _k in fake.seen] == ["GET"]


# --- Bug: TJSP returning only the first 5 movements ------------------------------------------------------------------


def movement_row(day, text, doc=None):
    link = (
        f'<a class="linkMovVincProc" href="#liberarAutoPorSenha" onclick="javascript:jQuery.saj.'
        f"validarAberturaIntimacaoNaoRecebida(event, this, 'AB0000000001', {doc}, '#liberarAutoPorSenha')\">{text}</a>"
        if doc
        else text
    )
    return (
        f'<tr class="fundoClaro containerMovimentacao"><td class="dataMovimentacao">{day}</td><td></td>'
        f'<td class="descricaoMovimentacao">{link}</td></tr>'
    )


FIRST_PAGE = (
    '<table><tbody id="tabelaPrimeiraPaginaMovimentacoes">'
    + "".join(movement_row(f"0{i}/01/2026", f"Movimento {i}") for i in range(1, 6))
    + '</tbody><tbody id="tabelaOutrasPaginasMovimentacoes" style="display: none;"></tbody></table>'
    + '<div id="divLinkMovimentacoesPaginado"><input type="hidden" id="cursorMovimentacoesPaginado" value="c2" />'
    + '<a id="linkMovimentacoesPaginado" data-modo="ajax">Mais</a></div>'
)
MIDDLE_PAGE = (
    "".join(movement_row(f"1{i}/01/2026", f"Movimento {5 + i}", doc=40 + i) for i in range(1, 6))
    + '<tr id="trCursorMovimentacoes" data-cursor="c3" style="display:none"><td></td></tr>'
)
LAST_PAGE = movement_row("20/01/2026", "Movimento 11")


def test_movement_pages_carry_the_cursor_of_the_next_one():
    rows, cursor = parsers.movement_page(FIRST_PAGE)
    assert [row["text"] for row in rows] == [f"Movimento {i}" for i in range(1, 6)] and cursor == "c2"
    rows, cursor = parsers.movement_page(MIDDLE_PAGE)
    assert len(rows) == 5 and cursor == "c3" and rows[0]["documents"][0]["documentId"] == "41"
    assert parsers.movement_page(LAST_PAGE) == (parsers.movements(LAST_PAGE), None)
    with pytest.raises(base.EsajParseError):
        parsers.movement_page('<div class="mensagemErroMovimentacoes">Não foi possível</div>')


def test_every_movement_page_is_read(monkeypatch):
    pages = {"": FIRST_PAGE, "c2": MIDDLE_PAGE, "c3": LAST_PAGE}
    seen = []

    def request(session, portal, method, path, **kwargs):
        seen.append(kwargs["params"])
        return reply(text=pages[kwargs["params"]["cursor"]])

    monkeypatch.setattr(base, "request", request)
    movements, complete = base.paged_movements(None, "tjsp-esaj", "cpopg", "AB0000000001")
    assert complete and len(movements) == 11 and movements[-1]["text"] == "Movimento 11"
    assert [params["cursor"] for params in seen] == ["", "c2", "c3"]
    assert all(params["processo.codigo"] == "AB0000000001" for params in seen)


def test_a_failed_or_endless_history_is_marked_incomplete(monkeypatch):
    monkeypatch.setattr(base, "request", lambda *a, **k: reply(text=MIDDLE_PAGE))
    monkeypatch.setattr(base, "MAX_MOVEMENT_PAGES", 3)
    movements, complete = base.paged_movements(None, "tjsp-esaj", "cpopg", "AB0000000001")
    assert len(movements) == 15 and not complete
    error = '<div class="mensagemErroMovimentacoes">Não foi possível</div>'
    replies = iter([reply(text=FIRST_PAGE), reply(text=error)])
    monkeypatch.setattr(base, "request", lambda *a, **k: next(replies))
    movements, complete = base.paged_movements(None, "tjsp-esaj", "cpopg", "AB0000000001")
    assert len(movements) == 5 and not complete


def test_get_process_returns_the_whole_history_and_flags_a_truncated_one(monkeypatch):
    detail = reply(
        text=f'<span id="numeroProcesso">{TJAL}</span><a id="btnExibirMovimentacoes">Exibir movimentações</a>',
        url="https://www2.tjal.jus.br/cpopg/show.do?processo.codigo=AB0000000001",
    )
    monkeypatch.setattr(base, "dashboard_process", lambda *a, **k: None)
    monkeypatch.setattr(base, "consultation_search", lambda *a: (detail, "http"))
    rows = [{"date": "01/01/2026", "text": "Movimento", "documents": []}] * 12
    monkeypatch.setattr(base, "paged_movements", lambda *a: (rows, True))
    result = base.load_process(None, "tjal-esaj", TJAL)
    assert len(result["movements"]) == 12 and "movementsTruncated" not in result
    monkeypatch.setattr(base, "paged_movements", lambda *a: (rows, False))
    assert base.load_process(None, "tjal-esaj", TJAL)["movementsTruncated"] is True
    assert parsers.movements_on_demand(detail.text) and not parsers.movements_on_demand("<table></table>")


# --- Every panel call waits for the session's first, slow answer ----------------------------------------------------


@pytest.mark.parametrize("call", ["peticoes", "audiencias", "lookup"])
def test_every_panel_list_gets_the_long_wait_and_reports_a_timeout(monkeypatch, call):
    seen = []

    def request(session, portal, method, path, **kwargs):
        seen.append((path, kwargs["timeout"]))
        raise exceptions.Timeout("Operation timed out")

    monkeypatch.setattr(base, "request", request)
    with pytest.raises(base.EsajBlocked) as error:
        if call == "lookup":
            base.dashboard_lookup(None, "tjsp-esaj", {"numerosProcesso": DIGITS})
        else:
            base.list_tasks(None, "tjsp-esaj", call)
    assert error.value.reason == "portal_timeout"
    assert seen == [(f"tarefas-adv/api/{'processos' if call == 'lookup' else call}", base.PANEL_TIMEOUT)]
    assert base.PANEL_TIMEOUT >= 120


# --- TJSP's throttle: "multiplas consultas simultâneas" ---------------------------------------------------------------

THROTTLED = (
    '<form id="formConsulta"><input type="radio" name="dadosConsulta.tipoNuProcesso"></form>'
    '<div id="spwTabelaMensagem"><td id="mensagemRetorno"><li>Foram identificadas multiplas consultas simultâneas.'
    "</li></td></div>"
)


def test_the_throttle_answer_is_recognized():
    assert parsers.simultaneous_queries(THROTTLED)
    assert parsers.simultaneous_queries(THROTTLED.replace("multiplas", "múltiplas").encode())
    assert not parsers.simultaneous_queries(f'<span id="numeroProcesso">{NUMBER}</span>')


def test_a_throttled_consultation_is_asked_again_after_a_pause(monkeypatch):
    detail = reply(text=f'<span id="numeroProcesso">{NUMBER}</span>', url=f"{HOST}/cpopg/show.do?processo.codigo=A1")
    answers = iter([reply(text=THROTTLED, url=f"{HOST}/cpopg/search.do"), detail])
    pauses = []
    monkeypatch.setattr(base, "request", lambda *a, **k: next(answers))
    monkeypatch.setattr(base.time, "sleep", pauses.append)
    assert base.consult(None, "tjsp-esaj", "cpopg/search.do", params={}) is detail
    assert pauses == [base.THROTTLE_PAUSE]


def test_a_portal_that_keeps_throttling_is_reported_as_such_not_as_a_missing_process(monkeypatch):
    seen = []

    def request(session, portal, method, path, **kwargs):
        seen.append(path)
        if path.endswith("open.do"):
            return reply(text="<form></form>", url=f"{HOST}/cpopg/open.do")
        return reply(text=THROTTLED, url=f"{HOST}/cpopg/search.do")

    monkeypatch.setattr(base, "request", request)
    monkeypatch.setattr(base.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(base, "dashboard_process", lambda *a, **k: None)
    monkeypatch.setattr(base, "bridge_search", lambda *a: pytest.fail("the bridge is not a way around the throttle"))
    with pytest.raises(base.EsajBlocked) as error:
        base.load_process(None, "tjsp-esaj", NUMBER)
    assert error.value.reason == "rate_limited"
    assert seen.count("cpopg/search.do") == base.THROTTLE_RETRIES + 1


# --- Bug: documents of a case the panel does not list ("No authorized document viewer link") ------------------------

CODE = "AB0000000001"
VIEWER_URL = (
    f"{HOST}/pastadigital/abrirPastaProcessoDigital.do?nuProcesso={NUMBER}&cdProcesso={CODE}&instanciaProcesso=pg"
    "&cdServico=190101&ticket=t0"
)
RECORDS_DETAIL = (
    f'<span id="numeroProcesso">{NUMBER}</span>'
    f'<a class="linkPasta btn" href="/cpopg/abrirPastaDigital.do?processo.codigo={CODE}">Visualizar autos</a>'
    f'<a class="linkPasta btn" href="/cpopg/abrirPastaDigital.do?processo.codigo={CODE}&acessibilidade=true">'
    "Visualizar autos</a>"
)
APPEALED_DETAIL = (
    f'<span id="numeroProcesso">{NUMBER}</span><span id="labelSituacaoProcesso">Em grau de recurso</span>'
    f'<a class="linkConsultaSG btn" href="/cpopg/abrirConsultaProcessoSG.do?nuProcesso={NUMBER}&cdProcessoSg=SG01'
    '&cdForoSg=990&isProcessoOrigemCr=false">Consultar em 2ª instância</a>'
)
SECOND_DETAIL = (
    f'<span id="numeroProcesso">{NUMBER}</span><input type="hidden" name="cdProcesso" value="SG0000000001">'
    '<a class="linkPasta btn" id="pbVisualizarAutos" href="#">Visualizar autos</a>'
)
PASSWORD_POPUP = '<p>É necessário informar uma senha</p><input name="senhaProcesso"><input name="cdProcesso">'


class Records:
    """A fake e-SAJ for the detail-page route to the viewer; the panel resolves nothing."""

    def __init__(self, detail, *, answer=VIEWER_URL, second=(200, VIEWER_URL), first=(200, VIEWER_URL)):
        self.detail, self.answer, self.second, self.first = detail, answer, second, first
        self.seen = []

    def __call__(self, session, portal, method, path, **kwargs):
        self.seen.append((path, kwargs.get("params")))
        if path == "tarefas-adv/api/intimacoes":
            return reply([])
        if path == "tarefas-adv/api/processos":
            return reply([])
        if path.startswith("/cpopg/abrirPastaDigital.do?"):
            return reply(text=self.answer, url=f"{HOST}{path}")
        if path == "cpopg/abrirPastaDigital.do":
            status, body = self.first
            return reply(text=body, status=status, url=f"{HOST}/{path}")
        if path.startswith("/cpopg/abrirConsultaProcessoSG.do"):
            return reply(text=SECOND_DETAIL, url=f"{HOST}/cposg/show.do?processo.codigo=SG0000000001")
        if path == "/cposg/verificarAcessoPastaDigital.do":
            assert kwargs["params"] == {"cdProcesso": "SG0000000001"} and kwargs["accept"] == (403,)
            status, body = self.second
            return reply(text=body, status=status, url=f"{HOST}{path}")
        if path.startswith("/pastadigital/abrirPastaProcessoDigital.do"):
            return reply(text=viewer(), url=f"{HOST}/pastadigital/abrirPastaProcessoDigital.do")
        raise AssertionError(f"unexpected request {method} {path}")

    def paths(self):
        return [path for path, _params in self.seen]


@pytest.fixture
def records(monkeypatch):
    def install(detail, **kwargs):
        fake = Records(reply(text=detail, url=f"{HOST}/cpopg/show.do?processo.codigo={CODE}"), **kwargs)
        monkeypatch.setattr(base, "request", fake)
        monkeypatch.setattr(base, "consultation_code", lambda *a: (CODE, fake.detail))
        return fake

    return install


def test_a_case_the_panel_does_not_list_opens_through_visualizar_autos(records):
    fake = records(RECORDS_DETAIL)
    items = base.list_documents(None, "tjsp-esaj", NUMBER)["items"]
    assert items[0]["documentId"] == "7" and items[0]["accessStatus"] == "permission_check_required"
    assert f"/cpopg/abrirPastaDigital.do?processo.codigo={CODE}" in fake.paths()  # not its accessible twin
    assert fake.paths()[-1].startswith("/pastadigital/abrirPastaProcessoDigital.do")
    assert "tarefas-adv/api/peticoes" not in fake.paths()  # with a code, the petitions are not reloaded
    assert parsers.records_link(RECORDS_DETAIL, "cpopg").endswith(f"processo.codigo={CODE}")


@pytest.mark.parametrize(
    "answer",
    [
        "https://evil.example/pastadigital/abrirPastaProcessoDigital.do?nuProcesso=" + NUMBER,
        f"{HOST}/pastadigital/outra.do?nuProcesso={NUMBER}",
        VIEWER_URL.replace(NUMBER, "0000009-00.2026.8.26.0001"),
    ],
)
def test_the_viewer_url_the_button_answers_is_checked_before_it_is_followed(records, answer):
    fake = records(RECORDS_DETAIL, answer=answer)
    with pytest.raises((UnsafeUrlError, base.EsajParseError)):
        base.list_documents(None, "tjsp-esaj", NUMBER)
    assert not any(path.startswith("/pastadigital/") for path in fake.paths())


def test_a_button_answer_that_is_not_a_url_is_no_viewer(records):
    fake = records(RECORDS_DETAIL, answer="<html>Erro</html>")
    with pytest.raises(base.EsajBlocked):
        base.list_documents(None, "tjsp-esaj", NUMBER)
    assert not any(path.startswith("/pastadigital/") for path in fake.paths())


def test_a_case_in_the_second_instance_opens_its_second_instance_records(records):
    fake = records(APPEALED_DETAIL)
    assert base.list_documents(None, "tjsp-esaj", NUMBER)["items"][0]["documentId"] == "7"
    assert "/cposg/verificarAcessoPastaDigital.do" in fake.paths()
    assert "cpopg/abrirPastaDigital.do" not in fake.paths()


def test_a_second_instance_password_falls_back_to_the_first_instance_records(records):
    fake = records(APPEALED_DETAIL, second=(403, PASSWORD_POPUP))
    assert base.list_documents(None, "tjsp-esaj", NUMBER)["items"][0]["documentId"] == "7"
    assert fake.seen[fake.paths().index("cpopg/abrirPastaDigital.do")][1] == {"processo.codigo": CODE}


def test_both_instances_refused_is_password_required(records):
    fake = records(APPEALED_DETAIL, second=(403, PASSWORD_POPUP), first=(403, PASSWORD_POPUP))
    with pytest.raises(base.EsajBlocked) as error:
        base.list_documents(None, "tjsp-esaj", NUMBER)
    assert error.value.reason == "password_required" and "second instance" in str(error.value)
    assert not any(path.startswith("/pastadigital/") for path in fake.paths())


def test_the_daily_limit_on_unlinked_cases_is_reported_and_nothing_else_is_tried(records):
    fake = records(APPEALED_DETAIL, second=(406, ""))
    with pytest.raises(base.EsajBlocked) as error:
        base.list_documents(None, "tjsp-esaj", NUMBER)
    assert error.value.reason == "access_limit"
    assert "cpopg/abrirPastaDigital.do" not in fake.paths()


def test_a_process_page_without_any_records_route_keeps_the_flow_answer(records):
    fake = records(f'<span id="numeroProcesso">{NUMBER}</span>', first=(200, "<html></html>"))
    with pytest.raises(base.EsajBlocked) as error:
        base.list_documents(None, "tjsp-esaj", NUMBER)
    assert error.value.reason == "flow_required"
    assert not any(path.startswith("/pastadigital/") for path in fake.paths())


def test_the_second_instance_link_must_name_this_process():
    assert parsers.second_instance_link(APPEALED_DETAIL, "cpopg", NUMBER)
    assert parsers.second_instance_link(APPEALED_DETAIL, "cpopg", "0000009-00.2026.8.26.0001") is None
    assert parsers.hidden_process_code(SECOND_DETAIL) == "SG0000000001"
    assert parsers.hidden_process_code("<p></p>") == ""


def test_a_403_is_still_a_refusal_unless_the_caller_reads_it(monkeypatch):
    class Session:
        def request(self, method, url, **kwargs):
            return SimpleNamespace(status_code=403, url=url, headers={}, text="", content=b"")

    monkeypatch.setattr(base, "follow_redirects", lambda session, response, **kwargs: response)
    with pytest.raises(base.EsajBlocked):
        base.request(Session(), "tjsp-esaj", "GET", "cposg/verificarAcessoPastaDigital.do")
    response = base.request(Session(), "tjsp-esaj", "GET", "cposg/verificarAcessoPastaDigital.do", accept=(403,))
    assert response.status_code == 403
