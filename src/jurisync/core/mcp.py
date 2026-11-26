"""Shared pieces for Python MCP servers: annotations and safe tool errors."""

import argparse
import fnmatch
import io
import logging
import os
import re
import sys
from contextlib import contextmanager
from pathlib import Path

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from jurisync.core.session_pool import REASONS

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)

log = logging.getLogger(__name__)

# Names that identify or unlock one person's account.
SECRET_PATTERNS = (
    "*_PASSWORD", "*_SENHA", "*_PIN", "*_2FA_*", "*_TOTP*", "*_OTP_SECRET", "*_P12", "*_PFX", "*_PASSPHRASE",
    "*_TOKEN", "*_SECRET", "*_API_KEY", "*_KEY", "*_DEVICE_ID", "*_EMAIL", "*_USERNAME", "*_USUARIO", "*_CPF",
    "*_EXPORT_DATA", "*_QR", "*_CREDENTIALS",
)  # fmt: skip


def is_secret_name(name):
    return any(fnmatch.fnmatchcase(name.upper(), pattern) for pattern in SECRET_PATTERNS)


# With a customer selected, the shared file provides only these: settings about this computer, never about an office.
# JURISYNC_* are locations, binaries and limits; the *_PATH/_BIN/_CACHE names point at installed programs and
# libraries; DJEN_BRIDGE_* is the server's own route to the CNJ API; the ADVBOX numbers are local limits; DISPLAY is
# the screen a launched browser opens on. Everything else (credentials, write grants such as *_ALLOW_WRITES, *_CERT,
# *_SESSION_ACCOUNT, *_OTP_PROFILE_*, *_LOGIN_URL and base URLs, session files, browser profiles, API keys billed to an
# office) comes from customers/<name>/.env or portals.toml, so turning writes on for one office leaves the others off.
MACHINE_PATTERNS = (
    "JURISYNC_*", "LIBREOFFICE_BIN", "*_CHROME_PATH", "PDFIUM_LIB_PATH", "ORT_DYLIB_PATH", "PDF_INSPECTOR_MODEL_CACHE",
    "DJEN_BRIDGE_*", "PJE_SESSION_DIR", "ADVBOX_TIMEOUT_SECONDS", "ADVBOX_MAX_DOWNLOAD_BYTES",
    "ADVBOX_MAX_PDF_TEXT_CHARS", "DISPLAY", "WAYLAND_DISPLAY",
)  # fmt: skip
NEVER_SHARED = ("JURISYNC_VAULT_KEY",)


def is_machine_name(name):
    name = name.upper()
    return name not in NEVER_SHARED and any(fnmatch.fnmatchcase(name, pattern) for pattern in MACHINE_PATTERNS)


def load_env_file(path=None):
    """Load KEY=VALUE lines like `node --env-file`: existing variables win.

    The path comes from JURISYNC_ENV_FILE (set per server in .mcp.json); without it the
    user's ``~/.config/jurisync/.env`` is used when it exists. When JURISYNC_CUSTOMER names a customer, its
    gitignored ``customers/<name>/.env`` is loaded first, so a customer's values
    take precedence over the shared file. The values its portals.toml ``[env].set`` pins (e.g. ``PJE_1G_CERT=true``)
    come next, so a checkout behaves like an installed bundle, which writes them into the customer's file.
    With a customer selected the shared file then provides machine settings only (``MACHINE_PATTERNS``); without
    one it is the single account's file and loads whole.
    """
    from jurisync.customers import customer_env_path, customer_name, fixed_variables
    from jurisync.paths import USER_ENV_FILE

    shared = path or os.environ.get("JURISYNC_ENV_FILE") or (USER_ENV_FILE if USER_ENV_FILE.exists() else None)
    if shared and not customer_name():
        # A client that registers the server without JURISYNC_CUSTOMER (Claude Desktop) can name it in the shared file.
        _load_lines(shared, only={"JURISYNC_CUSTOMER"})
    customer = customer_name()
    own = customer_env_path()
    if own:
        _load_lines(own)
    if customer:
        for key, value in fixed_variables(customer).items():
            os.environ.setdefault(key, value)
    if shared:
        # Only a checkout that keeps the office's own customers/<name>/.env treats the shared file as other offices'
        # secrets. An installed office (plugin or bundle) has no such file: its ~/.config/jurisync/.env is its own.
        _load_lines(shared, machine_only=bool(own and own.exists()))


def _load_lines(path, only=None, machine_only=False):
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return
    skipped = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        key, value = key.strip(), value.strip()
        if only is not None and key not in only:
            continue
        if machine_only and not is_machine_name(key):
            # A customer's account comes only from its own file: the shared one may hold another person's.
            skipped.append(key)
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        os.environ.setdefault(key, value)
    if skipped:
        log.info(
            "not read from the shared env file (they belong in customers/<name>/.env): %s", ", ".join(sorted(skipped))
        )


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


_ROOTS = "home|tmp|run|var|opt|usr|root|srv|mnt|etc|Users|app|data|proc|dev|private|media|bin|lib|sys"
_QUOTED_PATH = re.compile(r"""(['"])(?:~?/|[A-Za-z]:[\\/])[^'"\n]*\1""")
# An absolute path: a known root, any /two/segments, ~/ or a Windows drive (a space is kept while the path goes on).
_PATH = re.compile(
    rf"(?<![\w.:/\\\]-])(?:~[/\\]|/(?:{_ROOTS})(?![\w-])|/[\w.@+-]+/|[A-Za-z]:[\\/](?:[^\s'\"<>|]| (?=[^\s'\"<>|]*[\\/]))*)"
    r"[^\s'\"<>|]*"
)
_VALUE = r"""(?:"[^"]*"|'[^']*'|[^\s,;&]+)"""
_KEYS = (
    r"password|passwd|senha|pwd|pin|secret|token|ticket|signature|csrf|api[_-]?key|otp|totp|"
    r"PHPSESSID|JSESSIONID|CASTGC|session(?:[_-]?id)?|hash|viewstate|ca"
)
# Applied in order. The names are whole words (or the tail of a snake/dotted name): "Publicaca: 12" is not "ca: 12".
_REDACTIONS = (
    (re.compile(r"\bauthorization\s*[=:]\s*(?:[A-Za-z]+\s+)?\S+", re.I), "[redacted]"),
    (re.compile(r"\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{8,}"), "[redacted]"),
    (re.compile(r"https?://\S+", re.I), "[url]"),
    (_QUOTED_PATH, "[path]"),
    (_PATH, "[path]"),
    (re.compile(r"(?<![A-Za-zÀ-ÿ])(?:set-)?cookies?\s*[=:]\s*(?:[^\s;=]+=[^\s;]*;?\s*)+", re.I), "[redacted] "),
    (re.compile(rf"(?<![A-Za-zÀ-ÿ])(?:{_KEYS}|cookie)\s*[=:]\s*{_VALUE}", re.I), "[redacted]"),
    (re.compile(r"(?<![\d.])(\d{3})\.\d{3}\.\d{3}-(\d{2})(?!\d)"), r"\1.***.***-\2"),
    (re.compile(r"(\bCPF\W{0,3})(\d{3})\d{6}(\d{2})(?!\d)", re.I), r"\1\2******\3"),
)


def sanitize_diagnostics(value):
    """Strip URLs, local paths, credentials, session tokens and CPFs before text reaches the calling agent."""
    if value is None:
        return ""
    text = str(value)
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
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
    so unexpected exception text never reaches the caller. A failed login carries its reason code: ``[otp_refused]``.
    """
    try:
        yield
    except anticipated as error:
        reason = getattr(error, "reason", None)
        tag = f" [{reason}]" if reason in REASONS else ""
        raise ToolError(f"{label} request failed{tag}: {sanitize_diagnostics(error) or 'unknown'}") from error


# The ``portal`` field of every tool: the enum holds the keys, and one tool of the server lists them with their hosts.
PORTAL_HELP = "Portal key; see the enum."
PORTAL_NOTE = "\nPortals: "


def portal_note(keys):
    """The portals once, with their hosts: the line ``offer_portals`` (local) and ``Office`` (hosted) add to one tool."""
    from jurisync import portals

    return PORTAL_NOTE + ", ".join(f"{key} ({portals.get(key).host})" for key in keys) + "."


def with_portal_note(description, keys):
    return (description or "").partition(PORTAL_NOTE)[0] + portal_note(keys)


def offer_portals(server, keys):
    """List only ``keys`` as the ``portal`` choices of the server's tools, and the portals once, in its first tool.

    An agent shown every wired portal tries the ones the account has no login for.
    """
    keys, noted = list(keys), False
    for tool in server._tool_manager.list_tools():
        field = tool.parameters.get("properties", {}).get("portal")
        if field is None or "const" in field:
            continue
        field["enum"] = [key for key in field.get("enum", keys) if key in keys] or keys
        field["description"] = PORTAL_HELP
        if not noted:
            tool.description, noted = with_portal_note(tool.description, keys), True


def http_settings():
    """Streamable HTTP for local test installs reached through a tunnel.

    The Host header of a tunnel is not loopback, so the SDK's DNS-rebinding check is off. There is no
    authentication: bind to 127.0.0.1 (the default) and expose it only through a tunnel you control.
    """
    from mcp.server.transport_security import TransportSecuritySettings

    return TransportSecuritySettings(enable_dns_rebinding_protection=False)


def serve(server, on_exit=None, argv=None):
    """Run one server over stdio (default) or, with ``--http``, over streamable HTTP (for ChatGPT connectors)."""
    parser = argparse.ArgumentParser(description="Run this MCP server over stdio, or over HTTP with --http.")
    parser.add_argument("--http", action="store_true", help="serve streamable HTTP at /mcp instead of stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    if not args.http:
        return run_stdio(server, on_exit=on_exit)
    from jurisync.core.http import enable_request_logging

    enable_request_logging()
    try:
        server.run("streamable-http", host=args.host, port=args.port, transport_security=http_settings())
    finally:
        if on_exit is not None:
            on_exit()
