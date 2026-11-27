"""Read-only eproc client.

The browser is used only for login. All portal reads happen through the
transferred cookie jar while this module enforces the small observed route
surface.
"""

import argparse
import json
import re
import sys
import time
import warnings
import weakref
from contextlib import redirect_stdout
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from curl_cffi.requests.exceptions import RequestException

from jurisync import customers, portals
from jurisync.connectors.eproc import ciencia
from jurisync.connectors.eproc.auth import login, portal_totp_account
from jurisync.connectors.eproc.certificate import cert_login
from jurisync.connectors.eproc.documents import read_selected_documents
from jurisync.connectors.eproc.parsers import (
    PRAZO_FILTER,
    additional_information_request,
    parse_additional_information,
    parse_event_response,
    parse_hearing_list,
    parse_lawyer_process_report,
    parse_panel,
    parse_panel_counters,
    parse_process_detail,
    parse_process_exams,
    parse_process_hearings,
    parse_process_list_page,
    process_links,
    report_filter_request,
    report_page_request,
)
from jurisync.core import credentials, intimacao
from jurisync.core.http import IMPERSONATE, REDIRECTS, TIMEOUT, enable_request_logging, log_request, require_url
from jurisync.core.redaction import drop_keys
from jurisync.core.responses import listing
from jurisync.core.session_pool import MissingLogin, SessionExpired
from jurisync.customers import load_env_file

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
    # Parts of the open-deadline queue: the urgent deadlines (``urgente=true``) and those expiring today
    # (``vence_hoje=S``), each a link of its own in the panel.
    "urgent": "citacao_intimacao_prazo_aberto_listar",
    "due_today": "citacao_intimacao_prazo_aberto_listar",
}
QUEUE_ACTIONS = set(QUEUE_KINDS.values())
# The narrowed links of the open-deadline queue (``parsers.LINK_VARIANTS``).
QUEUE_VARIANTS = {"urgent": "urgente", "due_today": "vence_hoje"}
# Where the queue links sit in the panel's "citacao/intimacao" state payload (TJSP and other multi-context panels
# draw the counters through an ajax call instead of static links).
STATE_LINKS = {
    "open_deadlines": "intimacaoAberto",
    "pending": "intimacaoPendente",
    "pending_urgent": "intimacaoPendenteUrgente",
    "pending_agreement": "intimacaoPendenteAcordo",
    "art334": "citacaoArt334",
    "expired_deadline": "decursoPrazo",
    "hearing_notice": "intimacaoPauta",
    "urgent": "intimacaoAbertoUrgente",
}
PROCESS_REPORT_ACTION = "relatorio_processo_procurador_listar"
# The panel's lists of upcoming audiências: hearings, and conciliation hearings.
HEARING_LISTS = {"audiencia": "audiencia_futura_listar", "conciliacao": "audiencia_futura_conciliacao_listar"}
READ_ACTIONS = QUEUE_ACTIONS | {
    PROCESS_REPORT_ACTION,
    "painel_adv_listar",
    "painel_advogado_listar",  # TNU names its lawyer panel this way (auth.PANEL_MARKERS)
    "processo_pesquisa_rapida",
    "processo_selecionar",
    "processo_consulta_listar",
    "acessar_documento",
    "acessar_documento_implementacao",
    "processo_selecionar_pagina",  # TJRS pages the event table through the process page instead of ajax
    "processo_montar_informacoes_adicionais",  # the process page's "Informações Adicionais" (do_log=0)
    "audiencia_listar",  # the process' audiências
    "exame_listar",  # the process' perícias (JFRS)
    *HEARING_LISTS.values(),
}
# Where the quick search sends a number the instance does not hold (TJMG 2G, TRF2 2G, 16/11/2026); never requested.
NOT_FOUND_ACTION = "processo_consultar"
AJAX_ACTIONS = {"montar_tabela_eventos_filtrados", "retornar_citacao_intimacao_estado", "listar_painel_advogado"}
MAX_EVENT_PAGES = 5  # event pages read per process; a longer timeline reports event_pagination_limit
# Seconds the pending queues read for one session answer the next process reads; opening a document drops them,
# since the intimação it belongs to may no longer be pending.
PENDING_TTL = 120
clock = time.monotonic
_pending = weakref.WeakKeyDictionary()  # session -> (read at, queues)


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
    with warnings.catch_warnings():
        # The panel's counters answer XML; it is only searched for a login form here.
        warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
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


def send(session, method, url, *, data=None, ajax=False, portal, headers=None, stream=False):
    """The one guarded request to the portal: allowlisted route, no redirects, shared fingerprint and timeout."""
    require_route(url, AJAX_ACTIONS if ajax else READ_ACTIONS, ajax=ajax, portal=portal)
    try:
        response = session.request(
            method,
            url,
            data=data,
            headers={"Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8", **(headers or {})},
            allow_redirects=False,
            impersonate=IMPERSONATE,
            timeout=TIMEOUT,
            **({"stream": True} if stream else {}),
        )
    except RequestException:
        raise RuntimeError("eproc request failed") from None
    log_request(method, url, getattr(response, "status_code", None))
    return response


def request(session, method, url, *, data=None, ajax=False, portal):
    response = send(session, method, url, data=data, ajax=ajax, portal=portal)
    return response, _decode(response)


def _credentials(portal):
    config = portals.eproc(portal)
    username, password = config.env("USERNAME"), config.env("PASSWORD")
    if not username or not password:
        raise MissingLogin(f"Set {config.env_name('USERNAME')} and {config.env_name('PASSWORD')} in .env")
    return username, password


def uses_a3(portal):
    """<PREFIX>_AUTH=a3 for one portal, EPROC_AUTH=a3 for all, or ``a3_totp`` declared in the customer's
    portals.toml: log in with the A3 token instead of the password."""
    config = portals.eproc(portal)
    if (config.env("AUTH") or credentials.env("EPROC_AUTH")).strip().lower() == "a3":
        return True
    return customers.declared_method(config) == "a3_totp"


def auth_method(portal):
    """How this install logs in to the portal: the A3 token (``uses_a3``); else the method the customer's
    ``portals.toml`` declares (a ``certificate_*`` method logs in with the A1, any other with the password); without a
    declaration, the A1 where the portal opts in with ``<PREFIX>_CERT=true`` and the password otherwise."""
    config = portals.eproc(portal)
    if uses_a3(portal):
        return "a3_totp"
    declared = customers.declared_method(config)
    if declared:
        return declared
    return "certificate_totp" if config.uses_certificate() else "browser_totp"


def authenticate(portal):
    config = portals.eproc(portal)
    certificate = uses_a3(portal)
    if certificate and not (config.env("USERNAME") and config.env("PASSWORD")):
        from jurisync.connectors.eproc import a3

        return a3.login(portal)
    if auth_method(portal).startswith("certificate"):
        return cert_login(portal)
    username, password = _credentials(portal)
    account = portal_totp_account(portal)
    # The classic form takes the password alone ("Entrar") or the password plus the certificate ("Certificado
    # Digital"); the certificate route is taken only with <PREFIX>_AUTH=a3. Log in over HTTP when the portal shows the
    # classic form (nothing sent otherwise); a headless browser hangs on the certificate picker.
    from jurisync.connectors.eproc import http_login

    try:
        return http_login.login(username, password, account, portal=portal, certificate=certificate)
    except http_login.NotClassicLogin:
        pass
    if certificate:
        from jurisync.connectors.eproc import a3

        try:
            return a3.login(portal)
        except a3.NoKeycloakCertificate:
            pass  # classic form behind a bot challenge (TRF6): the browser posts the password, then the token
    return login(username, password, account, portal=portal, certificate=certificate)


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


def _state_endpoint(session, panel, queue, *, panel_url, portal):
    """The queue link the panel's own state payload advertises for the logged-in user, or None.

    Some panels (TJSP) show the queue counters through ``retornar_citacao_intimacao_estado`` (JSON) and keep only a
    "deadlines expiring today" link in the page. The first state tab is the user's own context.
    """
    return _state_link(_state_links(session, panel, panel_url=panel_url, portal=portal), queue, portal)


def _state_links(session, panel, *, panel_url, portal):
    """The queue links of the user's own context in the panel's state payload, or None when it has none."""
    endpoint = panel.get("stateEndpoint") if isinstance(panel, dict) else None
    if not endpoint:
        return None
    target = urljoin(panel_url or portals.eproc(portal).base_url, endpoint)
    require_route(target, AJAX_ACTIONS, ajax=True, portal=portal)
    response, body = request(session, "GET", target, ajax=True, portal=portal)
    if _response_error(response, body):
        return None
    try:
        users = json.loads(body).get("usuarios") or []
        links = (users[0] if users else {}).get("links")
    except (ValueError, AttributeError):
        return None
    return links if isinstance(links, dict) else None


def _state_link(links, queue, portal):
    link = (links or {}).get(STATE_LINKS.get(queue))
    if not isinstance(link, str) or _route_action(urljoin(portals.eproc(portal).base_url, link)) != QUEUE_KINDS[queue]:
        return None
    if _variant(link) != QUEUE_VARIANTS.get(queue):
        return None
    return link


def _variant(link):
    """The narrowing parameter of a queue link (``urgente``, ``vence_hoje``), or None for the full list."""
    query = parse_qs(urlsplit(link).query)
    return next((name for name in QUEUE_VARIANTS.values() if name in query), None)


def _queue_endpoint(session, panel, queue, *, panel_url, portal):
    """The link of ``queue``: the panel's own link, else the one its state payload advertises; None without one."""
    if queue not in QUEUE_KINDS:
        raise ValueError(f"queue must be one of {', '.join(QUEUE_KINDS)}")
    variant = QUEUE_VARIANTS.get(queue)
    if variant:
        endpoint = ((panel.get("variants") or {}).get(QUEUE_KINDS[queue]) or {}).get(variant)
    else:
        try:
            endpoint = _panel_endpoint(panel, queue)
        except RuntimeError:
            endpoint = None
    # A narrowed link ("vence_hoje=S") lists only part of the queue; prefer the full link of the state payload.
    if (endpoint is None or _variant(endpoint) != variant) and queue in STATE_LINKS:
        endpoint = _state_endpoint(session, panel, queue, panel_url=panel_url, portal=portal) or endpoint
    if endpoint is None or _variant(endpoint) != variant:
        return None
    return endpoint


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


def _iso_date(value):
    """``dd/mm/aaaa[ hh:mm[:ss]]`` -> ``aaaa-mm-dd``; None when the text holds no date."""
    found = re.search(r"(\d{2})/(\d{2})/(\d{4})", value or "")
    return f"{found[3]}-{found[2]}-{found[1]}" if found else None


def situation(item, queue):
    """The shared ``situacao``/``prazoFinal`` keys (``core.intimacao``) of one queue row.

    A pending row whose deadline start reads "Aguarda publicação no DJEN" (every pending TJMG row, 27/11/2026) waits
    for the publication, which gives the ciência; the portal does not let it be opened early.
    """
    end = _iso_date(item.get("deadlineEnd"))
    if queue in ciencia.PENDING_QUEUES:
        waiting = ciencia.awaiting_publication(item)
        return intimacao.fields(intimacao.AWAITING_PUBLICATION if waiting else intimacao.AWAITING_CIENCIA)
    if queue in ("open_deadlines", "urgent", "due_today"):
        return intimacao.fields(intimacao.RUNNING if end else intimacao.UNKNOWN, end, "portal")
    if queue == "expired_deadline":
        return intimacao.fields(intimacao.EXPIRED, end, "portal")
    return intimacao.fields(intimacao.UNKNOWN, end, "portal")


def _situated(items, queue):
    pending = queue in ciencia.PENDING_QUEUES
    return [{**item, "queue": queue, "cienciaPendente": pending, **situation(item, queue)} for item in items]


def list_notices(queue=None, *, session=None, panel_html=None, panel_url=None, portal):
    """One queue's rows; without ``queue``, what needs the lawyer: the pending queues and the open deadlines together.

    Every row carries ``situacao``, ``prazoFinal`` and ``prazoFinalTipo`` (``core.intimacao``) and its ``queue``.
    """
    portal = portals.eproc(portal).key  # aliases in, current key out
    if queue is not None and queue not in QUEUE_KINDS:
        raise ValueError(f"queue must be one of {', '.join(QUEUE_KINDS)}")
    owned = session is None
    if owned:
        session, panel_html, panel_url = authenticate(portal)
    try:
        base_url = portals.eproc(portal).base_url
        panel = parse_panel(panel_html, base_url)
        if queue is None:
            return _attention(session, panel, panel_url=panel_url, portal=portal)
        endpoint = _queue_endpoint(session, panel, queue, panel_url=panel_url, portal=portal)
        if endpoint is None:
            raise RuntimeError(f"Authenticated panel did not expose the {queue} queue")
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
            "items": _redact(_situated(items, queue)),
            "pagination": _redact(parsed.get("pagination", {})),
            "diagnostics": [],
        }
    finally:
        if owned:
            session.close()


def _attention(session, panel, *, panel_url, portal):
    """The pending queues and the open-deadline queue in one list, each intimação once."""
    queues = list(pending_queues(session, panel, panel_url=panel_url, portal=portal))
    base_url = portals.eproc(portal).base_url
    endpoint = _queue_endpoint(session, panel, "open_deadlines", panel_url=panel_url, portal=portal)
    if endpoint is None:
        queues.append(("open_deadlines", [], False, "the panel did not expose the open_deadlines queue"))
    else:
        url = urljoin(panel_url or base_url, endpoint)
        require_route(url, {QUEUE_KINDS["open_deadlines"]}, portal=portal)
        response, body = request(session, "GET", url, portal=portal)
        error = _response_error(response, body)
        if error == "session_expired":
            raise SessionExpired("eproc session expired")
        if error:
            queues.append(("open_deadlines", [], False, f"the open_deadlines queue answered {error}"))
        else:
            parsed = parse_process_list_page(body, base_url)
            complete = bool((parsed.get("pagination") or {}).get("complete"))
            queues.append(("open_deadlines", parsed.get("items", []), complete, None))
    # A row an earlier queue already listed is the same intimação; rows of one queue are all kept (the checkbox value
    # of the open-deadline queue is the process, shared by its intimações: JFRS and TJMG, 27/11/2026).
    items, seen = [], set()
    for queue, rows, _, _ in queues:
        keys = [(row.get("number"), row.get("deadlineEvent"), row.get("requestedAt")) for row in rows]
        items.extend(row for row, key in zip(_situated(rows, queue), keys, strict=True) if key not in seen)
        seen.update(keys)
    problems = [problem for *_, problem in queues if problem]
    return {
        "success": any(not problem for *_, problem in queues),
        "source": "eproc",
        "portal": portal,
        "queue": None,
        "queues": [
            {"queue": queue, "count": len(rows), "complete": complete, **({"problem": problem} if problem else {})}
            for queue, rows, complete, problem in queues
        ],
        "items": _redact(items),
        "pagination": {
            "itemCount": len(items),
            "complete": not problems and all(complete for _, _, complete, _ in queues),
        },
        "diagnostics": problems,
    }


def pending_queues(session, panel, *, panel_url, portal):
    """``[(queue, items, complete, problem)]``: the lawyer's pending citação/intimação queues.

    ``problem`` is set when a queue could not be read. The main pending queue must be advertised by the panel; the
    other kinds are read when it shows them. A reading without problems is reused for ``PENDING_TTL`` seconds by the
    same session, until ``forget_pending``.
    """
    cached = _pending.get(session)
    if cached is not None and clock() - cached[0] < PENDING_TTL:
        return cached[1]
    base_url = portals.eproc(portal).base_url
    queues, state = [], False
    for queue in ciencia.PENDING_QUEUES:
        try:
            try:
                endpoint = _panel_endpoint(panel, queue)
            except RuntimeError:
                if state is False:
                    state = _state_links(session, panel, panel_url=panel_url, portal=portal)
                endpoint = _state_link(state, queue, portal)
            if endpoint is None:
                if queue == "pending":
                    queues.append((queue, [], False, "the panel did not expose the pending queue"))
                continue
            url = urljoin(panel_url or base_url, endpoint)
            require_route(url, {QUEUE_KINDS[queue]}, portal=portal)
            response, body = request(session, "GET", url, portal=portal)
        except RuntimeError:
            queues.append((queue, [], False, f"the {queue} queue request failed"))
            continue
        error = _response_error(response, body)
        if error == "session_expired":
            raise SessionExpired("eproc session expired")  # nothing was opened yet: the read can be repeated
        if error:
            queues.append((queue, [], False, f"the {queue} queue answered {error}"))
            continue
        parsed = parse_process_list_page(body, base_url)
        queues.append((queue, parsed.get("items", []), bool((parsed.get("pagination") or {}).get("complete")), None))
    if not any(problem for *_, problem in queues):
        _pending[session] = (clock(), queues)
    return queues


def forget_pending(session):
    _pending.pop(session, None)


def pending_notices(session, panel, process_number, *, panel_url, portal):
    """``(rows, problem)``: this process' rows in the lawyer's pending citação/intimação queues.

    ``problem`` is set when a queue could not be read to its end, so an empty ``rows`` does not prove that nothing is
    pending.
    """
    wanted = re.sub(r"\D", "", process_number)
    rows, problem = [], None
    for queue, items, complete, error in pending_queues(session, panel, panel_url=panel_url, portal=portal):
        if error:
            problem = problem or error
            continue
        found = [item for item in items if re.sub(r"\D", "", item.get("number") or "") == wanted]
        rows.extend({**item, "queue": queue} for item in found)
        if not found and not complete:
            problem = problem or f"the {queue} queue was not read to its end"
    return rows, problem


# The panel's counter fieldsets (``listar_painel_advogado``): a label for the fieldset and for each counter. A name
# missing here is shown as the portal sends it.
PANEL_COUNTERS = {
    "fldCitacaoIntimacaoAdvogado": (
        "Citações/Intimações",
        {
            "citacaoIntimacaoUrgente": "Pendentes urgentes",
            "citacaoIntimacao": "Pendentes",
            "citacaoIntimacaoAcordo": "Pendentes com proposta de acordo",
            "processoPrazoAberto": "Prazo aberto",
            "processoPrazoAbertoUrgente": "Prazo aberto urgente",
            "dias": "Dias considerados no decurso de prazo",
            "decurso": "Decurso de prazo",
            "intimacaoPauta": "Intimações de pauta",
        },
    ),
    "fldAudiencia": (
        "Audiências",
        {
            "audienciaFutura": "Futuras",
            "audienciaRealizada": "Realizadas",
            "audienciaNaoRealizada": "Não realizadas",
            "audienciaFuturaConciliacao": "Conciliação: futuras",
            "audienciaRealizadaConciliacao": "Conciliação: realizadas",
            "audienciaNaoRealizadaConciliacao": "Conciliação: não realizadas",
        },
    ),
    "fldPericia": ("Perícias", {"periciaFutura": "Futuras"}),
    "fldConciliacoes": (
        "Conciliações",
        {
            "fcCriados": "Criadas",
            "fcAbertos": "Abertas",
            "fcAcordos": "Acordos",
            "fcRecusas": "Recusas",
            "fcDecursos": "Decursos",
            "fcMensagem": "Mensagens não respondidas",
            "fcPropostas": "Propostas",
        },
    ),
    "fldDepositosJudiciais": ("Depósitos judiciais", {"depositosJudiciaisComSaldo": "Com saldo"}),
    "fldPeticoesIniciaisPreparadas": (
        "Petições iniciais preparadas",
        {
            "peticoesIniciaisPreparadas": "Preparadas",
            "peticoesIniciaisJaPreparadas": "Já preparadas",
            "peticoesIniciaisPreparadasEscritorio": "Preparadas pelo escritório",
            "peticoesIniciaisJaPreparadasEscritorio": "Já preparadas pelo escritório",
        },
    ),
    "fldMovimentacoesPendentesCriadas": (
        "Movimentações pendentes",
        {
            "movimentacoesPendentesCriadas": "Criadas",
            "movimentacoesPreparadasCriadasEscritorio": "Preparadas pelo escritório",
        },
    ),
    "fldRecursosTribunal": ("Recursos ao tribunal", {"agravo": "Agravos"}),
    "fldSubstabelecimento": (
        "Substabelecimentos",
        {"substabelecimentosRecebidos": "Recebidos", "substabelecimentosEfetuados": "Efetuados"},
    ),
    "fldPeticoesSiapro": (
        "Petições Siapro",
        {
            "peticoesSiaproPendentes": "Pendentes",
            "peticoesSiaproJuntadas": "Juntadas",
            "peticoesSiaproCanceladas": "Canceladas",
        },
    ),
}
# The citações/intimações counters of one instance (``retornar_citacao_intimacao_estado``), named as the queues of
# eproc_list_intimacoes.
INSTANCE_COUNTERS = {
    "numCitacaoIntimacaoPendente": "pending",
    "numCitacaoIntimacaoPendenteUrgente": "pending_urgent",
    "numCitacaoIntimacaoPendenteAcordo": "pending_agreement",
    "numCitacaoArt334Pendente": "art334",
    "numCitacaoIntimacaoAberto": "open_deadlines",
    "numCitacaoIntimacaoAbertoUrgente": "urgent",
    "numDecursoPrazo": "expired_deadline",
    "numCitacaoIntimacaoPauta": "hearing_notice",
    "dias": "expiredDeadlineDays",
}


def read_counters(*, session=None, panel_html=None, panel_url=None, portal):
    """The panel's counters: every fieldset the panel fills by ajax, and the citações/intimações counters of each
    instance tab. A fieldset or instance the portal fails to answer is reported in its own entry; the others stand."""
    portal = portals.eproc(portal).key
    owned = session is None
    if owned:
        session, panel_html, panel_url = authenticate(portal)
    try:
        base_url = portals.eproc(portal).base_url
        panel = parse_panel(panel_html, base_url)
        diagnostics = []
        fieldsets = []
        endpoint = panel.get("countersEndpoint")
        if endpoint is None:
            diagnostics.append("panel_counters_unavailable")
        else:
            url = urljoin(panel_url or base_url, endpoint)
            fieldsets = [_fieldset(session, url, name, portal=portal) for name in panel["counterFieldsets"]]
        instances = [_instance(session, entry, panel_url or base_url, portal=portal) for entry in panel["instances"]]
        instances.sort(key=lambda entry: not entry["current"])
        return {
            "success": bool(fieldsets or instances),
            "source": "eproc",
            "portal": portal,
            "panel": fieldsets,
            "instances": instances,
            "dueToday": panel.get("dueTodayCount"),
            "diagnostics": diagnostics,
        }
    finally:
        if owned:
            session.close()


def _ajax(session, url, *, portal, data=None):
    """One panel ajax read; the body, or None when the portal answered an error. A login page expires the session."""
    require_route(url, AJAX_ACTIONS, ajax=True, portal=portal)
    response, body = request(session, "POST" if data is not None else "GET", url, data=data, ajax=True, portal=portal)
    error = _response_error(response, body)
    if error == "session_expired":
        raise SessionExpired("eproc session expired")
    return None if error else body


def _fieldset(session, url, name, *, portal):
    label, names = PANEL_COUNTERS.get(name, (name, {}))
    body = _ajax(session, url, portal=portal, data={"idFieldset": name, "exibePauta": "1"})
    values = parse_panel_counters(body) if body is not None else None
    if values is None:
        return {"fieldset": name, "label": label, "counters": [], "error": "the portal did not answer this fieldset"}
    return {
        "fieldset": name,
        "label": label,
        "counters": [{"key": key, "label": names.get(key, key), "count": value} for key, value in values.items()],
    }


def _instance(session, entry, base, *, portal):
    found = {"instance": entry["instance"], "label": entry["label"] or entry["instance"], "current": entry["current"]}
    body = _ajax(session, urljoin(base, entry["url"]), portal=portal)
    try:
        results = (json.loads(body).get("usuarios") or [{}])[0].get("resultados") or {}
    except (TypeError, ValueError, AttributeError, IndexError):
        return {**found, "counters": {}, "error": "the portal did not answer this instance"}
    counters = {}
    for key, name in INSTANCE_COUNTERS.items():
        value = str(results.get(key, ""))
        if value.isdigit():
            counters[name] = int(value)
    return {**found, "counters": counters}


def list_hearings(*, session=None, panel_html=None, panel_url=None, portal):
    """The upcoming audiências of the panel's two lists (hearings and conciliation), soonest first."""
    portal = portals.eproc(portal).key
    owned = session is None
    if owned:
        session, panel_html, panel_url = authenticate(portal)
    try:
        base_url = portals.eproc(portal).base_url
        panel = parse_panel(panel_html, base_url)
        items, lists, problems = [], [], []
        for kind, action in HEARING_LISTS.items():
            endpoint = panel["actions"].get(action)
            if not endpoint:
                lists.append({"kind": kind, "count": 0, "complete": False, "problem": "not in the panel"})
                continue
            url = urljoin(panel_url or base_url, endpoint)
            require_route(url, {action}, portal=portal)
            response, body = request(session, "GET", url, portal=portal)
            error = _response_error(response, body)
            if error == "session_expired":
                raise SessionExpired("eproc session expired")
            if error:
                problems.append(f"the {kind} list answered {error}")
                lists.append({"kind": kind, "count": 0, "complete": False, "problem": error})
                continue
            parsed = parse_hearing_list(body, base_url)
            lists.append({"kind": kind, "count": len(parsed["items"]), "complete": parsed["pagination"]["complete"]})
            items.extend({"kind": kind, **item, "date": _iso_date(item["start"])} for item in parsed["items"])
        items.sort(key=lambda item: (item["date"] or "9999", item["start"][11:]))
        return _redact(
            listing(
                items,
                key="audiencias",
                success=any("problem" not in entry for entry in lists),
                source="eproc",
                portal=portal,
                lists=lists,
                diagnostics=problems,
            )
        )
    finally:
        if owned:
            session.close()


MAX_REPORT_PAGES = 20


def list_processes(
    *,
    session=None,
    panel_html=None,
    panel_url=None,
    portal,
    max_pages=1,
    include_archived=None,
    prazo=None,
    latest_first=False,
):
    """Read Relação de Processos, up to ``max_pages`` pages of its pager.

    Without filters the portal's defaults apply, which leave baixados out on every portal. ``include_archived`` and
    ``prazo`` (a ``PRAZO_FILTER`` key) search again with the list form, as its Buscar button does; ``latest_first``
    opens the panel's "Últimas movimentações" link (latest movement first) instead of the plain list.

    The portal shows 50 rows a page (500 on TJSC). Later pages are asked for the way the pager does it: the list form
    is posted again with the same criteria and ``hdnInfraPaginaAtual``. ``pagination.complete`` is true only when
    every row the caption counts was read.
    """
    portal = portals.eproc(portal).key
    if not isinstance(max_pages, int) or isinstance(max_pages, bool) or not 1 <= max_pages <= MAX_REPORT_PAGES:
        raise ValueError(f"max_pages must be between 1 and {MAX_REPORT_PAGES}")
    if prazo is not None and prazo not in PRAZO_FILTER:
        raise ValueError(f"prazo must be one of {', '.join(PRAZO_FILTER)}")
    owned = session is None
    if owned:
        session, panel_html, panel_url = authenticate(portal)
    try:
        base_url = portals.eproc(portal).base_url
        panel = parse_panel(panel_html, base_url)
        notes = []
        endpoint = panel["actions"].get(PROCESS_REPORT_ACTION)
        latest = (panel.get("variants") or {}).get(PROCESS_REPORT_ACTION, {}).get("ord_ultimas_movimentacoes")
        if latest_first and latest:
            endpoint = latest
        elif latest_first:
            notes.append("latest_movement_order_unavailable")
        if not endpoint:
            raise RuntimeError("Authenticated panel did not expose Relação de Processos")
        endpoint = urljoin(panel_url or base_url, endpoint)
        require_route(endpoint, {PROCESS_REPORT_ACTION}, portal=portal)
        response, body = request(session, "GET", endpoint, portal=portal)
        error = _response_error(response, body)
        if not error and (include_archived is not None or prazo is not None):
            filtered = report_filter_request(body, base_url, include_archived=include_archived, prazo=prazo)
            if filtered is None:
                raise RuntimeError("Relação de Processos did not show its filter form")
            url, fields = filtered
            url = urljoin(endpoint, url)
            require_route(url, {PROCESS_REPORT_ACTION}, portal=portal)
            response, body = request(session, "POST", url, data=fields, portal=portal)
            error = _response_error(response, body)
        if error:
            parsed = {"items": [], "pagination": {}, "diagnostics": [error]}
        else:
            parsed = parse_lawyer_process_report(body, base_url)
            if not parsed["diagnostics"]:
                parsed = _report_pages(session, body, parsed, endpoint, max_pages=max_pages, portal=portal)
        if latest_first and latest:
            parsed.setdefault("filters", {})["order"] = "latest_movement"
        parsed["notes"] = notes + parsed.get("notes", [])
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


def _report_pages(session, body, first, endpoint, *, max_pages, portal):
    """``first`` plus the following report pages, at most ``max_pages`` in all, with truthful pagination."""
    base_url = portals.eproc(portal).base_url
    items = list(first["items"])
    pagination = dict(first["pagination"])
    total, total_pages = pagination.get("totalItems"), pagination.get("totalPages")
    notes = []
    page = 0
    while (total is None or len(items) < total) and page + 1 < max_pages and total_pages and page + 1 < total_pages:
        page += 1
        following = report_page_request(body, base_url, page)
        if following is None:
            notes.append("report_pager_unavailable")
            break
        url, fields = following
        url = urljoin(endpoint, url)
        require_route(url, {PROCESS_REPORT_ACTION}, portal=portal)
        response, body = request(session, "POST", url, data=fields, portal=portal)
        error = _response_error(response, body)
        if error == "session_expired":
            raise SessionExpired("eproc session expired")  # a list read can be repeated
        parsed = {"diagnostics": [error]} if error else parse_lawyer_process_report(body, base_url)
        if parsed["diagnostics"]:
            notes.append(f"report_page_{page + 1}_failed")
            break
        if parsed["pagination"].get("page") != page or parsed["pagination"].get("totalItems") != total:
            # The portal answered another page or a different list: stop rather than mix lists.
            notes.append(f"report_page_{page + 1}_mismatch")
            break
        items.extend(parsed["items"])
    pagination.update(
        {
            "pagesRead": page + 1 - (1 if notes else 0),
            "itemCount": len(items),
            "complete": total is not None and len(items) >= total,
        }
    )
    if not pagination["complete"] and not notes and total_pages and total_pages > max_pages:
        notes.append(f"stopped at maxPages={max_pages} of {total_pages}")
    return {**first, "items": items, "pagination": pagination, "notes": notes}


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


def _process_extras(session, detail_body, detail_url, *, portal):
    """``(additionalInformation, audiencias, pericias, diagnostics)`` from the lists the process page links to.

    One request each; ``audiencias``/``pericias`` are None when the page has no such button. A list that fails is
    reported in diagnostics and does not stop the read; a login page answer expires the session (nothing was opened).
    """
    base_url = portals.eproc(portal).base_url
    diagnostics = []

    def read(url, data, action, name):
        url = urljoin(detail_url, url)
        require_route(url, {action}, portal=portal)
        response, body = request(session, "POST" if data is not None else "GET", url, data=data, portal=portal)
        error = _response_error(response, body)
        if error == "session_expired":
            raise SessionExpired("eproc session expired")
        if error:
            diagnostics.append(f"{name}_failed")
            return None
        return body

    information = {}
    information_request = additional_information_request(detail_body, base_url)
    if information_request:
        body = read(*information_request, "processo_montar_informacoes_adicionais", "additional_information")
        information = parse_additional_information(body) if body is not None else {}
    links = process_links(detail_body, base_url)
    hearings = exams = None
    if links.get("hearings"):
        body = read(links["hearings"], None, "audiencia_listar", "audiencias")
        hearings = parse_process_hearings(body) if body is not None else None
    if links.get("exams"):
        body = read(links["exams"], None, "exame_listar", "pericias")
        exams = parse_process_exams(body) if body is not None else None
    return information, hearings, exams, diagnostics


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
    """Read one process; with ``document_ids`` also the text of those documents.

    The documents are always read. The result always carries the ``ciencia`` block (``ciencia.notice``), which the
    caller must relay to the user: opening the documents may have registered the ciência and started the deadline.
    """
    portal = portals.eproc(portal).key  # aliases in, current key out
    if not isinstance(process_number, str) or not portals.CNJ.fullmatch(process_number):
        raise ValueError("process_number must be a formatted CNJ number")
    if scope not in ("latest", "page"):
        raise ValueError("scope must be latest or page")
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
        require_route(detail_url, READ_ACTIONS | {NOT_FOUND_ACTION}, portal=portal)
        if _route_action(detail_url) == NOT_FOUND_ACTION:
            # A number this instance does not hold sends the quick search to the Consulta Processual form.
            return {
                "success": False,
                "source": "eproc",
                "portal": portal,
                "processNumber": process_number,
                "diagnostics": ["process_not_found"],
            }
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
        information, hearings, exams, extra_diagnostics = _process_extras(
            session, detail_body, detail_url, portal=portal
        )
        documents = {document.get("id"): document for document in detail.get("documents", []) if document.get("id")}
        for event in events:
            for document in event.get("documents", []):
                if document.get("id"):
                    documents[document["id"]] = document
        document_inventory = list(documents.values())
        internal = {**detail, "events": selected_events, "documents": document_inventory}
        attachments = []
        # Read the pending queues before opening anything: afterwards the intimação may no longer be pending.
        notices, problem = pending_notices(session, panel, process_number, panel_url=panel_url, portal=portal)
        block = ciencia.notice(
            process_number,
            document_ids,
            notices=notices,
            problem=problem,
            events=events,
            events_complete=bool(pagination.get("complete")) and not event_diagnostics,
        )
        if document_ids is not None:
            try:
                attachments = read_selected_documents(
                    internal, document_ids, session=session, portal=portal, save=save, ciencia_checked=True
                )
            finally:
                forget_pending(session)
        result = {
            "success": True,
            "source": "eproc",
            "portal": portal,
            "processNumber": process_number,
            "scope": scope,
            "scopeSemantics": "latest_date_bucket" if scope == "latest" else "timeline_pages",
            "metadata": {
                **{
                    key: value
                    for key, value in detail.items()
                    if key not in {"parties", "events", "documents", "eventRequest"}
                },
                # Counts cover every loaded event page, not just the first one.
                "eventCount": len(events),
                "documentCount": len(document_inventory),
                "additionalInformation": information,
            },
            "parties": detail.get("parties", []),
            "timeline": selected_events,
            "documentInventory": document_inventory,
            "attachments": attachments,
            "pagination": pagination,
            "diagnostics": event_diagnostics + extra_diagnostics,
            "prazos": ciencia.deadlines(events),
            "audiencias": hearings,
            "pericias": exams,
            "ciencia": block,
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
    queue.add_argument("--queue", choices=tuple(QUEUE_KINDS), help="omit for what needs attention")
    processes = subparsers.add_parser("processes", help="List Relação de Processos with default filters")
    processes.add_argument("--portal", choices=portals.keys("eproc"), required=True)
    processes.add_argument("--max-pages", type=int, default=1)
    processes.add_argument("--include-archived", action="store_true", default=None)
    processes.add_argument("--prazo", choices=tuple(PRAZO_FILTER))
    processes.add_argument("--latest-first", action="store_true")
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
            result = list_processes(
                portal=args.portal,
                max_pages=args.max_pages,
                include_archived=args.include_archived,
                prazo=args.prazo,
                latest_first=args.latest_first,
            )
        else:
            result = fetch_process(
                args.process_number,
                args.scope,
                args.document_id,
                portal=args.portal,
            )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result.get("success"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
