"""PJe MCP server: TRF1/TRF5 process context, notifications, and TRF5 drafts.

Authenticated sessions stay in memory per portal for the life of the server,
so consecutive calls skip the password/certificate + TOTP login.
"""

import os
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field, StringConstraints

from jurisync.connectors.pje import filing
from jurisync.connectors.pje.auth.sessions import is_authenticated
from jurisync.connectors.pje.notifications import read_portal, read_push
from jurisync.connectors.pje.process import fetch_process, login_portal, movement_candidates
from jurisync.core.mcp import READ_ONLY, load_env_file, require_success, run_stdio, tool_errors
from jurisync.core.session_pool import SessionPool

Portal = Literal["trf1-1g", "trf1-2g", "trf5-1g"]
RunId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")]
DraftReadStage = Literal["classification", "subject-options", "pending"]
DraftWriteStage = Literal[
    "create",
    "create-fill",
    "fill",
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


def _notifications(portal, read):
    with tool_errors("PJe", *ERRORS), sessions.session(portal) as session:
        return require_success("PJe", read(portal, session=session))


@server.tool(
    name="pje_get_process",
    title="Get PJe process context",
    description="Retrieve a read-only PJe process result from the selected portal with metadata, parties, bounded timeline pages, document inventory and optionally up to five selected document texts. TRF5 1º Grau/JEF uses certificate login. scope=latest returns the newest date bucket; scope=page returns the first timeline page; scope=all merges the bounded pages returned by the search layer. This returns movements, not confirmed publications.",
    annotations=READ_ONLY,
)
def get_process(
    processNumber: Annotated[str, Field(min_length=1)],
    portal: Portal,
    scope: Literal["latest", "page", "all"] = "page",
    documentIds: Annotated[list[Annotated[str, Field(min_length=1)]], Field(max_length=5)] = [],
) -> dict[str, Any]:
    with tool_errors("PJe", *ERRORS), sessions.session(portal) as session:
        result = fetch_process(processNumber, scope, portal=portal, session=session, document_ids=documentIds or None)
    result["movement_candidates"] = movement_candidates(result)
    return require_success("PJe", result)


@server.tool(
    name="pje_list_expediente_counters",
    title="List PJe Expedientes counters",
    description="Read authenticated PJe Expedientes bucket counters only. Expansion is intentionally not exposed until live evidence proves it has no acknowledgment or other side effect.",
    annotations=READ_ONLY,
)
def expediente_counters(portal: Portal) -> dict[str, Any]:
    return _notifications(portal, lambda name, session: read_portal(name, expand=False, session=session))


@server.tool(
    name="pje_list_watched_processes",
    title="List PJe Push watched processes",
    description="Read the authenticated PJe Push watched-process registry. It reports registered processes and bounded pagination metadata, not movement or publication email text; no page replay or watch mutations are performed.",
    annotations=READ_ONLY,
)
def watched_processes(portal: Portal) -> dict[str, Any]:
    return _notifications(portal, read_push)


def draft_paths(run_id):
    root = Path(os.environ.get("JURISYNC_FILING_DIR") or Path.home() / ".local/share/jurisync/filings").resolve()
    directory = root / run_id
    return directory / "payload.json", directory / "checkpoint.json"


def _draft_stage(label, stage, run_id=None, **options):
    if stage in filing.WRITE_STAGES and os.environ.get("PJE_ALLOW_DRAFT_WRITES") != "true":
        raise ToolError("PJe draft writes are disabled; set PJE_ALLOW_DRAFT_WRITES=true to create or fill a draft")
    if run_id is not None:
        payload, checkpoint = draft_paths(run_id)
        options["checkpoint_path"] = str(checkpoint)
        if stage != "subject-options":
            options["payload_path"] = str(payload)
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
    search: Annotated[str | None, Field(min_length=1, max_length=200)] = None,
    page: Annotated[int, Field(ge=1, le=50)] = 1,
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
    name: Annotated[str | None, Field(max_length=200)] = None,
    cpf: Annotated[str | None, Field(max_length=30)] = None,
) -> dict[str, Any]:
    name, cpf = (name or "").strip() or None, (cpf or "").strip() or None
    if not (name or cpf):
        raise ToolError("Name or CPF is required")
    return _draft_stage("PJe pending search", "pending-search", name=name, cpf=cpf)


@server.tool(
    name="pje_draft_write",
    title="Create or fill TRF5 PJe draft",
    description="Create or fill a TRF5 1G draft using a reviewed payload.json in a named local filing run. Writes a checkpoint immediately after Incluir and leaves the draft in Não Protocolados. Never protocols or submits the process. Requires PJE_ALLOW_DRAFT_WRITES=true. create-fill performs all available filling stages; individual stages support recovery and review.",
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True),
)
def draft_write(runId: RunId, stage: DraftWriteStage) -> dict[str, Any]:
    return _draft_stage("PJe draft", stage, runId)


def main():
    load_env_file()
    run_stdio(server, on_exit=sessions.close)


if __name__ == "__main__":
    main()
