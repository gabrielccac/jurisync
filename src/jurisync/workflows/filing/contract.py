"""The filing input contract: the one shape of folder the filing accepts, and its validator.

The filing knows nothing about where documents come from. Whoever gathers them (an agent through its connectors, the
ADVBOX export, a person) hands over ONE local folder:

    <folder>/
      any files, in any subfolders     PDFs, Word (.doc/.docx), images (.jpg/.jpeg/.png/.heic)
      sources.json                     optional: where each file came from

Rules: regular files only (symbolic links are refused), hidden names (starting with ".") are ignored, at most
MAX_FILES files.

`sources.json`, version 1:

    {
      "version": 1,
      "files": [
        {
          "file": "laudos/laudo-caps.pdf",       required: the file's path inside the folder
          "origin": "drive",                     required: free text naming the origin (drive, gerid, advbox, local...)
          "originId": "1AbC...",                 optional: the file's id at the origin
          "originPath": "Clientes/Maria/Laudos", optional: folder or path at the origin
          "originalName": "Laudo CAPS.pdf",      optional: the name at the origin
          "modifiedAt": "2026-10-01T12:00:00Z",  optional: last modification at the origin
          "link": "https://..."                  optional: an http(s) address to open it at the origin
        }
      ]
    }

The filing records these entries as *declared* origins (it cannot verify them) and files without an entry as
*local*. Producers write the file with `write_sources`, which validates it against the folder; consumers call
`validate_folder`. This module depends on the standard library only, so a producer can use it without depending on the
filing itself.
"""

import json
import os
import re
import stat
import tempfile
from pathlib import Path, PurePosixPath

CONTRACT_VERSION = 1
SOURCES = "sources.json"
REQUIRED = ("file", "origin")
OPTIONAL = ("originId", "originPath", "originalName", "modifiedAt", "link")
MAX_FILES = 200


def _text(value, field, where, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
        raise ValueError(f"{where}: {field} must be a nonempty text of at most {limit} characters")
    return value.strip()


def _inside(name, where):
    """A POSIX path inside the folder: not absolute, no backslash, no empty, `.` or `..` segment."""
    if "\\" in name or PurePosixPath(name).is_absolute() or any(part in ("", ".", "..") for part in name.split("/")):
        raise ValueError(f"{where}: file must be a path inside the folder, without '..'")
    return name


def validate_sources(data):
    """Check a parsed sources.json; returns {file: {origin, ...optional fields}}."""
    if not isinstance(data, dict) or set(data) - {"version", "files"}:
        raise ValueError(f"{SOURCES} must be an object with only version and files")
    if data.get("version") != CONTRACT_VERSION:
        raise ValueError(f"{SOURCES} version must be {CONTRACT_VERSION}")
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
        name = _inside(_text(entry.get("file"), "file", where, 300), where)
        if name in declared:
            raise ValueError(f"{where}: {name} is described twice")
        origin = {"origin": _text(entry.get("origin"), "origin", where, 40)}
        for field in OPTIONAL:
            if field in entry:
                origin[field] = _text(entry[field], field, where, 1000)
        if "link" in origin and not re.match(r"https?://", origin["link"]):
            raise ValueError(f"{where}: link must be an http(s) address")
        declared[name] = origin
    return declared


def load_sources(folder):
    """The folder's validated sources.json, or {} when it has none."""
    path = Path(folder) / SOURCES
    if not path.exists() and not path.is_symlink():
        return {}
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{SOURCES} must be a regular file")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{SOURCES} is not valid JSON: {error}") from None
    return validate_sources(data)


def input_files(folder):
    """The folder's input files (recursive, sorted) as POSIX paths; symlinks refused, hidden names ignored."""
    root = Path(folder)
    files = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part.startswith(".") for part in relative.parts):
            continue
        if path.is_symlink():
            raise ValueError(f"Symbolic links are not accepted in the input folder: {relative.as_posix()}")
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise ValueError(f"Not a regular file: {relative.as_posix()}")
        if relative.as_posix() != SOURCES:
            files.append(relative.as_posix())
    return files


def validate_folder(folder):
    """Check a whole input folder against the contract; returns {"files": [...], "sources": {...}}."""
    folder = Path(folder).expanduser()
    if not folder.is_absolute() or not folder.is_dir() or folder.is_symlink():
        raise ValueError("The input folder must be an absolute path to an existing folder (not a symbolic link)")
    sources = load_sources(folder)
    files = input_files(folder)
    if not files:
        raise ValueError("The input folder has no files")
    if len(files) > MAX_FILES:
        raise ValueError(f"The input folder has more than {MAX_FILES} files")
    missing = sorted(set(sources) - set(files))
    if missing:
        raise ValueError(f"{SOURCES} describes files that are not in the folder: {', '.join(missing)}")
    return {"files": files, "sources": sources}


def write_sources(folder, entries):
    """Write a validated sources.json (owner-only, atomic) into an input folder that already holds the files."""
    data = {"version": CONTRACT_VERSION, "files": list(entries)}
    validate_sources(data)
    folder = Path(folder)
    descriptor, temp = tempfile.mkstemp(dir=folder, prefix=".sources-")
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump(data, output, ensure_ascii=False, indent=2)
    os.chmod(temp, 0o600)
    os.replace(temp, folder / SOURCES)
    validate_folder(folder.resolve())
    return data
