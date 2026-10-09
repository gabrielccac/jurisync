"""INSS GERID MCP server: read-only PAT notifications, tasks, and downloads."""

import asyncio
import json
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field

from jurisync.connectors.inss.client import InssClient, read_saved_document, slice_text
from jurisync.core.mcp import READ_ONLY, load_env_file, sanitize_diagnostics, serve

LOCAL_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)
Digits = Annotated[str, Field(pattern=r"^\d+$")]
Filename = Annotated[str, Field(min_length=1, max_length=255)]
MaxChars = Annotated[int, Field(ge=1000, le=200_000, description="Largest piece of document text to return")]

client = InssClient()
server = MCPServer("inss-gerid", version="1.0.0")


async def call(operation):
    # Playwright raises its own Error type for browser/portal failures; all are anticipated here.
    try:
        value = await operation
    except Exception as error:
        raise ToolError(f"INSS request failed: {sanitize_diagnostics(error) or 'unknown'}") from error
    return value if isinstance(value, dict) else {"result": value}


def file_result(file, max_chars=40_000):
    """Describe a saved file and return the first window of its parsed text; the rest is read with inss_read_document."""
    return document_result(slice_text(file, 0, max_chars))


def document_result(file):
    file.pop("base64", None)
    markdown = file.pop("markdown", None)
    content = [TextContent(type="text", text=json.dumps(file, ensure_ascii=False, indent=2))]
    if markdown:
        content.append(TextContent(type="text", text=f"Extracted PDF Markdown:\n\n{markdown}"))
    return CallToolResult(content=content, structured_content={**file, "textReturned": bool(markdown)})


@server.tool(
    name="inss_gerid_ensure_session",
    title="Ensure INSS GERID session",
    description="Reuse a valid INSS PAT session headlessly or open Chrome for the user's A3/PIN login when required. Read-only.",
    annotations=READ_ONLY,
)
async def ensure_session() -> dict[str, Any]:
    return await call(client.ensure_session())


@server.tool(
    name="inss_list_notifications",
    title="List INSS PAT notifications",
    description="List notifications from the authenticated INSS PAT account. Read-only.",
    annotations=READ_ONLY,
)
async def list_notifications(first: Annotated[int, Field(ge=0)] = 0) -> dict[str, Any]:
    return await call(client.list_notifications(first))


@server.tool(
    name="inss_search_tasks",
    title="Search INSS PAT tasks",
    description="Search and paginate INSS PAT tasks with the portal's supported filters. Read-only.",
    annotations=READ_ONLY,
)
async def search_tasks(
    page: Annotated[int, Field(ge=1)] = 1,
    pageSize: Annotated[int, Field(ge=1, le=100)] = 20,
    cpf: str | None = None,
    nome: str | None = None,
    protocolo: str | None = None,
    status: str | None = None,
    dataAtualizacaoInicial: str | None = None,
    dataAtualizacaoFinal: str | None = None,
    situacaoTarefa: str | None = None,
) -> dict[str, Any]:
    return await call(
        client.search_tasks(
            page=page,
            pageSize=pageSize,
            cpf=cpf,
            nome=nome,
            protocolo=protocolo,
            status=status,
            dataAtualizacaoInicial=dataAtualizacaoInicial,
            dataAtualizacaoFinal=dataAtualizacaoFinal,
            situacaoTarefa=situacaoTarefa,
        )
    )


@server.tool(
    name="inss_get_task",
    title="Get INSS PAT task",
    description="Retrieve complete INSS PAT task details by protocol. Read-only.",
    annotations=READ_ONLY,
)
async def get_task(protocolo: Digits) -> dict[str, Any]:
    return await call(client.get_task(protocolo))


@server.tool(
    name="inss_get_attachment",
    title="Get INSS PAT attachment",
    description="Download one attachment listed by inss_get_task using its task protocol and attachment ID, and return its parsed text. Saves a new local file under downloaded_files/inss without overwriting existing files. Long documents return the first maxChars characters; continue with inss_read_document using nextOffset.",
    annotations=LOCAL_WRITE,
)
async def get_attachment(
    protocolo: Digits, anexoId: Digits, filename: Filename | None = None, maxChars: MaxChars = 40_000
) -> CallToolResult:
    return file_result(await call(client.download_attachment(protocolo, anexoId, filename)), maxChars)


@server.tool(
    name="inss_get_process",
    title="Get consolidated INSS PAT process",
    description="Generate and download the complete consolidated process report as a PDF, and return its parsed text. CPF and name are read from the active GERID credential. Saves a new local file under downloaded_files/inss without overwriting existing files. Long documents return the first maxChars characters; continue with inss_read_document using nextOffset.",
    annotations=LOCAL_WRITE,
)
async def get_process(
    protocolo: Digits, filename: Filename | None = None, maxChars: MaxChars = 40_000
) -> CallToolResult:
    return file_result(await call(client.download_process(protocolo, filename)), maxChars)


@server.tool(
    name="inss_read_document",
    title="Read a saved INSS document",
    description="Parse a file already saved by inss_get_process or inss_get_attachment and return one window of its text. Pass only the file name from the earlier result, and nextOffset to continue. No GERID login is needed.",
    annotations=READ_ONLY,
)
async def read_document(
    filename: Filename, offset: Annotated[int, Field(ge=0)] = 0, maxChars: MaxChars = 40_000
) -> CallToolResult:
    document = await call(asyncio.to_thread(read_saved_document, filename, offset, maxChars))
    return document_result(document)


def main():
    load_env_file()
    serve(server)


if __name__ == "__main__":
    main()
