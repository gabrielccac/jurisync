"""Bounded, same-session reads of known eproc documents."""

import hashlib
import html
import re
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup
from curl_cffi.requests.exceptions import RequestException

from jurisync import portals
from jurisync.core.document_text import extract_document_text
from jurisync.core.documents_dir import save_document
from jurisync.core.html_pdf import html_to_pdf
from jurisync.core.http import UnsafeUrlError, require_url

MAX_DOCUMENTS = 5
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024


def validate_document_ids(document_ids):
    if (
        not isinstance(document_ids, list)
        or not 1 <= len(document_ids) <= MAX_DOCUMENTS
        or any(not isinstance(value, str) or not re.fullmatch(r"[0-9]+", value) for value in document_ids)
        or len(set(document_ids)) != len(document_ids)
    ):
        raise ValueError("Select 1-5 unique numeric document ID strings")


def _read_response(response):
    content = bytearray()
    count = 0
    try:
        chunks = response.iter_content(chunk_size=64 * 1024)
    except AttributeError:
        chunks = [getattr(response, "content", b"")]
    for chunk in chunks:
        if not chunk:
            continue
        count += len(chunk)
        if count > MAX_DOCUMENT_BYTES:
            return None, "too_large"
        content.extend(chunk)
    return bytes(content), None


def _validate_url(url, action, document_id=None, *, portal):
    config = portals.eproc(portal)
    try:
        require_url(
            url, hosts={config.host}, path=f"{config.root}controlador.php", label="eproc document", allow_fragment=False
        )
    except UnsafeUrlError:
        raise ValueError("Document route is outside the supported read surface") from None
    query = parse_qs(urlsplit(url).query, keep_blank_values=True)
    if query.get("acao") != [action] or (document_id is not None and query.get("doc") != [document_id]):
        raise ValueError("Document route is outside the supported read surface")
    return query


def _wrapper_implementation(wrapper, wrapper_url):
    soup = BeautifulSoup(wrapper, "html.parser")
    iframe = soup.select_one("#conteudoIframe[src]")
    if iframe:
        return urljoin(wrapper_url, html.unescape(iframe["src"]))
    match = re.search(
        r"(?:https?://[^\"'\s]+)?controlador\.php\?acao=acessar_documento_implementacao[^\"'\s<]+",
        html.unescape(wrapper.decode("iso-8859-1", "ignore")),
    )
    return urljoin(wrapper_url, match.group(0)) if match else None


def _inventory(process):
    documents = list(process.get("documents") or process.get("documentInventory") or [])
    for event in process.get("events", []):
        documents.extend(event.get("documents", []))
    known = {}
    for document in documents:
        identifier = document.get("id")
        if identifier is None:
            continue
        if identifier in known and known[identifier].get("url") != document.get("url"):
            raise ValueError("Conflicting document locations for an ID")
        known[identifier] = document
    return known


def _item(document):
    return {
        "id": document["id"],
        "title": document.get("title") or document.get("name", ""),
        "status": None,
        "content_type": "",
        "format": "",
        "bytes": 0,
        "extractionStatus": "request_failed",
        "text": "",
    }


def read_selected_documents(process, document_ids, *, session, portal, save=False):
    portal = portals.eproc(portal).key
    """Read 1-5 IDs already present in the freshly parsed process inventory."""
    if process.get("success") is False:
        raise ValueError("A successful eproc process result is required")
    base_url = portals.eproc(portal).base_url
    number = process.get("number") or process.get("processNumber") or ""
    if not isinstance(number, str):
        raise ValueError("The process number is required")
    validate_document_ids(document_ids)
    known = _inventory(process)
    selected = []
    for identifier in document_ids:
        document = known.get(identifier)
        if not document:
            raise ValueError("Selected document ID is not in this process")
        url = document.get("url") or document.get("download_url")
        if not isinstance(url, str):
            raise ValueError("Selected document has no download location")
        _validate_url(urljoin(base_url, url), "acessar_documento", identifier, portal=portal)
        selected.append((document, urljoin(base_url, url)))

    results = []
    for document, wrapper_url in selected:
        result = _item(document)
        response = None
        implementation_response = None
        try:
            response = session.get(
                wrapper_url, allow_redirects=False, timeout=30, stream=True, headers={"Accept": "text/html,*/*"}
            )
            result["status"] = response.status_code
            if response.status_code in (301, 302, 303, 307, 308):
                result["extractionStatus"] = "redirected"
                results.append(result)
                continue
            if response.status_code != 200:
                result["extractionStatus"] = "http_error"
                results.append(result)
                continue
            wrapper, limit_status = _read_response(response)
            if limit_status:
                result["extractionStatus"] = limit_status
                results.append(result)
                continue
            denial, _ = extract_document_text(wrapper, "text/html")
            if denial not in ("extracted", "empty", "unrecognized_html"):
                result["extractionStatus"] = denial
                results.append(result)
                continue
            implementation_url = _wrapper_implementation(wrapper, wrapper_url)
            if not implementation_url:
                result["extractionStatus"] = "wrapper_invalid"
                results.append(result)
                continue
            _validate_url(implementation_url, "acessar_documento_implementacao", document["id"], portal=portal)
            implementation_response = session.get(
                implementation_url,
                allow_redirects=False,
                timeout=30,
                stream=True,
                headers={"Accept": "application/pdf,text/html,*/*", "Referer": wrapper_url},
            )
            result["status"] = implementation_response.status_code
            if implementation_response.status_code in (301, 302, 303, 307, 308):
                result["extractionStatus"] = "redirected"
                results.append(result)
                continue
            if implementation_response.status_code != 200:
                result["extractionStatus"] = "http_error"
                results.append(result)
                continue
            body, limit_status = _read_response(implementation_response)
            if limit_status:
                result["extractionStatus"] = limit_status
                results.append(result)
                continue
            result["content_type"] = implementation_response.headers.get("Content-Type", "")
            result["format"] = (
                "pdf" if body.lstrip().startswith(b"%PDF-") or "pdf" in result["content_type"].lower() else "html"
            )
            result["bytes"] = len(body)
            result["sha256"] = hashlib.sha256(body).hexdigest()
            result["extractionStatus"], result["text"] = extract_document_text(body, result["content_type"], number)
            if save and result["extractionStatus"] == "extracted":
                result["savedAs"] = _save(number, document["id"], body, result)
        except (RequestException, OSError):
            result["extractionStatus"] = "request_failed"
        except ValueError:
            raise
        except Exception:
            result["extractionStatus"] = "request_failed"
        finally:
            for reply in (implementation_response, response):
                if reply is not None:
                    try:
                        reply.close()
                    except Exception:
                        pass
        results.append(result)
    return results


def _save(number, doc_id, body, result):
    """Store the original in the shared documents folder; HTML acts become PDFs so they attach."""
    digits = "".join(ch for ch in str(number) if ch.isdigit())
    if result["format"] == "pdf":
        return save_document(f"{digits}_{doc_id}.pdf", body)
    pdf = html_to_pdf(body, result.get("content_type", ""))
    if pdf is not None:
        result["savedFormat"] = "pdf (rendered from html)"
        return save_document(f"{digits}_{doc_id}.pdf", pdf)
    result["savedFormat"] = "html"
    return save_document(f"{digits}_{doc_id}.html", body)
