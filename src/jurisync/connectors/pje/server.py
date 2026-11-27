"""PJe MCP server: TRF1/TRF5/TJAP process context, the Expedientes panel, Push, and TRF5 drafts.

Authenticated sessions stay in memory per portal for the life of the server,
so consecutive calls skip the password/certificate + TOTP login.
"""

import contextvars
import json
import threading
from datetime import date, timedelta
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints

from jurisync import customers, portals
from jurisync.connectors.pje import filing
from jurisync.connectors.pje.acervo import read_acervo
from jurisync.connectors.pje.audiencias import read_pauta
from jurisync.connectors.pje.auth.sessions import auth_method, is_authenticated
from jurisync.connectors.pje.inbox import read_counters, read_inbox
from jurisync.connectors.pje.process import (
    fetch_process,
    login_portal,
    movement_candidates,
    read_documents,
    require_own_number,
)
from jurisync.connectors.pje.push import read_push
from jurisync.core import certificate, ciencia, credentials, intimacao
from jurisync.core.documents import validate_document_ids
from jurisync.core.errors import InputError, NotFound, WritesDisabled
from jurisync.core.mcp import (
    DESTRUCTIVE,
    MAY_GIVE_CIENCIA,
    READ_ONLY,
    WRITE,
    JurisyncServer,
    require_success,
    sanitize_diagnostics,
    serve,
    tool_error,
    tool_errors,
)
from jurisync.core.responses import TEXT_BUDGET, fit_texts
from jurisync.core.runs import run_directory
from jurisync.core.session_pool import SessionPool
from jurisync.customers import ciencia_on_read, load_env_file
from jurisync.portals import PORTAL_HELP, offer_portals

Portal = Annotated[Literal[portals.keys("pje")], Field(description=PORTAL_HELP)]
RunId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")]
DraftReadStage = Literal["classification", "subject-options", "pending"]
DraftWriteStage = Literal[
    "create",
    "create-fill",
    "fill",
    "resume",
    "subject",
    "author",
    "public-bodies",
    "attorney",
    "characteristics",
    "main-document",
    "supporting-documents",
    "documents",
]


def configured_portals():
    """PJe portals this account can log in to: an authenticator plus the A1 (or the A3 token) where the portal takes
    it, else a username and password. Empty when none is; read through the credential scope (the office, hosted)."""

    def ready(key):
        config, method = portals.pje(key), auth_method(key)
        if method == "a3_totp":
            return True
        if config.pje1x:
            # PJe 1.x (no SSO): the A1 signs the login page's challenge and no authenticator code is asked.
            return method == "certificate_totp" and certificate.is_configured()
        # The national SSO asks the authenticator code on every login, with the A1 or the password alike.
        if not (config.env("2FA_DATA") or config.env("2FA_QR")):
            return False
        if method == "certificate_totp":
            return certificate.is_configured()
        return bool(config.env("USERNAME") and config.env("PASSWORD"))

    return customers.profile_portals(key for key in portals.keys("pje") if ready(key))


sessions = SessionPool(
    login=lambda portal: login_portal(portal)[0],
    refresh=lambda portal, session: session if is_authenticated(session, portal) else None,
    close=lambda session: session.close(),
)


DRAFTS_DISABLED = WritesDisabled(
    "PJe draft writes are disabled for this office.", setting="PJE_ALLOW_DRAFT_WRITES=true"
)


def draft_writes_enabled():
    """Draft writes are opt-in per account: hosted, the office's own setting; locally, the environment."""
    return credentials.env("PJE_ALLOW_DRAFT_WRITES") == "true"


def _allow_ciencia(requested):
    """An explicit acknowledgeCiencia wins; otherwise the customer's ``ciencia_on_read`` setting decides."""
    return ciencia_on_read() == "allow" if requested is None else requested


def _notifications(portal, read, process_number=None):
    with tool_errors("PJe"):
        if process_number:
            require_own_number(portal, process_number)  # before the session: a wrong court never costs a login
        return require_success("PJe", sessions.run(portal, lambda session: read(portal, session=session)))


def _window(date_from, date_to):
    """The pauta's start-date interval: today (or dateFrom) to 90 days after it, unless dateTo says otherwise."""
    first = date.fromisoformat(date_from) if date_from else date.today()
    last = date.fromisoformat(date_to) if date_to else first + timedelta(days=90)
    if last < first:
        raise InputError("dateTo must not be before dateFrom")
    return first.isoformat(), last.isoformat()


def draft_paths(run_id):
    directory = run_directory(run_id)
    return directory / "payload.json", directory / "checkpoint.json"


BACKGROUND_STAGES = frozenset({"create-fill", "fill", "resume"})
# Fills take minutes, longer than a tool call may last, so they run in a thread of this server and report through
# pje_get_draft_status. A fill that fails before its checkpoint exists can only leave its error here. Both are keyed
# by (tenant, runId), the identity the session pool uses: two offices may pick the same run name.
fill_jobs = {}
fill_errors = {}


def _job_key(run_id):
    return (credentials.tenant(), run_id)


def _fill_job(run_id, stage):
    key = _job_key(run_id)  # the job runs in a copy of the caller's context, so this is the caller's tenant
    payload, checkpoint = draft_paths(run_id)
    try:
        # The pooled session is reused and refreshed, so a fill and its retries do not log in again each time.
        with sessions.session("trf5-1g") as session:
            filing.run_stage(stage, payload_path=str(payload), checkpoint_path=str(checkpoint), session=session)
        fill_errors.pop(key, None)
    except BaseException as error:  # noqa: BLE001 - background job boundary: the error is kept for pje_get_draft_status
        # The text goes to the calling agent: no URLs, local paths or session tokens.
        fill_errors[key] = sanitize_diagnostics(f"{type(error).__name__}: {error}")[:300]


def _start_fill(run_id, requested):
    # `resume` is `fill` under the name an agent reaches for after a failure: it continues the run's own draft,
    # skipping the phases already recorded and sending only the documents PJe's list still lacks.
    stage = "fill" if requested == "resume" else requested
    if not draft_writes_enabled():
        raise tool_error("PJe draft", DRAFTS_DISABLED)
    key = _job_key(run_id)
    running = fill_jobs.get(key)
    if running is not None and running.is_alive():
        return {"runId": run_id, "success": True, "stage": requested, "state": "already-running"}
    payload, checkpoint = draft_paths(run_id)
    with tool_errors("PJe draft"):
        filing.validate_fill_payload(json.loads(payload.read_text(encoding="utf-8")))
        if stage == "create-fill" and checkpoint.exists():
            raise tool_error("PJe draft", InputError("This run already has a draft; use stage fill to continue it"))
        if stage == "fill" and not checkpoint.exists():
            raise tool_error("PJe draft", InputError("This run has no draft yet; use stage create-fill"))
    fill_errors.pop(key, None)
    # A new thread starts with an empty context: carry the caller's credential scope (the office, when hosted).
    context = contextvars.copy_context()
    job = threading.Thread(target=context.run, args=(_fill_job, run_id, stage), name=f"pje-fill-{run_id}", daemon=True)
    fill_jobs[key] = job
    job.start()
    return {
        "runId": run_id,
        "success": True,
        "stage": requested,
        "state": "started",
        "next": "Call pje_get_draft_status with this runId to follow progress; it finishes on its own.",
    }


def _draft_stage(label, stage, run_id=None, **options):
    if stage in filing.WRITE_STAGES and not draft_writes_enabled():
        raise tool_error("PJe draft", DRAFTS_DISABLED)
    if run_id is not None:
        payload, checkpoint = draft_paths(run_id)
        options["checkpoint_path"] = str(checkpoint)
        if stage != "subject-options":
            options["payload_path"] = str(payload)
    if stage == "status":  # reads local files only: no login needed
        with tool_errors(label):
            result = filing.run_stage(stage, **options)
    elif stage in filing.WRITE_STAGES:
        # A write is never repeated on its own: a second create would leave two drafts.
        with tool_errors(label), sessions.session("trf5-1g") as session:
            result = filing.run_stage(stage, session=session, **options)
    else:
        with tool_errors(label):
            result = sessions.run("trf5-1g", lambda session: filing.run_stage(stage, session=session, **options))
    return {**({"runId": run_id} if run_id is not None else {}), "success": True, "stage": stage, "result": result}


def create_server():
    server = JurisyncServer("pje")

    @server.tool(
        name="pje_get_process",
        title="Get PJe process context",
        description="Read a PJe process: metadata, parties, timeline and document inventory (ids and titles; read texts with pje_read_documents). expedientes lists each communication per recipient with channel, notice date, days, PJe's deadline (limite) and the open ones (abertos); expedientes.complete=false means it was not read in full. pericias (when the portal has the Perícia tab) lists each perícia: data, hora, periciado, valor, perito, situacao; it omits the place, which only the act designating it carries. audiencias (when the portal has the Audiência tab) lists each hearing: data, hora, tipo, orgaoJulgador, sala, situacao (designada, realizada, cancelada...), acordo, linkSalaVirtual when PJe shows it; the lawyer's hearings across processes come from pje_list_audiencias. expedientes.abertos: not answered, not closed by PJe, and ciência pending or deadline not passed (or not computed yet). pagination.complete is true when every timeline page was read. The timeline is movements, not confirmed publications. Read-only.",
        annotations=READ_ONLY,
    )
    def get_process(
        processNumber: Annotated[
            str,
            Field(min_length=1, description="CNJ process number with punctuation, e.g. '0005231-50.2026.4.05.8203'."),
        ],
        portal: Portal,
        scope: Annotated[
            Literal["latest", "page", "all"],
            Field(description="'page': first timeline page. 'latest': newest date only. 'all': every page, slower."),
        ] = "page",
    ) -> dict[str, Any]:
        with tool_errors("PJe"):
            require_own_number(portal, processNumber)  # before the session: a wrong court never costs a login
            result = sessions.run(
                portal, lambda session: fetch_process(processNumber, scope, portal=portal, session=session)
            )
        result["movement_candidates"] = movement_candidates(result)
        return require_success("PJe", result)

    @server.tool(
        name="pje_read_documents",
        title="Read PJe documents",
        description="Read 1-5 documents of a PJe process by id. Each has its status and text, or a failure saying why it was not read; status extracted_unnumbered: the text omits the process number; short_html: a short cover page, text not vouched for. A short cover (a petição typed as only its title) comes with anexos, the documents attached in the same timeline entry (up to 3; anexos_not_read lists the rest). A Certidão de Intimação also returns its parsed expedientes (recipient, medium, deadline days), which hold the client's deadline. save=true stores the original files in the shared documents folder as savedAs, e.g. to attach to a task. Ciência: reading the act of a communication whose ciência is pending, the documents attached to it, or any document of a process with a pending communication that has no document id may register the party's ciência and start the deadline, so it is refused without acknowledgeCiencia. "
        + ciencia.AUTHORIZATION
        + ". When the expedientes could not be read in full the read is refused with nothing opened (error starts 'ciência not checked'): retry later, or acknowledgeCiencia=true under the same authorization. settings.toml [pje] ciencia_on_read = 'allow' reads them by default. The ciencia block: estado 'nenhuma' (nothing pending read), 'dada' (a pending communication was read: its ciência may be registered and deadline started; see intimacoes) or 'desconhecida' (read with acknowledgeCiencia, expedientes unchecked), with an aviso: unless 'nenhuma', tell the user and note it in the task.",
        # With acknowledgeCiencia a read may register the ciência and start a deadline, and save=true writes a file.
        annotations=MAY_GIVE_CIENCIA,
    )
    def read_documents_tool(
        processNumber: Annotated[str, Field(min_length=1, description="CNJ process number with punctuation.")],
        portal: Portal,
        documentIds: Annotated[
            list[Annotated[str, Field(pattern=r"^[0-9]+$")]],
            Field(
                min_length=1,
                max_length=5,
                description='JSON list of documentId values from pje_get_process, e.g. ["195268244"].',
            ),
        ],
        textLimit: Annotated[int, Field(ge=200, le=100000, description="Max characters per text.")] = 20000,
        offset: Annotated[
            int, Field(ge=0, description="Start each text here: a cut text's truncated.nextOffset reads the rest.")
        ] = 0,
        save: Annotated[
            bool,
            Field(description="true returns savedAs file names."),
        ] = False,
        acknowledgeCiencia: Annotated[
            bool | None,
            Field(description=ciencia.ACKNOWLEDGE),
        ] = None,
    ) -> dict[str, Any]:
        # A repeat after an expired session only re-runs the search and the ciência check: read_documents does not
        # report an expiry once documents are being opened.
        with tool_errors("PJe"):
            require_own_number(portal, processNumber)
            validate_document_ids(documentIds)
            result = sessions.run(
                portal,
                lambda session: read_documents(
                    processNumber,
                    documentIds,
                    portal=portal,
                    session=session,
                    save=save,
                    acknowledge_ciencia=_allow_ciencia(acknowledgeCiencia),
                ),
            )
        reads = [read for document in result.get("documents", []) for read in (document, *document.get("anexos", []))]
        if fit_texts(reads, offset=offset, limit=textLimit):
            result["note"] = (
                f"Texts were cut to fit {TEXT_BUDGET} characters per answer: read the rest with the same documentIds "
                "(or only the cut one) and offset=truncated.nextOffset."
            )
        return require_success("PJe", result)

    @server.tool(
        name="pje_read_expediente_ato",
        title="Read the act behind a PJe expediente",
        description="Open the act of one communication from the process Expedientes (pje_get_process expedientes or pje_list_intimacoes): the expediente box (recipient, days, deadline, closed), the act's full text and its attached documents (documentId, title; read them with pje_read_documents). Acts whose ciência is registered open by default. Opening a pending one may register the party's ciência and start the deadline, so it is refused (ato.error) without acknowledgeCiencia. "
        + ciencia.AUTHORIZATION
        + ". settings.toml [pje] ciencia_on_read = 'allow' opens them by default. The ciencia block: estado 'nenhuma' (already registered, or nothing opened), 'dada' (a pending one opened now: its ciência may be registered and deadline started) or 'pendente' (pending, not opened), with intimacoes and an aviso: unless 'nenhuma', tell the user and note it in the task.",
        # Opening a pending act registers the ciência and starts a deadline: not a read-only, repeatable call.
        annotations=MAY_GIVE_CIENCIA,
    )
    def read_expediente_ato_tool(
        processNumber: Annotated[str, Field(min_length=1, description="CNJ process number with punctuation.")],
        portal: Portal,
        expedienteId: Annotated[
            str,
            Field(
                pattern=r"^[0-9]+$",
                description="Expediente id from pje_list_intimacoes or pje_get_process expedientes.",
            ),
        ],
        textLimit: Annotated[int, Field(ge=200, le=100000, description="Max characters of the act text.")] = 20000,
        acknowledgeCiencia: Annotated[
            bool | None,
            Field(description=ciencia.ACKNOWLEDGE),
        ] = None,
    ) -> dict[str, Any]:
        # An expired session shows up before the act is served, so a repeat never opens it twice.
        with tool_errors("PJe"):
            require_own_number(portal, processNumber)
            result = sessions.run(
                portal,
                lambda session: fetch_process(
                    processNumber,
                    "latest",
                    portal=portal,
                    session=session,
                    ato_expediente=expedienteId,
                    ato_text_limit=textLimit,
                    ato_allow_pending=_allow_ciencia(acknowledgeCiencia),
                ),
            )
        if result.get("success") and result.get("ato") is None:
            # The expedientes could not be read, so the ciência state of this one is unknown: nothing was opened.
            result["ato"] = {
                "expedienteId": expedienteId,
                "error": "the process' expedientes could not be read, so the act was not opened; try again later",
            }
        return require_success(
            "PJe",
            {k: result[k] for k in ("success", "source", "portal", "processNumber", "ato", "ciencia", "diagnostics")}
            | {
                "expediente": next(
                    (
                        i
                        for i in (result.get("expedientes") or {}).get("items", [])
                        if i["expedienteId"] == expedienteId
                    ),
                    None,
                )
            },
        )

    @server.tool(
        name="pje_get_counters",
        title="List PJe Expedientes counters",
        description="Read the PJe panel's Expedientes buckets with their counts; regions or bucket add the regions and inboxes under them. Read-only: opens no expediente, acknowledges nothing.",
        annotations=READ_ONLY,
    )
    def expediente_counters(
        portal: Portal,
        regions: Annotated[
            bool, Field(description="true lists regions and inboxes under every non-empty bucket (slow).")
        ] = False,
        bucket: Annotated[
            str | None,
            Field(description="Bucket index (e.g. '0') or part of its label: regions and inboxes of that bucket only."),
        ] = None,
    ) -> dict[str, Any]:
        return _notifications(
            portal, lambda name, session: read_counters(name, session=session, tree=regions, bucket=bucket)
        )

    @server.tool(
        name="pje_list_intimacoes",
        title="List PJe Expedientes inbox rows",
        description="List the communications in one PJe panel bucket, inbox by inbox, up to maxPages of 40 rows each. Each row: expediente id, type, recipient, channel, expedition date, ciência date and whether still pending, days, PJe's deadline (limite), process number, last movement; read the act with pje_read_expediente_ato. "
        + intimacao.DESCRIPTION
        + " In PJe the default bucket (0, Pendentes de ciência ou de resposta) is that list: awaiting ciência, ciência given with the deadline running, and no deadline. The others (see pje_get_counters): 1 only awaiting ciência (a Diário/DJEN row there is aguardando_publicacao: PJe shows no Tomar ciência button for it), 2 and 3 ciência given by the recipient or by the system/Diário, deadline running, 4 deadline ended in the last 10 days with no answer (prazo_vencido; outside the default, ask for it), 5 no deadline, 6 answered in the last 10 days. PJe counts as answered only a reply made through Responder: a petition filed through Juntar documentos leaves the expediente pending an answer. processNumber, expedidoDe/expedidoAte and expedienteId run the inbox's own search. inboxes[].complete is true only when every row PJe reports was read. Read-only: never takes ciência, replies or distributes.",
        annotations=READ_ONLY,
    )
    def list_inbox(
        portal: Portal,
        bucket: Annotated[
            str | None,
            Field(
                description="Bucket index or part of its label (see pje_get_counters). Empty: the pending bucket (Pendentes de ciência ou de resposta, or Meus Expedientes where the panel names it so). '4' lists the deadlines that ended in the last 10 days without an answer. An unknown index errors listing the valid ones."
            ),
        ] = None,
        regions: Annotated[
            list[str] | None,
            Field(
                description='JSON list, not a plain string: keeps inboxes whose path contains any text, e.g. ["Serra Talhada"].'
            ),
        ] = None,
        processNumber: Annotated[str | None, Field(description="CNJ process number with punctuation.")] = None,
        expedidoDe: Annotated[
            Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")] | None,
            Field(
                description="Expedited from, ISO YYYY-MM-DD (DD/MM/AAAA is refused).",
            ),
        ] = None,
        expedidoAte: Annotated[
            Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")] | None,
            Field(
                description="Expedited until, ISO YYYY-MM-DD; same as expedidoDe for one day.",
            ),
        ] = None,
        expedienteId: Annotated[
            Annotated[str, Field(pattern=r"^[0-9]+$")] | None, Field(description="Finds one row.")
        ] = None,
        maxPages: Annotated[int, Field(ge=1, le=50, description="Pages of 40 rows per inbox.")] = 5,
    ) -> dict[str, Any]:
        filters = {
            key: value
            for key, value in {
                "processNumber": processNumber,
                "from": expedidoDe,
                "to": expedidoAte,
                "expedienteId": expedienteId,
            }.items()
            if value
        }
        return _notifications(
            portal,
            lambda name, session: read_inbox(
                name, bucket=bucket, regions=regions, filters=filters or None, max_pages=maxPages, session=session
            ),
            processNumber,
        )

    @server.tool(
        name="pje_list_watched_processes",
        title="List PJe Push watched processes",
        description="List the PJe Push registry (Processos cadastrados): limit rows from offset, with total and hasMore; while hasMore, call again with offset += limit. total is null when pages were left unread and the portal shows no count; complete is true only when the whole registry was read. processNumber returns only that process' registrations (total 0: not watched). Registrations only, no movements. Read-only.",
        annotations=READ_ONLY,
    )
    def watched_processes(
        portal: Portal,
        limit: Annotated[int, Field(ge=1, le=500, description="Rows to return.")] = 100,
        offset: Annotated[int, Field(ge=0, description="Rows to skip.")] = 0,
        processNumber: Annotated[
            str | None, Field(description="CNJ process number with punctuation: only that process.")
        ] = None,
    ) -> dict[str, Any]:
        return _notifications(
            portal,
            lambda name, session: read_push(
                name, session=session, limit=limit, offset=offset, process_number=processNumber or None
            ),
            processNumber,
        )

    @server.tool(
        name="pje_list_processes",
        title="List the lawyer's PJe processes (Acervo)",
        description="List the lawyer's processes from the PJe panel's Acervo, per jurisdiction and box (Caixa de entrada, Arquivados, ...), up to maxPages per box. Items: number, class, subject, parties, órgão julgador, distribution date, last movement when shown, jurisdiction, box; on panels without boxes, the Acervo Geral and Arquivados buckets, with filing and archiving dates and no last movement. complete is true only when every box was read in full and the boxes match each jurisdiction's counter. One call reads up to 60 jurisdictions; the others come in notRead, read with a second call passing them as regions. On a PJe 1.x panel the Acervo's Pendentes de Manifestação and Acervo Geral grids: number, parties and autuação date, first page only, no regions. Read-only: opens nothing, takes no ciência.",
        annotations=READ_ONLY,
    )
    def list_processes(
        portal: Portal,
        regions: Annotated[
            list[str] | None,
            Field(
                description='JSON list, not a plain string: keeps jurisdictions whose name contains any text, e.g. ["Belo Horizonte"].'
            ),
        ] = None,
        maxPages: Annotated[int, Field(ge=1, le=50, description="Pages to read per box.")] = 5,
    ) -> dict[str, Any]:
        return _notifications(
            portal,
            lambda name, session: read_acervo(name, session=session, regions=regions or None, max_pages=maxPages),
        )

    @server.tool(
        name="pje_list_audiencias",
        title="List the lawyer's PJe hearings (Pauta de audiência)",
        description="List the hearings of the lawyer's processes from the PJe Pauta de audiência page, by start date between dateFrom and dateTo (default: today to 90 days ahead): data, hora, processNumber, classe, partes, orgaoJulgador, tipo, sala and situacao (Designada, Realizada, Cancelada, Redesignada, Não Realizada...), oldest first. complete is true when every row PJe counts was read. The room link of a virtual hearing is in pje_get_process audiencias. Not on PJe-JT or PJe 1.x panels. Read-only: opens no process, downloads nothing.",
        annotations=READ_ONLY,
    )
    def list_audiencias(
        portal: Portal,
        dateFrom: Annotated[
            Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")] | None,
            Field(description="First hearing date, ISO YYYY-MM-DD; default today."),
        ] = None,
        dateTo: Annotated[
            Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")] | None,
            Field(description="Last hearing date, ISO YYYY-MM-DD; default 90 days after dateFrom or today."),
        ] = None,
    ) -> dict[str, Any]:
        with tool_errors("PJe"):
            first, last = _window(dateFrom, dateTo)  # a wrong date never costs a login
        return _notifications(portal, lambda name, session: read_pauta(session, name, first, last))

    @server.tool(
        name="pje_get_draft",
        title="Inspect TRF5 PJe draft",
        description="Read current TRF5 1G classification choices, subject search results, or whether an existing draft appears in Não Protocolados. Uses payload.json and checkpoint.json in a named local filing run. No draft mutation.",
        annotations=READ_ONLY,
    )
    def draft_inspect(
        runId: RunId,
        stage: DraftReadStage,
        search: Annotated[
            str | None,
            Field(
                min_length=1,
                max_length=200,
                description="Text to search subjects; required for stage 'subject-options'.",
            ),
        ] = None,
        page: Annotated[int, Field(ge=1, le=50, description="Page of results.")] = 1,
    ) -> dict[str, Any]:
        if stage == "subject-options" and not (search or "").strip():
            raise tool_error("PJe", InputError("Subject search text is required"))
        return _draft_stage("PJe draft", stage, runId, search=search, page=page)

    @server.tool(
        name="pje_search_pending_drafts",
        title="Search TRF5 PJe drafts in Não Protocolados",
        description="Search the authenticated TRF5 1G Não Protocolados list by party name, CPF, or both. Returns matching draft IDs, opening links, and summaries from the first results page. Read only; does not require a filing run.",
        annotations=READ_ONLY,
    )
    def search_pending_drafts(
        name: Annotated[
            str | None, Field(max_length=200, description="Party name, up to 200 characters. Give name, cpf, or both.")
        ] = None,
        cpf: Annotated[
            str | None,
            Field(max_length=30, description="Party CPF, with or without punctuation. Give name, cpf, or both."),
        ] = None,
    ) -> dict[str, Any]:
        name, cpf = (name or "").strip() or None, (cpf or "").strip() or None
        if not (name or cpf):
            raise tool_error("PJe", InputError("Name or CPF is required"))
        return _draft_stage("PJe pending search", "pending-search", name=name, cpf=cpf)

    @server.tool(
        name="pje_fill_draft",
        title="Create or fill TRF5 PJe draft",
        description="Create or fill a TRF5 1G draft from the reviewed payload.json of a named filing run; it stays in Não Protocolados and is never protocolled. Requires PJE_ALLOW_DRAFT_WRITES=true. create-fill, fill and resume run in the background and return at once: follow them with pje_get_draft_status. After a failed or interrupted run use resume: it continues the same draft and uploads only the documents PJe still lacks (never create-fill again). Single stages run within the call and can time out on a slow portal, so prefer resume.",
        annotations=WRITE,
    )
    def draft_write(runId: RunId, stage: DraftWriteStage) -> dict[str, Any]:
        if stage in BACKGROUND_STAGES:
            return _start_fill(runId, stage)
        return _draft_stage("PJe draft", stage, runId)

    @server.tool(
        name="pje_delete_draft",
        title="Delete a TRF5 PJe draft",
        description="Delete one draft from Não Protocolados by its draft number. The row must show the author's name, otherwise nothing is deleted. Uses the portal's own Remover action and confirms the draft is gone from a fresh search. Irreversible. Requires PJE_ALLOW_DRAFT_WRITES=true.",
        annotations=DESTRUCTIVE,
    )
    def draft_delete(
        draftId: Annotated[str, StringConstraints(pattern=r"^\d{1,12}$")],
        authorName: Annotated[str, Field(min_length=3, max_length=200)],
    ) -> dict[str, Any]:
        return _draft_stage("PJe draft delete", "delete-draft", name=authorName, draft_id=draftId)

    @server.tool(
        name="pje_get_draft_status",
        title="TRF5 PJe draft run status",
        description="Report where a filing run stands: running, interrupted, failed or complete; which phases are recorded; the current step and the latest steps with durations. checkPje=true also reads the draft in PJe and compares its documents with the payload, which is the real test that a draft is complete. Read only.",
        annotations=READ_ONLY,
    )
    def draft_status(runId: RunId, checkPje: bool = False) -> dict[str, Any]:
        _, checkpoint = draft_paths(runId)
        key = _job_key(runId)
        job = fill_jobs.get(key)
        if not checkpoint.exists():
            if job is not None and job.is_alive():
                return {"runId": runId, "success": True, "result": {"state": "starting", "conclusion": "not_checked"}}
            if key in fill_errors:
                return {"runId": runId, "success": True, "result": {"state": "failed", "error": fill_errors[key]}}
            raise tool_error("PJe draft", NotFound("This run has no checkpoint yet"))
        response = _draft_stage("PJe draft status", "status-check" if checkPje else "status", runId)
        if job is not None and job.is_alive() and response["result"]["state"] == "unknown":
            response["result"]["state"] = "running"
        if key in fill_errors and not (job is not None and job.is_alive()):
            # A start that failed before it touched the checkpoint (a login that did not go through) is not in the
            # checkpoint; without this line the run would still look as if nothing had been tried.
            response["result"]["lastStartError"] = fill_errors[key]
        return response

    return server


def main():
    load_env_file()
    keys = configured_portals() or portals.keys("pje")
    server = create_server()
    offer_portals(server, keys)
    sessions.only(keys)
    serve(server, on_exit=sessions.close)


if __name__ == "__main__":
    main()
