"""Shared pieces for Python MCP servers: annotations and safe tool errors."""

import argparse
import fnmatch
import io
import logging
import os
import re
import secrets
import sys
from contextlib import contextmanager
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from mcp.types import ToolAnnotations

from jurisync.core import calls, errors

# Annotation presets: every tool takes one of these, never its own ToolAnnotations (docs/padroes.md, section 4).
# They are hints for the client's confirmation prompts; the real guards (write grants, acknowledgeCiencia) stay here.
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)
# Reads the portal and saves a file in the shared folder (or a local run folder); nothing changes in the portal.
SAVES_FILE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True)
# Opens what may register a pending ciência: the deadline starts and cannot be undone. Every portal, the same rule.
MAY_GIVE_CIENCIA = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True)
# Adds something (task, folder, draft, device); repeating adds again unless the tool dedupes (WRITE_IDEMPOTENT).
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)
WRITE_IDEMPOTENT = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True)
# Changes or removes what exists; REPLACE sets a value, so repeating it changes nothing more.
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True)
REPLACE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True)
PRESETS = (READ_ONLY, SAVES_FILE, MAY_GIVE_CIENCIA, WRITE, WRITE_IDEMPOTENT, DESTRUCTIVE, REPLACE)

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
    """Turn a connector's success=False result into a sanitized tool error (code ``portal_error`` unless it names one)."""
    value = sanitize_result(value)
    if value.get("success") is False:
        reason = value.get("error") or "; ".join(value.get("diagnostics") or []) or "unknown"
        code = value.get("errorCode") or errors.PortalError.code
        problem = errors.Problem(code, reason, errors.PortalError.next_step, False, errors.AGENT)
        raise ToolError(errors.render(label, problem, sanitize_diagnostics))
    return value


@contextmanager
def tool_errors(label):
    """Report anticipated failures (``errors.describe``) to the model as sanitized tool errors in one format.

    Anything else propagates: the server answers with the generic ``[unexpected]`` error and the text stays in the log.
    """
    try:
        yield
    except ToolError:
        raise
    except Exception as error:
        problem = errors.describe(error)
        if problem is None:
            raise
        raise ToolError(errors.render(label, problem, sanitize_diagnostics)) from error


def tool_error(label, error):
    """The tool error for an anticipated failure detected outside ``tool_errors``: ``raise tool_error("PJe", InputError(...))``."""
    return ToolError(errors.render(label, errors.describe(error) or errors.UNEXPECTED, sanitize_diagnostics))


class JurisyncServer(MCPServer):
    """The MCP server every connector builds in its ``create_server``: each call gets an id (the ``ref`` of its
    errors), and a crash reaches the agent as the generic ``[unexpected]`` error with that ref, never its text."""

    def __init__(self, name, **options):
        options.setdefault("version", "1.0.0")
        super().__init__(name, **options)

    async def call_tool(self, name, arguments, context=None):
        with calls.bound():
            try:
                return await super().call_tool(name, arguments, context)
            except UnexpectedToolError as error:
                log.error("tool %s failed unexpectedly (ref=%s)", name, calls.current(), exc_info=error.__cause__)
                raise UnexpectedToolError(errors.render(name, errors.UNEXPECTED, sanitize_diagnostics)) from error


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


LOOPBACK = ("127.0.0.1", "localhost", "[::1]")
HTTP_SECRET_VAR = "JURISYNC_HTTP_SECRET"


def http_settings(host="127.0.0.1", public_host=None):
    """Streamable HTTP for local installs: the SDK's DNS-rebinding check accepts only this machine's Host and Origin,
    the address bound with ``--host`` (a LAN address; not 0.0.0.0 or ::, which name no host), and ``public_host`` (the
    tunnel's address) when one is given. A page in the browser that points its own domain at 127.0.0.1 is refused.
    """
    from mcp.server.transport_security import TransportSecuritySettings

    names = list(LOOPBACK)
    if host not in ("127.0.0.1", "localhost", "::1", "0.0.0.0", "::", ""):
        names.append(f"[{host}]" if ":" in host else host)
    hosts = [*names, *(f"{name}:*" for name in names)]
    origins = [*(f"http://{name}" for name in names), *(f"http://{name}:*" for name in names)]
    if public_host:
        hosts += [public_host, f"{public_host}:*"]
        origins.append(f"https://{public_host}")
    return TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=origins)


def http_prefix(host, public_host=None):
    """The path every URL starts with: empty when only this machine reaches the server, otherwise a secret segment
    (``JURISYNC_HTTP_SECRET`` for a URL that survives restarts, else a new one per start). Whoever lacks it gets 404:
    the tunnel URL alone no longer calls the tools with this machine's credentials.
    """
    if not public_host and host in ("127.0.0.1", "localhost", "::1"):
        return ""
    if not public_host and host in ("0.0.0.0", "::", ""):
        raise SystemExit(f"--host {host} names no address a client could use: give the LAN address or --public-host")
    secret = os.environ.get(HTTP_SECRET_VAR, "").strip()
    if secret and not re.fullmatch(r"[A-Za-z0-9_-]{24,}", secret):
        raise SystemExit(f"{HTTP_SECRET_VAR}: use 24 or more letters, digits, - or _")
    return "/" + (secret or secrets.token_urlsafe(24))


def http_urls(host, port, public_host, prefix, paths):
    """Where to reach each path, printed at start: the secret is in them, so they go only to this terminal."""
    base = f"https://{public_host}" if public_host else f"http://{host}:{port}"
    return [f"{base}{prefix}{path}" for path in paths]


def serve(server, on_exit=None, argv=None):
    """Run one server over stdio (default) or, with ``--http``, over streamable HTTP (for ChatGPT connectors)."""
    parser = argparse.ArgumentParser(description="Run this MCP server over stdio, or over HTTP with --http.")
    parser.add_argument("--http", action="store_true", help="serve streamable HTTP at /mcp instead of stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--public-host", help="the tunnel's host name (e.g. abc.trycloudflare.com); adds a secret path")
    args = parser.parse_args(argv)
    if not args.http:
        return run_stdio(server, on_exit=on_exit)
    from jurisync.core.http import enable_request_logging

    enable_request_logging()
    path = http_prefix(args.host, args.public_host) + "/mcp"
    for url in http_urls(args.host, args.port, args.public_host, "", [path]):
        print(f"MCP em {url}", file=sys.stderr)
    try:
        server.run(
            "streamable-http",
            host=args.host,
            port=args.port,
            transport_security=http_settings(args.host, args.public_host),
            streamable_http_path=path,
        )
    finally:
        if on_exit is not None:
            on_exit()
