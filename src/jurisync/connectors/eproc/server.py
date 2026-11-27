"""Eproc MCP server for every configured Eproc portal. Read-only toward the portal, except that opening the document
of a pending intimação may register its ciência: eproc_get_process refuses that, with nothing opened, unless the
caller passes acknowledgeCiencia (or the customer set ``[eproc] ciencia_on_read = "allow"``), and always reports
what it did in its ``ciencia`` block.

Each portal keeps its own session: the Selenium login runs once per portal;
later calls reuse the authenticated cookies and re-read the panel. A call the portal answers with its login page
logs in again once and is repeated.
"""

from typing import Annotated, Any, Literal

from pydantic import Field

from jurisync import customers, portals
from jurisync.connectors.eproc.auth import is_panel
from jurisync.connectors.eproc.client import (
    MAX_REPORT_PAGES,
    PRAZO_FILTER,
    QUEUE_KINDS,
    _response_error,
    auth_method,
    authenticate,
    fetch_process,
    list_hearings,
    list_notices,
    list_processes,
    read_counters,
    request,
)
from jurisync.core import certificate, ciencia, intimacao
from jurisync.core.mcp import (
    MAY_GIVE_CIENCIA,
    READ_ONLY,
    JurisyncServer,
    require_success,
    serve,
    tool_errors,
)
from jurisync.core.responses import ENVELOPE, listing
from jurisync.core.session_pool import SessionExpired, SessionPool
from jurisync.customers import ciencia_on_read, load_env_file
from jurisync.portals import PORTAL_HELP, offer_portals


def refresh_panel(portal, value):
    session, _, panel_url = value
    response, body = request(session, "GET", panel_url, portal=portal)
    if _response_error(response, body) or not is_panel(body):
        return None
    return session, body, panel_url


sessions = SessionPool(login=authenticate, refresh=refresh_panel, close=lambda value: value[0].close())


def configured_portals():
    """Eproc portals whose login is set in the current credentials; ``()`` when none is."""

    def ready(config):
        method = auth_method(config.key)
        if method == "a3_totp":
            login = True
        elif method.startswith("certificate"):
            login = certificate.is_configured()
        else:
            login = config.env("USERNAME") and config.env("PASSWORD")
        return (config.env("2FA_DATA") or config.env("2FA_QR")) and login

    return customers.profile_portals(key for key in portals.keys("eproc") if ready(portals.eproc(key)))


def expired(result):
    """A result whose page was the portal's login page. Such a read stopped before any document was opened."""
    if "session_expired" in result.get("diagnostics", []):
        raise SessionExpired("eproc session expired")
    return result


def _allow_ciencia(requested):
    """An explicit acknowledgeCiencia wins; otherwise the customer's ``[eproc] ciencia_on_read`` setting decides."""
    return ciencia_on_read(system="eproc") == "allow" if requested is None else requested


def create_server(keys=None):
    keys = tuple(keys or portals.keys("eproc"))
    Portal = Annotated[Literal[keys], Field(description=PORTAL_HELP)]  # noqa: N806 - built from the configured portals
    Queue = Literal[tuple(QUEUE_KINDS)]  # noqa: N806 - a type built from the queue kinds
    server = JurisyncServer("eproc")

    def call(portal, read):
        with tool_errors("Eproc"):
            return require_success(
                "Eproc",
                sessions.run(
                    portal,
                    lambda value: expired(
                        read(session=value[0], panel_html=value[1], panel_url=value[2], portal=portal)
                    ),
                ),
            )

    def enveloped(result, max_pages=None):
        """The common list envelope; ``next`` only when the portal's pager has pages a larger maxPages reaches."""
        pagination = result.get("pagination") or {}
        pages = pagination.get("totalPages") or 0
        more = None
        if max_pages and not pagination.get("complete") and max_pages < min(pages, MAX_REPORT_PAGES):
            more = {"maxPages": min(pages, MAX_REPORT_PAGES)}
        return {**result, **listing(result.get("items") or [], total=pagination.get("totalItems"), more=more)}

    @server.tool(
        name="eproc_list_intimacoes",
        title="List Eproc intimações",
        description=(
            "List the lawyer's citações/intimações of an Eproc portal. Without queue: what needs attention, the pending "
            "queues (awaiting ciência or the DJEN publication) and the open deadlines together, each intimação once "
            "(queues[] says what each queue gave). With queue: that queue only. "
            + intimacao.DESCRIPTION
            + " Rows also "
            "carry queue, cienciaPendente (true in the pending queues; situacao says whether it awaits the ciência or "
            "the DJEN publication), "
            "processNumber (also number, its old name), court (juízo), parties, class, subject, deadlineEvent, requestedAt, deadlineStart and "
            "deadlineEnd as the portal shows them. Counts of every queue: eproc_get_counters. "
            + ENVELOPE
            + " Read-only: opens no intimação."
        ),
        annotations=READ_ONLY,
    )
    def eproc_list_intimacoes(
        portal: Portal,
        queue: Annotated[
            Queue | None,
            Field(
                description=(
                    "Omit for what needs attention. 'pending': awaiting ciência or publication; 'pending_urgent', "
                    "'pending_agreement', 'art334': the other pending queues; 'open_deadlines': prazo aberto, deadline "
                    "running; 'urgent': open urgent deadlines; 'due_today': deadlines ending today; "
                    "'expired_deadline': deadlines that ended in the last 30 days (decurso de prazo); 'hearing_notice': "
                    "intimações de pauta."
                )
            ),
        ] = None,
    ) -> dict[str, Any]:
        return enveloped(call(portal, lambda **context: list_notices(queue, **context)))

    @server.tool(
        name="eproc_get_counters",
        title="Get Eproc panel counters",
        description=(
            "Read the counters of the lawyer's Eproc panel. panel: each fieldset the panel fills (citações/intimações, "
            "audiências, perícias, conciliações, depósitos judiciais, petições iniciais preparadas, movimentações "
            "pendentes, recursos, substabelecimentos...) with label and count per counter; a fieldset the portal fails "
            "to answer carries error. instances: the citações/intimações counters of every instance tab of the panel "
            "(the portal's own section, the court of appeal, sister sections), named as the eproc_list_intimacoes "
            "queues; current=true is the instance this portal lists, the others are read in their own portal. "
            "dueToday: deadlines ending today. "
            "Read-only: opens nothing."
        ),
        annotations=READ_ONLY,
    )
    def eproc_get_counters(portal: Portal) -> dict[str, Any]:
        return call(portal, lambda **context: read_counters(**context))

    @server.tool(
        name="eproc_list_audiencias",
        title="List Eproc upcoming audiências",
        description=(
            "List the lawyer's upcoming audiências in an Eproc portal, from the panel's two lists (hearings and "
            "conciliation hearings), soonest first. Each: kind (audiencia or conciliacao), processNumber (also number, its old name), court (juízo), "
            "parties, event (what was scheduled), location, start and end as the portal shows them, date (ISO) and "
            "observation. lists[] says how many each list gave. The audiências of one process: eproc_get_process. "
            "Read-only."
        ),
        annotations=READ_ONLY,
    )
    def eproc_list_audiencias(portal: Portal) -> dict[str, Any]:
        return call(portal, lambda **context: list_hearings(**context))

    @server.tool(
        name="eproc_list_processes",
        title="List Eproc lawyer processes",
        description=(
            "List the authenticated lawyer's processes (Relação de Processos); not a court-wide search. Every portal "
            "leaves baixados (archived) out unless includeArchived=true. prazo filters by the intimação deadline "
            "(aguardando_abertura, aberto, vencido, fechado, sem_prazo); latestMovementFirst orders by the latest "
            "movement. Rows: processNumber (also number, its old name), parties (pole active/passive; the "
            "procedural role is in eproc_get_process), class, subject, last event, dates, claim value. "
            "pagination.totalItems is "
            "the portal's count; pagination.complete=false means rows remain unread. "
            + ENVELOPE
            + " Details and documents: eproc_get_process. Read-only."
        ),
        annotations=READ_ONLY,
    )
    def eproc_list_processes(
        portal: Portal,
        maxPages: Annotated[
            int,
            Field(ge=1, le=MAX_REPORT_PAGES, description="Pages to read (50 rows each, more on some portals)."),
        ] = 1,
        includeArchived: Annotated[
            bool | None,
            Field(
                description="true also lists baixados (Listar processos baixados); omitted: the portal's default, without them."
            ),
        ] = None,
        prazo: Annotated[
            Literal[tuple(PRAZO_FILTER)] | None,
            Field(description="Only processes whose intimação deadline is in this state; omitted: all."),
        ] = None,
        latestMovementFirst: Annotated[
            bool,
            Field(description="true: latest movement first (the panel's Últimas movimentações)."),
        ] = False,
    ) -> dict[str, Any]:
        result = call(
            portal,
            lambda **context: list_processes(
                max_pages=maxPages,
                include_archived=includeArchived,
                prazo=prazo,
                latest_first=latestMovementFirst,
                **context,
            ),
        )
        return enveloped(result, maxPages)

    @server.tool(
        name="eproc_get_process",
        title="Get Eproc process context",
        description=(
            "Read an Eproc process: parties, events, document inventory and the texts of documentIds (no document is "
            "opened without them). Ciência: opening a document of a process with a citação/intimação awaiting "
            "ciência in the lawyer's pending queues may register the ciência and start the deadline, so documentIds "
            "are refused with nothing opened (error starts 'ciência pending') without acknowledgeCiencia; when the "
            "queues could not be read in full they are refused too (error starts 'ciência not checked'): retry later "
            "or acknowledge. "
            + ciencia.AUTHORIZATION
            + ". settings.toml [eproc] ciencia_on_read = 'allow' opens them by default. The ciencia block, checked "
            "against the pending queues: estado 'nenhuma' (nothing pending involved), 'pendente' (one awaits "
            "ciência, no document opened), 'dada' (opened with acknowledgeCiencia: the ciência may be registered and "
            "the deadline started) or 'desconhecida' (opened with acknowledgeCiencia, queues not checked; motivo "
            "says why), with intimacoes and an aviso: unless 'nenhuma', tell the user and note it in the task. "
            "prazos: the deadline of every intimação event (eventId, refersTo, party, days, status "
            "aberto/fechado/aguardando_abertura, closedBy, start and end ISO dates, djenAvailable/djenPublished). "
            "metadata.additionalInformation: the Informações Adicionais (justiça gratuita, sigilo, prevenção, valor "
            "da causa...); audiencias and pericias: the process' lists (null where the portal has no such button). "
            "save=true stores each read document (HTML as PDF) in the shared documents folder."
        ),
        annotations=MAY_GIVE_CIENCIA,
    )
    def eproc_get_process(
        portal: Portal,
        processNumber: Annotated[
            str,
            Field(min_length=1, description="CNJ process number with punctuation, e.g. '5004320-76.2026.4.04.7104'."),
        ],
        scope: Annotated[
            Literal["latest", "page"],
            Field(
                description="'page': the timeline, up to 5 event pages (see pagination). 'latest': newest date only."
            ),
        ] = "page",
        documentIds: Annotated[
            list[Annotated[str, Field(min_length=1)]],
            Field(
                max_length=5,
                description="JSON list of document ids from documentInventory (looked up in all of it, whatever the scope).",
            ),
        ] = [],
        save: Annotated[
            bool,
            Field(description="true returns savedAs file names (shared documents folder)."),
        ] = False,
        acknowledgeCiencia: Annotated[
            bool | None,
            Field(description=ciencia.ACKNOWLEDGE),
        ] = None,
    ) -> dict[str, Any]:
        return call(
            portal,
            lambda **context: fetch_process(
                processNumber,
                scope,
                documentIds or None,
                save=save,
                acknowledge_ciencia=_allow_ciencia(acknowledgeCiencia),
                **context,
            ),
        )

    return server


def main():
    load_env_file()
    keys = configured_portals() or portals.keys("eproc")
    sessions.only(keys)
    server = create_server(keys)
    offer_portals(server, keys)
    serve(server, on_exit=sessions.close)


if __name__ == "__main__":
    main()
