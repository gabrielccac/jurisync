"""DJEN MCP server: public communication search, certidão text, and deadlines."""

import json
import urllib.error
from datetime import date
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from jurisync.connectors.djen.client import DjenError, fetch_certidao, search_page
from jurisync.connectors.djen.deadlines import calculate_deadline
from jurisync.connectors.djen.normalize import normalize_item
from jurisync.core.document_text import extract_document_text
from jurisync.core.mcp import READ_ONLY, load_env_file, run_stdio, tool_errors

REQUEST_ERRORS = (DjenError, ValueError, urllib.error.URLError, TimeoutError)

server = MCPServer("djen", version="1.0.0")


@server.tool(
    name="djen_search_communications",
    title="Search DJEN communications",
    description="Read one page of public DJEN communications by exact OAB, UF, date window, and optional tribunal. Returns normalized text and metadata; routing decisions belong to the skill. No authentication or poll-state mutation.",
    annotations=READ_ONLY,
)
def search_communications(
    numeroOab: Annotated[str, Field(pattern=r"(?i)^\d+(?:-[ABENOS])?$")],
    ufOab: Annotated[str, Field(pattern=r"(?i)^[A-Z]{2}$")],
    inicio: date,
    fim: date,
    tribunal: Annotated[str | None, Field(pattern=r"(?i)^[A-Z0-9]+$")] = None,
    pagina: Annotated[int, Field(ge=1)] = 1,
    itens: Annotated[int, Field(ge=5, le=50)] = 10,
) -> dict[str, Any]:
    params = {
        "numeroOab": numeroOab,
        "ufOab": ufOab,
        "dataDisponibilizacaoInicio": inicio.isoformat(),
        "dataDisponibilizacaoFim": fim.isoformat(),
    }
    if tribunal:
        params["siglaTribunal"] = tribunal
    with tool_errors("DJEN", *REQUEST_ERRORS):
        result = search_page(params, pagina, itens)
    result["items"] = [normalize_item(item) for item in result["items"]]
    return result


@server.tool(
    name="djen_get_certidao_text",
    title="Read DJEN certidão text",
    description="Fetch a public DJEN certidão PDF by communication hash and return extracted text with extraction status. Scanned PDFs may report ocr_needed.",
    annotations=READ_ONLY,
)
def get_certidao_text(
    hash: Annotated[str, Field(pattern=r"^[A-Za-z0-9]{1,128}$")],
    textLimit: Annotated[int, Field(ge=1, le=100_000)] = 50_000,
) -> dict[str, Any]:
    with tool_errors("DJEN", *REQUEST_ERRORS):
        pdf = fetch_certidao(hash)
    status, text = extract_document_text(pdf, "application/pdf")
    return {
        "hash": hash,
        "bytes": len(pdf),
        "extractionStatus": status,
        "text": text[:textLimit],
        "textTruncated": len(text) > textLimit,
    }


@server.tool(
    name="djen_calculate_deadline",
    title="Calculate DJEN judicial deadline",
    description="Count a verified number of procedural business days from DJEN availability using the sourced JFPE court calendar. Returns publication, first counted day, counted dates, and fatal date for ADVBOX. Only covers the calendar's declared period and court; does not infer a period from notice text.",
    annotations=READ_ONLY,
)
def deadline(
    availableOn: date,
    days: Annotated[int, Field(ge=1, le=365)],
    calendar: Literal["jfpe-2026-q4"] = "jfpe-2026-q4",
) -> dict[str, Any]:
    with tool_errors("DJEN deadline", ValueError, KeyError, OSError, json.JSONDecodeError):
        return calculate_deadline(availableOn.isoformat(), days, calendar)


def main():
    load_env_file()
    run_stdio(server)


if __name__ == "__main__":
    main()
