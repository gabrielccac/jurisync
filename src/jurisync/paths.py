"""Fixed locations for local state.

Defaults are anchored to the repository, not the working directory: Claude Desktop and Claude Code start the MCP
servers from different directories, and a working-directory default sends the same server to different folders.
"""

import json
import os
import shutil
from functools import cache
from importlib import metadata
from pathlib import Path
from urllib.parse import unquote, urlsplit

REPO_ROOT = Path(__file__).resolve().parents[2]

# Mutable state (sessions, downloads). A repository checkout keeps it beside the code; an installed package
# (uvx, pip) has no writable repository, so it uses the user's home instead.
STATE_DIR = REPO_ROOT if (REPO_ROOT / "pyproject.toml").exists() else Path.home() / ".local" / "share" / "jurisync"
# Default location of the shared secrets file when JURISYNC_ENV_FILE is not set.
USER_ENV_FILE = Path.home() / ".config" / "jurisync" / ".env"
# Customer folders (profiles, settings) live in the repository's customers/, which an installed package does not carry.
USER_CUSTOMERS_DIR = Path.home() / ".config" / "jurisync" / "customers"


@cache
def installed_from():
    """The local folder the package was installed from (`uv tool install ~/code/jurisync`), if any.

    Installers record it in the standard direct_url.json (PEP 610); a package from an index has none.
    """
    try:
        record = json.loads(metadata.distribution("jurisync").read_text("direct_url.json") or "{}")
    except (metadata.PackageNotFoundError, ValueError):
        return None
    url = urlsplit(record.get("url", ""))
    return Path(unquote(url.path)) if url.scheme == "file" and "dir_info" in record else None


def customers_root():
    """Where customer folders are: JURISYNC_CUSTOMERS_DIR, else this checkout's customers/, else the checkout the
    package was installed from, else ~/.config/jurisync/customers."""
    if os.environ.get("JURISYNC_CUSTOMERS_DIR"):
        return Path(os.environ["JURISYNC_CUSTOMERS_DIR"]).expanduser()
    source = installed_from()
    for candidate in (REPO_ROOT / "customers", source / "customers" if source else None, USER_CUSTOMERS_DIR):
        if candidate is not None and candidate.is_dir():
            return candidate
    return REPO_ROOT / "customers"


# Filing data (client documents): inputs/ holds the folders exporters write for pje_filing_prepare, runs/ the
# assembled runs. Never inside a repository checkout, whatever STATE_DIR is.
FILING_HOME = Path.home() / ".local" / "share" / "jurisync"
LEGACY_FILING_FOLDERS = {"runs": ("filings",), "inputs": ("filing-input", "intake")}


def filing_root():
    """JURISYNC_FILING_DIR, else ~/.local/share/jurisync/filing; it holds inputs/ and runs/."""
    configured = os.environ.get("JURISYNC_FILING_DIR")
    return Path(configured).expanduser().resolve() if configured else FILING_HOME / "filing"


def filing_folder(kind):
    """The private folder of one kind of filing data ("inputs" or "runs"), created on first use.

    With the default root, folders an earlier version kept elsewhere (filings/, filing-input/, intake/) are moved in
    first, so their runs and inputs keep working under the new names.
    """
    root = filing_root()
    folder = root / kind
    for directory in (root, folder):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
    if not os.environ.get("JURISYNC_FILING_DIR"):
        migrate_legacy_filing_folder(kind, folder)
    return folder


def migrate_legacy_filing_folder(kind, folder, home=None):
    """Move each entry of the legacy folders of `kind` into `folder`; an entry whose name is taken stays where it is.

    Returns the names moved and the names left behind."""
    moved, kept = [], []
    for legacy_name in LEGACY_FILING_FOLDERS[kind]:
        legacy = (home or FILING_HOME) / legacy_name
        if not legacy.is_dir() or legacy.is_symlink():
            continue
        for entry in sorted(legacy.iterdir()):
            target = folder / entry.name
            if target.exists():
                kept.append(f"{legacy_name}/{entry.name}")
                continue
            shutil.move(str(entry), str(target))
            for path in (target, *(target.rglob("*") if target.is_dir() else ())):
                path.chmod(0o700 if path.is_dir() else 0o600)
            moved.append(entry.name)
        if not any(legacy.iterdir()):
            legacy.rmdir()
    return moved, kept
