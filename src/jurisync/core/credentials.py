"""Where a connector reads its account: the process environment locally, one tenant's vault entry when hosted.

A local install keeps one account per process, so ``env`` and ``read_file`` read ``os.environ`` as before. The hosted
server serves several offices from one process: for each request it opens a ``scope`` with that tenant's values, and
inside it every read comes from those values only. A name the tenant does not have is empty, never the server's own
environment, so one office can never log in with another's (or the operator's) credentials.

Files (the A1 certificate) travel as bytes in the scope and are read with ``read_file``; nothing is written to disk.
"""

import contextvars
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Scope:
    tenant: str
    values: dict = field(default_factory=dict)
    files: dict = field(default_factory=dict)


_current = contextvars.ContextVar("jurisync_credentials", default=None)


@contextmanager
def scope(tenant, values=None, files=None):
    """Read every credential from ``values`` (name -> text) and ``files`` (name -> bytes) inside the block."""
    if not tenant:
        raise ValueError("A credentials scope needs a tenant id")
    token = _current.set(Scope(tenant, dict(values or {}), dict(files or {})))
    try:
        yield
    finally:
        _current.reset(token)


def tenant():
    """The tenant of the current scope, or "" for a local single-account install."""
    current = _current.get()
    return current.tenant if current else ""


def env(name, default=""):
    """A credential or per-account setting: the scope's value when hosted, ``os.environ`` locally."""
    current = _current.get()
    if current is None:
        return os.environ.get(name, default)
    return current.values.get(name, default)


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
