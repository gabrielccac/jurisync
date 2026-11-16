"""Eproc MCP server for every configured Eproc portal. Read-only toward the portal, except that opening the document
of a pending intimação may register its ciência (the result then carries a ``ciencia`` notice for the user).

Each portal keeps its own session: the Selenium login runs once per portal;
later calls reuse the authenticated cookies and re-read the panel.
"""

import sys
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from jurisync import portals
from jurisync.connectors.eproc.auth import is_panel
from jurisync.connectors.eproc.client import (
    QUEUE_KINDS,
    _response_error,
    authenticate,
    fetch_process,
    list_notices,
    list_processes,
    request,
    uses_a3,
)
from jurisync.core.mcp import READ_ONLY, load_env_file, require_success, serve, tool_errors
from jurisync.core.session_pool import SessionPool

ERRORS = (ValueError, RuntimeError, OSError, KeyError)
# eproc_get_process can store a file in the shared folder and, by opening a document, register a ciência.
READS_AND_SAVES = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True)


def anticipated_errors():
    """ERRORS plus Selenium's WebDriverException (browser/network failures during login).

    Selenium is imported only when a browser login runs, so the class is looked up instead of imported: while the
    module is not loaded no such exception can have been raised.
    """
    selenium = sys.modules.get("selenium.common.exceptions")
    return (*ERRORS, selenium.WebDriverException) if selenium else ERRORS


def refresh_panel(portal, value):
    session, _, panel_url = value
    response, body = request(session, "GET", panel_url, portal=portal)
    if _response_error(response, body) or not is_panel(body):
        return None
    return session, body, panel_url


sessions = SessionPool(login=authenticate, refresh=refresh_panel, close=lambda value: value[0].close())


def configured_portals():
    """Eproc portals whose credentials are set; all wired portals when none are."""
    keys = portals.keys("eproc")
    ready = tuple(
        key
        for key in keys
        if (portals.eproc(key).env("2FA_DATA") or portals.eproc(key).env("2FA_QR"))
        and (portals.eproc(key).env("USERNAME") or portals.eproc(key).uses_certificate() or uses_a3(key))
    )
    return ready or keys


def describe(keys):
    return ", ".join(f"{key} ({portals.eproc(key).host})" for key in keys)


def create_server(keys=None):
    keys = tuple(keys or configured_portals())
    Portal = Literal[keys]  # noqa: N806 - a type built from the configured portals
    Queue = Literal[tuple(QUEUE_KINDS)]  # noqa: N806 - a type built from the queue kinds
    server = MCPServer("eproc", version="1.0.0")

    def call(portal, read):
        with (
            tool_errors("Eproc", *anticipated_errors()),
            sessions.session(portal) as (session, panel_html, panel_url),
        ):
            return require_success(
                "Eproc", read(session=session, panel_html=panel_html, panel_url=panel_url, portal=portal)
            )

    @server.tool(
        name="eproc_list_notices",
        title="List Eproc notices",
        description=(
            "Read one citation/intimation queue of an Eproc portal. queue kinds: "
            + ", ".join(QUEUE_KINDS)
            + f". Portals: {describe(keys)}. Read-only."
        ),
        annotations=READ_ONLY,
    )
    def eproc_list_notices(
        portal: Annotated[Portal, Field(description="Portal key from the list above, e.g. 'trf4-1g-rs'.")],
        queue: Annotated[
            Queue,
            Field(
                description="Queue to read: 'open_deadlines' (prazo aberto, a deadline already running) or 'pending' (citação/intimação still waiting for ciência). See the description for all kinds."
            ),
        ],
    ) -> dict[str, Any]:
        return call(portal, lambda **context: list_notices(queue, **context))

    @server.tool(
        name="eproc_list_processes",
        title="List Eproc lawyer processes",
        description=(
            "Read the first page of Relação de Processos for the authenticated lawyer, using the portal's "
            "default filters (TRF6 excludes baixados by default). No CNJ required. Returns case numbers, "
            "parties, class, subject, last event, dates and claim value. Check filters and pagination.complete; "
            "this is not an unrestricted court-wide search and does not fetch subsequent pages. "
            "Use eproc_get_process for details/documents. Requires the report link in the authenticated panel. "
            f"Portals: {describe(keys)}. Read-only."
        ),
        annotations=READ_ONLY,
    )
    def eproc_list_processes(
        portal: Annotated[Portal, Field(description="Portal key from the list above, e.g. 'trf6-1g'.")],
    ) -> dict[str, Any]:
        return call(portal, list_processes)

    @server.tool(
        name="eproc_get_process",
        title="Get Eproc process context",
        description=(
            "Read an Eproc process, parties, events, document inventory and up to five selected document texts. "
            "Without documentIds it is a pure read. With documentIds the documents are always opened. When the process "
            "had a citação/intimação waiting for ciência, the response carries a top-level ciencia block "
            "(pendente, intimacoes, aviso); when that could not be checked, ciencia.verificada=false with an aviso. "
            "Opening the documents may have registered the lawyer's ciência and started the deadline: always relay "
            "ciencia.aviso to the user and note it in the task. With save=true each read document is stored in the "
            "shared documents folder (HTML acts are rendered to PDF) and reported as savedAs, so a task can attach it "
            "by file name. Not annotated read-only because of the save and ciência effects. "
            f"Portals: {describe(keys)}."
        ),
        annotations=READS_AND_SAVES,
    )
    def eproc_get_process(
        portal: Annotated[Portal, Field(description="Portal key from the list above.")],
        processNumber: Annotated[
            str,
            Field(min_length=1, description="CNJ process number with punctuation, e.g. '5004320-76.2026.4.04.7104'."),
        ],
        scope: Annotated[
            Literal["latest", "page"],
            Field(
                description="'page' (default): the timeline, up to 5 event pages (see pagination). 'latest': newest date only."
            ),
        ] = "page",
        documentIds: Annotated[
            list[Annotated[str, Field(min_length=1)]],
            Field(
                max_length=5,
                description="Optional JSON list of up to 5 document ids from the documentInventory of the process; empty means no texts.",
            ),
        ] = [],
        save: Annotated[
            bool,
            Field(
                description="true stores each read document in the shared documents folder and returns savedAs names for advbox_create_task."
            ),
        ] = False,
    ) -> dict[str, Any]:
        return call(
            portal,
            lambda **context: fetch_process(
                processNumber,
                scope,
                documentIds or None,
                save=save,
                **context,
            ),
        )

    return server


def main():
    load_env_file()
    serve(create_server(), on_exit=sessions.close)


if __name__ == "__main__":
    main()
