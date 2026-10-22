"""Read-only eproc client.

The browser is used only for login. All portal reads happen through the
transferred cookie jar while this module enforces the small observed route
surface.
"""

import argparse
import json
import re
import sys
from contextlib import redirect_stdout
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup
from curl_cffi.requests.exceptions import RequestException

from jurisync import portals
from jurisync.connectors.eproc.auth import login, portal_totp_account
from jurisync.connectors.eproc.certificate import cert_login
from jurisync.connectors.eproc.documents import read_selected_documents
from jurisync.connectors.eproc.parsers import (
    parse_event_response,
    parse_lawyer_process_report,
    parse_panel,
    parse_process_detail,
    parse_process_list_page,
)
from jurisync.core.http import IMPERSONATE, enable_request_logging, require_url
from jurisync.core.mcp import load_env_file
from jurisync.core.redaction import drop_keys

TIMEOUT = 30
REDIRECTS = {301, 302, 303, 307, 308}
# Read-only citation/intimation queues, keyed by the tool's `queue` argument.
# The panel advertises which of these a deployment exposes; the extra kinds are
# the same "process list" layout as pending/open_deadlines.
QUEUE_KINDS = {
    "open_deadlines": "citacao_intimacao_prazo_aberto_listar",
    "pending": "citacao_intimacao_pendente_listar",
    "pending_urgent": "citacao_intimacao_pendente_listar_urgente",
    "pending_agreement": "citacao_intimacao_pendente_listar_acordo",
    "art334": "citacao_art334_pendente_listar",
    "expired_deadline": "citacao_intimacao_decurso_prazo_listar",
    "hearing_notice": "intimacao_de_pauta_listar",
}
QUEUE_ACTIONS = set(QUEUE_KINDS.values())
PROCESS_REPORT_ACTION = "relatorio_processo_procurador_listar"
READ_ACTIONS = QUEUE_ACTIONS | {
    PROCESS_REPORT_ACTION,
    "painel_adv_listar",
    "processo_pesquisa_rapida",
    "processo_selecionar",
    "processo_consulta_listar",
    "acessar_documento",
    "acessar_documento_implementacao",
    "processo_selecionar_pagina",  # TJRS pages the event table through the process page instead of ajax
}
AJAX_ACTIONS = {"montar_tabela_eventos_filtrados", "retornar_citacao_intimacao_estado"}
CNJ = re.compile(r"^\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}$")
MAX_EVENT_PAGES = 5


def controller_path(config, *, ajax=False):
    """The portal's controller script under its own application root (/eprocV2/ at TRF4, /eproc/ at TRF6)."""
    return f"{config.root}{'controlador_ajax.php' if ajax else 'controlador.php'}"


def require_route(value, actions=READ_ACTIONS, *, ajax=False, portal):
    """Validate a portal URL discovered from authenticated HTML."""
    config = portals.eproc(portal)
    require_url(
        value,
        hosts={config.host},
        path=controller_path(config, ajax=ajax),
        label="eproc",
        allow_fragment=False,
    )
    query = parse_qs(urlsplit(value).query)
    key = "acao_ajax" if ajax else "acao"
    action = query.get(key, [""])[0]
    if action not in actions:
        raise RuntimeError(f"Unexpected eproc read action: {action or 'missing'}")
    return value


def _decode(response):
    content = getattr(response, "content", b"")
    if isinstance(content, str):
        return content
    return content.decode("iso-8859-1", errors="replace")


def classify_body(body):
    lowered = body.casefold()
    soup = BeautifulSoup(body, "html.parser")
    if soup.select_one("#username, [name='username'], [name=\"username\"]") or any(
        token in lowered
        for token in (
            "keycloak",
            "sessão expirada",
            "sessao expirada",
            "login eproc",
        )
    ):
        return "session_expired"
    # Subject options can contain "não autorizado" without indicating an access failure.
    for node in soup.select("script, style, option"):
        node.decompose()
    lowered = soup.get_text(" ", strip=True).casefold()
    if any(
        token in lowered
        for token in (
            "código de segurança inválido",
            "codigo de seguranca invalido",
            "sem acesso ao documento",
            "acesso negado",
            "access denied",
            "não autorizado",
            "nao autorizado",
        )
    ):
        return "access_denied"
    return None


def _response_error(response, body=""):
    state = classify_body(body)
    if state:
        return state
    if getattr(response, "status_code", 0) != 200:
        return f"http_{response.status_code}"
    return None


def request(session, method, url, *, data=None, ajax=False, portal):
    require_route(url, AJAX_ACTIONS if ajax else READ_ACTIONS, ajax=ajax, portal=portal)
    try:
        response = session.request(
            method,
            url,
            data=data,
            headers={"Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8"},
            allow_redirects=False,
            impersonate=IMPERSONATE,
            timeout=TIMEOUT,
        )
    except RequestException:
        raise RuntimeError("eproc request failed") from None
    body = _decode(response)
    return response, body


def _credentials(portal):
    config = portals.eproc(portal)
    username, password = config.env("USERNAME"), config.env("PASSWORD")
    if not username or not password:
        raise RuntimeError(f"Set {config.env_name('USERNAME')} and {config.env_name('PASSWORD')} in .env")
    return username, password


def authenticate(portal):
    if portals.eproc(portal).uses_certificate():
        return cert_login(portal)
    username, password = _credentials(portal)
    return login(username, password, portal_totp_account(portal), portal=portal)


def _first(mapping, *keys):
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        value = mapping.get(key)
        if value:
            return value
    return None


def _panel_endpoint(panel, queue):
    action = QUEUE_KINDS.get(queue)
    if action is None:
        raise ValueError(f"queue must be one of {', '.join(QUEUE_KINDS)}")
    if isinstance(panel, dict):
        queues = _first(panel, "queues", "filas", "queueUrls", "queue_urls") or {}
        value = queues.get(queue) if isinstance(queues, dict) else None
        if isinstance(value, dict):
            value = _first(value, "url", "href")
        if value:
            return value
        advertised = panel.get("actions") or panel.get("queueActions") or {}
        value = advertised.get(action) if isinstance(advertised, dict) else None
        if isinstance(value, dict):
            value = _first(value, "url", "href")
        if value:
            return value
        value = panel.get(action)
        if isinstance(value, dict):
            value = _first(value, "url", "href")
        if value:
            return value
    raise RuntimeError(f"Authenticated panel did not expose the {queue} queue")


def _route_action(url, *, ajax=False):
    query = parse_qs(urlsplit(url).query)
    return query.get("acao_ajax" if ajax else "acao", [""])[0]


def _search_endpoint(panel_html, panel):
    if isinstance(panel, dict):
        value = panel.get("quickSearch")
        if isinstance(value, dict):
            action = value.get("action")
            method = str(value.get("method", "")).lower()
            fields = (value.get("numberField"), value.get("submitField"), value.get("returnField"))
            if (
                action
                and method == "post"
                and all(isinstance(field, str) and field for field in fields)
                and isinstance(value.get("returnValue"), str)
            ):
                return value
    raise RuntimeError("Authenticated panel did not expose a complete quick process search form")


REDACTED_KEYS = {"url", "href", "hash", "action", "requesturl", "request_url", "key", "event", "sameinstance"}


def _redact(value):
    """Remove adapter URLs and hashes from outward JSON."""
    return drop_keys(value, REDACTED_KEYS)


def list_notices(queue, *, session=None, panel_html=None, panel_url=None, portal):
    portal = portals.eproc(portal).key  # aliases in, current key out
    if queue not in QUEUE_KINDS:
        raise ValueError(f"queue must be one of {', '.join(QUEUE_KINDS)}")
    owned = session is None
    if owned:
        session, panel_html, panel_url = authenticate(portal)
    try:
        base_url = portals.eproc(portal).base_url
        panel = parse_panel(panel_html, base_url)
        endpoint = _panel_endpoint(panel, queue)
        require_route(urljoin(panel_url or base_url, endpoint), portal=portal)
        response, body = request(session, "GET", urljoin(panel_url or base_url, endpoint), portal=portal)
        error = _response_error(response, body)
        if error:
            return {
                "success": False,
                "source": "eproc",
                "portal": portal,
                "queue": queue,
                "items": [],
                "diagnostics": [error],
            }
        parsed = parse_process_list_page(body, base_url)
        items = parsed.get("items", [])
        return {
            "success": True,
            "source": "eproc",
            "portal": portal,
            "queue": queue,
            "items": _redact(items),
            "pagination": _redact(parsed.get("pagination", {})),
            "diagnostics": [],
        }
    finally:
        if owned:
            session.close()


def list_processes(*, session=None, panel_html=None, panel_url=None, portal):
    """Read the first lawyer-report page with the portal's default filters."""
    portal = portals.eproc(portal).key
    owned = session is None
    if owned:
        session, panel_html, panel_url = authenticate(portal)
    try:
        base_url = portals.eproc(portal).base_url
        panel = parse_panel(panel_html, base_url)
        endpoint = panel["actions"].get(PROCESS_REPORT_ACTION)
        if not endpoint:
            raise RuntimeError("Authenticated panel did not expose Relação de Processos")
        endpoint = urljoin(panel_url or base_url, endpoint)
        require_route(endpoint, {PROCESS_REPORT_ACTION}, portal=portal)
        response, body = request(session, "GET", endpoint, portal=portal)
        error = _response_error(response, body)
        parsed = (
            {"items": [], "pagination": {}, "diagnostics": [error]}
            if error
            else parse_lawyer_process_report(body, base_url)
        )
        return _redact(
            {
                "success": not parsed["diagnostics"],
                "source": "eproc",
                "portal": portal,
                "scope": "lawyer_processes",
                **parsed,
            }
        )
    finally:
        if owned:
            session.close()


def _latest(events):
    if not events:
        return []

    def day(item):
        value = item.get("dateTime", item.get("date", "")) if isinstance(item, dict) else ""
        return (
            re.search(r"\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2}", str(value)).group(0)
            if re.search(r"\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2}", str(value))
            else str(value)
        )

    newest = day(events[0])
    return [event for event in events if day(event) == newest]


def _merge_events(existing, incoming):
    merged = []
    seen = set()
    for event in [*existing, *incoming]:
        if not isinstance(event, dict):
            continue
        marker = event.get("id") or (event.get("dateTime"), event.get("description"), event.get("label"))
        if marker in seen:
            continue
        seen.add(marker)
        merged.append(event)
    return merged


def _event_pages(session, detail_url, detail, initial_events, *, portal):
    request_info = detail.get("eventRequest")
    if not isinstance(request_info, dict):
        return initial_events, {"complete": False, "loadedPages": 0}, ["event_refresh_unavailable"]
    event_url = request_info.get("url")
    data = request_info.get("data")
    if not isinstance(event_url, str) or not isinstance(data, dict):
        return initial_events, {"complete": False, "loadedPages": 0}, ["event_refresh_unavailable"]
    data = dict(data)
    event_url = urljoin(detail_url, event_url)
    require_route(event_url, AJAX_ACTIONS, ajax=True, portal=portal)
    diagnostics = []
    events = list(initial_events)
    pagination = {"complete": False, "loadedPages": 0}
    try:
        response, body = request(session, "POST", event_url, data=data, ajax=True, portal=portal)
        if response.status_code != 200 or _response_error(response, body):
            raise RuntimeError("event_refresh_failed")
        parsed = parse_event_response(body, portals.eproc(portal).base_url)
        if not isinstance(parsed, dict):
            raise RuntimeError("event_refresh_failed")
        events = _merge_events(events, parsed.get("events", []))
        pagination = dict(parsed.get("pagination") or {})
        pagination["loadedPages"] = 1
        next_url = pagination.get("url")
        total = pagination.get("totalPages")
        if next_url and isinstance(total, int) and total > 1:
            for page in range(1, min(total, MAX_EVENT_PAGES)):
                page_url = urljoin(event_url, next_url)
                page_ajax = "acao_ajax=" in page_url
                require_route(page_url, AJAX_ACTIONS if page_ajax else READ_ACTIONS, ajax=page_ajax, portal=portal)
                page_data = dict(data)
                page_data["pagina"] = str(page)
                page_response, page_body = request(
                    session, "POST", page_url, data=page_data, ajax=page_ajax, portal=portal
                )
                if page_response.status_code != 200 or _response_error(page_response, page_body):
                    raise RuntimeError("event_refresh_failed")
                page_result = parse_event_response(page_body, portals.eproc(portal).base_url)
                if not isinstance(page_result, dict):
                    raise RuntimeError("event_refresh_failed")
                events = _merge_events(events, page_result.get("events", []))
                pagination = dict(page_result.get("pagination") or pagination)
                pagination["loadedPages"] = page + 1
                next_url = pagination.get("url", next_url)
        loaded = pagination.get("loadedPages", 1)
        pagination["complete"] = bool(total is not None and loaded >= total) if total else not next_url
        if total and total > MAX_EVENT_PAGES:
            pagination["complete"] = False
            diagnostics.append("event_pagination_limit")
    except (RuntimeError, RequestException):
        diagnostics.append("event_refresh_failed")
        pagination["complete"] = False
    return events, pagination, diagnostics


def fetch_process(
    process_number,
    scope="page",
    document_ids=None,
    *,
    session=None,
    panel_html=None,
    panel_url=None,
    portal,
    save=False,
):
    portal = portals.eproc(portal).key  # aliases in, current key out
    if not isinstance(process_number, str) or not CNJ.fullmatch(process_number):
        raise ValueError("process_number must be a formatted CNJ number")
    if scope not in ("latest", "page"):
        raise ValueError("scope must be latest or page")
    if document_ids is not None and scope != "page":
        raise ValueError("document_ids requires scope=page")
    owned = session is None
    if owned:
        session, panel_html, panel_url = authenticate(portal)
    try:
        base_url = portals.eproc(portal).base_url
        panel = parse_panel(panel_html, base_url)
        search = _search_endpoint(panel_html, panel)
        search_url = urljoin(panel_url or base_url, search["action"])
        require_route(search_url, portal=portal)
        if _route_action(search_url) != "processo_pesquisa_rapida":
            raise RuntimeError("Unexpected eproc process search action")
        data = {
            search["numberField"]: process_number,
            search["submitField"]: search.get("submitValue") or "",
            search["returnField"]: search["returnValue"],
        }
        search_response, search_body = request(session, "POST", search_url, data=data, portal=portal)
        if search_response.status_code not in REDIRECTS:
            error = _response_error(search_response, search_body) or "process_search_failed"
            return {
                "success": False,
                "source": "eproc",
                "portal": portal,
                "processNumber": process_number,
                "diagnostics": [error],
            }
        location = search_response.headers.get("Location")
        if not location:
            raise RuntimeError("eproc process search redirect had no location")
        detail_url = urljoin(search_response.url or search_url, location)
        require_route(detail_url, portal=portal)
        if _route_action(detail_url) != "processo_selecionar":
            raise RuntimeError("Unexpected eproc process search redirect action")
        detail_response, detail_body = request(session, "GET", detail_url, portal=portal)
        error = _response_error(detail_response, detail_body)
        if error:
            return {
                "success": False,
                "source": "eproc",
                "portal": portal,
                "processNumber": process_number,
                "diagnostics": [error],
            }
        detail = parse_process_detail(detail_body, base_url)
        if detail.get("number") != process_number:
            return {
                "success": False,
                "source": "eproc",
                "portal": portal,
                "processNumber": process_number,
                "diagnostics": ["process_not_found"],
            }
        events, pagination, event_diagnostics = _event_pages(
            session, detail_url, detail, detail.get("events", []), portal=portal
        )
        selected_events = _latest(events) if scope == "latest" else events
        documents = {document.get("id"): document for document in detail.get("documents", []) if document.get("id")}
        for event in events:
            for document in event.get("documents", []):
                if document.get("id"):
                    documents[document["id"]] = document
        document_inventory = list(documents.values())
        internal = {**detail, "events": selected_events, "documents": document_inventory}
        attachments = []
        if document_ids is not None:
            if read_selected_documents is None:
                raise RuntimeError("eproc document adapter is unavailable")
            attachments = read_selected_documents(internal, document_ids, session=session, portal=portal, save=save)
        result = {
            "success": True,
            "source": "eproc",
            "portal": portal,
            "processNumber": process_number,
            "scope": scope,
            "scopeSemantics": "latest_date_bucket" if scope == "latest" else "first_timeline_page",
            "metadata": {
                **{
                    key: value
                    for key, value in detail.items()
                    if key not in {"parties", "events", "documents", "eventRequest"}
                },
                # Counts cover every loaded event page, not just the first one.
                "eventCount": len(events),
                "documentCount": len(document_inventory),
            },
            "parties": detail.get("parties", []),
            "timeline": selected_events,
            "documentInventory": document_inventory,
            "attachments": attachments,
            "pagination": pagination,
            "diagnostics": event_diagnostics,
        }
        return _redact(result)
    finally:
        if owned:
            session.close()


def main():
    parser = argparse.ArgumentParser(description="Read eproc process data")
    subparsers = parser.add_subparsers(dest="command", required=True)
    queue = subparsers.add_parser("queue")
    queue.add_argument("--portal", choices=portals.keys("eproc"), required=True)
    queue.add_argument("--queue", choices=("open_deadlines", "pending"), required=True)
    processes = subparsers.add_parser("processes", help="List the first Relação de Processos page with default filters")
    processes.add_argument("--portal", choices=portals.keys("eproc"), required=True)
    process = subparsers.add_parser("process")
    process.add_argument("--portal", choices=portals.keys("eproc"), required=True)
    process.add_argument("--process-number", required=True)
    process.add_argument("--scope", choices=("latest", "page"), default="page")
    process.add_argument("--document-id", action="append")
    args = parser.parse_args()
    enable_request_logging()
    load_env_file(".env")
    with redirect_stdout(sys.stderr):
        if args.command == "queue":
            result = list_notices(args.queue, portal=args.portal)
        elif args.command == "processes":
            result = list_processes(portal=args.portal)
        else:
            result = fetch_process(args.process_number, args.scope, args.document_id, portal=args.portal)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result.get("success"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
