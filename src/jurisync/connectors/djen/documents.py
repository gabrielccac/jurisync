"""Notice-doc + attachment fetching for DJEN items.

Two layers:
- public: ``link`` doc viewer + certidão need no auth (``*.jus.br``, https only).
- authenticated: case attachments live in PJe/eproc; this module resolves
  *which* portal owns the case and dispatches to the existing portal readers.

The PDF/HTML parser stays portal-agnostic: endpoints return bytes, parsing
happens once above them via the shared inspector. Never wire parsing per
endpoint — ``extract_text`` below is the single choke point.
"""

import re
import urllib.error
import urllib.parse
import urllib.request

from jurisync import portals

REQUEST_TIMEOUT = 30
MAX_LINK_BYTES = 10 * 1024 * 1024

# CNJ J segment → system family (fallback when link is empty/generic).
JUSTICE_SYSTEMS = {
    "4": "federal",
    "8": "state",
    "2": "labor",
    "6": "electoral",
    "7": "military",
}


def format_mascara(numero_digits):
    """20 digits → NNNNNNN-DD.AAAA.J.TR.OOOO (caderno records lack the mask)."""
    digits = re.sub(r"\D", "", str(numero_digits or ""))
    if len(digits) != 20:
        raise ValueError("numero_processo deve ter 20 dígitos")
    return f"{digits[0:7]}-{digits[7:9]}.{digits[9:13]}.{digits[13]}.{digits[14:16]}.{digits[16:20]}"


def _resolved(portal, reason, host=None):
    return {"system": portal.system, "portal": portal.key, "wired": portal.wired, "host": host, "reason": reason}


def _cnj_fallback(numero_digits):
    digits = re.sub(r"\D", "", str(numero_digits or ""))
    if len(digits) != 20:
        return {"system": "unknown", "portal": None, "wired": False, "reason": "numero_processo sem 20 dígitos"}
    candidates = portals.for_process(format_mascara(digits))
    segment = f"CNJ {digits[13]}.{digits[14:16]}.{digits[16:20]}"
    if len(candidates) == 1:
        return _resolved(candidates[0], segment)
    if candidates:  # e.g. TRF1: the instance is not in the number
        return {
            "system": candidates[0].system,
            "portal": "/".join(portal.key for portal in candidates),
            "wired": all(portal.wired for portal in candidates),
            "reason": segment,
        }
    system = JUSTICE_SYSTEMS.get(digits[13], "unknown")
    return {"system": system, "portal": None, "wired": False, "reason": f"{segment} (sem conector)"}


def resolve_portal(item):
    """Which portal owns this case: link host first, CNJ segments fallback."""
    link = item.get("link") or ""
    try:
        host = urllib.parse.urlsplit(link).hostname or ""
    except ValueError:
        host = ""
    if host:
        portal = portals.for_host(host)
        if portal:
            return _resolved(portal, f"link host {host}", host)
        if host.endswith(".jus.br"):
            return {
                "system": "unknown",
                "portal": None,
                "wired": False,
                "host": host,
                "reason": f"link host {host} fora da tabela",
            }
    numero = item.get("numero_processo") or item.get("numeroprocessocommascara") or ""
    resolved = _cnj_fallback(numero)
    resolved["host"] = host or None
    return resolved


def fetch_link_document(url, timeout=REQUEST_TIMEOUT, max_bytes=MAX_LINK_BYTES):
    """Public notice doc (no auth). Only https *.jus.br. Returns (content_type, bytes)."""
    parts = urllib.parse.urlsplit(url or "")
    host = parts.hostname or ""
    if parts.scheme != "https" or not host.endswith(".jus.br"):
        raise ValueError("link fora do domínio *.jus.br (https)")
    req = urllib.request.Request(url, headers={"Accept": "text/html,application/pdf,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            final = resp.url or ""
            final_host = urllib.parse.urlsplit(final).hostname or ""
            if final_host and not final_host.endswith(".jus.br"):
                raise ValueError("redirect fora do domínio *.jus.br")
            content_type = resp.headers.get("Content-Type", "")
            total = 0
            chunks = []
            while True:
                chunk = resp.read(1024 * 256)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError("documento excede o limite de bytes")
                chunks.append(chunk)
            return content_type, b"".join(chunks)
    except urllib.error.HTTPError as exc:
        raise ValueError(f"link doc: HTTP {exc.code}") from exc


def extract_text(content, content_type=""):
    """Single choke point: shared portal-agnostic inspector. Returns (status, text)."""
    from jurisync.core.document_text import extract_document_text

    return extract_document_text(content, content_type)


def fetch_attachments(item, *, session=None, document_ids=None, process=None):
    """List+fetch case attachments via the resolved portal reader.

    Caller supplies the authenticated ``session`` (per-customer account, same
    pattern as the pje/eproc clients). Returns unified records with parsed
    text attached. Raises ValueError when the portal is unwired.
    """
    resolved = resolve_portal(item)
    if not resolved["wired"]:
        raise ValueError(f"portal sem conector: {resolved['reason']}")
    if session is None:
        raise ValueError("session autenticada é obrigatória (conta do cliente)")
    if not document_ids:
        raise ValueError("document_ids (1-5, do inventário) é obrigatório")
    mascara = item.get("numero_processo_mascara") or item.get("numeroprocessocommascara")
    if not mascara:
        mascara = format_mascara(item.get("numero_processo") or "")
    system, portal = resolved["system"], resolved["portal"]
    if system == "pje":
        from jurisync.connectors.pje import documents as pje_documents
        from jurisync.connectors.pje import process as pje_client

        fetched = process or pje_client.fetch_process(
            mascara, "page", portal=portal, session=session, document_ids=list(document_ids)
        )
        records = pje_documents.read_selected_documents(fetched, list(document_ids), session=session)
    elif system == "eproc":
        from jurisync.connectors.eproc import client as eproc_client
        from jurisync.connectors.eproc import documents as eproc_documents

        fetched = process or eproc_client.fetch_process(
            mascara, session=session, document_ids=list(document_ids), portal=portal
        )
        records = eproc_documents.read_selected_documents(fetched, list(document_ids), session=session, portal=portal)
    else:  # pragma: no cover - guarded by wired check above
        raise ValueError(f"sistema sem leitor: {system}")
    unified = []
    for record in records:
        entry = dict(record)
        blob = record.get("content") or record.get("body")
        if isinstance(blob, (bytes, bytearray)) and not entry.get("text"):
            status, text = extract_text(bytes(blob), record.get("content_type", ""))
            entry["extractionStatus"] = status
            entry["text"] = text
        unified.append(entry)
    return {"portal": resolved, "processo": mascara, "attachments": unified}
