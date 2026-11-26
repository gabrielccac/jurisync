"""The id of the tool call in progress, for the ref in errors and (later) in log lines.

Bound once per call by ``core.mcp.JurisyncServer`` or, before it, by the hosted ``Office``; a ``ContextVar`` so threads
started with ``anyio.to_thread`` (sync tools) see it too.
"""

import secrets
from contextlib import contextmanager
from contextvars import ContextVar

_call = ContextVar("jurisync_call", default="")


def current():
    return _call.get()


def new_id():
    return "c-" + secrets.token_hex(4)


@contextmanager
def bound():
    """A new call id for the block, unless an outer one is already bound (the hosted Office binds before the server)."""
    if _call.get():
        yield _call.get()
        return
    token = _call.set(new_id())
    try:
        yield _call.get()
    finally:
        _call.reset(token)
