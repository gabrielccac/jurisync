"""A run's single entry point: the input folder, copied into `inbox/` with each file's hash and declared origin.

The filing does not know where documents come from. It accepts one local folder in the shape of the filing input
contract (`contract.py`: files plus an optional `sources.json`) and copies it into the run. `prepare` then reads only
the inbox.

The run's `inbox.json` records, for every inbox file, the sha256 of the bytes copied and its origin:

- `declared`: described by the folder's `sources.json` (whatever produced the folder said so; the filing cannot
  verify it), with the declared `source` (drive, gerid, advbox...) and the optional fields,
- `local`: a file the `sources.json` does not describe.

The folder's own location is never recorded, only paths inside it.
"""

import hashlib
import unicodedata
from pathlib import Path, PurePosixPath

from jurisync.connectors.pje.filing.checkpoint import now

from .contract import validate_folder

INBOX = "inbox"
INBOX_RECORD = "inbox.json"


def _sha256(content):
    return hashlib.sha256(content).hexdigest()


def _inbox_name(name, taken):
    """A unique inbox name for a file name (NFC, no path separators); a repeated name gets -2, -3..."""
    clean = unicodedata.normalize("NFC", name).replace("/", "_").replace("\\", "_").strip() or "documento"
    stem, suffix = Path(clean).stem, Path(clean).suffix
    candidate, count = clean, 1
    while candidate.casefold() in taken:
        count += 1
        candidate = f"{stem}-{count}{suffix}"
    taken.add(candidate.casefold())
    return candidate


def _reset(store):
    store.delete_tree(INBOX)
    store.delete(INBOX_RECORD)


def _add(store, record, taken, name, content, origin):
    """Copy one file into the inbox, hashing exactly the bytes written."""
    inbox_name = f"{INBOX}/{_inbox_name(name, taken)}"
    store.write_bytes(inbox_name, content)
    record.append(
        {
            "path": inbox_name,
            "name": PurePosixPath(inbox_name).name,
            "sha256": _sha256(content),
            "size": len(content),
            "copiedAt": now(),
            "origin": origin,
        }
    )


def fill_from_folder(store, folder):
    """Validate an input folder against the contract and copy it into the run's inbox."""
    folder = Path(folder).expanduser()
    checked = validate_folder(folder)
    _reset(store)
    record, taken = [], set()
    for name in checked["files"]:
        declared = checked["sources"].get(name)
        if declared:
            fields = {key: value for key, value in declared.items() if key != "origin"}
            origin = {"kind": "declared", "source": declared["origin"], **fields}
        else:
            origin = {"kind": "local", "source": "local"}
        _add(store, record, taken, PurePosixPath(name).name, (folder / name).read_bytes(), {**origin, "file": name})
    store.write_json(INBOX_RECORD, {"version": 1, "filledAt": now(), "files": record})
    return read_inbox(store)


def read_inbox(store):
    """The inbox record, after checking every inbox file still has the hash taken when it was copied."""
    if not store.exists(INBOX_RECORD):
        raise ValueError("This run has no inbox yet: prepare it from an input folder first")
    record = store.read_json(INBOX_RECORD)
    for entry in record["files"]:
        if _sha256(store.read_bytes(entry["path"])) != entry["sha256"]:
            raise ValueError(f"Inbox file changed after it was copied: {entry['name']}")
    return record


def origin_view(origin):
    """What a reviewer sees of an origin: everything declared, never a local path."""
    return {key: value for key, value in (origin or {}).items() if value}
