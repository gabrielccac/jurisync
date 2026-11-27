"""The account's session API, mailbox and task panel (``tarefas-adv``): lists only, nothing opened."""

import datetime
import re

from curl_cffi import requests

from jurisync import portals
from jurisync.connectors.esaj import cas, common


def get_session(session, portal):
    from jurisync.connectors.esaj import bridge
    from jurisync.connectors.esaj.parsers import public_json

    response = common.request(session, portal, "GET", "esaj/api/auth/session")
    if response.status_code == 404:
        # The older portal (TJAM) has no session API: the CAS script answers, without OABs or services.
        if not cas.cas_logged_in(session, portals.esaj(portal)):
            raise common.EsajSessionExpired("e-SAJ session expired")
        return {"usuarioLogado": True, "sessionSource": "sajcas", "captchaBridge": bridge.bridge_status()}
    value = common.json_response(response)
    if not isinstance(value, dict) or not value.get("usuarioLogado"):
        raise common.EsajSessionExpired("e-SAJ session expired")
    result = public_json({k: value[k] for k in ("usuarioLogado", "oabs", "servicosDisponiveis") if k in value})
    result["captchaBridge"] = bridge.bridge_status()
    return result


def list_messages(session, portal, page=1):
    """One mailbox page (20 messages): each with ``naoLida``, plus ``lastPageShown`` (the pager's last number) and
    ``next`` (the page to ask for next, None on the last one)."""
    from jurisync.connectors.esaj.parsers import mailbox_pager, messages

    if not isinstance(page, int) or isinstance(page, bool) or not 1 <= page <= 1000:
        raise ValueError("page must be between 1 and 1000")
    # Opening a message can change its read status; only the mailbox listing is fetched.
    response = common.request(session, portal, "GET", "caixapostal/abrirCaixaPostal.do")
    if page > 1:
        response = common.request(session, portal, "GET", "caixapostal/listaPaginada.do", params={"pag": page})
    if response.status_code != 200:
        raise common.EsajParseError(f"e-SAJ mailbox failed (HTTP {response.status_code})")
    # The mailbox is served as ISO-8859-1 behind two conflicting <meta> tags (UTF-8 first): the HTTP header's charset
    # (what ``response.text`` follows) decides, otherwise subjects come out as mojibake ("Eletr˘nico").
    items = messages(response.text)
    last, has_next = mailbox_pager(response.text)
    last = max(last or page, page)
    return {
        "page": page,
        "items": items,
        "unread": sum(item["naoLida"] for item in items),
        "lastPageShown": last,
        "next": page + 1 if has_next or last > page else None,
    }


def list_tasks(session, portal, kind, state="AGUARDANDO_ASSINATURA", date_from=None, date_to=None):
    """One panel list. Petitions with a period (``date_from``/``date_to``, ISO dates) come from the panel's own period
    filter, which answers the whole period; without one the panel sends only a recent slice (seen live on TJSP: 3
    concluded petitions where the last 30 days had 6)."""
    from jurisync.connectors.esaj.parsers import public_json

    if kind not in ("intimacoes", "audiencias", "peticoes"):
        raise ValueError("kind must be intimacoes, audiencias or peticoes")
    if state not in PETITION_STATES:
        raise ValueError("Invalid petition state")
    if (date_from or date_to) and kind != "peticoes":
        raise ValueError("dateFrom and dateTo filter only kind=peticoes")
    if kind == "intimacoes":
        return {"kind": kind, "items": public_json(intimations(session, portal))}
    if date_from or date_to:
        first, last = period(date_from, date_to)
        items, windows = petitions_in_period(session, portal, state, first, last)
        dates = {"from": first.isoformat(), "to": last.isoformat(), "requests": windows}
        return {"kind": kind, "items": public_json(items), "period": dates}
    params = {"situacao": state} if kind == "peticoes" else {}
    return {"kind": kind, "items": public_json(common.json_response(panel(session, portal, kind, params=params)))}


# The panel's period filter refuses more than 30 days at once ("Período máximo para consulta é de 30 dias"); a longer
# period is asked in windows of 30 days, up to a year.
FILTER_DAYS = 30
MAX_PERIOD_DAYS = 366


def period(date_from, date_to):
    """``(first, last)`` dates of a requested period; a missing end is today, a missing start 30 days before the end."""

    def parse(value, name):
        try:
            return datetime.date.fromisoformat(value)
        except (TypeError, ValueError):
            raise ValueError(f"{name} must be an ISO date (YYYY-MM-DD)") from None

    last = parse(date_to, "dateTo") if date_to else datetime.date.today()
    first = parse(date_from, "dateFrom") if date_from else last - datetime.timedelta(days=FILTER_DAYS - 1)
    if first > last:
        raise ValueError("dateFrom must not be after dateTo")
    if (last - first).days + 1 > MAX_PERIOD_DAYS:
        raise ValueError(f"The period may span at most {MAX_PERIOD_DAYS} days")
    return first, last


def petitions_in_period(session, portal, state, first, last):
    """``(items, requests)``: the petitions in ``state`` whose dates fall in the period, as the panel's own filter
    posts it (``peticoes-filtro``, start and end of day in local time), one window of up to 30 days per request."""
    items, seen, windows, start = [], set(), 0, first
    while start <= last:
        end = min(start + datetime.timedelta(days=FILTER_DAYS - 1), last)
        body = {
            "situacao": state,
            "dataInicio": f"{start.isoformat()}T00:00:00.000",
            "dataFim": f"{end.isoformat()}T23:59:59.999",
        }
        answer = common.json_response(panel(session, portal, "peticoes-filtro", body=body))
        if not isinstance(answer, list):
            raise common.EsajParseError("Unexpected petition filter response")
        for item in answer:
            key = repr(sorted(item.items())) if isinstance(item, dict) else repr(item)
            if key not in seen:
                seen.add(key)
                items.append(item)
        windows += 1
        start = end + datetime.timedelta(days=1)
    return items, windows


# TJSP's panel assembles the account's lists on the first API request of a session, whichever list it is: seen live,
# the first ``peticoes`` call took 57 s and the first ``intimacoes`` 28 s (other days 65 s and over 3 minutes), then
# both answered in under a second. The API takes no paging or period parameter (the panel's own page sends a plain
# GET), so every panel call gets this longer wait and a timeout is reported as such.
PANEL_TIMEOUT = 150


INTIMATIONS_TIMEOUT = PANEL_TIMEOUT


def panel(session, portal, name, params=None, body=None):
    """One ``tarefas-adv/api/<name>`` request with the panel's wait; a timeout is ``portal_timeout``. A ``body`` is
    posted as JSON (only the petition filter, a read: ``common.READ_POSTS``)."""
    method, extra = ("POST", {"json": body}) if body is not None else ("GET", {"params": params})
    try:
        return common.request(session, portal, method, f"tarefas-adv/api/{name}", timeout=PANEL_TIMEOUT, **extra)
    except requests.exceptions.Timeout:
        raise common.EsajBlocked(
            f"The portal did not finish the panel's {name} list within {PANEL_TIMEOUT} s. The court assembles the "
            "lists on the first request of a session and answers the next ones quickly: try again in a few minutes",
            "portal_timeout",
        ) from None


def intimations(session, portal):
    """The panel's intimações (each with its process code and the ``ciencia`` flag). Never marks any of them."""
    items = common.json_response(panel(session, portal, "intimacoes"))
    if not isinstance(items, list):
        raise common.EsajParseError("Unexpected intimações response")
    return items


PETITION_STATES = ("AGUARDANDO_ASSINATURA", "AGUARDANDO_DISTRIBUICAO", "AGUARDANDO_JUNTADA", "CONCLUIDA")


def dashboard_lookup(session, portal, params):
    """The panel's ``processos`` answer for ``numerosProcesso`` or ``cdsProcesso`` (repeatable) parameters."""
    items = common.json_response(panel(session, portal, "processos", params=params))
    if not isinstance(items, list):
        raise common.EsajParseError("Unexpected process dashboard response")
    return items


def dashboard_entry(items, digits):
    """The entry of the process, first instance before second: the panel lists both, in no fixed order."""
    matches = [
        item
        for item in items
        if isinstance(item, dict) and re.sub(r"\D", "", str(item.get("nuProcesso") or "")) == digits
    ]
    return min(matches, key=lambda item: item.get("instancia") != "PG", default=None)


PROCESS_CODE = re.compile(r"[A-Za-z0-9]{1,40}")


def dashboard_process(session, portal, number, code=None, *, petitions=True):
    """The account panel's entry for a process (``urlCpo``, ``urlPasta``), or None.

    By number the panel resolves only the cases loaded into this session's task panels (enough on TJMS); by process
    code it resolves the account's cases directly, which is what its own page does (needed on TJSP and TJAL). The code
    comes from the caller (the consultation's ``processo.codigo``) or from a petition the panel lists.
    """
    digits = re.sub(r"\D", "", number)
    item = dashboard_entry(dashboard_lookup(session, portal, {"numerosProcesso": digits}), digits)
    if not item and isinstance(code, str) and PROCESS_CODE.fullmatch(code):
        item = dashboard_entry(dashboard_lookup(session, portal, {"cdsProcesso": code}), digits)
    if item or not petitions:
        return item
    tried = {code}
    for state in PETITION_STATES:
        listed = common.json_response(panel(session, portal, "peticoes", params={"situacao": state}))
        for petition in listed if isinstance(listed, list) else []:
            process = petition.get("processo") if isinstance(petition, dict) else None
            if not isinstance(process, dict) or re.sub(r"\D", "", str(process.get("nuProcesso") or "")) != digits:
                continue
            value = process.get("cdProcesso")
            if isinstance(value, str) and PROCESS_CODE.fullmatch(value) and value not in tried:
                tried.add(value)
                item = dashboard_entry(dashboard_lookup(session, portal, {"cdsProcesso": value}), digits)
                if item:
                    return item
        item = dashboard_entry(dashboard_lookup(session, portal, {"numerosProcesso": digits}), digits)
        if item:
            return item
    return None
