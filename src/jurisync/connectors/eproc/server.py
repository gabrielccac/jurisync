"""Eproc MCP server for every configured Eproc portal. Read-only toward the portal, except that opening the document
of a pending intimação may register its ciência (eproc_get_process always reports it in its ``ciencia`` block).

Each portal keeps its own session: the Selenium login runs once per portal;
later calls reuse the authenticated cookies and re-read the panel. A call the portal answers with its login page
logs in again once and is repeated.
"""

import sys
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from jurisync import customers, portals
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
from jurisync.core.session_pool import SessionExpired, SessionPool

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
    """Eproc portals whose login is set in the current credentials; ``()`` when none is."""

    def ready(config):
        password = config.env("USERNAME") and config.env("PASSWORD")
        return (config.env("2FA_DATA") or config.env("2FA_QR")) and (
            password or config.uses_certificate() or uses_a3(config.key)
        )

    return customers.profile_portals(key for key in portals.keys("eproc") if ready(portals.eproc(key)))


def describe(keys):
    return ", ".join(f"{key} ({portals.eproc(key).host})" for key in keys)


def expired(result):
    """A result whose page was the portal's login page. Such a read stopped before any document was opened."""
    if "session_expired" in result.get("diagnostics", []):
        raise SessionExpired("eproc session expired")
    return result


def create_server(keys=None):
    keys = tuple(keys or portals.keys("eproc"))
    Portal = Literal[keys]  # noqa: N806 - a type built from the configured portals
    Queue = Literal[tuple(QUEUE_KINDS)]  # noqa: N806 - a type built from the queue kinds
    server = MCPServer("eproc", version="1.0.0")

    def call(portal, read):
        with tool_errors("Eproc", *anticipated_errors()):
            return require_success(
                "Eproc",
                sessions.run(
                    portal,
                    lambda value: expired(
                        read(session=value[0], panel_html=value[1], panel_url=value[2], portal=portal)
                    ),
                ),
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
            "Without documentIds no document is opened. With documentIds the documents are always opened. The "
            "response always carries a top-level ciencia block, checked against the lawyer's pending queues: "
            "estado 'nenhuma' (nothing pending involved), 'pendente' (a citação/intimação awaits ciência and no "
            "document was opened), 'dada' (documents of a process with one were opened: the ciência may have been "
            "registered and the deadline started) or 'desconhecida' (documents were opened but the queues could not "
            "be checked; motivo says why), with intimacoes and an aviso. Always relay ciencia.aviso to the user and "
            "note it in the task. With save=true each read document is stored in the "
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
    keys = configured_portals() or portals.keys("eproc")
    sessions.only(keys)
    serve(create_server(keys), on_exit=sessions.close)


if __name__ == "__main__":
    main()
