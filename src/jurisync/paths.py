"""Fixed locations for local state.

Defaults are anchored to the repository, not the working directory: Claude Desktop and Claude Code start the MCP
servers from different directories, and a working-directory default sends the same server to different folders.
"""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Mutable state (sessions, downloads). A repository checkout keeps it beside the code; an installed package
# (uvx, pip) has no writable repository, so it uses the user's home instead.
STATE_DIR = REPO_ROOT if (REPO_ROOT / "pyproject.toml").exists() else Path.home() / ".local" / "share" / "jurisync"
# Default location of the shared secrets file when JURISYNC_ENV_FILE is not set.
USER_ENV_FILE = Path.home() / ".config" / "jurisync" / ".env"


def _override(variable):
    value = os.environ.get(variable, "").strip() if variable else ""
    return Path(value).expanduser() if value else None


def user_cache(name, override=None):
    """``~/.cache/jurisync/<name>`` (honoring ``XDG_CACHE_HOME``), or the path in the ``override`` variable.

    Disposable state: session cookies, tokens, downloaded models.
    """
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return _override(override) or base / "jurisync" / name


def user_data(name, override=None):
    """``~/.local/share/jurisync/<name>``, or the path in the ``override`` variable.

    State worth keeping: saved documents, filing runs, the hosted store.
    """
    return _override(override) or Path.home() / ".local" / "share" / "jurisync" / name
