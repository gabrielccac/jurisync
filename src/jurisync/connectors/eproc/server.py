"""Eproc MCP server for every configured Eproc portal. Read-only.

Each portal keeps its own session: the Selenium login runs once per portal;
later calls reuse the authenticated cookies and re-read the panel.
"""

from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import Field
from selenium.common.exceptions import WebDriverException

from jurisync import portals
from jurisync.connectors.eproc.auth import PANEL_MARKER
from jurisync.connectors.eproc.client import _response_error, authenticate, fetch_process, list_notices, request
from jurisync.core.mcp import READ_ONLY, load_env_file, require_success, serve, tool_errors
from jurisync.core.session_pool import SessionPool

# Selenium raises WebDriverException for browser/network failures during login.
ERRORS = (ValueError, RuntimeError, OSError, KeyError, WebDriverException)


def refresh_panel(portal, value):
    session, _, panel_url = value
    response, body = request(session, "GET", panel_url, portal=portal)
    if _response_error(response, body) or PANEL_MARKER not in body:
        return None
    return session, body, panel_url


sessions = SessionPool(login=authenticate, refresh=refresh_panel, close=lambda value: value[0].close())


def configured_portals():
    """Eproc portals whose credentials are set; all wired portals when none are."""
    keys = portals.keys("eproc")
    ready = tuple(key for key in keys if portals.eproc(key).env("USUARIO") and portals.eproc(key).env("EXPORT_DATA"))
    return ready or keys


def describe(keys):
    return ", ".join(f"{key} ({portals.eproc(key).host})" for key in keys)


def create_server(keys=None):
    keys = tuple(keys or configured_portals())
    Portal = Literal[keys]  # noqa: N806 - a type built from the configured portals
    server = MCPServer("eproc", version="1.0.0")

    def call(portal, read):
        with tool_errors("Eproc", *ERRORS), sessions.session(portal) as (session, panel_html, panel_url):
            return require_success(
                "Eproc", read(session=session, panel_html=panel_html, panel_url=panel_url, portal=portal)
            )

    @server.tool(
        name="eproc_list_notices",
        title="List Eproc notices",
        description=f"Read one Eproc portal's open-deadline or pending citation/intimation queue. Portals: {describe(keys)}. Read-only.",
        annotations=READ_ONLY,
    )
    def eproc_list_notices(portal: Portal, queue: Literal["open_deadlines", "pending"]) -> dict[str, Any]:
        return call(portal, lambda **context: list_notices(queue, **context))

    @server.tool(
        name="eproc_get_process",
        title="Get Eproc process context",
        description=f"Read an Eproc process, parties, events, document inventory and up to five selected document texts. With save=true each read document is stored in the shared documents folder (HTML acts are rendered to PDF) and reported as savedAs, so a task can attach it by file name. Portals: {describe(keys)}. Read-only toward eproc.",
        annotations=READ_ONLY,
    )
    def eproc_get_process(
        portal: Portal,
        processNumber: Annotated[str, Field(min_length=1)],
        scope: Literal["latest", "page"] = "page",
        documentIds: Annotated[list[Annotated[str, Field(min_length=1)]], Field(max_length=5)] = [],
        save: bool = False,
    ) -> dict[str, Any]:
        return call(
            portal, lambda **context: fetch_process(processNumber, scope, documentIds or None, save=save, **context)
        )

    return server


def main():
    load_env_file()
    serve(create_server(), on_exit=sessions.close)


if __name__ == "__main__":
    main()
