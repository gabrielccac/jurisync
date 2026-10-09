"""Fixed locations for local state.

Defaults are anchored to the repository, not the working directory: Claude Desktop and Claude Code start the MCP
servers from different directories, and a working-directory default sends the same server to different folders.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
