"""The recorded approval of a run's payload: nothing writes to PJe unless approval.json matches payload.json.

`pje_filing_approve` writes approval.json after the user has seen the finalize summary. It holds the sha256 of the payload
file and of every document, so any later change to the payload or a file voids it. Writing a new payload removes it.
"""

import hashlib
import json
from pathlib import Path

from jurisync.connectors.pje.filing.checkpoint import now
from jurisync.connectors.pje.filing.payload import payload_documents, resolve_documents, validate_fill_payload
from jurisync.connectors.pje.filing.store import APPROVAL, PAYLOAD


def _documents(payload):
    return [
        {"name": Path(item["path"]).name, "type": item.get("type"), "sha256": item.get("sha256")}
        for item in payload_documents(payload)
    ]


def approve(store):
    """Record approval of the run's current payload after checking every document still has its approved content."""
    if not store.exists(PAYLOAD):
        raise ValueError("This run has no payload yet; call pje_filing_build first")
    content = store.read_bytes(PAYLOAD)
    payload = json.loads(content)
    validate_fill_payload(resolve_documents(payload, store))
    approval = {
        "approvedAt": now(),
        "payloadSha256": hashlib.sha256(content).hexdigest(),
        "documents": _documents(payload),
    }
    store.write_json(APPROVAL, approval)
    return approval


def revoke(store):
    """Drop the approval; called whenever a new payload is written."""
    store.delete(APPROVAL)


def require_approval(store):
    """Refuse unless approval.json approves exactly the run's payload.json."""
    if not store.exists(APPROVAL):
        raise ValueError(
            "This payload has not been approved: show the pje_filing_build summary to the user and call pje_filing_approve "
            "once they confirm it"
        )
    approval = store.read_json(APPROVAL)
    content = store.read_bytes(PAYLOAD)
    if approval.get("payloadSha256") != hashlib.sha256(content).hexdigest():
        raise ValueError("The payload changed after it was approved: review it again and call pje_filing_approve")
    if approval.get("documents") != _documents(json.loads(content)):
        raise ValueError(
            "The approved documents differ from the payload's: review it again and call pje_filing_approve"
        )
    return approval
