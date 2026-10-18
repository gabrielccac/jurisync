"""The checkpoint file: which draft a run works on and which phases are already done for this payload."""

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from jurisync.connectors.pje.filing.names import fold, upload_name


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


def write_private_json(path, data):
    """Replace a run file atomically (owner-only), so an interrupted write never leaves a broken file."""
    destination = Path(path)
    descriptor, temp = tempfile.mkstemp(dir=destination.parent, prefix=f".{destination.stem}-")
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump(data, output, ensure_ascii=False, indent=2)
    os.chmod(temp, 0o600)
    os.replace(temp, destination)


def write_checkpoint(path, data):
    write_private_json(path, data)


def file_sha256(path):
    return hashlib.sha256(Path(path).expanduser().read_bytes()).hexdigest()


class UploadLedger:
    """Which content this run sent to PJe under which file name.

    PJe lists a draft's documents by file name only, so a name on the draft says nothing about its bytes. Before each
    upload the run records `checkpoint["uploads"][sha256] = {name, type}`; a listed name then counts as this document
    only when the record for that name holds the document's own sha256.
    """

    def __init__(self, checkpoint=None, save=None):
        self.checkpoint = checkpoint if checkpoint is not None else {}
        self.save = save

    @property
    def uploads(self):
        return self.checkpoint.setdefault("uploads", {})

    def recorded(self, path):
        """The sha256 values recorded under this file name (PJe's name comparison: ASCII-folded, case-insensitive)."""
        name = fold(upload_name(Path(path)))
        return [sha for sha, entry in self.uploads.items() if fold(entry["name"]) == name]

    def record(self, document):
        """Note the content about to be sent under this name, replacing any earlier record of the same name."""
        path = Path(document["path"]).expanduser()
        for sha in self.recorded(path):
            del self.uploads[sha]
        self.uploads[document["sha256"]] = {"name": upload_name(path), "type": document.get("type")}
        self.checkpoint["updatedAt"] = now()
        if self.save:
            self.save(self.checkpoint)

    def mismatch(self, document):
        """Why the copy of this document's name on the draft may not be this content, or None when it is."""
        path = Path(document["path"]).expanduser()
        recorded = self.recorded(path)
        if document.get("sha256") in recorded:
            return None
        if recorded:
            return f"{path.name} is on the draft with other content than the approved file (it changed after upload)"
        return f"{path.name} is on the draft, but this run has no record of which content was uploaded under that name"
