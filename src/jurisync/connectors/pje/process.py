"""Common read interface for TRF1, TRF5 and TJAP; returns movements, not publications."""

import argparse
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

from jurisync import portals
from jurisync.connectors.pje import search
from jurisync.connectors.pje.auth.sessions import auth_method, load_session, save_session
from jurisync.connectors.pje.auth.sso import login_session
from jurisync.connectors.pje.documents import read_selected_documents, validate_document_ids
from jurisync.connectors.pje.expedientes import (
    NoticeUnchecked,
    PendingNotice,
    ciencia_report,
    open_deadlines,
    pending_message,
    pending_notice,
    unchecked_message,
)
from jurisync.connectors.pje.parsers import parse_process_number
from jurisync.core import ciencia
from jurisync.core.http import enable_request_logging
from jurisync.core.redaction import drop_keys
from jurisync.core.session_pool import SessionExpired
from jurisync.core.totp import accounts_from_image, parse_accounts, portal_account


def credentials(portal):
    """Return (username, password, TOTP account) from this portal's own variables."""
    portal = portals.pje(portal)
    values = {}
    for field in ("USERNAME", "PASSWORD"):
        values[field] = portal.env(field)
        if not values[field]:
            raise RuntimeError(f"Set {portal.env_name(field)}")
    if portal.env("2FA_DATA"):
        account = portal_account(portal, parse_accounts(portal.env("2FA_DATA")))
    elif portal.env("2FA_QR"):
        account = portal_account(portal, accounts_from_image(Path(portal.env("2FA_QR"))))
    else:
        raise RuntimeError(f"Set {portal.env_name('2FA_DATA')} or {portal.env_name('2FA_QR')}")
    return values["USERNAME"], values["PASSWORD"], account


def login_portal(portal):
    """Authenticate with the method assigned to this portal."""
    config = portals.pje(portal)
    cached = load_session(portal)
    if cached is not None:
        return cached, None
    method = auth_method(portal)
    if method == "a3_totp":
        from jurisync.connectors.pje.auth.certificate import a3_cert_login

        session, response = a3_cert_login(portal), None
    elif method == "certificate_totp":
        from jurisync.connectors.pje.auth.certificate import cert_login

        session, response = cert_login(portal), None
    else:
        session, response = login_session(*credentials(portal), config.login_url, config.host, portal=portal)
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
    ato_expediente=None,
    ato_text_limit=20000,
    ato_allow_pending=False,
):
    """Pass an authenticated session to reuse it across searches in one workflow.

    latest means the latest date bucket, which may contain several movements.
    page means the first HTML timeline page; all merges the bounded pages returned
    by the search layer. Document bodies are never fetched here: ``read_documents`` is the only
    reader, because it checks for a pending ciência first.
    """
    config = portals.pje(portal)
    if scope not in ("latest", "page", "all"):
        raise ValueError("scope must be latest, page or all")
    number_parts = parse_process_number(process_number)
    if not config.owns_number(number_parts):
        raise ValueError(f"Process number does not belong to {config.tribunal}")
    if download_dir and not fetch_documents:
        raise ValueError("download_dir requires fetch_documents=True")
    if fetch_documents or download_dir:
        raise ValueError("Bulk document fetching is retired; use read_documents")
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
            "expedientes": _expedientes(result.get("expedientes"), complete=_expedientes_complete(result)),
            "ato": result.get("ato"),
            **({"ciencia": result.get("ciencia") or ciencia_report(ciencia.NONE)} if ato_expediente else {}),
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
    if not config.owns_number(number_parts):
        raise ValueError(f"Process number does not belong to {config.tribunal}")
    validate_document_ids(document_ids)
    owned_session = session is None
    if owned_session:
        session, _ = login_portal(portal)
    try:
        result = dict(search.search_process(session, process_number, None, portal=portal) or {})
        if not result.get("success"):
            return {
                "success": False,
                "portal": portal,
                "processNumber": process_number,
                "documents": [],
                "error": result.get("error") or "the process could not be opened",
                "diagnostics": list(result.get("diagnostics") or []),
            }
        timeline = _all_timeline(result)
        unchecked = _notice_unchecked(result)
        pending = pending_notice(result.get("expedientes") or [], document_ids, timeline)
        if not acknowledge_ciencia:
            # Fail closed: nothing is fetched unless the expedientes prove no pending ciência is involved.
            if unchecked:
                raise NoticeUnchecked(unchecked_message(unchecked))
            if pending:
                raise PendingNotice(pending_message(pending))
        try:
            documents = read_selected_documents(
                {**result, "portal": portal, "timeline": timeline}, document_ids, session=session, save=save
            )
        except SessionExpired as error:
            # A document may already have been opened: a repeat would find its ciência registered and report
            # nothing pending, so the expiry is reported instead of retried.
            raise RuntimeError(str(error)) from error
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
        "ciencia": _read_ciencia(pending, unchecked),
    }


def _read_ciencia(pending, unchecked):
    """The ``ciencia`` block of a document read: ``pending`` was read (acknowledged), or the check was not possible."""
    if not pending:
        return ciencia_report(ciencia.UNKNOWN if unchecked else ciencia.NONE)
    block = ciencia_report(ciencia.GIVEN, pending)
    if unchecked:
        block["aviso"] += " A lista de expedientes não pôde ser lida por inteiro: pode haver outras comunicações."
    return block


def _expedientes_complete(result):
    """A search result that does not say its expedientes are complete is taken at its word only when it has them."""
    return result.get("expedientes") is not None and result.get("expedientes_complete", True) is not False


def _notice_unchecked(result):
    """Why the ciência state of this process is unknown ("unavailable", "incomplete"), or None when it is known."""
    if result.get("expedientes") is None:
        return "unavailable"
    return None if _expedientes_complete(result) else "incomplete"


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


REDACTED_KEYS = {"download_url", "url", "href", "hash", "ca", "viewstate", "viewer_action", "viewer"}


def _expedientes(items, limit=30, *, complete=True):
    """Newest communications first, as PJe lists them, plus the ones still open.

    ``complete`` is false when the portal's list was not read in full: a pending ciência may be missing from it.
    """
    if items is None:
        return None
    items = [
        {**{k: v for k, v in item.items() if k not in ("atoPath", "respostaPath")}, "temAto": bool(item["atoPath"])}
        for item in items
    ]
    return {
        "total": len(items),
        "complete": complete,
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


def main():
    parser = argparse.ArgumentParser(
        description="Read PJe process movements (TRF1/TJAP password login, TRF5 certificate login)"
    )
    parser.add_argument(
        "--portal",
        choices=("trf1-1g", "trf1-2g", "trf3-1g", "trf5-1g", "trf5-2g", "tjap-1g", "tjpe-1g", "tjpe-2g"),
        required=True,
    )
    parser.add_argument("--process-number", required=True)
    parser.add_argument("--scope", choices=("latest", "page", "all"), default="page")
    parser.add_argument("--save-detail-html", help="Save the authenticated 1G detail response locally")
    args = parser.parse_args()
    enable_request_logging()
    with redirect_stdout(sys.stderr):
        result = fetch_process(
            args.process_number,
            args.scope,
            portal=args.portal,
            raw_html_path=args.save_detail_html,
        )
    result["movement_candidates"] = movement_candidates(result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result.get("success"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
