"""The checkpoint file: which draft a run works on and which phases are already done for this payload."""

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
ERROR_ACCESS_DENIED = 5
STILL_ACTIVE = 259


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
        pid = int(pid)
    except (ValueError, TypeError):
        return False
    if os.name == "nt":
        # os.kill on Windows terminates the process whatever the signal: ask the kernel whether it is still running.
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ctypes.get_last_error() == ERROR_ACCESS_DENIED
        try:
            code = ctypes.c_ulong()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
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
