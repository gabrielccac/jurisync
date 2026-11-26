"""DJEN MCP server: public communication search, certidão text, and deadlines."""

import contextvars
import json
import re
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BeforeValidator, Field

from jurisync import customers
from jurisync.connectors.djen import render
from jurisync.connectors.djen.client import DjenError, _check_window, each_variant, fetch_certidao, search_page
from jurisync.connectors.djen.deadlines import DEFAULT_COURT, calculate_deadline
from jurisync.connectors.djen.documents import resolve_portal
from jurisync.connectors.djen.normalize import compact_item, normalize_item
from jurisync.core import credentials
from jurisync.core.document_text import extract_document_text
from jurisync.core.mcp import READ_ONLY, JurisyncServer, load_env_file, sanitize_diagnostics, serve, tool_errors

REQUEST_ERRORS = (DjenError, ValueError, urllib.error.URLError, TimeoutError)


FORMAT_HELP = (
    "'json': items. 'linhas' (line per item) or 'resumo' (by category; operative sentence of textLimit chars, "
    "default 140): checks + triage text."
)


def as_text(items, declared, pages, complete, formato, text_limit, merged=0):
    """The 'linhas'/'resumo' view: result checks plus rendered text instead of item objects.

    ``merged`` is how many repeated items the search already dropped (the same id under two OAB variants)."""
    items, removed = render.dedupe(items)
    check = render.checks(int(declared or 0), items, removed + merged, pages, complete)
    limit = text_limit or 140
    body = (
        render.summary(items, min(limit, 110), hint="djen_get_certidao_text")
        if formato == "resumo"
        else render.lines(items, limit)
    )
    return {"checagem": check, "texto": render.checks_line(check) + "\n" + body}


OAB_VARIANTS_HELP = "true: also OAB suffixes -A -B -E -N -O -S, merged; false: exact numeroOab."
MAX_SAVED_WINDOW_DAYS = 31
MAX_PARALLEL_SEARCHES = 4


def run_search(params, pagina, itens, variants=True):
    """One page of one search. With an OAB and ``variants``, one page per registration suffix, merged by id.

    Returns ``count`` (sum of the variants' totals), ``returned`` (items read before merging), ``more`` and, when
    the variants ran, ``oabVariants`` with each suffix's numbers. The variants are read in parallel; one that fails
    is reported with its ``error`` and the others are kept. When every variant fails, the first error is raised.
    """
    number = params.get("numeroOab")
    if not (number and variants):
        result = search_page(params, pagina, itens)
        count = int(result.get("count") or 0)
        return {**result, "count": count, "returned": len(result["items"]), "more": pagina * itens < count}
    base = re.sub(r"(?i)-[ABENOS]$", "", str(number))
    results = each_variant(lambda suffix: search_page({**params, "numeroOab": base + suffix}, pagina, itens))
    if all(isinstance(page, Exception) for _, page in results):
        raise results[0][1]
    items, seen, report = [], set(), []
    for suffix, page in results:
        if isinstance(page, Exception):
            error = sanitize_diagnostics(page) or type(page).__name__
            report.append({"numeroOab": base + suffix, "count": 0, "returned": 0, "more": False, "error": error})
            continue
        count = int(page.get("count") or 0)
        for item in page["items"]:
            if item["id"] not in seen:
                seen.add(item["id"])
                items.append(item)
        report.append(
            {
                "numeroOab": base + suffix,
                "count": count,
                "returned": len(page["items"]),
                "more": pagina * itens < count,
            }
        )
    return {
        "status": "success",
        "count": sum(r["count"] for r in report),
        "items": items,
        "returned": sum(r["returned"] for r in report),
        "more": any(r["more"] for r in report),
        "oabVariants": report,
    }


def _failed_variants(result):
    return [variant["numeroOab"] for variant in result.get("oabVariants") or () if "error" in variant]


# Clients send digit-only strings such as '61216' as JSON numbers; take them back as text.
# The `pattern` rules sit on the inner `str` so pydantic >= 2.14 never applies them to None.
DIGITS_AS_TEXT = BeforeValidator(lambda value: str(value) if type(value) is int else value)


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


def create_server():
    server = JurisyncServer("djen")

    @server.tool(
        name="djen_search_communications",
        title="Search DJEN communications",
        description="One page of public DJEN communications for a date window and at least one key (OAB + UF, nomeAdvogado, nomeParte, texto, numeroProcesso). Item portal = issuing system key ('trf5-1g'); when inferred from the CNJ number it can list several ('trf5-1g/trf5-2g'). compact/textLimit shrink big pages (texto_chars = original length; full text: djen_get_certidao_text). No authentication or poll-state mutation.",
        annotations=READ_ONLY,
    )
    def search_communications(
        inicio: Annotated[
            date,
            Field(description="YYYY-MM-DD."),
        ],
        fim: Annotated[date, Field(description="YYYY-MM-DD, inclusive.")],
        numeroOab: Annotated[
            Annotated[str, DIGITS_AS_TEXT, Field(pattern=r"(?i)^\d+(?:-[ABENOS])?$")] | None,
            Field(
                description="OAB number without UF, e.g. '14663' or '14663-A'. Requires ufOab.",
            ),
        ] = None,
        ufOab: Annotated[
            Annotated[str, Field(pattern=r"(?i)^[A-Z]{2}$")] | None,
            Field(description="e.g. 'PB'."),
        ] = None,
        nomeAdvogado: Annotated[
            str | None,
            Field(
                min_length=3,
                max_length=120,
                description="As published.",
            ),
        ] = None,
        nomeParte: Annotated[str | None, Field(min_length=3, max_length=120)] = None,
        texto: Annotated[
            str | None,
            Field(min_length=3, max_length=200),
        ] = None,
        numeroProcesso: Annotated[
            Annotated[str, DIGITS_AS_TEXT, Field(pattern=r"^\d{7,25}$")] | None,
            Field(
                description="Digits only, no punctuation.",
            ),
        ] = None,
        meio: Annotated[
            Literal["D", "E"] | None,
            Field(description="'D' = Diário, 'E' = eletrônico."),
        ] = None,
        tribunal: Annotated[
            Annotated[str, Field(pattern=r"(?i)^[A-Z0-9]+$")] | None,
            Field(
                description="e.g. 'TRF5'; empty = all.",
            ),
        ] = None,
        pagina: Annotated[int, Field(ge=1)] = 1,
        itens: Annotated[int, Field(ge=5, le=50)] = 10,
        compact: Annotated[bool, Field(description="Routing fields + one text copy.")] = False,
        textLimit: Annotated[
            int,
            Field(
                ge=0,
                le=100_000,
                description="Max chars per text when compact; 0 = no cut.",
            ),
        ] = 0,
        formato: Annotated[
            Literal["json", "linhas", "resumo"],
            Field(description=FORMAT_HELP),
        ] = "json",
        oabVariants: Annotated[bool, Field(description=OAB_VARIANTS_HELP)] = True,
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
        with tool_errors("DJEN"):
            if numeroOab and not ufOab:
                raise ValueError("numeroOab requires ufOab")
            _check_window(inicio.isoformat(), fim.isoformat())
            result = run_search(params, pagina, itens, oabVariants)
        more = result.pop("more")
        merged = result.pop("returned") - len(result["items"])
        items = [normalize_item(item) for item in result["items"]]
        for item in items:
            item["portal"] = resolve_portal(item)
        if formato != "json":
            complete = pagina == 1 and not more and not _failed_variants(result)
            pages = len(result.get("oabVariants") or [1])
            return as_text(
                [compact_item(item, 0) for item in items],
                result.get("count"),
                pages,
                complete,
                formato,
                textLimit,
                merged,
            )
        result["items"] = [compact_item(item, textLimit) for item in items] if compact else items
        return result

    @server.tool(
        name="djen_get_certidao_text",
        title="Read DJEN certidão text",
        description="Text of a public DJEN certidão PDF by hash, with extractionStatus (scanned: ocr_needed) and textTruncated.",
        annotations=READ_ONLY,
    )
    def get_certidao_text(
        hash: Annotated[
            str,
            Field(
                pattern=r"^[A-Za-z0-9]{1,128}$",
                description="Item 'hash' from djen_search_communications.",
            ),
        ],
        textLimit: Annotated[int, Field(ge=1, le=100_000)] = 50_000,
    ) -> dict[str, Any]:
        with tool_errors("DJEN"):
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
        description="Count procedural business days (CPC art. 219) from DJEN availability to the fatal date. Base calendar: weekends, national holidays, CPC art. 220 suspension (20/12-20/01); Federal Justice also Lei 5.010/66 art. 62 and Corpus Christi. Without a local calendar for the court and period, calendarComplete is false and warnings lists local holidays to check before fixing the date. Does not infer the term from notice text nor count calendar-day (criminal) terms.",
        annotations=READ_ONLY,
    )
    def deadline(
        availableOn: Annotated[
            date,
            Field(description="DJEN availability date, YYYY-MM-DD."),
        ],
        days: Annotated[int, Field(ge=1, le=365, description="Business days.")],
        calendar: Annotated[
            Literal["jfpe-2026-q4"] | None,
            Field(description="Legacy; same as court='JFPE'. Prefer court."),
        ] = None,
        court: Annotated[
            Annotated[str, Field(pattern=r"^[A-Za-z0-9]{2,12}$")] | None,
            Field(
                description="Court calendar, e.g. 'JFPE', 'TRF5'. Default: office setting, else JFPE (the only local calendar)."
            ),
        ] = None,
    ) -> dict[str, Any]:
        with tool_errors("DJEN deadline"):
            configured = (
                None if court or calendar else str(customers.settings().get("djen", {}).get("court") or "").strip()
            )
            result = calculate_deadline(availableOn.isoformat(), days, calendar, court or configured or None)
            if configured:  # the office's own court is not an assumption
                result["warnings"] = [w for w in result["warnings"] if not w.startswith("Nenhum tribunal informado")]
            elif not court and not calendar:
                result["courtSource"] = f"default ({DEFAULT_COURT})"
            return result

    @server.tool(
        name="djen_list_saved_searches",
        title="List the office's saved DJEN searches",
        description="Saved DJEN searches of this office (run by djen_search_saved). Read-only.",
        annotations=READ_ONLY,
    )
    def list_saved_searches() -> dict[str, Any]:
        with tool_errors("DJEN"):
            return {"searches": saved_searches()}

    @server.tool(
        name="djen_search_saved",
        title="Run the office's saved DJEN searches",
        description="Run all saved DJEN searches for one window (max 31 days) and page, merged; items as in djen_search_communications plus savedSearch. Repeat with pagina+1 while any search has more. A failed search reports error and complete=false (its items are missing). Read-only.",
        annotations=READ_ONLY,
    )
    def search_saved(
        inicio: Annotated[date, Field(description="YYYY-MM-DD.")],
        fim: Annotated[date, Field(description="YYYY-MM-DD, inclusive.")],
        tribunal: Annotated[
            Annotated[str, Field(pattern=r"(?i)^[A-Z0-9]+$")] | None,
            Field(description="Overrides each search's court."),
        ] = None,
        meio: Annotated[Literal["D", "E"] | None, Field(description="'D' = Diário, 'E' = eletrônico.")] = None,
        pagina: Annotated[int, Field(ge=1)] = 1,
        itens: Annotated[int, Field(ge=5, le=50, description="Per search.")] = 50,
        compact: Annotated[bool, Field(description="Routing fields + one text copy.")] = True,
        textLimit: Annotated[
            int, Field(ge=0, le=100_000, description="Max chars per text when compact; 0 = no cut.")
        ] = 1500,
        formato: Annotated[Literal["json", "linhas", "resumo"], Field(description=FORMAT_HELP)] = "json",
        oabVariants: Annotated[bool, Field(description=OAB_VARIANTS_HELP)] = True,
    ) -> dict[str, Any]:
        text_view = formato != "json"
        with tool_errors("DJEN"):
            _check_window(inicio.isoformat(), fim.isoformat(), MAX_SAVED_WINDOW_DAYS)
            searches = saved_searches()
            if not searches:
                raise ValueError("This office has no saved DJEN searches")

            def run(search):
                params = {"dataDisponibilizacaoInicio": inicio.isoformat(), "dataDisponibilizacaoFim": fim.isoformat()}
                for key, value in (
                    *((k, search.get(k)) for k in SEARCH_KEYS if k != "tribunal"),
                    ("siglaTribunal", tribunal or search.get("tribunal")),
                    ("meio", meio),
                ):
                    if value:
                        params[key] = value
                try:
                    return run_search(params, pagina, itens, oabVariants)
                except REQUEST_ERRORS as error:
                    return error

            # Each search runs in a copy of this call's context, so the office's credentials reach the worker threads.
            with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL_SEARCHES, len(searches))) as pool:
                futures = [pool.submit(contextvars.copy_context().run, run, search) for search in searches]
                results = [future.result() for future in futures]
            failures = [result for result in results if isinstance(result, Exception)]
            if len(failures) == len(results):
                raise failures[0]
            report, items, seen = [], [], set()
            for search, result in zip(searches, results, strict=True):
                entry = {"label": search["label"], "tribunal": tribunal or search.get("tribunal")}
                if isinstance(result, Exception):
                    entry.update(count=0, pagina=pagina, returned=0, new=0, more=False)
                    entry["error"] = sanitize_diagnostics(result) or "unknown"
                    report.append(entry)
                    continue
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
                    items.append(
                        compact_item(item, 0) if text_view else compact_item(item, textLimit) if compact else item
                    )
                entry.update(
                    count=result["count"], pagina=pagina, returned=result["returned"], new=fresh, more=result["more"]
                )
                if "oabVariants" in result:
                    entry["oabVariants"] = result["oabVariants"]
                    if failed := [v for v in result["oabVariants"] if "error" in v]:
                        entry["error"] = "; ".join(f"{v['numeroOab']}: {v['error']}" for v in failed)
                report.append(entry)
            complete = not any("error" in entry for entry in report)
            if text_view:
                returned = sum(r["returned"] for r in report)
                check = render.checks(
                    sum(r["count"] for r in report),
                    items,
                    returned - len(items),
                    len(report),
                    complete and not any(r["more"] for r in report),
                )
                searches_line = " ; ".join(
                    f"{r['label']}=ERRO" if "error" in r else f"{r['label']}={r['count']}{'+' if r['more'] else ''}"
                    for r in report
                )
                limit = textLimit if textLimit != 1500 else 140
                body = (
                    render.summary(items, min(limit, 110), hint="djen_get_certidao_text")
                    if formato == "resumo"
                    else render.lines(items, limit)
                )
                lines = [render.checks_line(check), f"# buscas: {searches_line}"]
                lines += [f"# ERRO {r['label']}: {r['error']} (resultado incompleto)" for r in report if "error" in r]
                return {
                    "inicio": inicio.isoformat(),
                    "fim": fim.isoformat(),
                    "complete": complete,
                    "checagem": check,
                    "texto": "\n".join([*lines, body]),
                }
            return {
                "inicio": inicio.isoformat(),
                "fim": fim.isoformat(),
                "complete": complete,
                "searches": report,
                "items": items,
            }

    return server


def main():
    load_env_file()
    serve(create_server())


if __name__ == "__main__":
    main()
