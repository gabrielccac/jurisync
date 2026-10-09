"""The checkpoint file: which draft a run works on and which phases are already done for this payload."""

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path


def fingerprint(value):
    """Stable digest of the payload slice a phase worked from; a changed slice means the phase must run again."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:16]


def documents_fingerprint(documents):
    """Fingerprint documents by name, type and content, so a re-prepared run with the same files matches."""
    items = []
    for item in (documents["main"], *documents.get("supporting", [])):
        path = Path(item["path"]).expanduser()
        items.append(
            {
                "name": path.name,
                "type": item.get("type"),
                "description": item.get("description"),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    return fingerprint(items)


def run_directory(run_id):
    """The folder of one filing run. Every server and tool that touches a run must use this same root."""
    root = Path(os.environ.get("JURISYNC_FILING_DIR") or Path.home() / ".local/share/jurisync/filings").resolve()
    return root / run_id


def phase_fingerprints(payload):
    """The fingerprint each phase records in the ledger for this payload (absent phases are omitted)."""
    from jurisync.connectors.pje.filing.payload import payload_subjects

    prints = {
        "subjects": fingerprint(payload_subjects(payload)),
        "parties": fingerprint(
            {key: payload.get(key) for key in ("authorCpf", "author", "attorney", "passive", "otherParticipants")}
        ),
    }
    if "characteristics" in payload:
        prints["characteristics"] = fingerprint(payload["characteristics"])
    if "documents" in payload:
        prints["documents"] = documents_fingerprint(payload["documents"])
    return prints


def pid_alive(pid):
    try:
        os.kill(int(pid), 0)
    except (ProcessLookupError, ValueError, TypeError):
        return False
    except PermissionError:
        return True
    return True


def now():
    return datetime.now(UTC).isoformat(timespec="seconds")


def write_checkpoint(path, data):
    """Replace the checkpoint atomically (owner-only), so an interrupted write never leaves a broken file."""
    destination = Path(path)
    descriptor, temp = tempfile.mkstemp(dir=destination.parent, prefix=".checkpoint-")
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump(data, output, ensure_ascii=False, indent=2)
    os.chmod(temp, 0o600)
    os.replace(temp, destination)
