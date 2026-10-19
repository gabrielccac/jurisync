"""PJe MCP server: TRF1/TRF5/TJAP process context, the Expedientes panel, Push, and 1st-degree initial filing drafts.

Authenticated sessions stay in memory per portal for the life of the server,
so consecutive calls skip the password/certificate + TOTP login.
"""

import os
import threading
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field, StringConstraints

from jurisync.connectors.pje import filing
from jurisync.connectors.pje.auth.sessions import is_authenticated
from jurisync.connectors.pje.filing.approval import require_approval
from jurisync.connectors.pje.filing.constants import FILING_PORTALS
from jurisync.connectors.pje.filing.payload import resolve_documents
from jurisync.connectors.pje.filing.store import CHECKPOINT, PAYLOAD, open_run
from jurisync.connectors.pje.inbox import read_counters, read_inbox
from jurisync.connectors.pje.process import fetch_process, login_portal, movement_candidates, read_documents
from jurisync.connectors.pje.push import read_push
from jurisync.core.mcp import READ_ONLY, load_env_file, require_success, serve, tool_errors
from jurisync.core.session_pool import SessionPool
from jurisync.customers import ciencia_on_read
from jurisync.workflows.filing.cleanup import cleanup_all, cleanup_run
from jurisync.workflows.filing.server import register as register_filing_tools

Portal = Annotated[
    Literal["trf1-1g", "trf1-2g", "trf5-1g", "tjap-1g"],
    Field(
        description="PJe portal: 'trf1-1g' (TRF1 1º grau), 'trf1-2g' (TRF1 2º grau), 'trf5-1g' (TRF5 1º grau, JEF by certificate login), 'tjap-1g' (TJAP 1º grau, password login)."
    ),
]
RunId = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"),
    Field(description="Filing run id (letters, digits, _ or -, up to 64), the same runId used in pje_filing_prepare."),
]
DraftReadStage = Literal["classification", "subject-options", "pending", "attachment-types"]
DraftCreateStage = Literal["create", "create-fill"]
DraftFillStage = Literal[
    "all",
    "subject",
    "author",
    "public-bodies",
    "attorney",
    "characteristics",
    "main-document",
    "supporting-documents",
    "documents",
]
# The old single write tool's stages, kept for its deprecated alias.
DraftWriteStage = Literal["create", "create-fill", "fill", *DraftFillStage.__args__[1:]]
# Portal code raises these for anticipated failures (bad input, portal or login errors).
ERRORS = (ValueError, RuntimeError, OSError, KeyError)

sessions = SessionPool(
    login=lambda portal: login_portal(portal)[0],
    refresh=lambda portal, session: session if is_authenticated(session, portal) else None,
    close=lambda session: session.close(),
)
server = MCPServer("pje", version="1.0.0")


def _allow_ciencia(requested):
    """An explicit acknowledgeCiencia wins; otherwise the customer's ``ciencia_on_read`` setting decides."""
    return ciencia_on_read() == "allow" if requested is None else requested


def _notifications(portal, read):
    with tool_errors("PJe", *ERRORS), sessions.session(portal) as session:
        return require_success("PJe", read(portal, session=session))


@server.tool(
    name="pje_get_process",
    title="Get PJe process context",
    description="Retrieve a read-only PJe process result from the selected portal with metadata, parties, bounded timeline pages, document inventory and optionally up to five selected document texts. TRF5 1º Grau/JEF uses certificate login. scope=latest returns the newest date bucket; scope=page returns the first timeline page; scope=all merges the bounded pages returned by the search layer. It also returns expedientes from the process Expedientes tab: each communication per recipient with channel, notice date, days and the deadline date PJe computed (limite), plus the ones still open (abertos). This returns movements, not confirmed publications.",
    annotations=READ_ONLY,
)
def get_process(
    processNumber: Annotated[
        str, Field(min_length=1, description="CNJ process number with punctuation, e.g. '0005231-50.2026.4.05.8203'.")
    ],
    portal: Portal,
    scope: Annotated[
        Literal["latest", "page", "all"],
        Field(
            description="'page' (default): first timeline page. 'latest': newest date only. 'all': merges bounded pages, slower."
        ),
    ] = "page",
    documentIds: Annotated[
        list[Annotated[str, Field(min_length=1)]],
        Field(
            max_length=5,
            description='Optional JSON list of up to 5 document ids from documentInventory, e.g. ["195268244"]; empty means no texts.',
        ),
    ] = [],
) -> dict[str, Any]:
    with tool_errors("PJe", *ERRORS), sessions.session(portal) as session:
        result = fetch_process(processNumber, scope, portal=portal, session=session, document_ids=documentIds or None)
    result["movement_candidates"] = movement_candidates(result)
    return require_success("PJe", result)


@server.tool(
    name="pje_read_documents",
    title="Read PJe documents",
    description="Read 1-5 joined documents of a PJe process by ID, without returning the timeline. Returns each document's extraction status and text (PDFs, HTML including documents that omit the process number as extracted_unnumbered, and audio when transcription is installed). A Certidão de Intimação also returns its parsed expedientes (recipient, medium, deadline in days), which hold the client's deadline. With save=true the original file is stored in the shared documents folder and reported as savedAs, so advbox_create_task can attach it by file name. Use textLimit to bound long texts. Everything is read by default except the act of a communication whose ciência is not registered yet: reading it may register the party's ciência and start the deadline, so it is refused until the user explicitly agrees; then repeat the call with acknowledgeCiencia=true. A customer whose settings.toml says [pje] ciencia_on_read = 'allow' reads them by default. When such a read happens the result has ciencia.pendenteAoLer and an aviso: tell the user and note it in the task. If the Expedientes tab cannot be read the check is skipped and the result says cienciaChecked=false.",
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True),
)
def read_documents_tool(
    processNumber: Annotated[str, Field(min_length=1, description="CNJ process number with punctuation.")],
    portal: Portal,
    documentIds: Annotated[
        list[Annotated[str, Field(pattern=r"^[0-9]+$")]],
        Field(
            min_length=1,
            max_length=5,
            description='JSON list of 1 to 5 numeric document ids (documentId from pje_get_process), e.g. ["195268244"].',
        ),
    ],
    textLimit: Annotated[
        int, Field(ge=200, le=100000, description="Max characters per text, 200 to 100000; default 20000.")
    ] = 20000,
    save: Annotated[
        bool,
        Field(description="true stores the original files in the shared documents folder and returns savedAs names."),
    ] = False,
    acknowledgeCiencia: Annotated[
        bool | None,
        Field(
            description="Leave empty. true registers the party's ciência and starts the deadline; only use after the user explicitly agrees."
        ),
    ] = None,
) -> dict[str, Any]:
    with tool_errors("PJe", *ERRORS), sessions.session(portal) as session:
        result = read_documents(
            processNumber,
            documentIds,
            portal=portal,
            session=session,
            save=save,
            acknowledge_ciencia=_allow_ciencia(acknowledgeCiencia),
        )
    for document in result.get("documents", []):
        text = document.get("text", "")
        document["textTruncated"] = len(text) > textLimit
        document["text"] = text[:textLimit]
    return require_success("PJe", result)


@server.tool(
    name="pje_read_expediente_ato",
    title="Read the act behind a PJe expediente",
    description="Open the act of one communication listed in the process Expedientes tab (see expedientes in pje_get_process): the expediente box (recipient, days, deadline, closed), the full text of the act and the documents attached to it (documentId, title: read them with pje_read_documents). Communications whose notice (ciência) is already registered are opened by default. A pending one is refused (error in ato.error) because opening it may register the party's ciência and start the deadline: ask the user, and only with their explicit yes repeat the call with acknowledgeCiencia=true (customers with ciencia_on_read = 'allow' in settings.toml open them by default). When a pending one is opened, ato.ciencia lists it with an aviso: tell the user and note it in the task.",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True),
)
def read_expediente_ato_tool(
    processNumber: Annotated[str, Field(min_length=1, description="CNJ process number with punctuation.")],
    portal: Portal,
    expedienteId: Annotated[
        str,
        Field(
            pattern=r"^[0-9]+$",
            description="Numeric expediente id from pje_list_inbox or pje_get_process expedientes, e.g. '34932797'.",
        ),
    ],
    textLimit: Annotated[
        int, Field(ge=200, le=100000, description="Max characters of the act text, 200 to 100000; default 20000.")
    ] = 20000,
    acknowledgeCiencia: Annotated[
        bool | None,
        Field(
            description="Leave empty. Pending ciência is refused without it. true registers the ciência and starts the deadline: only after the user explicitly agrees."
        ),
    ] = None,
) -> dict[str, Any]:
    with tool_errors("PJe", *ERRORS), sessions.session(portal) as session:
        result = fetch_process(
            processNumber,
            "latest",
            portal=portal,
            session=session,
            ato_expediente=expedienteId,
            ato_text_limit=textLimit,
            ato_allow_pending=_allow_ciencia(acknowledgeCiencia),
        )
    return require_success(
        "PJe",
        {k: result[k] for k in ("success", "source", "portal", "processNumber", "ato", "diagnostics")}
        | {
            "expediente": next(
                (i for i in (result.get("expedientes") or {}).get("items", []) if i["expedienteId"] == expedienteId),
                None,
            )
        },
    )


@server.tool(
    name="pje_list_expediente_counters",
    title="List PJe Expedientes counters",
    description="Read the PJe lawyer panel's Expedientes buckets (the same for every login) with their counts. With regions=true each bucket that has items also lists its regions and inboxes with counts (jurisdictions, boxes the profile has), which vary per user. Opening a bucket or expanding a region does not notify anyone; no expediente row is opened and nothing is acknowledged.",
    annotations=READ_ONLY,
)
def expediente_counters(
    portal: Portal,
    regions: Annotated[
        bool, Field(description="true also lists regions and inboxes under each non-empty bucket.")
    ] = False,
) -> dict[str, Any]:
    return _notifications(portal, lambda name, session: read_counters(name, session=session, tree=regions))


@server.tool(
    name="pje_list_inbox",
    title="List PJe Expedientes inbox rows",
    description="List the communications of a PJe panel bucket (default 0, Pendentes de ciência ou de resposta; pass its index or part of its label) from the panel's inboxes, region by region, all pages up to maxPages (40 rows per page). Each row has the expediente id, type, recipient, channel, expedition date, notice (ciência) and whether it is still pending, days, PJe's deadline (limite), the process number and the last movement; read the act with pje_read_expediente_ato and the process with pje_get_process. regions keeps only inboxes whose path contains one of the texts. processNumber, expedidoDe/expedidoAte (YYYY-MM-DD, expedition date interval) and expedienteId run the inbox's own search. inboxes[].complete is true only when the rows read equal the total PJe reports. Read-only: it never takes ciência, replies, selects or distributes expedientes.",
    annotations=READ_ONLY,
)
def list_inbox(
    portal: Portal,
    bucket: Annotated[
        str | None,
        Field(
            description="Bucket index (e.g. '0') or part of its label, e.g. 'Pendentes de ciência'. Default: bucket 0. See pje_list_expediente_counters."
        ),
    ] = None,
    regions: Annotated[
        list[str] | None,
        Field(
            description='JSON list of texts; keeps inboxes whose path contains any of them, e.g. ["Serra Talhada"]. Not a plain string.'
        ),
    ] = None,
    processNumber: Annotated[
        str | None, Field(description="CNJ process number with punctuation, runs the inbox search.")
    ] = None,
    expedidoDe: Annotated[
        str | None,
        Field(
            pattern=r"^\d{4}-\d{2}-\d{2}$",
            description="Start of expedition date, ISO YYYY-MM-DD (e.g. '2026-10-15'). DD/MM/AAAA is refused.",
        ),
    ] = None,
    expedidoAte: Annotated[
        str | None,
        Field(
            pattern=r"^\d{4}-\d{2}-\d{2}$",
            description="End of expedition date, ISO YYYY-MM-DD. Use the same day as expedidoDe for one day.",
        ),
    ] = None,
    expedienteId: Annotated[
        str | None, Field(pattern=r"^[0-9]+$", description="Numeric expediente id to find one row.")
    ] = None,
    maxPages: Annotated[
        int, Field(ge=1, le=50, description="Pages of 40 rows to read per inbox, 1 to 50; default 5.")
    ] = 5,
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
    )


@server.tool(
    name="pje_list_watched_processes",
    title="List PJe Push watched processes",
    description="Read the authenticated PJe Push watched-process registry. It reports registered processes and bounded pagination metadata, not movement or publication email text; no page replay or watch mutations are performed.",
    annotations=READ_ONLY,
)
def watched_processes(portal: Portal) -> dict[str, Any]:
    return _notifications(portal, read_push)


BACKGROUND_STAGES = frozenset({"create-fill", "fill"})
# Fills take minutes, longer than a tool call may last, so they run in a thread of this server and report through
# pje_draft_status. A fill that fails before its checkpoint exists can only leave its error here.
fill_jobs = {}
fill_errors = {}


def _fill_job(run_id, stage):
    try:
        filing.run_stage(stage, run=open_run(run_id), portal=_run_portal(open_run(run_id)))
        fill_errors.pop(run_id, None)
    except BaseException as error:
        fill_errors[run_id] = f"{type(error).__name__}: {str(error)[:300]}"


def _start_fill(run_id, stage):
    if os.environ.get("PJE_ALLOW_DRAFT_WRITES") != "true":
        raise ToolError("PJe draft writes are disabled; set PJE_ALLOW_DRAFT_WRITES=true to create or fill a draft")
    running = fill_jobs.get(run_id)
    if running is not None and running.is_alive():
        return {"runId": run_id, "success": True, "stage": stage, "state": "already-running"}
    run = open_run(run_id)
    with tool_errors("PJe draft", *ERRORS):
        filing.validate_fill_payload(resolve_documents(run.read_json(PAYLOAD), run))
        require_approval(run)
        if stage == "create-fill" and run.exists(CHECKPOINT):
            raise ToolError("This run already has a draft; continue it with pje_draft_fill")
        if stage == "fill" and not run.exists(CHECKPOINT):
            raise ToolError("This run has no draft yet; create it with pje_draft_create")
    fill_errors.pop(run_id, None)
    job = threading.Thread(target=_fill_job, args=(run_id, stage), name=f"pje-fill-{run_id}", daemon=True)
    fill_jobs[run_id] = job
    job.start()
    return {
        "runId": run_id,
        "success": True,
        "stage": stage,
        "state": "started",
        "next": "Call pje_draft_status with this runId to follow progress; it finishes on its own.",
    }


def _run_portal(run):
    """The portal a run files on (its payload's `portal`); runs without a payload, and other stages, use TRF5."""
    if run is not None and run.exists(PAYLOAD):
        try:
            portal = run.read_json(PAYLOAD).get("portal")
        except ValueError:
            portal = None
        if portal in FILING_PORTALS:
            return portal
    return "trf5-1g"


def _draft_stage(label, stage, run_id=None, **options):
    if stage in filing.WRITE_STAGES and os.environ.get("PJE_ALLOW_DRAFT_WRITES") != "true":
        raise ToolError("PJe draft writes are disabled; set PJE_ALLOW_DRAFT_WRITES=true to create or fill a draft")
    if run_id is not None:
        options["run"] = open_run(run_id)
    if stage == "status":  # reads local files only: no login needed
        with tool_errors(label, *ERRORS):
            result = filing.run_stage(stage, **options)
    else:
        portal = options.pop("portal", None) or _run_portal(options.get("run"))
        with tool_errors(label, *ERRORS), sessions.session(portal) as session:
            result = filing.run_stage(stage, session=session, portal=portal, **options)
    return {**({"runId": run_id} if run_id is not None else {}), "success": True, "stage": stage, "result": result}


DEPRECATED = "Deprecated name, kept for a while: use {} instead. "


def _tool_with_alias(old_name, new_name, **options):
    """Register a tool under its new name and, for a while, its old one (description says which to use)."""

    def add(function):
        server.add_tool(function, name=new_name, **options)
        alias = {**options, "title": options["title"] + " (old name)"}
        alias["description"] = DEPRECATED.format(new_name) + options["description"]
        server.add_tool(function, name=old_name, **alias)
        return function

    return add


@_tool_with_alias(
    "pje_draft_inspect",
    "pje_draft_options",
    title="Read PJe draft options",
    description="Read, for a named local filing run, what the portal offers before or while filling a draft: classification choices (classification), subject search results (subject-options, needs `search`), whether the run's draft is still in Não Protocolados (pending), or the attachment types the draft's Anexar form lists (attachment-types, needs a draft; also stored in the run so pje_filing_build checks the payload's types against it). Uses the run's payload.json and checkpoint.json. Changes nothing in PJe.",
    annotations=READ_ONLY,
)
def draft_options(
    runId: RunId,
    stage: Annotated[
        DraftReadStage,
        Field(
            description="'classification' (choices of the register form), 'subject-options' (subject search, needs search), 'pending' (is the run's draft in Não Protocolados), 'attachment-types' (the Anexar tab's types; needs a draft)."
        ),
    ],
    search: Annotated[
        str | None,
        Field(
            min_length=1, max_length=200, description="Text to search subjects; required for stage 'subject-options'."
        ),
    ] = None,
    page: Annotated[int, Field(ge=1, le=50, description="Page of results, 1 to 50; default 1.")] = 1,
) -> dict[str, Any]:
    if stage == "subject-options" and not (search or "").strip():
        raise ToolError("Subject search text is required")
    return _draft_stage("PJe draft", stage, runId, search=search, page=page)


@_tool_with_alias(
    "pje_search_pending_drafts",
    "pje_draft_search",
    title="Search PJe drafts in Não Protocolados",
    description="Search the authenticated portal's Não Protocolados list (TRF5 1G unless `portal` is given) by party name, CPF, or both. Returns matching draft IDs, opening links, and summaries from the first results page. Read only; does not require a filing run.",
    annotations=READ_ONLY,
)
def draft_search(
    name: Annotated[
        str | None, Field(max_length=200, description="Party name, up to 200 characters. Give name, cpf, or both.")
    ] = None,
    cpf: Annotated[
        str | None, Field(max_length=30, description="Party CPF, with or without punctuation. Give name, cpf, or both.")
    ] = None,
    portal: Annotated[
        Literal["trf1-1g", "trf5-1g"],
        Field(description="Portal whose Não Protocolados list is searched; default trf5-1g."),
    ] = "trf5-1g",
) -> dict[str, Any]:
    name, cpf = (name or "").strip() or None, (cpf or "").strip() or None
    if not (name or cpf):
        raise ToolError("Name or CPF is required")
    return _draft_stage("PJe pending search", "pending-search", name=name, cpf=cpf, portal=portal)


APPROVAL_NOTE = (
    "Refuses unless pje_filing_approve recorded the user's approval of this exact payload and every document still "
    "has its approved sha256. Never protocols or submits the process: the draft stays in Não Protocolados. Requires "
    "PJE_ALLOW_DRAFT_WRITES=true."
)


@server.tool(
    name="pje_draft_create",
    title="Create a PJe 1st-degree draft",
    description="Create the draft of an initial filing on the run payload's portal (TRF5 1G or TRF1 1G) from the approved payload.json in a named local filing run, and by default fill it (create-fill: subject, parties, characteristics, documents) in the background: it returns at once, follow it with pje_draft_status. stage create only runs Incluir. A checkpoint is written right after Incluir. "
    + APPROVAL_NOTE,
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True),
)
def draft_create(
    runId: RunId,
    stage: Annotated[
        DraftCreateStage,
        Field(
            description="'create-fill' (default: Incluir, then fill everything in the background) or 'create' (Incluir only)."
        ),
    ] = "create-fill",
) -> dict[str, Any]:
    if stage == "create-fill":
        return _start_fill(runId, stage)
    return _draft_stage("PJe draft", stage, runId)


@server.tool(
    name="pje_draft_fill",
    title="Fill a PJe draft (one step or everything left)",
    description="Continue the draft of a run that already has one: `all` (default) runs every step not yet recorded, in the background (follow it with pje_draft_status); a named stage (subject, author, public-bodies, attorney, characteristics, main-document, supporting-documents, documents) runs just that step and returns its result. Steps already done for this payload are skipped, so rerunning after a failure is safe. "
    + APPROVAL_NOTE,
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True),
)
def draft_fill(
    runId: RunId,
    stage: Annotated[
        DraftFillStage,
        Field(description="'all' (default: every step not done yet, in the background) or one step to run now."),
    ] = "all",
) -> dict[str, Any]:
    if stage == "all":
        return _start_fill(runId, "fill")
    return _draft_stage("PJe draft", stage, runId)


@server.tool(
    name="pje_draft_write",
    title="Create or fill a PJe draft (old name)",
    description=DEPRECATED.format("pje_draft_create (create, create-fill) or pje_draft_fill (fill and single stages)")
    + "Create or fill a TRF5 1G or TRF1 1G draft from the approved payload of a named local filing run. "
    + APPROVAL_NOTE,
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True),
)
def draft_write(
    runId: RunId, stage: Annotated[DraftWriteStage, Field(description="Create or fill stage, as in the old tool.")]
) -> dict[str, Any]:
    if stage in BACKGROUND_STAGES:
        return _start_fill(runId, stage)
    return _draft_stage("PJe draft", stage, runId)


@server.tool(
    name="pje_draft_delete",
    title="Delete a PJe draft",
    description="Delete one draft from Não Protocolados by its draft number. The row must show the author's name, otherwise nothing is deleted. Uses the portal's own Remover action and confirms the draft is gone from a fresh search. Irreversible. Requires PJE_ALLOW_DRAFT_WRITES=true.",
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True),
)
def draft_delete(
    draftId: Annotated[
        str, StringConstraints(pattern=r"^\d{1,12}$"), Field(description="Draft number in Não Protocolados, digits.")
    ],
    authorName: Annotated[
        str,
        Field(min_length=3, max_length=200, description="Author name the draft's row must show, e.g. 'MARIA SILVA'."),
    ],
) -> dict[str, Any]:
    return _draft_stage("PJe draft delete", "delete-draft", name=authorName, draft_id=draftId)


@server.tool(
    name="pje_draft_status",
    title="PJe draft run status",
    description="Report where a filing run stands: running, interrupted, failed or complete; which phases are recorded; the current step and the latest steps with durations. checkPje=true also reads the draft in PJe and compares its documents with the payload, which is the real test that a draft is complete. Read only.",
    annotations=READ_ONLY,
)
def draft_status(
    runId: RunId,
    checkPje: Annotated[
        bool, Field(description="true also reads the draft in PJe and compares its documents with the payload.")
    ] = False,
) -> dict[str, Any]:
    job = fill_jobs.get(runId)
    if not open_run(runId).exists(CHECKPOINT):
        if job is not None and job.is_alive():
            return {"runId": runId, "success": True, "result": {"state": "starting", "conclusion": "not_checked"}}
        if runId in fill_errors:
            return {"runId": runId, "success": True, "result": {"state": "failed", "error": fill_errors[runId]}}
        raise ToolError("This run has no checkpoint yet")
    response = _draft_stage("PJe draft status", "status-check" if checkPje else "status", runId)
    if job is not None and job.is_alive() and response["result"]["state"] == "unknown":
        response["result"]["state"] = "running"
    return response


@server.tool(
    name="pje_filing_cleanup",
    title="Remove a filing run and its input documents",
    description="Delete a local filing run (assembled documents, payload, review) and the input folder it was prepared from, to keep client documents only while needed. Removed when its draft is no longer in Não Protocolados (protocolled or deleted; checked in PJe), or, for a run without a draft, once it is older than olderThanDays (default JURISYNC_FILING_RETENTION_DAYS, 30). A run whose draft is still pending is kept unless the user explicitly confirmed removing it (confirmPendingDraft=true). Without runId it sweeps every run with the same rules (never removing a pending draft's run) and every unused input folder older than the retention. Local files only: never touches the draft in PJe.",
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True),
)
def filing_cleanup(
    runId: Annotated[
        str | None,
        StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"),
        Field(description="Run to remove; omit to sweep every run and unused input folder."),
    ] = None,
    olderThanDays: Annotated[
        float | None,
        Field(
            ge=0,
            le=3650,
            description="Age in days after which a run without a draft goes; default 30 (JURISYNC_FILING_RETENTION_DAYS).",
        ),
    ] = None,
    confirmPendingDraft: Annotated[
        bool,
        Field(description="true only when the user explicitly confirmed removing a run whose draft is still pending."),
    ] = False,
) -> dict[str, Any]:
    running = {run_id for run_id, job in fill_jobs.items() if job.is_alive()}

    def draft_pending(run_id):
        return _draft_stage("PJe draft", "pending", run_id)["result"]["inNaoProtocolados"]

    with tool_errors("Filing cleanup", *ERRORS):
        if runId is None:
            if confirmPendingDraft:
                raise ToolError("confirmPendingDraft applies to one named run only")
            return cleanup_all(draft_pending=draft_pending, older_than_days=olderThanDays, running=running)
        return cleanup_run(
            runId,
            draft_pending=draft_pending,
            older_than_days=olderThanDays,
            confirm_pending=confirmPendingDraft,
            running=runId in running,
        )


# The filing preparation tools (pje_filing_prepare, pje_filing_review, pje_filing_build, pje_filing_approve...) are offered here
# too, so one PJe server takes a filing from the input folder to the draft. Only this server module imports the
# workflow; the connector's own filing package never does (tests/workflows/test_filing_architecture.py).
register_filing_tools(server)


def main():
    load_env_file()
    serve(server, on_exit=sessions.close)


if __name__ == "__main__":
    main()
