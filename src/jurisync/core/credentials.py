"""Where a connector reads its account: the process environment locally, one tenant's vault entry when hosted.

A local install keeps one account per process, so ``env`` and ``read_file`` read ``os.environ`` as before. The hosted
server serves several offices from one process: for each request it opens a ``scope`` with that tenant's values, and
inside it every read comes from those values only. A name the tenant does not have is empty, never the server's own
environment, so one office can never log in with another's (or the operator's) credentials.

Files (the A1 certificate) travel as bytes in the scope and are read with ``read_file``; nothing is written to disk.
A connector that learns a new value of its own (ADVBOX's device id) stores it with ``save``.

Each scope carries a ``signature`` of its credentials. The operator changes a vault from another process
(``jurisync-hosted``), so what the server keeps in memory for an office (logins, clients, tokens, a cool-down after
rejected credentials) is kept with the signature it was made under and dropped when the office's signature changes.
"""

import contextvars
import hashlib
import os
import tempfile
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from jurisync.paths import REPO_ROOT


@dataclass(frozen=True)
class Scope:
    tenant: str
    values: dict = field(default_factory=dict)
    files: dict = field(default_factory=dict)
    saver: Callable[[str, str], None] | None = None
    signature: str = ""


_current: contextvars.ContextVar[Scope | None] = contextvars.ContextVar("jurisync_credentials", default=None)


def digest(values, files, skip=()):
    """A short SHA-256 of credentials (``values`` and ``files``), ignoring the names in ``skip``."""
    hashed = hashlib.sha256()
    for kind, entries in ((b"v", values or {}), (b"f", files or {})):
        for name in sorted(set(entries) - set(skip)):
            value = entries[name]
            value = value if isinstance(value, bytes) else str(value).encode("utf-8")
            for part in (kind, name.encode("utf-8"), value):
                hashed.update(len(part).to_bytes(8, "big") + part)
    return hashed.hexdigest()[:16]


@contextmanager
def scope(tenant, values=None, files=None, saver=None, signature=None):
    """Read every credential from ``values`` (name -> text) and ``files`` (name -> bytes) inside the block.

    ``saver(name, value)``, when given, stores one value in the tenant's vault entry for later requests (see ``save``).
    ``signature`` defaults to the ``digest`` of the values and files.
    """
    if not tenant:
        raise ValueError("A credentials scope needs a tenant id")
    values, files = dict(values or {}), dict(files or {})
    if signature is None:
        signature = digest(values, files)
    token = _current.set(Scope(tenant, values, files, saver, signature))
    try:
        yield
    finally:
        _current.reset(token)


def tenant():
    """The tenant of the current scope, or "" for a local single-account install."""
    current = _current.get()
    return current.tenant if current else ""


def signature():
    """The current scope's credentials signature; "" locally (a changed .env takes a restart)."""
    current = _current.get()
    return current.signature if current else ""


def env(name, default=""):
    """A credential or per-account setting: the scope's value when hosted, ``os.environ`` locally."""
    current = _current.get()
    if current is None:
        return os.environ.get(name, default)
    return current.values.get(name, default)


def service_credential(name):
    """A secret systemd hands the service as a file (``LoadCredential``/``LoadCredentialEncrypted``), or "".

    The file in ``$CREDENTIALS_DIRECTORY`` is named like the variable, so the secret is never in the environment, on
    a command line or in the unit file.
    """
    directory = os.environ.get("CREDENTIALS_DIRECTORY", "").strip()
    if not directory or "/" in name:
        return ""
    try:
        return (Path(directory) / name).read_text(encoding="utf-8").strip()
    except (FileNotFoundError, NotADirectoryError):
        return ""


def machine(name, default=""):
    """A setting of the server itself (a CAPTCHA key it pays for, a model name), never of one office.

    Always the process environment or the service's systemd credentials, also inside a tenant scope: an office's vault
    entry cannot set or override them. Only for ``JURISYNC_*`` names (``core.mcp.MACHINE_PATTERNS``), never for an
    account or a write grant.
    """
    if not name.startswith("JURISYNC_") or name == "JURISYNC_VAULT_KEY":
        raise ValueError(f"{name} is not a machine setting")
    return os.environ.get(name) or service_credential(name) or default


def has(name):
    """Whether the account has this credential, as a value or (hosted) as file bytes."""
    current = _current.get()
    if current is None:
        return bool(os.environ.get(name, "").strip())
    return bool(current.values.get(name, "").strip()) or name in current.files


def read_file(name):
    """The bytes of a file credential: the scope's bytes when hosted, the file ``os.environ[name]`` points to locally."""
    current = _current.get()
    if current is not None:
        if name not in current.files:
            raise RuntimeError(f"{name} is not set for this account")
        return current.files[name]
    path = os.environ.get(name, "").strip().strip('"')
    if not path:
        raise RuntimeError(f"Set {name}")
    return Path(path).expanduser().read_bytes()


def customers_dir():
    return Path(os.environ.get("JURISYNC_CUSTOMERS_DIR") or REPO_ROOT / "customers")


def customer_name():
    return env("JURISYNC_CUSTOMER").strip()


def customer_dir(name):
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise ValueError(f"invalid customer name: {name!r}")
    return customers_dir() / name


def customer_env_path(name=None):
    name = name or customer_name()
    return customer_dir(name) / ".env" if name else None


def save(name, value):
    """Store a new value of one credential and use it from now on in this process.

    Hosted: calls the scope's ``saver(name, value)``, which must persist it in the tenant's vault entry (or raise), then
    updates the scope so the rest of the request reads it. A scope without a saver cannot store anything. Locally:
    rewrites that one line of the env file the process loaded (the customer's own file when it has one, else the shared
    file), keeping it private (0600), and sets ``os.environ``. The value is never printed.
    """
    current = _current.get()
    if current is not None:
        if current.saver is None:
            raise RuntimeError("This office cannot store credentials")
        current.saver(name, value)
        current.values[name] = value
        return
    _rewrite_env_line(_loaded_env_file(), name, value)
    os.environ[name] = value


def _loaded_env_file():
    from jurisync.paths import USER_ENV_FILE

    own = customer_env_path()
    if own and own.exists():
        return own
    shared = os.environ.get("JURISYNC_ENV_FILE") or (USER_ENV_FILE if USER_ENV_FILE.exists() else None)
    if not shared:
        raise RuntimeError("No env file to store the credential in; set JURISYNC_ENV_FILE")
    return Path(shared).expanduser()


def _rewrite_env_line(path, name, value):
    if "\r" in value or "\n" in value:
        # A line break would end the assignment and start another one in the env file.
        raise ValueError(f"{name} must be a single line")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = []
    quote = "'" if '"' in value else '"'
    quoted = f"{quote}{value}{quote}" if any(character in value for character in " #'\"") else value
    replaced = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.removeprefix("export ").split("=", 1)[0].strip() == name and "=" in stripped:
            lines[index] = f"{'export ' if stripped.startswith('export ') else ''}{name}={quoted}"
            replaced = True
    if not replaced:
        lines.append(f"{name}={quoted}")
    path.parent.mkdir(parents=True, exist_ok=True)
    # A unique private file next to the target, so two writers never share it and the swap stays on one filesystem.
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write("\n".join(lines) + "\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
