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
    BUDGET_NOTE,
    MAX_HEARING_PAGES,
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
from jurisync.connectors.eproc.consulta import MAX_SEARCH_PAGES, search_processes, search_term
from jurisync.connectors.eproc.filtros import by_process, intimation_dates, page_count, process_digits
from jurisync.connectors.eproc.listas import (
    CUSTAS,
    MAX_MESSAGE_PAGES,
    MESSAGE_FILTERS,
    PREPARED,
    list_custas,
    list_messages,
    list_paradas,
    list_prepared,
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
from jurisync.core.responses import DATES, ENVELOPE, dated, listing
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
    Process = Annotated[  # noqa: N806 - an argument type shared by the list tools
        str | None,
        Field(description="Only this process (CNJ number, e.g. '5000001-00.2026.4.04.7200'), among the rows read."),
    ]
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

    QUEUE_DATES = ("requestedAt", "deadlineStart", "deadlineEnd", "sessionStart", "sessionEnd")

    def enveloped(result, dates, max_pages=None, process_number=None):
        """The common list envelope, the items' ``dates`` in ISO; ``next`` only when pages remain that a larger maxPages
        reaches (the pager's count, or one estimated from the rows of a page: ``filtros.page_count``).

        With ``process_number`` only that process' rows stay (``filtros.by_process``)."""
        result, total = by_process(result, process_number)
        for item in result["items"]:
            dated(item, *dates)
        pagination = result.get("pagination") or {}
        pages = page_count(pagination) or 0
        more = None
        budget = BUDGET_NOTE in result.get("notes", [])
        if max_pages and not pagination.get("complete") and not budget and max_pages < min(pages, MAX_REPORT_PAGES):
            more = {"maxPages": min(pages, MAX_REPORT_PAGES)}
        return {**result, **listing(result["items"], total=total, more=more)}

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
            "the DJEN publication; it is about this row, while the ciência lock of eproc_get_process covers the "
            "whole process: a false row does not mean the process' documents may be opened), "
            "processNumber (also number, its old name), court (juízo), parties, class, subject, deadlineEvent, requestedAt, deadlineStart and "
            "deadlineEnd. Counts of every queue: eproc_get_counters. "
            + DATES
            + " "
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
        processNumber: Process = None,
    ) -> dict[str, Any]:
        with tool_errors("Eproc"):
            wanted = process_digits(processNumber)
        result = call(portal, lambda **context: list_notices(queue, **context))
        return enveloped(result, QUEUE_DATES, process_number=wanted)

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
        title="List Eproc audiências",
        description=(
            "List the lawyer's audiências in an Eproc portal from the panel's lists: upcoming (default, soonest "
            "first), held or not held (latest first), each with its conciliation twin. Use it for the hearing "
            "agenda; nothing is opened, so no ciência is registered. The audiências of one process: "
            "eproc_get_process. Read-only. "
            "Cost: one page of each list by default; maxPages reads more of long lists; kind reads only one of the "
            "two. "
            "Each item: kind (audiencia or conciliacao), processNumber (also number, its old name), court (juízo), "
            "parties, event (what was scheduled), location, start and end, date (the day of start; items without one "
            "come last) and observation. lists[] says what each list gave (problem when it could not be read). "
            + DATES
            + " "
            + ENVELOPE
        ),
        annotations=READ_ONLY,
    )
    def eproc_list_audiencias(
        portal: Portal,
        situation: Annotated[
            Literal["upcoming", "held", "not_held"],
            Field(
                description=(
                    "'upcoming': designada, redesignada, prorrogada, adiada, antecipada; 'held': realizadas; "
                    "'not_held': canceladas or não realizadas."
                )
            ),
        ] = "upcoming",
        kind: Annotated[
            Literal["audiencia", "conciliacao"] | None,
            Field(description="Only the hearings list or only the conciliation list; both when omitted."),
        ] = None,
        processNumber: Annotated[
            str | None,
            Field(description="CNJ number with punctuation: keep only this process' audiências."),
        ] = None,
        maxPages: Annotated[
            int,
            Field(ge=1, le=MAX_HEARING_PAGES, description="Pages of each list to read when the portal pages it."),
        ] = 1,
        offset: Annotated[
            int,
            Field(ge=0, description="Items to skip, from next when an answer was cut to fit the size budget."),
        ] = 0,
    ) -> dict[str, Any]:
        result = call(
            portal,
            lambda **context: list_hearings(
                situation, kind, processNumber, max_pages=maxPages, offset=offset, **context
            ),
        )
        for hearing in result.get("audiencias") or []:
            dated(hearing, "start", "end")
        return result

    @server.tool(
        name="eproc_list_processes",
        title="List Eproc lawyer processes",
        description=(
            "List the authenticated lawyer's processes (Relação de Processos); not a court-wide search. Every portal "
            "leaves baixados (archived) out unless includeArchived=true. prazo filters by the intimação deadline "
            "(aguardando_abertura, aberto, vencido, fechado, sem_prazo) and intimationDateFrom/To by the intimation date "
            "(each filter costs one more request); latestMovementFirst orders by the latest "
            "movement. Rows: processNumber (also number, its old name), parties (pole active/passive; the "
            "procedural role is in eproc_get_process), class, subject, last event, dates, claim value. "
            "pagination.totalItems is "
            "the portal's count; pagination.complete=false means rows remain unread. "
            + DATES
            + " "
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
        intimationDateFrom: Annotated[
            str | None,
            Field(
                pattern=r"^\d{4}-\d{2}-\d{2}$",
                description="YYYY-MM-DD, first intimation day (Data Início; Data da Intimação where it is the only one).",
            ),
        ] = None,
        intimationDateTo: Annotated[
            str | None,
            Field(pattern=r"^\d{4}-\d{2}-\d{2}$", description="YYYY-MM-DD, last intimation day (Data Fim)."),
        ] = None,
        processNumber: Process = None,
    ) -> dict[str, Any]:
        with tool_errors("Eproc"):
            wanted = process_digits(processNumber)
            start, end = intimation_dates(intimationDateFrom, intimationDateTo)
        result = call(
            portal,
            lambda **context: list_processes(
                max_pages=maxPages,
                include_archived=includeArchived,
                prazo=prazo,
                latest_first=latestMovementFirst,
                date_from=start,
                date_to=end,
                **context,
            ),
        )
        return enveloped(result, ("lastEventAt", "filedAt"), maxPages, wanted)

    @server.tool(
        name="eproc_search_processes",
        title="Search Eproc court processes",
        description=(
            "Search the court (Consulta Processual) by party name, CPF/CNPJ, OAB or originating process number, with "
            "exactly one criterion per call. A court-wide public search: it returns other lawyers' and third parties' "
            "processes, unlike eproc_list_processes (the lawyer's own Relação). Read-only.\n"
            "Cost: one request for the search page plus one per result page (maxPages); includeClosed adds baixados.\n"
            "Answer: processes (processNumber, caseClass, parties with pole, subject, court, status, filedAt and the "
            "portal's other columns in extra). " + ENVELOPE + " " + DATES + " Details and documents: eproc_get_process."
        ),
        annotations=READ_ONLY,
    )
    def eproc_search_processes(
        portal: Portal,
        partyName: Annotated[
            str | None, Field(description="Party name, at least name and surname (Nome da Parte).")
        ] = None,
        taxId: Annotated[
            str | None, Field(description="CPF (11 digits) or CNPJ (14), digits with optional . - / punctuation.")
        ] = None,
        oab: Annotated[
            str | None, Field(description="OAB registration: UF and number, e.g. 'SC123456' or '12345/SC'.")
        ] = None,
        originNumber: Annotated[
            str | None, Field(description="Originating or related process number (Originário/Relacionado).")
        ] = None,
        includeClosed: Annotated[bool, Field(description="true also shows baixados (Exibir Baixados).")] = False,
        maxPages: Annotated[int, Field(ge=1, le=MAX_SEARCH_PAGES, description="Result pages to read.")] = 1,
    ) -> dict[str, Any]:
        with tool_errors("Eproc"):
            term = search_term(party_name=partyName, tax_id=taxId, oab=oab, origin_number=originNumber)
        return call(
            portal,
            lambda **context: search_processes(term, include_closed=includeClosed, max_pages=maxPages, **context),
        )

    @server.tool(
        name="eproc_list_mensagens",
        title="List Eproc messages",
        description=(
            "Read the lawyer's Caixa de Mensagens (court notices sent through the portal), unread or read. Only the "
            "list is read: no message is opened, since opening one marks it read. "
            "status picks the portal's own filter; page is the portal's page (0-based) and offset continues a page cut "
            "to fit the answer: follow next. notes says when the portal has rows this call could not reach. "
            "Rows: sentAt (ISO), title, text as the list shows it (CPFs masked). "
            + ENVELOPE
            + " "
            + DATES
            + " Read-only."
        ),
        annotations=READ_ONLY,
    )
    def eproc_list_mensagens(
        portal: Portal,
        status: Annotated[
            Literal[tuple(MESSAGE_FILTERS)],
            Field(description="'unread': não lidas (the portal's default view); 'read': lidas."),
        ] = "unread",
        page: Annotated[
            int, Field(ge=0, le=MAX_MESSAGE_PAGES - 1, description="Portal page to read (0-based), from next.page.")
        ] = 0,
        offset: Annotated[int, Field(ge=0, description="Messages of that page to skip, from next.offset.")] = 0,
    ) -> dict[str, Any]:
        return call(portal, lambda **context: list_messages(status, page, offset, **context))

    @server.tool(
        name="eproc_list_paradas",
        title="List Eproc system outages and deadline suspensions",
        description=(
            "Read the portal's Relatório de Paradas: system outages, whether each one extended deadlines "
            "(prorrogação de prazo), and the dates deadlines were suspended. Use it to check whether a deadline's "
            'final day moved; the portal\'s own "Final Prazo" is what the queues show. One page, no parameters. '
            "paradas: description, prorrogacao (true/false, null when the portal says neither), start, end (ISO); "
            "suspensoes: date (ISO), type. " + ENVELOPE + " " + DATES + " Read-only."
        ),
        annotations=READ_ONLY,
    )
    def eproc_list_paradas(portal: Portal) -> dict[str, Any]:
        return call(portal, list_paradas)

    @server.tool(
        name="eproc_list_peticoes_preparadas",
        title="List Eproc prepared filings",
        description=(
            "List what the lawyer prepared in the portal and has not filed yet: petições iniciais pendentes de "
            "distribuição or movimentações pendentes (petições intercorrentes). A draft left here was not filed, so a "
            "deadline may be at risk. Only the list is read; nothing is distributed or filed. "
            "kind picks the list; one page. "
            "Rows: processNumber (when the row shows one) and columns (each cell under its header, dates ISO). An empty list is reported only when the page says so; another layout is an error. "
            "notes flags that this list was not checked against real rows and any row whose cells did not match the "
            "headers (kept unlabelled in cells). " + ENVELOPE + " " + DATES + " Read-only."
        ),
        annotations=READ_ONLY,
    )
    def eproc_list_peticoes_preparadas(
        portal: Portal,
        kind: Annotated[
            Literal[tuple(PREPARED)],
            Field(
                description="'iniciais': petições iniciais pendentes de distribuição; 'movimentacoes': movimentações pendentes."
            ),
        ] = "iniciais",
    ) -> dict[str, Any]:
        return call(portal, lambda **context: list_prepared(kind, **context))

    @server.tool(
        name="eproc_list_custas",
        title="List Eproc generated payment guias",
        description=(
            "List the guias the user already generated in the portal: custas, or depósitos judiciais. A portal whose "
            "menu has no such list answers not_available. Nothing is generated or paid. "
            "kind picks the list; one page. "
            "Rows: processNumber (when shown) and columns (each cell under its header, dates ISO). An empty list is reported only when the page says so; another layout is an error. "
            "notes flags that this list was not checked against real rows and any row whose cells did not match the "
            "headers (kept unlabelled in cells). " + ENVELOPE + " " + DATES + " Read-only."
        ),
        annotations=READ_ONLY,
    )
    def eproc_list_custas(
        portal: Portal,
        kind: Annotated[
            Literal[tuple(CUSTAS)],
            Field(description="'custas': guias de custas; 'depositos': guias de depósito judicial."),
        ] = "custas",
    ) -> dict[str, Any]:
        return call(portal, lambda **context: list_custas(kind, **context))

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
            "save=true stores each read document (HTML as PDF) in the shared documents folder. Inventory: the timeline is "
            "read up to 5 event pages; when it was not read to its end, or a documentId is not in it, the Árvore (the "
            "case's full document list, nothing opened) is listed too and inventorySource says 'tree' (else 'timeline'). "
            "inventory='tree' always lists it, 'timeline' never (one request less). With the Árvore, documentInventory "
            "shows 150 entries and inventory.next gives the arguments for the rest (inventoryOffset; documentIds empty and "
            "save false, so nothing is opened again); a timeline-only inventory comes whole. Documents found in the Árvore "
            "are opened under the same ciência rule. timeline[].dateTime, documentInventory[].date, metadata.cover.filedAt, "
            "audiencias start/end, prazos[].dateTime and the ciencia block's intimacoes and eventos: " + DATES
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
        inventory: Annotated[
            Literal["auto", "timeline", "tree"],
            Field(description="'auto': list the Árvore only when the timeline is incomplete. 'tree' or 'timeline'."),
        ] = "auto",
        inventoryOffset: Annotated[
            int,
            Field(
                ge=0, description="First documentInventory entry to show when the Árvore was listed (inventory.next)."
            ),
        ] = 0,
    ) -> dict[str, Any]:
        result = call(
            portal,
            lambda **context: fetch_process(
                processNumber,
                scope,
                documentIds or None,
                save=save,
                acknowledge_ciencia=_allow_ciencia(acknowledgeCiencia),
                inventory=inventory,
                inventory_offset=inventoryOffset,
                **context,
            ),
        )
        dated((result.get("metadata") or {}).get("cover") or {}, "filedAt")
        for document in result.get("documentInventory") or []:
            dated(document, "date")
        for event in result.get("timeline") or []:
            dated(event, "dateTime")
        for hearing in result.get("audiencias") or []:
            dated(hearing, "start", "end")
        for prazo in result.get("prazos") or []:
            dated(prazo, "dateTime")
        block = result.get("ciencia") or {}
        for row in block.get("intimacoes") or []:
            dated(row, *QUEUE_DATES)
        for marker in block.get("eventos") or []:
            dated(marker, "dateTime")
        return result

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
