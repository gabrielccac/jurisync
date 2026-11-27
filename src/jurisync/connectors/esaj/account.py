"""The account's processes: the consultations by the lawyer's OABs.

Neither the panel (tarefas-adv: tasks only) nor the account menu has a "my processes" list. The first-instance
consultation searches by OAB (``cbPesquisa=NUMOAB``) and lists every case where that OAB is registered, 25 a page
(``trocarPagina.do?paginaConsulta=N``); a row with "Incidentes e recursos" loads its children on demand
(``obterProcessosFilhos.do``). Where the court has them (``adapter.APPEALS``), the second-instance and Colégio Recursal
consultations take the same search with ``dePesquisa``. The account's OABs come from ``esaj/api/auth/session``. These
are the public consultations: nothing in them opens an intimação or a document.
"""

import re
import time
import urllib.parse

from jurisync.connectors.esaj import common, search

MAX_LIST_PAGES = 40


LIST_PAUSE = 1  # seconds between pages: TJSP refuses consultations asked less than about a second apart


# An OAB search that found nothing is not repeated for this long in the same login (most of a lawyer's OABs are of
# other states and find nothing in this court: 22 of 24 on TJSP, every run). refresh=True searches them all again.
EMPTY_TTL = 6 * 3600


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


def appeal_params(oab):
    """The second-instance (and Colégio Recursal) form: the term goes in ``dePesquisa``."""
    return {
        "conversationId": "",
        "paginaConsulta": "0",
        "cbPesquisa": "NUMOAB",
        "dePesquisa": oab,
        "tipoNuProcesso": "UNIFICADO",
        "localPesquisa.cdLocal": "-1",
    }


def consultations(adapter):
    """``{instancia: application}`` of the consultations by OAB this court serves."""
    return {"1g": adapter.CONSULTATION, **getattr(adapter, "APPEALS", {})}


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
        # The second instance answers a single match at search.do itself; its code is the page's hidden cdProcesso.
        "codigo": query.get("processo.codigo", [None])[0] or parsers.hidden_process_code(response.content) or None,
        "foro": query.get("processo.foro", [None])[0],
        "parte": None,
        "classe": fields.get("classeProcesso"),
        "assunto": fields.get("assuntoProcesso"),
        "recebidoEm": None,
        "local": fields.get("varaProcesso") or fields.get("orgaoJulgadorProcesso") or fields.get("foroProcesso"),
    }


def is_detail(response, application):
    from jurisync.connectors.esaj import parsers

    path = urllib.parse.urlsplit(response.url).path
    if path.endswith(f"/{application}/show.do"):
        return True
    page = parsers.soup(response.content)
    return page.find(id="listagemDeProcessos") is None and page.select_one("#numeroProcesso") is not None


def oab_page(session, portal, adapter, oab, page, application=None):
    """``(items, total)`` of one page of the consultation by ``oab``; a CAPTCHA is answered with a minted token.
    ``application`` defaults to the first-instance consultation."""
    return oab_answer(session, portal, adapter, oab, page, application)[:2]


def oab_answer(session, portal, adapter, oab, page, application=None):
    """``oab_page`` with the page's response too (its rows link the incidents loaded on demand)."""
    from jurisync.connectors.esaj import parsers

    application = application or adapter.CONSULTATION
    first = application == adapter.CONSULTATION
    path = f"{application}/search.do" if page == 1 else f"{application}/trocarPagina.do"
    params = oab_params(oab) if first else appeal_params(oab)
    if page > 1:
        params = params | {"paginaConsulta": str(page)}

    def understood(response):
        if parsers.captcha(response.content):
            return None
        if is_detail(response, application):
            return [detail_item(response, application)], 1, response
        try:
            return (*parsers.process_listing(response.content, application), response)
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


def incident_children(session, portal, application, oab, page_html):
    """The children ("Incidentes e recursos") of every row of a first-instance page that has them, as the page's own
    script loads them; each child carries ``pai`` (the parent's code). A failed load is counted, not raised."""
    from jurisync.connectors.esaj import parsers

    children, failed = [], 0
    headers = {"X-CSRF-Token": token} if (token := parsers.csrf_token(page_html)) else {}
    for code, foro in parsers.incident_parents(page_html):
        time.sleep(LIST_PAUSE)
        params = oab_params(oab) | {"cdProcessoMaster": code, "cdForoProcesso": foro}
        try:
            response = search.consult(
                session, portal, f"{application}/obterProcessosFilhos.do", params=params, headers=headers
            )
        except common.EsajBlocked as error:
            if error.reason == "rate_limited":
                raise
            failed += 1
            continue
        if response.status_code != 200:
            failed += 1
            continue
        children.extend({**child, "pai": code} for child in parsers.incident_children(response.content, application))
    return children, failed


def empty_cache(session):
    """``{(portal, instancia, oab): time}`` of the searches that found nothing in this login."""
    cache = getattr(session, "jurisync_empty_oabs", None)
    if cache is None:
        cache = {}
        try:
            session.jurisync_empty_oabs = cache
        except AttributeError:
            pass  # a session that takes no attributes keeps no cache
    return cache


def search_oab(session, portal, adapter, instancia, application, account, max_pages):
    """``(found, summary)`` of every page of one OAB in one consultation, plus the incidents of the first instance."""
    oab, page, total, found, children_failed = account["oab"], 1, None, [], 0
    while True:
        time.sleep(LIST_PAUSE)
        items, count, response = oab_answer(session, portal, adapter, oab, page, application)
        total = count if total is None else total
        known = {(item["codigo"], item["processNumber"]) for item in found}
        fresh = [item for item in items if (item["codigo"], item["processNumber"]) not in known]
        found.extend(fresh)
        if instancia == "1g" and fresh:
            children, failed = incident_children(session, portal, application, oab, response.content)
            children_failed += failed
            found.extend(child for child in children if (child["codigo"], child["processNumber"]) not in known)
        if not fresh or total is None or len(found) >= total or page >= max_pages:
            break
        page += 1
    listed = [item for item in found if "pai" not in item]
    summary = {
        "oab": oab,
        "regular": account["regular"],
        "instancia": instancia,
        "total": total,
        "returned": len(listed),
        "pages": page,
        "complete": total is not None and len(listed) >= total,
    }
    if instancia == "1g":
        summary["incidentes"] = len(found) - len(listed)
        if children_failed:
            summary["incidentesNotLoaded"] = children_failed
    return found, summary


def list_processes(session, portal, max_pages=10, instancias=None, refresh=False):
    """The account's processes: the consultation by each of the lawyer's OABs in each instance the court serves (all
    pages up to ``max_pages`` per OAB), merged per instance (a case found by two OABs is listed once, with both).

    A search that found nothing in this login is skipped for ``EMPTY_TTL`` unless ``refresh``; its summary says so.
    """
    if not isinstance(max_pages, int) or isinstance(max_pages, bool) or not 1 <= max_pages <= MAX_LIST_PAGES:
        raise ValueError(f"maxPages must be between 1 and {MAX_LIST_PAGES}")
    adapter = common.adapter_for(portal)
    available = consultations(adapter)
    wanted = list(instancias or available)
    if not set(wanted) <= set(available):
        raise ValueError(f"instancias must be among {', '.join(available)} on this portal")
    cache = empty_cache(session)
    merged, searches = {}, []
    oabs = account_oabs(session, portal)
    for instancia in wanted:
        application = available[instancia]
        for account in oabs:
            key = (portal, instancia, account["oab"])
            when = cache.get(key)
            if when is not None and not refresh and time.monotonic() - when < EMPTY_TTL:
                searches.append(
                    {
                        "oab": account["oab"],
                        "regular": account["regular"],
                        "instancia": instancia,
                        "total": 0,
                        "returned": 0,
                        "pages": 0,
                        "complete": True,
                        "skipped": f"found nothing {int((time.monotonic() - when) // 60)} min ago in this login",
                    }
                )
                continue
            found, summary = search_oab(session, portal, adapter, instancia, application, account, max_pages)
            searches.append(summary)
            if summary["total"] == 0 and not found:
                cache[key] = time.monotonic()
            else:
                cache.pop(key, None)
            for item in found:
                merge = (instancia, item["codigo"] or item["processNumber"])
                merged.setdefault(merge, {**item, "instancia": instancia, "oabs": []})["oabs"].append(account["oab"])
    items = list(merged.values())
    return {
        "instancias": {instancia: available[instancia] for instancia in wanted},
        "consultation": adapter.CONSULTATION,
        "oabs": searches,
        "total": len(items),
        "complete": bool(searches) and all(search["complete"] for search in searches),
        "items": items,
    }
