"""Bounded reads of joined documents; no login, arbitrary URLs, or Expedientes.

The process result must come from the trusted adapter in the same workflow.
Do not expose this function over HTTP with caller-supplied process results.
"""

import hashlib
import re
from urllib.parse import urlsplit

from curl_cffi.requests.exceptions import RequestException

from jurisync import portals
from jurisync.connectors.pje import jsf
from jurisync.connectors.pje.parsers import VIEWER_ACTION, parse_process_number
from jurisync.connectors.pje.search import DETAIL_PATH
from jurisync.core.document_text import extract_document_text
from jurisync.core.documents import MAX_DOCUMENT_BYTES, validate_document_ids
from jurisync.core.documents_dir import save_document
from jurisync.core.http import IMPERSONATE


def read_selected_documents(process, document_ids, *, session, save=False):
    """Read 1-5 known document IDs with an already-authenticated session.

    Validate the whole selection before any request. Use only the exact adapter
    download route, refuse redirects, cap each streamed body at 10 MiB, and leave
    the supplied session open. Returned records omit session-bearing URLs.
    Network failures return metadata with no evidence text; there is no retry/login.
    The fetch is bound to the process (ID and download path), so HTML that omits the process number is
    still accepted as ``extracted_unnumbered`` when it is a substantial page without error markers.
    With ``save`` the original bytes are written to the shared documents folder and reported as ``savedAs``.
    """
    portal = process.get("portal")
    try:
        config = portals.pje(portal)
    except ValueError:
        raise ValueError("A successful supported PJe process result is required") from None
    if process.get("success") is not True:
        raise ValueError("A successful supported PJe process result is required")
    number = process.get("numero_processo")
    parts = None
    if isinstance(number, str):
        try:
            parts = parse_process_number(number)
        except (ValueError, IndexError):
            parts = None
    if parts is None or not config.owns_number(parts):
        raise ValueError("Process number tribunal does not match the portal")
    validate_document_ids(document_ids)
    if config.kz:
        from jurisync.connectors.pje import kz

        return kz.read_documents(process, document_ids, session=session, save=save)
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
    host = config.host
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
                re.escape(config.root)
                + r"seam/resource/rest/pje-legacy/documento/download/(?:[A-Za-z0-9-]+/[A-Za-z0-9_-]+/"
                + process_id
                + r"/)?"
                + re.escape(doc_id),
                parsed.path,
            )
        ):
            raise ValueError("Document download location is outside the supported read route")
        selected.append(document)
    viewer = process.get("viewer")
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
            response = _download(session, config, document["download_url"])
            action = document.get("viewer_action")
            # TJMT serves a document only once the process page shows it in its viewer, as a click does.
            if response.status_code != 200 and action and _show_in_viewer(session, config, viewer, action):
                response.close()
                response = None
                response = _download(session, config, document["download_url"])
            item.update(status=response.status_code, content_type=response.headers.get("Content-Type", ""))
            if response.status_code != 200:
                # The first-instance copy of a second-instance document is not served here: read the visible one.
                item["extractionStatus"] = (
                    "first_instance_copy" if document.get("first_instance_copy") else "http_error"
                )
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
                        bytes(content), item["content_type"], number, allow_unnumbered=True
                    )
                    if save:
                        item["savedAs"] = save_document(_saved_name(number, document["id"], content, item), content)
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


def _download(session, config, url):
    return session.get(
        url,
        headers={
            "Accept": "application/pdf,text/html,*/*",
            "Referer": f"https://{config.host}{config.root}Processo/ConsultaProcesso/listView.seam",
        },
        impersonate=IMPERSONATE,
        allow_redirects=False,
        timeout=30,
        stream=True,
    )


def _show_in_viewer(session, config, viewer, action):
    """Click the document's timeline link on the process page, so the download route serves it.

    Only the adapter's own detail page and a timeline link id are accepted; any failure means not shown.
    """
    url = viewer.get("url") if isinstance(viewer, dict) else None
    fields = viewer.get("fields") if isinstance(viewer, dict) else None
    if not isinstance(url, str) or not isinstance(fields, dict) or not VIEWER_ACTION.fullmatch(str(action)):
        return False
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != config.host
        or parsed.port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or parsed.path != config.root + DETAIL_PATH
    ):
        return False
    payload = {
        **fields,
        "AJAXREQUEST": "_viewRoot",
        action: action,
        "ajaxSingle": action,
        "AJAX:EVENTS_COUNT": "1",
    }
    try:
        jsf.post(session, url, payload, label="PJe document viewer")
    except (RuntimeError, RequestException):
        return False
    return True


def _saved_name(number, doc_id, content, item):
    digits = "".join(ch for ch in str(number) if ch.isdigit())
    kind = item.get("content_type", "").casefold()
    if bytes(content[:5]) == b"%PDF-" or "pdf" in kind:
        extension = "pdf"
    elif kind.startswith("audio/") or bytes(content[:3]) == b"ID3":
        extension = "mp3"
    else:
        extension = "html"
    return f"{digits}_{doc_id}.{extension}"


EXPEDIENTE = re.compile(
    r"(?P<parte>[^()]{2,120}?)\s*\((?P<documentId>\d+)\s*-\s*(?P<documento>[^)]{1,80})\)\s*"
    r"(?P<meio>Diário Eletrônico|Sistema|Correios|Central de Mandados|Edital|Pessoal)\s+"
    r"(?:(?P<dias>\d+)\s+dias?|(?P<zero>\d+)\s+sem prazo)\s+(?P<pessoal>Sim|Não)"
)


def parse_expedientes(text):
    """Parse the EXPEDIENTES GERADOS table of a Certidão de Intimação into one record per recipient."""
    start = str(text).find("EXPEDIENTES GERADOS")
    if start < 0:
        return []
    body = text[start:]
    end = body.find("O(s) documento(s)")
    body = body[: end if end > 0 else len(body)]
    body = body.replace("EXPEDIENTES GERADOS Parte Documento Meio Prazo Pessoal", " ")
    records = []
    for match in EXPEDIENTE.finditer(body):
        days = match.group("dias")
        records.append(
            {
                "parte": " ".join(match.group("parte").split()),
                "documentId": match.group("documentId"),
                "documento": match.group("documento").strip(),
                "meio": match.group("meio"),
                "prazoDias": int(days) if days else None,
                "semPrazo": days is None,
                "pessoal": match.group("pessoal") == "Sim",
            }
        )
    return records
