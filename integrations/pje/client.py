"""Common read interface for TRF1 1G and 2G; returns movements, not publications."""

import argparse
from contextlib import redirect_stdout
import json
import os
import sys

from .auth import login_session
from . import trf1_1g, trf1_2g
from .documents import read_selected_documents, validate_document_ids


def credentials(portal):
    if portal not in ("1g", "2g"):
        raise ValueError("portal must be 1g or 2g")
    values = []
    for field in ("USERNAME", "PASSWORD", "2FA_DATA"):
        name = f"PJE_{portal.upper()}_{field}"
        value = os.environ.get(name) or os.environ.get(f"PJE_{field}")
        if not value:
            raise RuntimeError(f"Set {name} (or shared PJE_{field})")
        values.append(value)
    return tuple(values)


def fetch_process(process_number, scope="page", fetch_documents=False, download_dir=None, *, portal="1g", session=None, raw_html_path=None, document_ids=None):
    """Pass an authenticated session to reuse it across searches in one workflow.

    latest means the latest date bucket, which may contain several movements.
    page means the first HTML timeline page. all is retained as a compatibility alias for page.
    Bodies are fetched only with fetch_documents=True or explicit document_ids.
    document_ids selects at most five joined documents and extracts supported HTML.
    """
    if portal not in ("1g", "2g"):
        raise ValueError("portal must be 1g or 2g")
    if scope not in ("latest", "page", "all"):
        raise ValueError("scope must be latest, page or all")
    trf1_2g.parse_process_number(process_number)
    if download_dir and not fetch_documents:
        raise ValueError("download_dir requires fetch_documents=True")
    if document_ids is not None and (scope not in ("page", "all") or fetch_documents or download_dir):
        raise ValueError("document_ids requires scope=page and cannot combine with bulk downloads")
    if document_ids is not None:
        validate_document_ids(document_ids)
    host = f"pje{portal}.trf1.jus.br"
    owned_session = session is None
    if owned_session:
        session, _ = login_session(*credentials(portal), f"https://{host}/pje/login.seam", host)
    try:
        search = trf1_1g.search_process_1g if portal == "1g" else trf1_2g.search_process
        if portal == "1g" and raw_html_path:
            result = search(session, process_number, raw_html_path)
        else:
            result = search(session, process_number)
        timeline = result.pop("timeline", [])
        result.pop("downloads", None)
        selected = timeline[:1] if scope == "latest" else timeline
        attachments = trf1_2g.download_documents(
            session, selected, download_dir, fetch=fetch_documents,
            referer_url=f"https://{host}/pje/Processo/ConsultaProcesso/listView.seam",
        ) if result.get("success") else []
        if document_ids is not None and result.get("success"):
            attachments = read_selected_documents(
                {**result, "portal": portal, "timeline": selected}, document_ids, session=session)
        result.update(
            source="pje",
            portal=portal,
            scope=scope,
            scope_semantics="latest_date_bucket" if scope == "latest" else "first_timeline_page",
            first_timeline_page=scope != "latest",
            timeline=selected,
            attachments=attachments,
        )
        return result
    finally:
        if owned_session:
            session.close()


def movement_candidates(result):
    candidates = []
    for day in result.get("timeline", []):
        for movement in day.get("movements", []):
            documents = [
                {"id": item.get("id"), "title": item.get("title"), "url": item.get("download_url")}
                for item in movement.get("documents", [])
            ]
            candidates.append({
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
            })
    return candidates


# Compatibility for callers of the original adapter. These records are movements.
publication_candidates = movement_candidates


def main():
    parser = argparse.ArgumentParser(description="Read TRF1 PJe process movements")
    parser.add_argument("--portal", choices=("1g", "2g"), default="1g")
    parser.add_argument("--process-number", required=True)
    parser.add_argument("--scope", choices=("latest", "page", "all"), default="page")
    parser.add_argument("--fetch-documents", action="store_true")
    parser.add_argument("--download-dir")
    parser.add_argument("--save-detail-html", help="Save the authenticated 1G detail response locally")
    parser.add_argument("--document-id", action="append", help="Read one joined document ID; repeat up to five times, requires --scope page")
    args = parser.parse_args()
    with redirect_stdout(sys.stderr):
        result = fetch_process(
            args.process_number,
            args.scope,
            args.fetch_documents,
            args.download_dir,
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
