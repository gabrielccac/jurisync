"""Fixed locations for local state.

Defaults are anchored to the repository, not the working directory: Claude Desktop and Claude Code start the MCP
servers from different directories, and a working-directory default sends the same server to different folders.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Mutable state (sessions, downloads). A repository checkout keeps it beside the code; an installed package
# (uvx, pip) has no writable repository, so it uses the user's home instead.
STATE_DIR = REPO_ROOT if (REPO_ROOT / "pyproject.toml").exists() else Path.home() / ".local" / "share" / "jurisync"
# Default location of the shared secrets file when JURISYNC_ENV_FILE is not set.
USER_ENV_FILE = Path.home() / ".config" / "jurisync" / ".env"
