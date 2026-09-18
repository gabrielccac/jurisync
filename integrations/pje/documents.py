"""Bounded reads of joined documents; no login, arbitrary URLs, or Expedientes.

The process result must come from the trusted adapter in the same workflow.
Do not expose this function over HTTP with caller-supplied process results.
"""

import hashlib
import re
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from curl_cffi.requests.exceptions import RequestException

from .trf1_2g import parse_process_number


MAX_DOCUMENTS = 5
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024


def validate_document_ids(document_ids):
    if (not isinstance(document_ids, list) or not 1 <= len(document_ids) <= MAX_DOCUMENTS
            or any(not isinstance(doc_id, str) or not re.fullmatch(r"[0-9]+", doc_id) for doc_id in document_ids)
            or len(set(document_ids)) != len(document_ids)):
        raise ValueError("Select 1-5 unique numeric document ID strings")


def extract_document_text(content, content_type, process_number):
    """Extract supported HTML bodies conservatively; never treat a PDF as text.

    A process-number check is a format guard, not proof of document authenticity.
    HTML without that number is left unresolved for the caller to inspect.
    """
    if not content.strip():
        return "empty", ""
    if content.lstrip().startswith(b"%PDF-"):
        return "pdf_extractor_unavailable", ""
    if "html" not in content_type.lower() and not content.lstrip().lower().startswith((b"<html", b"<!doctype html")):
        return "unsupported_content", ""
    soup = BeautifulSoup(content, "html.parser")
    title = soup.title.get_text(" ", strip=True).casefold() if soup.title else ""
    if soup.select_one("form, input, select, textarea, meta[http-equiv='refresh']") or any(
        token in title for token in ("login", "entrar", "erro", "error", "acesso negado", "access denied")
    ):
        return "unrecognized_html", ""
    for node in soup.select("script, style, head, iframe, object, embed, [hidden], [aria-hidden='true']"):
        node.decompose()
    text = " ".join(soup.get_text(" ", strip=True).split())
    if not text:
        return "empty", ""
    if process_number not in text or text.casefold().startswith((
        "acesso negado", "access denied", "documento indispon", "documento sigiloso",
        "sessão expirada", "não autorizado", "erro ao",
    )):
        return "unrecognized_html", ""
    return "extracted", text


def read_selected_documents(process, document_ids, *, session):
    """Read 1-5 known document IDs with an already-authenticated session.

    Validate the whole selection before any request. Use only the exact adapter
    download route, refuse redirects, cap each streamed body at 10 MiB, and leave
    the supplied session open. Returned records omit session-bearing URLs.
    Network failures return metadata with no evidence text; there is no retry/login.
    """
    portal = process.get("portal")
    if process.get("success") is not True or portal not in ("1g", "2g"):
        raise ValueError("A successful supported PJe process result is required")
    number = process.get("numero_processo")
    if not isinstance(number, str) or parse_process_number(number)[3:5] != ("4", "01"):
        raise ValueError("Only TRF1 process numbers are supported")
    validate_document_ids(document_ids)
    known = {}
    for day in process.get("timeline", []):
        for movement in day.get("movements", []):
            for document in movement.get("documents", []):
                doc_id = document.get("id")
                if doc_id in known and known[doc_id].get("download_url") != document.get("download_url"):
                    raise ValueError("Conflicting download locations for a document ID")
                known[doc_id] = document
    host = f"pje{portal}.trf1.jus.br"
    process_id = process.get("processo_id")
    if not isinstance(process_id, str) or not re.fullmatch(r"[0-9]+", process_id):
        raise ValueError("The adapter's numeric process ID is required")
    selected = []
    for doc_id in document_ids:
        if doc_id not in known:
            raise ValueError("Selected document ID is not in this process")
        document = known[doc_id]
        url = document.get("download_url")
        if not isinstance(url, str):
            raise ValueError("Selected document has no download location")
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.hostname != host or parsed.port not in (None, 443)
                or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment
                or not re.fullmatch(r"/pje/seam/resource/rest/pje-legacy/documento/download/TRF1/[A-Za-z0-9_-]+/" + process_id + "/" + re.escape(doc_id), parsed.path)):
            raise ValueError("Document download location is outside the supported read route")
        selected.append(document)
    results = []
    for document in selected:
        item = {"id": document["id"], "title": document.get("title", ""),
                "status": None, "content_type": "", "bytes": 0,
                "extractionStatus": "request_failed", "text": ""}
        response = None
        try:
            response = session.get(document["download_url"], headers={
                "Accept": "application/pdf,text/html,*/*",
                "Referer": f"https://{host}/pje/Processo/ConsultaProcesso/listView.seam",
            }, impersonate="chrome", allow_redirects=False, timeout=30, stream=True)
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
                    item["extractionStatus"], item["text"] = extract_document_text(
                        bytes(content), item["content_type"], number)
        except RequestException:
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
