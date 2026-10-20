"""Common read interface for TRF1, TRF5 and TJAP; returns movements, not publications."""

import argparse
import json
import sys
from contextlib import redirect_stdout

from jurisync import portals
from jurisync.connectors.pje import search
from jurisync.connectors.pje.auth.sessions import auth_method, load_session, save_session
from jurisync.connectors.pje.auth.sso import login_session
from jurisync.connectors.pje.documents import read_selected_documents, validate_document_ids
from jurisync.connectors.pje.expedientes import (
    PendingNotice,
    ciencia_report,
    open_deadlines,
    pending_message,
    pending_notice,
)
from jurisync.connectors.pje.parsers import parse_process_number
from jurisync.core.http import enable_request_logging
from jurisync.core.redaction import drop_keys
from jurisync.core.totp import parse_accounts, parse_index, select_account


def credentials(portal):
    """Return (username, password, TOTP account) from this portal's own variables."""
    portal = portals.pje(portal)
    values = {}
    for field in ("USERNAME", "PASSWORD", "2FA_DATA"):
        values[field] = portal.env(field)
        if not values[field]:
            raise RuntimeError(f"Set {portal.env_name(field)}")
    account = select_account(
        parse_accounts(values["2FA_DATA"]),
        match=portal.env("OTP_PROFILE_MATCH"),
        index=parse_index(portal.env("OTP_PROFILE_INDEX"), portal.env_name("OTP_PROFILE_INDEX")),
        label=portal.env_prefix,
    )
    return values["USERNAME"], values["PASSWORD"], account


def login_portal(portal):
    """Authenticate with the method assigned to this portal."""
    config = portals.pje(portal)
    cached = load_session(portal)
    if cached is not None:
        return cached, None
    if auth_method(portal) == "certificate_totp":
        from jurisync.connectors.pje.auth.certificate import cert_login

        session, response = cert_login(portal), None
    else:
        session, response = login_session(*credentials(portal), config.login_url, config.host)
    try:
        save_session(portal, session)
    except OSError:
        pass  # Authentication remains usable if the private cache is unavailable.
    return session, response


def fetch_process(
    process_number,
    scope="page",
    fetch_documents=False,
    download_dir=None,
    *,
    portal,
    session=None,
    raw_html_path=None,
    document_ids=None,
    ato_expediente=None,
    ato_text_limit=20000,
    ato_allow_pending=False,
):
    """Pass an authenticated session to reuse it across searches in one workflow.

    latest means the latest date bucket, which may contain several movements.
    page means the first HTML timeline page; all merges the bounded pages returned
    by the search layer. Bodies are fetched only with explicit document_ids.
    document_ids selects at most five joined documents and extracts supported HTML/PDF.
    """
    config = portals.pje(portal)
    if scope not in ("latest", "page", "all"):
        raise ValueError("scope must be latest, page or all")
    number_parts = parse_process_number(process_number)
    if number_parts[4] != config.region:
        raise ValueError(f"Process number does not belong to {config.tribunal}")
    if download_dir and not fetch_documents:
        raise ValueError("download_dir requires fetch_documents=True")
    if document_ids is not None and (scope not in ("page", "all") or fetch_documents or download_dir):
        raise ValueError("document_ids requires scope=page or all and cannot combine with bulk downloads")
    if fetch_documents or download_dir:
        raise ValueError("Bulk document fetching is retired; pass document_ids")
    if document_ids is not None:
        validate_document_ids(document_ids)
    owned_session = session is None
    if owned_session:
        session, _ = login_portal(portal)
    try:
        ato_args = (
            {"ato_expediente": ato_expediente, "ato_text_limit": ato_text_limit, "ato_allow_pending": ato_allow_pending}
            if ato_expediente
            else {}
        )
        raw_result = search.search_process(session, process_number, raw_html_path, portal=portal, **ato_args)
        result = dict(raw_result or {})
        timeline = _all_timeline(result)
        selected = _select_timeline(timeline, scope, _first_timeline_page(result))
        attachments = []
        if document_ids is not None and result.get("success"):
            attachments = read_selected_documents(
                {**result, "portal": portal, "timeline": timeline}, document_ids, session=session
            )
        inventory = _document_inventory(result, timeline)
        pagination = dict(result.get("pagination") or {})
        pagination.setdefault("page", 1 if result.get("success") else 0)
        loaded_pages = pagination.get("loadedPages", 0)
        loaded_pages = loaded_pages if isinstance(loaded_pages, int) else 0
        pagination["loadedPages"] = max(
            loaded_pages, (len(result.get("timeline_pages") or []) or 1) if result.get("success") else 0
        )
        if "complete" not in pagination:
            pagination["complete"] = bool(result.get("success")) and not pagination.get("more_pages_possible", False)
        normalized = {
            "success": bool(result.get("success")),
            "source": "pje",
            "portal": portal,
            "processNumber": process_number,
            "scope": scope,
            "scopeSemantics": {
                "latest": "latest_date_bucket",
                "page": "first_timeline_page",
                "all": "bounded_timeline_pages",
            }[scope],
            "metadata": result.get("process_details") or result.get("metadata") or {},
            "parties": result.get("parties") or [],
            "timeline": selected,
            "documentInventory": inventory,
            "attachments": attachments,
            "expedientes": _expedientes(result.get("expedientes")),
            "ato": result.get("ato"),
            "pagination": pagination,
            "diagnostics": list(result.get("diagnostics") or []),
            # Minimal compatibility for movement_candidates and old callers.
            "numero_processo": result.get("numero_processo", process_number),
        }
        if result.get("processo_id") is not None:
            normalized["processo_id"] = result["processo_id"]
        if result.get("error"):
            normalized["diagnostics"].append(result["error"])
        if not result.get("success") and result.get("search_results"):
            search_results = result["search_results"]
            normalized["searchSummary"] = {
                "tableFound": search_results.get("table_found"),
                "count": search_results.get("count"),
            }
        return _redact(normalized)
    finally:
        if owned_session:
            session.close()


def read_documents(process_number, document_ids, *, portal, session=None, save=False, acknowledge_ciencia=False):
    """Read 1-5 joined documents of one process without returning the timeline.

    Each document carries its extraction status and text; a Certidão de Intimação also carries its parsed
    ``expedientes`` (recipient, medium, deadline in days). With ``save`` the original bytes are written to the
    shared documents folder so another tool can attach them by file name.
    """
    from jurisync.connectors.pje.documents import parse_expedientes

    config = portals.pje(portal)
    number_parts = parse_process_number(process_number)
    if number_parts[4] != config.region:
        raise ValueError(f"Process number does not belong to {config.tribunal}")
    validate_document_ids(document_ids)
    owned_session = session is None
    if owned_session:
        session, _ = login_portal(portal)
    try:
        result = dict(search.search_process(session, process_number, None, portal=portal) or {})
        if not result.get("success"):
            return {"success": False, "portal": portal, "processNumber": process_number, "documents": []}
        timeline = _all_timeline(result)
        checked = _require_notice(result.get("expedientes"), document_ids) if not acknowledge_ciencia else None
        pending = pending_notice(result.get("expedientes") or [], document_ids)
        documents = read_selected_documents(
            {**result, "portal": portal, "timeline": timeline}, document_ids, session=session, save=save
        )
    finally:
        if owned_session:
            session.close()
    for document in documents:
        if "EXPEDIENTES GERADOS" in document.get("text", ""):
            document["expedientes"] = parse_expedientes(document["text"])
    return {
        "success": True,
        "source": "pje",
        "portal": portal,
        "processNumber": process_number,
        "processId": result.get("processo_id"),
        "documents": documents,
        **({"cienciaChecked": False} if checked is False else {}),
        **({"ciencia": ciencia_report(pending)} if pending else {}),
    }


def _require_notice(expedientes, document_ids):
    """Refuse a pending communication's document; False when the tab could not be checked."""
    if expedientes is None:
        return False
    pending = pending_notice(expedientes, document_ids)
    if pending:
        raise PendingNotice(pending_message(pending))
    return True


def _all_timeline(result):
    pages = result.get("timeline_pages")
    if not isinstance(pages, list) or not pages:
        return list(result.get("timeline") or [])
    timeline = []
    for page in pages[:5]:
        if isinstance(page, dict):
            timeline.extend(page.get("timeline", []))
        elif isinstance(page, list):
            timeline.extend(page)
    return timeline


def _first_timeline_page(result):
    pages = result.get("timeline_pages")
    if isinstance(pages, list) and pages:
        first = pages[0]
        if isinstance(first, dict) and isinstance(first.get("timeline"), list):
            return list(first["timeline"])
        if isinstance(first, list):
            return first
    return list(result.get("first_timeline") or result.get("timeline") or [])


def _select_timeline(timeline, scope, first_page):
    if scope == "page":
        return first_page
    if scope == "all" or not timeline:
        return timeline
    newest = timeline[0].get("date")
    return [day for day in timeline if day.get("date") == newest]


def _document_inventory(result, timeline):
    documents = list(result.get("document_inventory") or result.get("documentInventory") or [])
    documents.extend(
        document
        for day in timeline
        for movement in day.get("movements", [])
        for document in movement.get("documents", [])
    )
    merged = {}
    for document in documents:
        identifier = document.get("id")
        if identifier is not None:
            merged[identifier] = {**merged.get(identifier, {}), **document}
    return list(merged.values())


REDACTED_KEYS = {"download_url", "url", "href", "hash", "ca", "viewstate"}


def _expedientes(items, limit=30):
    """Newest communications first, as PJe lists them, plus the ones still open."""
    if items is None:
        return None
    items = [
        {**{k: v for k, v in item.items() if k not in ("atoPath", "respostaPath")}, "temAto": bool(item["atoPath"])}
        for item in items
    ]
    return {
        "total": len(items),
        "abertos": open_deadlines(items),
        "items": items[:limit],
        "truncated": len(items) > limit,
    }


def _redact(value):
    return drop_keys(value, REDACTED_KEYS)


def movement_candidates(result):
    candidates = []
    for day in result.get("timeline", []):
        for movement in day.get("movements", []):
            documents = [
                {"id": item.get("id"), "title": item.get("title"), "url": item.get("download_url")}
                for item in movement.get("documents", [])
            ]
            candidates.append(
                {
                    "source": "pje",
                    "recordType": "movement",
                    "portal": result.get("portal"),
                    "processNumber": result.get("numero_processo"),
                    "date": day.get("date"),
                    "time": movement.get("time"),
                    "text": movement.get("movement", ""),
                    "movement_texts": movement.get("movement_texts", [movement.get("movement", "")]),
                    "entryType": movement.get("entry_type"),
                    "typeCode": movement.get("type_code"),
                    "origin": movement.get("origin"),
                    "originCode": movement.get("origin_code"),
                    "documents": documents,
                    "hasDocuments": bool(documents),
                    # ponytail: attachment-only hint retained for compatibility; replace with semantic triage after real publication mapping.
                    "needsContext": bool(documents),
                    "contextAssessment": "not_analyzed",
                }
            )
    return candidates


# Compatibility for callers of the original adapter. These records are movements.
publication_candidates = movement_candidates


def main():
    parser = argparse.ArgumentParser(
        description="Read PJe process movements (TRF1/TJAP password login, TRF5 certificate login)"
    )
    parser.add_argument(
        "--portal", choices=("trf1-1g", "trf1-2g", "trf3-1g", "trf5-1g", "trf5-2g", "tjap-1g"), required=True
    )
    parser.add_argument("--process-number", required=True)
    parser.add_argument("--scope", choices=("latest", "page", "all"), default="page")
    parser.add_argument("--save-detail-html", help="Save the authenticated 1G detail response locally")
    parser.add_argument(
        "--document-id",
        action="append",
        help="Read one inventory document ID; repeat up to five times (scope page or all)",
    )
    args = parser.parse_args()
    enable_request_logging()
    with redirect_stdout(sys.stderr):
        result = fetch_process(
            args.process_number,
            args.scope,
            portal=args.portal,
            raw_html_path=args.save_detail_html,
            document_ids=args.document_id,
        )
    result["movement_candidates"] = movement_candidates(result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result.get("success"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
