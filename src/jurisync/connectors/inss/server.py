"""INSS GERID MCP server: read-only PAT notifications, tasks, and downloads."""

import json
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field

from jurisync.connectors.inss.client import InssClient
from jurisync.core.mcp import READ_ONLY, load_env_file, run_stdio, sanitize_diagnostics

LOCAL_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)
Digits = Annotated[str, Field(pattern=r"^\d+$")]
Filename = Annotated[str, Field(min_length=1, max_length=255)]

client = InssClient()
server = MCPServer("inss-gerid", version="1.0.0")


async def call(operation):
    # Playwright raises its own Error type for browser/portal failures; all are anticipated here.
    try:
        value = await operation
    except Exception as error:
        raise ToolError(f"INSS request failed: {sanitize_diagnostics(error) or 'unknown'}") from error
    return value if isinstance(value, dict) else {"result": value}


def file_result(file):
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
    name="inss_download_attachment",
    title="Download INSS PAT attachment",
    description="Download one attachment listed by inss_get_task using its task protocol and attachment ID. Saves a new local file under downloaded_files/inss without overwriting existing files.",
    annotations=LOCAL_WRITE,
)
async def download_attachment(protocolo: Digits, anexoId: Digits, filename: Filename | None = None) -> CallToolResult:
    return file_result(await call(client.download_attachment(protocolo, anexoId, filename)))


@server.tool(
    name="inss_download_process",
    title="Download consolidated INSS PAT process",
    description="Generate and download the complete consolidated process report as a PDF. CPF and name are read from the active GERID credential. Saves a new local file under downloaded_files/inss without overwriting existing files.",
    annotations=LOCAL_WRITE,
)
async def download_process(protocolo: Digits, filename: Filename | None = None) -> CallToolResult:
    return file_result(await call(client.download_process(protocolo, filename)))


def main():
    load_env_file()
    run_stdio(server)


if __name__ == "__main__":
    main()
