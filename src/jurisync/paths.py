"""Fixed locations for local state.

Defaults are anchored to the repository, not the working directory: Claude Desktop and Claude Code start the MCP
servers from different directories, and a working-directory default sends the same server to different folders.
"""

import json
import os
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
