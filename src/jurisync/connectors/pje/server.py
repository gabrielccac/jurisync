"""PJe MCP server: TRF1/TRF5/TJAP process context, the Expedientes panel, Push, and TRF5 drafts.

Authenticated sessions stay in memory per portal for the life of the server,
so consecutive calls skip the password/certificate + TOTP login.
"""

import json
import os
import threading
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field, StringConstraints

from jurisync import portals
from jurisync.connectors.pje import filing
from jurisync.connectors.pje.auth.sessions import is_authenticated
from jurisync.connectors.pje.filing.checkpoint import run_directory
from jurisync.connectors.pje.inbox import read_counters, read_inbox
from jurisync.connectors.pje.process import fetch_process, login_portal, movement_candidates, read_documents
from jurisync.connectors.pje.push import read_push
from jurisync.core.mcp import READ_ONLY, load_env_file, require_success, serve, tool_errors
from jurisync.core.session_pool import SessionPool
from jurisync.customers import ciencia_on_read

Portal = Annotated[
    Literal[portals.keys("pje")],
    Field(
        description="PJe portal: 'trf1-1g' (TRF1 1º grau), 'trf1-2g' (TRF1 2º grau), 'trf3-1g' (TRF3 1º grau), 'trf3-2g' (TRF3 2º grau, Turmas Recursais de SP), 'trf5-1g' (TRF5 1º grau, JEF by certificate login), 'trf5-2g' (TRF5 2º grau, certificate login), 'trf5-tru' (TRF5 Turma Regional de Uniformização, certificate login), 'tjap-1g' (TJAP 1º grau, password login), 'tjpe-1g' and 'tjpe-2g' (TJPE 1º and 2º grau, password login). Any portal can also log in with the A1 certificate (PJE_A1_P12/PJE_A1_PIN) when it opts in with <PREFIX>_CERT=true."
    ),
]
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
    description="Read 1-5 joined documents of a PJe process by ID, without returning the timeline. Returns each document's extraction status and text (PDFs, HTML including documents that omit the process number as extracted_unnumbered, or short_html for a short cover page whose text is returned but not vouched for, and audio when transcription is installed). A Certidão de Intimação also returns its parsed expedientes (recipient, medium, deadline in days), which hold the client's deadline. With save=true the original file is stored in the shared documents folder and reported as savedAs, so advbox_create_task can attach it by file name. Use textLimit to bound long texts. Everything is read by default except the act of a communication whose ciência is not registered yet: reading it may register the party's ciência and start the deadline, so it is refused until the user explicitly agrees; then repeat the call with acknowledgeCiencia=true. A customer whose settings.toml says [pje] ciencia_on_read = 'allow' reads them by default. When such a read happens the result has ciencia.pendenteAoLer and an aviso: tell the user and note it in the task. If the Expedientes tab cannot be read the check is skipped and the result says cienciaChecked=false.",
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
        Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")] | None,
        Field(
            description="Start of expedition date, ISO YYYY-MM-DD (e.g. '2026-10-15'). DD/MM/AAAA is refused.",
        ),
    ] = None,
    expedidoAte: Annotated[
        Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")] | None,
        Field(
            description="End of expedition date, ISO YYYY-MM-DD. Use the same day as expedidoDe for one day.",
        ),
    ] = None,
    expedienteId: Annotated[
        Annotated[str, Field(pattern=r"^[0-9]+$")] | None, Field(description="Numeric expediente id to find one row.")
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


def draft_paths(run_id):
    directory = run_directory(run_id)
    return directory / "payload.json", directory / "checkpoint.json"


BACKGROUND_STAGES = frozenset({"create-fill", "fill", "resume"})
# Fills take minutes, longer than a tool call may last, so they run in a thread of this server and report through
# pje_draft_status. A fill that fails before its checkpoint exists can only leave its error here.
fill_jobs = {}
fill_errors = {}


def _fill_job(run_id, stage):
    payload, checkpoint = draft_paths(run_id)
    try:
        # The pooled session is reused and refreshed, so a fill and its retries do not log in again each time.
        with sessions.session("trf5-1g") as session:
            filing.run_stage(stage, payload_path=str(payload), checkpoint_path=str(checkpoint), session=session)
        fill_errors.pop(run_id, None)
    except BaseException as error:
        fill_errors[run_id] = f"{type(error).__name__}: {str(error)[:300]}"


def _start_fill(run_id, requested):
    # `resume` is `fill` under the name an agent reaches for after a failure: it continues the run's own draft,
    # skipping the phases already recorded and sending only the documents PJe's list still lacks.
    stage = "fill" if requested == "resume" else requested
    if os.environ.get("PJE_ALLOW_DRAFT_WRITES") != "true":
        raise ToolError("PJe draft writes are disabled; set PJE_ALLOW_DRAFT_WRITES=true to create or fill a draft")
    running = fill_jobs.get(run_id)
    if running is not None and running.is_alive():
        return {"runId": run_id, "success": True, "stage": requested, "state": "already-running"}
    payload, checkpoint = draft_paths(run_id)
    with tool_errors("PJe draft", *ERRORS):
        filing.validate_fill_payload(json.loads(payload.read_text(encoding="utf-8")))
        if stage == "create-fill" and checkpoint.exists():
            raise ToolError("This run already has a draft; use stage fill to continue it")
        if stage == "fill" and not checkpoint.exists():
            raise ToolError("This run has no draft yet; use stage create-fill")
    fill_errors.pop(run_id, None)
    job = threading.Thread(target=_fill_job, args=(run_id, stage), name=f"pje-fill-{run_id}", daemon=True)
    fill_jobs[run_id] = job
    job.start()
    return {
        "runId": run_id,
        "success": True,
        "stage": requested,
        "state": "started",
        "next": "Call pje_draft_status with this runId to follow progress; it finishes on its own.",
    }


def _draft_stage(label, stage, run_id=None, **options):
    if stage in filing.WRITE_STAGES and os.environ.get("PJE_ALLOW_DRAFT_WRITES") != "true":
        raise ToolError("PJe draft writes are disabled; set PJE_ALLOW_DRAFT_WRITES=true to create or fill a draft")
    if run_id is not None:
        payload, checkpoint = draft_paths(run_id)
        options["checkpoint_path"] = str(checkpoint)
        if stage != "subject-options":
            options["payload_path"] = str(payload)
    if stage == "status":  # reads local files only: no login needed
        with tool_errors(label, *ERRORS):
            result = filing.run_stage(stage, **options)
    else:
        with tool_errors(label, *ERRORS), sessions.session("trf5-1g") as session:
            result = filing.run_stage(stage, session=session, **options)
    return {**({"runId": run_id} if run_id is not None else {}), "success": True, "stage": stage, "result": result}


@server.tool(
    name="pje_draft_inspect",
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
            min_length=1, max_length=200, description="Text to search subjects; required for stage 'subject-options'."
        ),
    ] = None,
    page: Annotated[int, Field(ge=1, le=50, description="Page of results, 1 to 50; default 1.")] = 1,
) -> dict[str, Any]:
    if stage == "subject-options" and not (search or "").strip():
        raise ToolError("Subject search text is required")
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
        str | None, Field(max_length=30, description="Party CPF, with or without punctuation. Give name, cpf, or both.")
    ] = None,
) -> dict[str, Any]:
    name, cpf = (name or "").strip() or None, (cpf or "").strip() or None
    if not (name or cpf):
        raise ToolError("Name or CPF is required")
    return _draft_stage("PJe pending search", "pending-search", name=name, cpf=cpf)


@server.tool(
    name="pje_draft_write",
    title="Create or fill TRF5 PJe draft",
    description="Create or fill a TRF5 1G draft using a reviewed payload.json in a named local filing run. Writes a checkpoint immediately after Incluir and leaves the draft in Não Protocolados. Never protocols or submits the process. Requires PJE_ALLOW_DRAFT_WRITES=true. create-fill, fill and resume run in the background and return at once: follow them with pje_draft_status. After a failed or interrupted run call resume: it continues the same draft from where it stopped and uploads only the documents PJe's list still lacks (never create-fill again). The single-stage calls (documents, characteristics, ...) run inside the tool call, which can expire on a slow portal, so prefer resume. Individual stages support recovery and review.",
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True),
)
def draft_write(runId: RunId, stage: DraftWriteStage) -> dict[str, Any]:
    if stage in BACKGROUND_STAGES:
        return _start_fill(runId, stage)
    return _draft_stage("PJe draft", stage, runId)


@server.tool(
    name="pje_draft_delete",
    title="Delete a TRF5 PJe draft",
    description="Delete one draft from Não Protocolados by its draft number. The row must show the author's name, otherwise nothing is deleted. Uses the portal's own Remover action and confirms the draft is gone from a fresh search. Irreversible. Requires PJE_ALLOW_DRAFT_WRITES=true.",
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True),
)
def draft_delete(
    draftId: Annotated[str, StringConstraints(pattern=r"^\d{1,12}$")],
    authorName: Annotated[str, Field(min_length=3, max_length=200)],
) -> dict[str, Any]:
    return _draft_stage("PJe draft delete", "delete-draft", name=authorName, draft_id=draftId)


@server.tool(
    name="pje_draft_status",
    title="TRF5 PJe draft run status",
    description="Report where a filing run stands: running, interrupted, failed or complete; which phases are recorded; the current step and the latest steps with durations. checkPje=true also reads the draft in PJe and compares its documents with the payload, which is the real test that a draft is complete. Read only.",
    annotations=READ_ONLY,
)
def draft_status(runId: RunId, checkPje: bool = False) -> dict[str, Any]:
    _, checkpoint = draft_paths(runId)
    job = fill_jobs.get(runId)
    if not checkpoint.exists():
        if job is not None and job.is_alive():
            return {"runId": runId, "success": True, "result": {"state": "starting", "conclusion": "not_checked"}}
        if runId in fill_errors:
            return {"runId": runId, "success": True, "result": {"state": "failed", "error": fill_errors[runId]}}
        raise ToolError("This run has no checkpoint yet")
    response = _draft_stage("PJe draft status", "status-check" if checkPje else "status", runId)
    if job is not None and job.is_alive() and response["result"]["state"] == "unknown":
        response["result"]["state"] = "running"
    if runId in fill_errors and not (job is not None and job.is_alive()):
        # A start that failed before it touched the checkpoint (a login that did not go through) is not in the
        # checkpoint; without this line the run would still look as if nothing had been tried.
        response["result"]["lastStartError"] = fill_errors[runId]
    return response


def main():
    load_env_file()
    serve(server, on_exit=sessions.close)


if __name__ == "__main__":
    main()
