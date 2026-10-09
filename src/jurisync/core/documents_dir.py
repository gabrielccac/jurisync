"""Shared folder where readers save fetched documents so another tool can attach them by file name."""

import os
import re
from pathlib import Path

NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def documents_directory():
    configured = os.environ.get("JURISYNC_DOCUMENTS_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".local" / "share" / "jurisync" / "documents"


def save_document(name, content):
    """Write ``content`` under ``name`` (a plain file name) in the shared folder and return the name."""
    if not NAME_PATTERN.fullmatch(name or ""):
        raise ValueError("Saved document names use letters, digits, dot, dash and underscore only")
    root = documents_directory()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    (root / name).write_bytes(content)
    return name


def resolve_saved_document(name):
    """Return the path of a previously saved document; names are confined to the shared folder."""
    if not NAME_PATTERN.fullmatch(name or ""):
        raise ValueError("Saved document names use letters, digits, dot, dash and underscore only")
    root = documents_directory().resolve()
    path = (root / name).resolve()
    if path.parent != root:
        raise ValueError("Saved document must be inside the shared documents folder")
    if not path.is_file():
        raise FileNotFoundError(f"No saved document named {name}")
    return path
