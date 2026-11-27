"""The account's processes: the consultation by the lawyer's OABs.

Neither the panel (tarefas-adv: tasks only) nor the account menu has a "my processes" list. The first-instance
consultation searches by OAB (``cbPesquisa=NUMOAB``) and lists every case where that OAB is registered, 25 a page
(``trocarPagina.do?paginaConsulta=N``); the account's OABs come from ``esaj/api/auth/session``. It is the public
consultation: nothing in it opens an intimação or a document.
"""

import re
import time
import urllib.parse

from jurisync.connectors.esaj import common, search

MAX_LIST_PAGES = 40


LIST_PAUSE = 1  # seconds between pages: TJSP refuses consultations asked less than about a second apart


def account_oabs(session, portal):
    """The OABs of the logged-in lawyer, as the consultation takes them (``"123456MG"``)."""
    response = common.request(session, portal, "GET", "esaj/api/auth/session")
    if response.status_code == 404:
        raise common.EsajBlocked(
            "This e-SAJ (the older portal) has no account API with the lawyer's OABs, so it cannot list the "
            "account's processes",
            "not_supported",
        )
    value = common.json_response(response)
    if not isinstance(value, dict) or not value.get("usuarioLogado"):
        raise common.EsajSessionExpired("e-SAJ session expired")
    oabs = []
    for item in value.get("oabs") or []:
        oab = re.sub(r"[^0-9A-Za-z]", "", str(item.get("stringOab") or "")) if isinstance(item, dict) else ""
        if oab and oab not in [known["oab"] for known in oabs]:
            oabs.append({"oab": oab, "regular": item.get("regular")})
    return oabs


def oab_params(oab):
    return {
        "conversationId": "",
        "cbPesquisa": "NUMOAB",
        "dadosConsulta.valorConsulta": oab,
        "dadosConsulta.tipoNuProcesso": "UNIFICADO",
        "cdForo": "-1",
    }


def detail_item(response, application):
    """The one process a search answered with its detail page instead of a list (a single match)."""
    from jurisync.connectors.esaj import parsers

    query = urllib.parse.parse_qs(urllib.parse.urlsplit(response.url).query)
    page = parsers.soup(response.content)
    shown = page.select_one("#numeroProcesso, #nuProcessoUnificadoFormatado")
    number = parsers.CNJ.search(shown.get_text(" ", strip=True)) if shown else None
    if number is None:
        raise common.EsajParseError(f"e-SAJ {application} answered a detail page without a process number")
    fields = parsers.process(response.content, number.group(0))["fields"]
    return {
        "processNumber": number.group(0),
        "codigo": query.get("processo.codigo", [None])[0],
        "foro": query.get("processo.foro", [None])[0],
        "parte": None,
        "classe": fields.get("classeProcesso"),
        "assunto": fields.get("assuntoProcesso"),
        "recebidoEm": None,
        "local": fields.get("varaProcesso") or fields.get("foroProcesso"),
    }


def oab_page(session, portal, adapter, oab, page):
    """``(items, total)`` of one page of the consultation by ``oab``; a CAPTCHA is answered with a minted token."""
    from jurisync.connectors.esaj import parsers

    application = adapter.CONSULTATION
    path = f"{application}/search.do" if page == 1 else f"{application}/trocarPagina.do"
    params = oab_params(oab) | ({"paginaConsulta": str(page)} if page > 1 else {})

    def understood(response):
        if parsers.captcha(response.content):
            return None
        if urllib.parse.urlsplit(response.url).path.endswith(f"/{application}/show.do"):
            return [detail_item(response, application)], 1
        try:
            return parsers.process_listing(response.content, application)
        except common.EsajParseError:
            return None  # the search form again: the portal wants the token its page sends

    if not getattr(adapter, "CAPTCHA", False):
        answer = understood(search.consult(session, portal, path, params=params))
        if answer is not None:
            return answer
    start = common.request(session, portal, "GET", f"{application}/open.do")
    tokens = search.token_fields(session, portal, application, start)
    answer = understood(search.consult(session, portal, path, params=params | tokens))
    if answer is None:
        raise common.EsajBlocked("The consultation by OAB answered with a CAPTCHA", "captcha_required")
    return answer


def list_processes(session, portal, max_pages=10):
    """The account's first-instance processes: the consultation by each of the lawyer's OABs, all pages up to
    ``max_pages`` per OAB, merged (a case found by two OABs is listed once, with both)."""
    if not isinstance(max_pages, int) or isinstance(max_pages, bool) or not 1 <= max_pages <= MAX_LIST_PAGES:
        raise ValueError(f"maxPages must be between 1 and {MAX_LIST_PAGES}")
    adapter = common.adapter_for(portal)
    merged, searches = {}, []
    for account in account_oabs(session, portal):
        oab, page, total, found = account["oab"], 1, None, []
        while True:
            if page > 1 or searches:
                time.sleep(LIST_PAUSE)
            items, count = oab_page(session, portal, adapter, oab, page)
            total = count if total is None else total
            known = {(item["codigo"], item["processNumber"]) for item in found}
            fresh = [item for item in items if (item["codigo"], item["processNumber"]) not in known]
            found.extend(fresh)
            if not fresh or total is None or len(found) >= total or page >= max_pages:
                break
            page += 1
        complete = total is not None and len(found) >= total
        searches.append(
            {
                "oab": oab,
                "regular": account["regular"],
                "total": total,
                "returned": len(found),
                "pages": page,
                "complete": complete,
            }
        )
        for item in found:
            key = item["codigo"] or item["processNumber"]
            merged.setdefault(key, {**item, "oabs": []})["oabs"].append(oab)
    items = list(merged.values())
    return {
        "instance": "1g",
        "consultation": adapter.CONSULTATION,
        "oabs": searches,
        "total": len(items),
        "complete": bool(searches) and all(search["complete"] for search in searches),
        "items": items,
    }
