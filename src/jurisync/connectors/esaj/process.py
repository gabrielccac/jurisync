"""One process's detail page and its on-demand movements."""

import urllib.parse

from jurisync import portals
from jurisync.connectors.esaj import common, dashboard, search


def load_process(session, portal, number):
    from jurisync.connectors.esaj import parsers, tjsp

    number = common.process_number(number, portal)
    adapter = common.adapter_for(portal)
    detail_response = None
    source = "dashboard"
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
    result["consultationSource"] = source
    return result


# Pages of the on-demand movements list (TJSP serves 5 movements a page); a longer history is reported as truncated.
MAX_MOVEMENT_PAGES = 200


def paged_movements(session, portal, application, code):
    """``(movements, complete)``: every page of ``carregarMovimentacoesAjax.do``, as the page's "Mais" link reads them.

    The first answer holds the first page and an opaque cursor; each next page brings the cursor of the one after it,
    and the last brings none. The process page must have been opened in this session first.
    """
    from jurisync.connectors.esaj import parsers

    result, cursor = [], ""
    for _ in range(MAX_MOVEMENT_PAGES):
        response = common.request(
            session,
            portal,
            "GET",
            f"{application}/carregarMovimentacoesAjax.do",
            params={"processo.codigo": code, "cursor": cursor},
        )
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
