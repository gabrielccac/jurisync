"""Shared folder where readers save fetched documents so another tool can attach them by file name.

On the hosted server each office gets its own subfolder, so one office never attaches or reads another's files.
"""

import re

from jurisync.core import credentials
from jurisync.paths import user_data

NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def documents_directory():
    root = user_data("documents", "JURISYNC_DOCUMENTS_DIR")
    tenant = credentials.tenant()
    return root / "tenants" / tenant if tenant else root


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


# Files inside a saved folder keep the name they have at the source (accents and spaces carry meaning, e.g.
# "Procuração.pdf"); only separators and leading dots are refused. The folder itself uses the strict NAME_PATTERN.
FOLDER_FILE_PATTERN = re.compile(r"[^\W_][\w .,()\-]{0,180}")


def save_folder_file(folder, name, content):
    """Write ``content`` as ``name`` in the saved folder ``folder`` (created on demand); never overwrites.

    A connector that pulls a set of documents for a workflow (a filing, a review) saves them with this and hands the
    folder name on, whatever system they came from. Returns the name actually written.
    """
    from jurisync.core.pdf import available_path

    if not NAME_PATTERN.fullmatch(folder or ""):
        raise ValueError("Saved folder names use letters, digits, dot, dash and underscore only")
    if not FOLDER_FILE_PATTERN.fullmatch(name or ""):
        raise ValueError("Unsupported file name for a saved folder")
    directory = documents_directory() / folder
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = available_path(directory, name)
    path.write_bytes(content)
    return path.name


def resolve_saved_folder(name):
    """The existing saved folder ``name`` (confined to the shared documents folder) and its regular files, sorted."""
    if not NAME_PATTERN.fullmatch(name or ""):
        raise ValueError("Saved folder names use letters, digits, dot, dash and underscore only")
    root = documents_directory().resolve()
    path = root / name
    if path.is_symlink() or path.resolve().parent != root or not path.is_dir():
        raise FileNotFoundError(f"No saved folder named {name}")
    files = sorted(entry for entry in path.iterdir() if entry.is_file() and not entry.is_symlink())
    return path, files
