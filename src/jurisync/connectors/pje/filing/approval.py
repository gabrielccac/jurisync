"""The recorded approval of a run's payload: nothing writes to PJe unless approval.json matches payload.json.

`filing_approve` writes approval.json after the user has seen the finalize summary. It holds the sha256 of the payload
file and of every document, so any later change to the payload or a file voids it. Writing a new payload removes it.
"""

import hashlib
import json
from pathlib import Path

from jurisync.connectors.pje.filing.checkpoint import now, write_private_json
from jurisync.connectors.pje.filing.payload import validate_fill_payload

APPROVAL = "approval.json"


def _documents(payload):
    documents = payload.get("documents") or {}
    items = [documents["main"], *documents.get("supporting", [])] if documents.get("main") else []
    return [{"name": Path(item["path"]).name, "type": item.get("type"), "sha256": item.get("sha256")} for item in items]


def approve(directory):
    """Record approval of the run's current payload after checking every document still has its approved content."""
    payload_path = Path(directory) / "payload.json"
    if not payload_path.exists():
        raise ValueError("This run has no payload yet; call filing_finalize first")
    content = payload_path.read_bytes()
    payload = json.loads(content)
    validate_fill_payload(payload)
    approval = {
        "approvedAt": now(),
        "payloadSha256": hashlib.sha256(content).hexdigest(),
        "documents": _documents(payload),
    }
    write_private_json(Path(directory) / APPROVAL, approval)
    return approval


def revoke(directory):
    """Drop the approval; called whenever a new payload is written."""
    (Path(directory) / APPROVAL).unlink(missing_ok=True)


def require_approval(payload_path):
    """Refuse unless approval.json approves exactly this payload file."""
    payload_path = Path(payload_path)
    approval_path = payload_path.with_name(APPROVAL)
    if not approval_path.exists():
        raise ValueError(
            "This payload has not been approved: show the filing_finalize summary to the user and call filing_approve "
            "once they confirm it"
        )
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    content = payload_path.read_bytes()
    if approval.get("payloadSha256") != hashlib.sha256(content).hexdigest():
        raise ValueError("The payload changed after it was approved: review it again and call filing_approve")
    if approval.get("documents") != _documents(json.loads(content)):
        raise ValueError("The approved documents differ from the payload's: review it again and call filing_approve")
    return approval
