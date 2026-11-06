"""DJEN MCP server: public communication search, certidão text, and deadlines."""

import json
import urllib.error
from datetime import date
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import BeforeValidator, Field

from jurisync import customers
from jurisync.connectors.djen import render
from jurisync.connectors.djen.client import DjenError, fetch_certidao, search_page
from jurisync.connectors.djen.deadlines import calculate_deadline
from jurisync.connectors.djen.documents import resolve_portal
from jurisync.connectors.djen.normalize import compact_item, normalize_item
from jurisync.core import credentials
from jurisync.core.document_text import extract_document_text
from jurisync.core.mcp import READ_ONLY, load_env_file, serve, tool_errors

REQUEST_ERRORS = (DjenError, ValueError, urllib.error.URLError, TimeoutError)

server = MCPServer("djen", version="1.0.0")

FORMAT_HELP = (
    "'json' (default) returns the items for routing. 'linhas' returns checks plus one line per item; 'resumo' returns "
    "checks, counts by category and the items grouped by category with the operative sentence. Both text views are "
    "for reading and triage; textLimit sets the characters of that sentence (default 140)."
)


def as_text(items, declared, pages, complete, formato, text_limit):
    """The 'linhas'/'resumo' view: result checks plus rendered text instead of item objects."""
    items, removed = render.dedupe(items)
    check = render.checks(int(declared or 0), items, removed, pages, complete)
    limit = text_limit or 140
    body = (
        render.summary(items, min(limit, 110), hint="djen_get_certidao_text")
        if formato == "resumo"
        else render.lines(items, limit)
    )
    return {"checagem": check, "texto": render.checks_line(check) + "\n" + body}


# Clients send digit-only strings such as '61216' as JSON numbers; take them back as text.
# The `pattern` rules sit on the inner `str` so pydantic >= 2.14 never applies them to None.
DIGITS_AS_TEXT = BeforeValidator(lambda value: str(value) if type(value) is int else value)


@server.tool(
    name="djen_search_communications",
    title="Search DJEN communications",
    description="Read one page of public DJEN communications for a date window and at least one search key: OAB + UF, advogado name (nomeAdvogado), party name, free text (texto) or process number, with optional tribunal and meio (D = Diário, E = eletrônico). Returns normalized text and metadata; routing decisions belong to the skill. Each item has portal: the court system that issued it (portal key such as 'trf5-1g' or 'trf5-2g', read from the communication link host; from the CNJ number when there is no link, which can list several keys like 'trf5-1g/trf5-2g'). A full 50-item page can exceed tool output limits: compact=true keeps one copy of the text plus the fields routing needs, and textLimit cuts each text (texto_chars keeps the original length; the full text stays available through djen_get_certidao_text). No authentication or poll-state mutation.",
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
        Annotated[str, DIGITS_AS_TEXT, Field(pattern=r"(?i)^\d+(?:-[ABENOS])?$")] | None,
        Field(
            description="OAB number without the UF, e.g. '14663' (supplementary registrations: '14663-A'). Requires ufOab.",
        ),
    ] = None,
    ufOab: Annotated[
        Annotated[str, Field(pattern=r"(?i)^[A-Z]{2}$")] | None,
        Field(description="Two-letter UF of the OAB, e.g. 'PB'."),
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
        Annotated[str, DIGITS_AS_TEXT, Field(pattern=r"^\d{7,25}$")] | None,
        Field(
            description="Process number with DIGITS ONLY, no dots or dashes: '50043207620264047104' for 5004320-76.2026.4.04.7104. A formatted number is refused.",
        ),
    ] = None,
    meio: Annotated[
        Literal["D", "E"] | None,
        Field(description="Medium: 'D' = Diário (Diário de Justiça), 'E' = eletrônico. Other values are refused."),
    ] = None,
    tribunal: Annotated[
        Annotated[str, Field(pattern=r"(?i)^[A-Z0-9]+$")] | None,
        Field(
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
    formato: Annotated[
        Literal["json", "linhas", "resumo"],
        Field(description=FORMAT_HELP),
    ] = "json",
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
    for item in items:
        item["portal"] = resolve_portal(item)
    if formato != "json":
        complete = pagina == 1 and itens >= int(result.get("count") or 0)
        return as_text([compact_item(item, 0) for item in items], result.get("count"), 1, complete, formato, textLimit)
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
    description="Count a verified number of procedural business days from DJEN availability using the sourced JFPE court calendar. Returns publication, first counted day, counted dates, and the fatal date (ISO and DD/MM/AAAA). Only covers the calendar's declared period and court; does not infer a period from notice text.",
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


SEARCH_KEYS = ("numeroOab", "ufOab", "nomeAdvogado", "nomeParte", "texto", "tribunal")


def saved_searches():
    """The office's saved DJEN searches: ``DJEN_SEARCHES`` (JSON list) for the account, else the customer's
    ``settings.toml`` ``[[djen.searches]]`` tables. Each search has a ``label`` and OAB + UF or another key."""
    raw = credentials.env("DJEN_SEARCHES").strip()
    searches = json.loads(raw) if raw else customers.settings().get("djen", {}).get("searches", [])
    if not isinstance(searches, list):
        raise ValueError("DJEN_SEARCHES must be a JSON list of searches")
    result = []
    for index, search in enumerate(searches):
        if not isinstance(search, dict):
            raise ValueError("Each saved DJEN search is a table with a label and its keys")
        entry = {key: str(search[key]).strip() for key in SEARCH_KEYS if str(search.get(key, "")).strip()}
        if entry.get("numeroOab") and not entry.get("ufOab"):
            raise ValueError(f"Saved search {index + 1}: numeroOab requires ufOab")
        if not any(key in entry for key in ("numeroOab", "nomeAdvogado", "nomeParte", "texto")):
            raise ValueError(f"Saved search {index + 1} has no search key")
        entry["label"] = str(search.get("label") or f"busca {index + 1}")
        result.append(entry)
    return result


@server.tool(
    name="djen_list_saved_searches",
    title="List the office's saved DJEN searches",
    description="List the DJEN searches saved for this office (OAB + UF, lawyer name, party or text, optional court), the same ones djen_search_saved runs. Read-only.",
    annotations=READ_ONLY,
)
def list_saved_searches() -> dict[str, Any]:
    with tool_errors("DJEN", ValueError, json.JSONDecodeError):
        return {"searches": saved_searches()}


@server.tool(
    name="djen_search_saved",
    title="Run the office's saved DJEN searches",
    description="Run every saved DJEN search of this office for one date window and one page, and merge the communications without duplicates (same id). Each search reports its count and whether more pages exist; call again with pagina+1 while any search has more. tribunal narrows every search to one court (e.g. 'TRF5'). compact and textLimit work as in djen_search_communications. Read-only.",
    annotations=READ_ONLY,
)
def search_saved(
    inicio: Annotated[date, Field(description="Start of the availability window, ISO YYYY-MM-DD.")],
    fim: Annotated[date, Field(description="End of the window, ISO YYYY-MM-DD, inclusive.")],
    tribunal: Annotated[
        Annotated[str, Field(pattern=r"(?i)^[A-Z0-9]+$")] | None,
        Field(description="Court acronym for every search, e.g. 'TRF5'. Empty keeps each search's own court."),
    ] = None,
    meio: Annotated[Literal["D", "E"] | None, Field(description="'D' = Diário, 'E' = eletrônico.")] = None,
    pagina: Annotated[int, Field(ge=1, description="Page of every search, starting at 1.")] = 1,
    itens: Annotated[int, Field(ge=5, le=50, description="Items per page and search, 5 to 50; default 50.")] = 50,
    compact: Annotated[bool, Field(description="true keeps one copy of the text plus the routing fields.")] = True,
    textLimit: Annotated[int, Field(ge=0, le=100_000, description="Max characters per text when compact.")] = 1500,
    formato: Annotated[Literal["json", "linhas", "resumo"], Field(description=FORMAT_HELP)] = "json",
) -> dict[str, Any]:
    text_view = formato != "json"
    with tool_errors("DJEN", *REQUEST_ERRORS, json.JSONDecodeError):
        searches = saved_searches()
        if not searches:
            raise ValueError("This office has no saved DJEN searches")
        report, items, seen = [], [], set()
        for search in searches:
            params = {"dataDisponibilizacaoInicio": inicio.isoformat(), "dataDisponibilizacaoFim": fim.isoformat()}
            court = tribunal or search.get("tribunal")
            for key, value in (
                *((k, search.get(k)) for k in SEARCH_KEYS if k != "tribunal"),
                ("siglaTribunal", court),
                ("meio", meio),
            ):
                if value:
                    params[key] = value
            result = search_page(params, pagina, itens)
            fresh = 0
            for raw in result["items"]:
                item = normalize_item(raw)
                key = item.get("id") or item.get("hash")
                if key in seen:
                    continue
                seen.add(key)
                fresh += 1
                item["portal"] = resolve_portal(item)
                item["savedSearch"] = search["label"]
                items.append(compact_item(item, 0) if text_view else compact_item(item, textLimit) if compact else item)
            count = int(result.get("count") or 0)
            report.append(
                {
                    "label": search["label"],
                    "tribunal": court,
                    "count": count,
                    "pagina": pagina,
                    "returned": len(result["items"]),
                    "new": fresh,
                    "more": pagina * itens < count,
                }
            )
        if text_view:
            returned = sum(r["returned"] for r in report)
            check = render.checks(
                sum(r["count"] for r in report),
                items,
                returned - len(items),
                len(report),
                not any(r["more"] for r in report),
            )
            searches_line = " ; ".join(f"{r['label']}={r['count']}{'+' if r['more'] else ''}" for r in report)
            limit = textLimit if textLimit != 1500 else 140
            body = (
                render.summary(items, min(limit, 110), hint="djen_get_certidao_text")
                if formato == "resumo"
                else render.lines(items, limit)
            )
            text = "\n".join([render.checks_line(check), f"# buscas: {searches_line}", body])
            return {"inicio": inicio.isoformat(), "fim": fim.isoformat(), "checagem": check, "texto": text}
        return {"inicio": inicio.isoformat(), "fim": fim.isoformat(), "searches": report, "items": items}


def main():
    load_env_file()
    serve(server)


if __name__ == "__main__":
    main()
