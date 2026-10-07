"""Shared pieces for Python MCP servers: annotations and safe tool errors."""

import io
import os
import re
import sys
from contextlib import contextmanager
from pathlib import Path

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)


def load_env_file(path=None):
    """Load KEY=VALUE lines like `node --env-file`: existing variables win.

    The path comes from JURISYNC_ENV_FILE (set per server in .mcp.json);
    without it, nothing is loaded.
    """
    path = path or os.environ.get("JURISYNC_ENV_FILE")
    if not path:
        return
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        os.environ.setdefault(key, value)


def run_stdio(server, on_exit=None):
    """Serve over stdio while connector code keeps printing progress lines.

    The SDK diverts fd 1 to stderr while serving, but points it back at the
    protocol pipe on exit. Unbuffered stdout leaves no pending prints to be
    flushed onto the protocol stream at that point.
    """
    from jurisync.core.http import enable_request_logging

    enable_request_logging()
    sys.stdout.flush()
    # Deliberately never closed: fd 1 belongs to the process, not to this wrapper.
    sys.stdout = io.TextIOWrapper(open(1, "wb", buffering=0, closefd=False), encoding="utf-8", write_through=True)  # noqa: SIM115
    try:
        server.run("stdio")
    finally:
        if on_exit is not None:
            on_exit()


def sanitize_diagnostics(value):
    """Strip URLs and session-like tokens before text reaches the calling agent."""
    if value is None:
        return ""
    text = re.sub(r"https?://\S+", "[url]", str(value), flags=re.I)
    text = re.sub(r"(?:PHPSESSID|session|cookie|hash|viewstate|ca)\s*[=:]\s*\S+", "[redacted]", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()[:2000]


def sanitize_result(value):
    """Sanitize the error and diagnostics fields of a connector result."""
    value = dict(value)
    if isinstance(value.get("error"), str):
        value["error"] = sanitize_diagnostics(value["error"])
    diagnostics = value.get("diagnostics")
    if diagnostics is not None:
        diagnostics = diagnostics if isinstance(diagnostics, list) else [diagnostics]
        value["diagnostics"] = [sanitize_diagnostics(item) for item in diagnostics]
    return value


def require_success(label, value):
    """Turn a connector's success=False result into a sanitized tool error."""
    value = sanitize_result(value)
    if value.get("success") is False:
        reason = value.get("error") or "; ".join(value.get("diagnostics") or []) or "unknown"
        raise ToolError(f"{label} request failed: {reason}")
    return value


@contextmanager
def tool_errors(label, *anticipated):
    """Report anticipated failures to the model as sanitized tool errors.

    Anything else propagates and the SDK reports only a generic tool failure,
    so unexpected exception text never reaches the caller.
    """
    try:
        yield
    except anticipated as error:
        raise ToolError(f"{label} request failed: {sanitize_diagnostics(error) or 'unknown'}") from error
