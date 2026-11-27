"""One process's detail page and its on-demand movements, in the first instance or (where the court has them) the
second instance and the Colégio Recursal."""

import urllib.parse

from jurisync import portals
from jurisync.connectors.esaj import common, dashboard, search


def load_process(session, portal, number, instancia="1g", code=None):
    """The process detail of ``instancia`` ("1g", or one of the court's ``APPEALS``); ``code`` (the consultation's
    process code, e.g. a ``codigo`` of esaj_list_processes) picks the entry directly. The page must show ``number``."""
    from jurisync.connectors.esaj import account

    number = common.process_number(number, portal)
    if code is not None and not (isinstance(code, str) and dashboard.PROCESS_CODE.fullmatch(code)):
        raise ValueError("processCode must be the consultation's process code (letters and digits)")
    available = account.consultations(common.adapter_for(portal))
    if instancia not in available:
        raise ValueError(f"instancia must be among {', '.join(available)} on this portal")
    if instancia == "1g":
        result = load_first_instance(session, portal, number, code)
    else:
        result = load_appeal(session, portal, number, available[instancia], code)
    return {"instancia": instancia, **result}


def load_first_instance(session, portal, number, code=None):
    from jurisync.connectors.esaj import parsers, tjsp

    adapter = common.adapter_for(portal)
    detail_response = None
    source = "dashboard"
    link = None
    if code:
        link = portals.esaj(portal).url(f"{adapter.CONSULTATION}/show.do?processo.codigo={code}")
        source = "http"
    else:
        # The panel resolves a number only once its task lists are loaded; reloading them pays off only where the
        # consultation answers with a CAPTCHA (TJMS). Elsewhere the plain consultation is the cheaper way in.
        data = dashboard.dashboard_process(session, portal, number, petitions=getattr(adapter, "CAPTCHA", False))
        link = data.get("urlCpo") if data else None
        if link and urllib.parse.urlsplit(link).path != f"/{adapter.CONSULTATION}/show.do":
            link = None  # only a second-instance entry (cposg): the first-instance consultation below finds the case
    if not link:
        response, source = search.consultation_search(session, portal, adapter, number)
        if "/show.do" in urllib.parse.urlsplit(response.url).path:
            detail_response, link = response, response.url
        else:
            links = parsers.process_links(response.content, adapter.CONSULTATION)
            if len(links) != 1:
                # The plain search answered without a result (form page): retry with an HTTP-minted token.
                try:
                    start = common.request(session, portal, "GET", f"{adapter.CONSULTATION}/open.do")
                    response = search.token_search(session, portal, adapter, number, start)
                    source = "http_token"
                except common.EsajBlocked as error:
                    if error.reason == "rate_limited":
                        raise
                    if parsers.not_found(response.content):
                        raise common.EsajBlocked(
                            "This e-SAJ has no process with this number (it may be on another system of the court)",
                            "not_found",
                        ) from None
                    raise common.EsajBlocked("No unique process detail available to this account") from None
                if "/show.do" in urllib.parse.urlsplit(response.url).path:
                    detail_response, link = response, response.url
                else:
                    links = parsers.process_links(response.content, adapter.CONSULTATION)
                    if len(links) != 1:
                        raise common.EsajBlocked("No unique process detail available to this account")
                    link = urllib.parse.urljoin(response.url, links[0])
            else:
                link = urllib.parse.urljoin(response.url, links[0])
    config = portals.esaj(portal)
    common.require_portal_url(config, link)
    parts = urllib.parse.urlsplit(link)
    if parts.path != f"/{adapter.CONSULTATION}/show.do":
        raise common.EsajParseError("Unexpected process detail endpoint")
    response = detail_response or search.consult(
        session, portal, parts.path + ("?" + parts.query if parts.query else "")
    )
    if parsers.captcha(response.content):
        try:
            response = search.token_page(session, portal, link)
            source = "http_token"
        except common.EsajBlocked as error:
            if error.reason == "rate_limited":
                raise
            response = search.bridge_page(session, portal, link)
            source = "browser_bridge"
        if parsers.captcha(response.content):
            raise common.EsajBlocked("Consultation requires CAPTCHA", "captcha_required")
    result = parsers.process(response.content, number)
    code = urllib.parse.parse_qs(parts.query).get("processo.codigo", [""])[0]
    if code and (adapter is tjsp or parsers.movements_on_demand(response.content)):
        movements, complete = paged_movements(session, portal, adapter.CONSULTATION, code)
        if movements:
            result["movements"] = movements
            if not complete:
                result["movementsTruncated"] = True
    if parsers.second_instance_link(response.content, adapter.CONSULTATION, number):
        result["segundaInstancia"] = "The page links this case in the second instance: instancia=2g reads it there."
    result["consultationSource"] = source
    return result


def appeal_search_params(number):
    """The second-instance form's search by process number (``cbPesquisa=NUMPROC``)."""
    return {
        "conversationId": "",
        "paginaConsulta": "0",
        "cbPesquisa": "NUMPROC",
        "numeroDigitoAnoUnificado": number[:15],
        "foroNumeroUnificado": number[-4:],
        "dePesquisaNuUnificado": [number, "UNIFICADO"],
        "dePesquisa": "",
        "tipoNuProcesso": "UNIFICADO",
        "localPesquisa.cdLocal": "-1",
    }


def load_appeal(session, portal, number, application, code=None):
    """The second-instance (or Colégio Recursal) detail: by ``code``, or by number. A single match is answered with the
    detail page itself; several (an appeal and an interlocutory appeal share the number) list as ``alternatives`` and
    the first is read. Its movements come from ``carregarMovimentacoesAjax.do?cdProcesso=`` (not ``processo.codigo``).
    """
    from jurisync.connectors.esaj import account, parsers

    if code:
        response = search.consult(session, portal, f"{application}/show.do", params={"processo.codigo": code})
    else:
        response = search.consult(session, portal, f"{application}/search.do", params=appeal_search_params(number))
    if parsers.captcha(response.content):
        raise common.EsajBlocked("Consultation requires CAPTCHA", "captcha_required")
    alternatives = []
    if not account.is_detail(response, application):
        try:
            items, _total = parsers.process_listing(response.content, application)
        except common.EsajParseError:
            items = []
        items = [item for item in items if item["processNumber"] == number and item["codigo"]]
        if not items:
            raise common.EsajBlocked(
                f"The {application} consultation has no process with this number for this account", "not_found"
            )
        alternatives = [{"codigo": item["codigo"], "classe": item["classe"]} for item in items[1:]]
        response = search.consult(
            session, portal, f"{application}/show.do", params={"processo.codigo": items[0]["codigo"]}
        )
    result = parsers.process(response.content, number)
    code = parsers.hidden_process_code(response.content)
    if code and dashboard.PROCESS_CODE.fullmatch(code):
        movements, complete = paged_movements(session, portal, application, code, parameter="cdProcesso")
        if movements:
            result["movements"] = movements
            if not complete:
                result["movementsTruncated"] = True
    if alternatives:
        result["alternatives"] = alternatives
    result["processCode"] = code or None
    result["consultationSource"] = "http"
    return result


# Pages of the on-demand movements list (TJSP serves 5 movements a page); a longer history is reported as truncated.
MAX_MOVEMENT_PAGES = 200


def paged_movements(session, portal, application, code, parameter="processo.codigo"):
    """``(movements, complete)``: every page of ``carregarMovimentacoesAjax.do``, as the page's "Mais" link reads them.

    The first answer holds the first page and an opaque cursor; each next page brings the cursor of the one after it,
    and the last brings none. The process page must have been opened in this session first. The second instance
    names the code ``cdProcesso`` and sends no cursor on the first call, as its page does.
    """
    from jurisync.connectors.esaj import parsers

    result, cursor = [], ""
    for _ in range(MAX_MOVEMENT_PAGES):
        params = {parameter: code}
        if cursor or parameter == "processo.codigo":
            params["cursor"] = cursor
        response = common.request(session, portal, "GET", f"{application}/carregarMovimentacoesAjax.do", params=params)
        if response.status_code != 200:
            return result, False
        try:
            page, cursor = parsers.movement_page(response.content)
        except common.EsajParseError:
            return result, False
        result.extend(page)
        if not cursor:
            return result, True
    return result, False
