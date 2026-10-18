"""A run's single entry point: the files to prepare, copied into `inbox/`, each with its hash and its origin.

jurisync does not go and fetch documents itself (except the ADVBOX protocol folder, kept for compatibility): the agent
gathers them through its connectors (Drive, GERID, ADVBOX...) into one local folder, optionally with a `sources.json`
saying where each file came from. `fill_from_folder` copies that folder into the run; `fill_from_advbox` fills the same
inbox from ADVBOX. `prepare` then reads only the inbox.

The run's `inbox.json` records, for every inbox file, the sha256 taken while copying it and its origin:

- `declared`: described by the folder's `sources.json` (written by the agent; jurisync cannot verify it),
- `local`: a file of the folder that `sources.json` does not describe,
- `fetched`: obtained by jurisync itself (ADVBOX).

A folder's `sources.json` (version 1):

    {
      "version": 1,
      "files": [
        {
          "file": "laudos/laudo-caps.pdf",      required: path inside the folder, as written there
          "origin": "drive",                    required: free text, e.g. drive, gerid, advbox, local
          "originId": "1AbC...",                optional: the file's id at the origin
          "originPath": "Clientes/Maria/Laudos",optional: folder or path at the origin
          "originalName": "Laudo CAPS.pdf",     optional: the name at the origin
          "modifiedAt": "2026-10-01T12:00:00Z", optional: last modification at the origin
          "link": "https://drive.google.com/..."optional: where to open it at the origin
        }
      ]
    }
"""

import hashlib
import json
import re
import stat
import unicodedata
from pathlib import Path, PurePosixPath

from jurisync.connectors.pje.filing.checkpoint import now
from jurisync.connectors.pje.filing.store import WORK, run_name

INBOX = "inbox"
INBOX_RECORD = "inbox.json"
SOURCES = "sources.json"
SOURCES_VERSION = 1
REQUIRED = ("file", "origin")
OPTIONAL = ("originId", "originPath", "originalName", "modifiedAt", "link")
MAX_FILES = 200


def _sha256(content):
    return hashlib.sha256(content).hexdigest()


def _text(value, field, where, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
        raise ValueError(f"{where}: {field} must be a nonempty text of at most {limit} characters")
    return value.strip()


def load_sources(folder):
    """The folder's validated sources.json as {relative file: declared origin}, or {} when the folder has none."""
    path = Path(folder) / SOURCES
    if not path.exists():
        return {}
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{SOURCES} must be a regular file")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{SOURCES} is not valid JSON: {error}") from None
    if not isinstance(data, dict) or set(data) - {"version", "files"}:
        raise ValueError(f"{SOURCES} must be an object with only version and files")
    if data.get("version") != SOURCES_VERSION:
        raise ValueError(f"{SOURCES} version must be {SOURCES_VERSION}")
    files = data.get("files")
    if not isinstance(files, list):
        raise ValueError(f"{SOURCES} files must be a list")
    declared = {}
    for index, entry in enumerate(files):
        where = f"{SOURCES} files[{index}]"
        if not isinstance(entry, dict):
            raise ValueError(f"{where} must be an object")
        unknown = sorted(set(entry) - set(REQUIRED) - set(OPTIONAL))
        if unknown:
            raise ValueError(f"{where} has unknown fields: {', '.join(unknown)}")
        name = _text(entry.get("file"), "file", where, 300)
        try:
            name = run_name(name)
        except ValueError:
            raise ValueError(f"{where}: file must be a path inside the folder, without '..'") from None
        if name in declared:
            raise ValueError(f"{where}: {name} is described twice")
        origin = {"kind": "declared", "source": _text(entry.get("origin"), "origin", where, 40)}
        for field in OPTIONAL:
            if field in entry:
                origin[field] = _text(entry[field], field, where, 1000)
        if "link" in origin and not re.match(r"https?://", origin["link"]):
            raise ValueError(f"{where}: link must be an http(s) address")
        declared[name] = origin
    return declared


def _folder_files(folder):
    """Regular files of the folder (recursively), as POSIX names; symlinks are refused, hidden files ignored."""
    root = Path(folder)
    files = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part.startswith(".") for part in relative.parts):
            continue
        if path.is_symlink():
            raise ValueError(f"Symbolic links are not accepted in the source folder: {relative.as_posix()}")
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise ValueError(f"Not a regular file: {relative.as_posix()}")
        if relative.as_posix() != SOURCES:
            files.append(relative.as_posix())
    return files


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
    """Copy a local folder into the run's inbox. The folder's own location is not recorded, only paths inside it."""
    folder = Path(folder).expanduser()
    if not folder.is_absolute() or not folder.is_dir():
        raise ValueError("The source folder must be an absolute path to an existing folder")
    declared = load_sources(folder)
    files = _folder_files(folder)
    if not files:
        raise ValueError("The source folder has no files")
    if len(files) > MAX_FILES:
        raise ValueError(f"The source folder has more than {MAX_FILES} files")
    missing = sorted(set(declared) - set(files))
    if missing:
        raise ValueError(f"{SOURCES} describes files that are not in the folder: {', '.join(missing)}")
    _reset(store)
    record, taken = [], set()
    for name in files:
        origin = declared.get(name) or {"kind": "local", "source": "local"}
        _add(store, record, taken, PurePosixPath(name).name, (folder / name).read_bytes(), {**origin, "file": name})
    store.write_json(INBOX_RECORD, {"version": 1, "filledAt": now(), "from": "folder", "files": record})
    return read_inbox(store)


def fill_from_advbox(store, documents, customer_id, folder):
    """Fill the run's inbox from one ADVBOX customer folder (origin `fetched`, source `advbox`)."""
    files = documents.list_customer_files(customer_id, folder)["files"]
    if not files:
        raise ValueError(f"The selected folder has no files: {folder}")
    _reset(store)
    download_area = f"{WORK}/advbox"
    store.delete_tree(download_area)
    record, taken = [], set()
    for entry in files:
        downloaded = documents.download_file(
            entry.get("downloadUrl") or entry["url"],
            filename=entry["name"],
            directory=store.workspace(download_area),
            inspect=False,
        )
        name = store.name_of(downloaded["path"])
        content = store.read_bytes(name)
        store.delete(name)
        origin = {"kind": "fetched", "source": "advbox", "originPath": folder, "originalName": entry["name"]}
        if entry.get("fileId"):
            origin["originId"] = str(entry["fileId"])
        if entry.get("uploadedAt"):
            origin["modifiedAt"] = entry["uploadedAt"]
        _add(store, record, taken, entry["name"], content, origin)
    store.delete_tree(download_area)
    store.write_json(
        INBOX_RECORD,
        {
            "version": 1,
            "filledAt": now(),
            "from": "advbox",
            "customerId": str(customer_id),
            "folder": folder,
            "files": record,
        },
    )
    return read_inbox(store)


def read_inbox(store):
    """The inbox record, after checking every inbox file still has the hash taken when it was copied."""
    if not store.exists(INBOX_RECORD):
        raise ValueError("This run has no inbox yet: fill it from a folder or from ADVBOX first")
    record = store.read_json(INBOX_RECORD)
    for entry in record["files"]:
        if _sha256(store.read_bytes(entry["path"])) != entry["sha256"]:
            raise ValueError(f"Inbox file changed after it was copied: {entry['name']}")
    return record


def origin_view(origin):
    """What a reviewer sees of an origin: everything declared or fetched, never a local path."""
    return {key: value for key, value in (origin or {}).items() if value}
