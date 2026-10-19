"""DJEN MCP server: public communication search, certidão text, and deadlines."""

import json
import urllib.error
from datetime import date
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import BeforeValidator, Field

from jurisync.connectors.djen.client import DjenError, fetch_certidao, search_page
from jurisync.connectors.djen.deadlines import calculate_deadline
from jurisync.connectors.djen.normalize import compact_item, normalize_item
from jurisync.core.document_text import extract_document_text
from jurisync.core.mcp import READ_ONLY, load_env_file, serve, tool_errors

REQUEST_ERRORS = (DjenError, ValueError, urllib.error.URLError, TimeoutError)

server = MCPServer("djen", version="1.0.0")

# Clients send digit-only strings such as '61216' as JSON numbers; take them back as text.
DIGITS_AS_TEXT = BeforeValidator(lambda value: str(value) if type(value) is int else value)


@server.tool(
    name="djen_search_communications",
    title="Search DJEN communications",
    description="Read one page of public DJEN communications for a date window and at least one search key: OAB + UF, advogado name (nomeAdvogado), party name, free text (texto) or process number, with optional tribunal and meio (D = Diário, E = eletrônico). Returns normalized text and metadata; routing decisions belong to the skill. A full 50-item page can exceed tool output limits: compact=true keeps one copy of the text plus the fields routing needs, and textLimit cuts each text (texto_chars keeps the original length; the full text stays available through djen_get_certidao_text). No authentication or poll-state mutation.",
    annotations=READ_ONLY,
)
def search_communications(
    inicio: Annotated[
        date,
        Field(
            description="Start of the availability window, ISO YYYY-MM-DD (e.g. '2026-10-15'). DD/MM/AAAA is refused."
        ),
    ],
    fim: Annotated[
        date, Field(description="End of the window, ISO YYYY-MM-DD, inclusive. Same day as inicio for one day.")
    ],
    numeroOab: Annotated[
        str | None,
        DIGITS_AS_TEXT,
        Field(
            pattern=r"(?i)^\d+(?:-[ABENOS])?$",
            description="OAB number without the UF, e.g. '14663' (supplementary registrations: '14663-A'). Requires ufOab.",
        ),
    ] = None,
    ufOab: Annotated[
        str | None, Field(pattern=r"(?i)^[A-Z]{2}$", description="Two-letter UF of the OAB, e.g. 'PB'.")
    ] = None,
    nomeAdvogado: Annotated[
        str | None,
        Field(
            min_length=3,
            max_length=120,
            description="Lawyer name as it appears in the publication, 3 to 120 characters, e.g. 'VICTOR HUGO VALERIANO PINTO'.",
        ),
    ] = None,
    nomeParte: Annotated[
        str | None, Field(min_length=3, max_length=120, description="Party name, 3 to 120 characters.")
    ] = None,
    texto: Annotated[
        str | None,
        Field(min_length=3, max_length=200, description="Free text found in the publication, 3 to 200 characters."),
    ] = None,
    numeroProcesso: Annotated[
        str | None,
        DIGITS_AS_TEXT,
        Field(
            pattern=r"^\d{7,25}$",
            description="Process number with DIGITS ONLY, no dots or dashes: '50043207620264047104' for 5004320-76.2026.4.04.7104. A formatted number is refused.",
        ),
    ] = None,
    meio: Annotated[
        Literal["D", "E"] | None,
        Field(description="Medium: 'D' = Diário (Diário de Justiça), 'E' = eletrônico. Other values are refused."),
    ] = None,
    tribunal: Annotated[
        str | None,
        Field(
            pattern=r"(?i)^[A-Z0-9]+$",
            description="Court acronym, letters and digits, e.g. 'TRF5', 'TJSP'. Leave empty for all courts.",
        ),
    ] = None,
    pagina: Annotated[int, Field(ge=1, description="Page of results, starting at 1.")] = 1,
    itens: Annotated[int, Field(ge=5, le=50, description="Items per page, 5 to 50; default 10.")] = 10,
    compact: Annotated[
        bool, Field(description="true keeps one copy of the text plus the routing fields, which reduces output size.")
    ] = False,
    textLimit: Annotated[
        int,
        Field(
            ge=0,
            le=100_000,
            description="Max characters per text when compact is used; 0 means no cut. texto_chars keeps the original length.",
        ),
    ] = 0,
) -> dict[str, Any]:
    params = {
        "dataDisponibilizacaoInicio": inicio.isoformat(),
        "dataDisponibilizacaoFim": fim.isoformat(),
    }
    optional = {
        "numeroOab": numeroOab,
        "ufOab": ufOab,
        "nomeAdvogado": nomeAdvogado,
        "nomeParte": nomeParte,
        "texto": texto,
        "numeroProcesso": numeroProcesso,
        "meio": meio,
        "siglaTribunal": tribunal,
    }
    params.update({key: value for key, value in optional.items() if value})
    with tool_errors("DJEN", *REQUEST_ERRORS):
        if numeroOab and not ufOab:
            raise ValueError("numeroOab requires ufOab")
        result = search_page(params, pagina, itens)
    items = [normalize_item(item) for item in result["items"]]
    result["items"] = [compact_item(item, textLimit) for item in items] if compact else items
    return result


@server.tool(
    name="djen_get_certidao_text",
    title="Read DJEN certidão text",
    description="Fetch a public DJEN certidão PDF by communication hash and return extracted text with extraction status. Scanned PDFs may report ocr_needed.",
    annotations=READ_ONLY,
)
def get_certidao_text(
    hash: Annotated[
        str,
        Field(
            pattern=r"^[A-Za-z0-9]{1,128}$",
            description="The 'hash' field of a DJEN item from djen_search_communications, letters and digits only.",
        ),
    ],
    textLimit: Annotated[
        int, Field(ge=1, le=100_000, description="Max characters of the extracted text, 1 to 100000; default 50000.")
    ] = 50_000,
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
    availableOn: Annotated[
        date,
        Field(
            description="Date the communication became available in the DJEN, ISO YYYY-MM-DD. The publication is the next business day and counting starts after it."
        ),
    ],
    days: Annotated[
        int, Field(ge=1, le=365, description="Number of business days in the procedural term, 1 to 365, e.g. 5.")
    ],
    calendar: Annotated[
        Literal["jfpe-2026-q4"],
        Field(
            description="Court calendar id. Only 'jfpe-2026-q4' exists (JFPE, Q4 2026); dates outside that period are not covered."
        ),
    ] = "jfpe-2026-q4",
) -> dict[str, Any]:
    with tool_errors("DJEN deadline", ValueError, KeyError, OSError, json.JSONDecodeError):
        return calculate_deadline(availableOn.isoformat(), days, calendar)


def main():
    load_env_file()
    serve(server)


if __name__ == "__main__":
    main()
