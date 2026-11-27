"""The account's session API, mailbox and task panel (``tarefas-adv``): lists only, nothing opened."""

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
    from jurisync.connectors.esaj.parsers import messages

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
    return {"page": page, "items": messages(response.text)}


def list_tasks(session, portal, kind, state="AGUARDANDO_ASSINATURA"):
    from jurisync.connectors.esaj.parsers import public_json

    if kind not in ("intimacoes", "audiencias", "peticoes"):
        raise ValueError("kind must be intimacoes, audiencias or peticoes")
    if state not in PETITION_STATES:
        raise ValueError("Invalid petition state")
    if kind == "intimacoes":
        return {"kind": kind, "items": public_json(intimations(session, portal))}
    params = {"situacao": state} if kind == "peticoes" else {}
    return {"kind": kind, "items": public_json(common.json_response(panel(session, portal, kind, params=params)))}


# TJSP's panel assembles the account's lists on the first API request of a session, whichever list it is: seen live,
# the first ``peticoes`` call took 57 s and the first ``intimacoes`` 28 s (other days 65 s and over 3 minutes), then
# both answered in under a second. The API takes no paging or period parameter (the panel's own page sends a plain
# GET), so every panel call gets this longer wait and a timeout is reported as such.
PANEL_TIMEOUT = 150


INTIMATIONS_TIMEOUT = PANEL_TIMEOUT


def panel(session, portal, name, params=None):
    """One ``tarefas-adv/api/<name>`` request with the panel's wait; a timeout is ``portal_timeout``."""
    try:
        return common.request(session, portal, "GET", f"tarefas-adv/api/{name}", params=params, timeout=PANEL_TIMEOUT)
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
