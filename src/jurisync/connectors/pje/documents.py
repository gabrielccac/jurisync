"""Bounded reads of joined documents; no login, arbitrary URLs, or Expedientes.

The process result must come from the trusted adapter in the same workflow.
Do not expose this function over HTTP with caller-supplied process results.
"""

import hashlib
import re
from urllib.parse import urlsplit

from curl_cffi.requests.exceptions import RequestException

from jurisync import portals
from jurisync.connectors.pje.parsers import parse_process_number
from jurisync.core.document_text import extract_document_text as _extract_document_text

MAX_DOCUMENTS = 5
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024


def validate_document_ids(document_ids):
    if (
        not isinstance(document_ids, list)
        or not 1 <= len(document_ids) <= MAX_DOCUMENTS
        or any(not isinstance(doc_id, str) or not re.fullmatch(r"[0-9]+", doc_id) for doc_id in document_ids)
        or len(set(document_ids)) != len(document_ids)
    ):
        raise ValueError("Select 1-5 unique numeric document ID strings")


def extract_document_text(content, content_type, process_number=""):
    """Legacy helper name; selected reads use the shared status unchanged."""
    status, text = _extract_document_text(content, content_type, process_number)
    if status == "session_expired":
        return "unrecognized_html", ""
    if status == "pdf_parse_error":
        return "pdf_extractor_unavailable", ""
    return status, text


def read_selected_documents(process, document_ids, *, session):
    """Read 1-5 known document IDs with an already-authenticated session.

    Validate the whole selection before any request. Use only the exact adapter
    download route, refuse redirects, cap each streamed body at 10 MiB, and leave
    the supplied session open. Returned records omit session-bearing URLs.
    Network failures return metadata with no evidence text; there is no retry/login.
    """
    portal = process.get("portal")
    if process.get("success") is not True or portal not in ("trf1-1g", "trf1-2g", "trf5-1g"):
        raise ValueError("A successful supported PJe process result is required")
    number = process.get("numero_processo")
    region = None
    if isinstance(number, str):
        try:
            region = parse_process_number(number)[4]
        except (ValueError, IndexError):
            region = None
    expected_region = {"trf1-1g": "01", "trf1-2g": "01", "trf5-1g": "05"}.get(portal)
    if region is None or region != expected_region:
        raise ValueError("Process number tribunal does not match the portal")
    validate_document_ids(document_ids)
    known = {}
    documents = list(process.get("document_inventory") or process.get("documentInventory") or [])
    documents.extend(
        document
        for day in process.get("timeline", [])
        for movement in day.get("movements", [])
        for document in movement.get("documents", [])
    )
    for document in documents:
        doc_id = document.get("id")
        if doc_id is None:
            continue
        current = known.get(doc_id)
        current_url = (current.get("download_url") or current.get("url")) if current else None
        new_url = document.get("download_url") or document.get("url")
        if current and current_url and new_url and current_url != new_url:
            raise ValueError("Conflicting download locations for a document ID")
        if current:
            merged = {**current, **document}
            if not new_url and current_url:
                merged["download_url"] = current_url
            known[doc_id] = merged
        else:
            known[doc_id] = document
    host = portals.pje(portal).host
    process_id = process.get("processo_id")
    if not isinstance(process_id, str) or not re.fullmatch(r"[0-9]+", process_id):
        raise ValueError("The adapter's numeric process ID is required")
    selected = []
    for doc_id in document_ids:
        if doc_id not in known:
            raise ValueError("Selected document ID is not in this process")
        document = known[doc_id]
        url = document.get("download_url") or document.get("url")
        if not isinstance(url, str):
            raise ValueError("Selected document has no download location")
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != host
            or parsed.port not in (None, 443)
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or not re.fullmatch(
                r"/pje/seam/resource/rest/pje-legacy/documento/download/TRF[15]/[A-Za-z0-9_-]+/"
                + process_id
                + "/"
                + re.escape(doc_id),
                parsed.path,
            )
        ):
            raise ValueError("Document download location is outside the supported read route")
        selected.append(document)
    results = []
    for document in selected:
        item = {
            "id": document["id"],
            "title": document.get("title", ""),
            "status": None,
            "content_type": "",
            "bytes": 0,
            "extractionStatus": "request_failed",
            "text": "",
        }
        response = None
        try:
            response = session.get(
                document["download_url"],
                headers={
                    "Accept": "application/pdf,text/html,*/*",
                    "Referer": f"https://{host}/pje/Processo/ConsultaProcesso/listView.seam",
                },
                impersonate="chrome",
                allow_redirects=False,
                timeout=30,
                stream=True,
            )
            item.update(status=response.status_code, content_type=response.headers.get("Content-Type", ""))
            if response.status_code != 200:
                item["extractionStatus"] = "http_error"
            else:
                content = bytearray()
                for chunk in response.iter_content():
                    item["bytes"] += len(chunk)
                    if item["bytes"] > MAX_DOCUMENT_BYTES:
                        item["extractionStatus"] = "too_large"
                        break
                    content.extend(chunk)
                else:
                    item["sha256"] = hashlib.sha256(content).hexdigest()
                    item["extractionStatus"], item["text"] = _extract_document_text(
                        bytes(content), item["content_type"], number
                    )
        except (RequestException, OSError):
            # Exception messages can include session-bearing request URLs.
            item.update(extractionStatus="request_failed", text="")
        finally:
            if response is not None:
                try:
                    response.close()
                except RequestException:
                    item.update(extractionStatus="request_failed", text="")
        results.append(item)
    return results
