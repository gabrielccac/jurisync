"""The tool call in progress: its id (the ref in errors and in log lines) and what the logs may say about it.

Bound once per call by ``core.mcp.JurisyncServer`` or, before it, by the hosted ``Office``; a ``ContextVar`` so threads
started with ``anyio.to_thread`` (sync tools) see it too. The fields are only the tool name, the office id and a portal
key: never arguments or results (LGPD).
"""

import secrets
from contextlib import contextmanager
from contextvars import ContextVar

_call = ContextVar("jurisync_call", default="")
_fields: ContextVar[dict | None] = ContextVar("jurisync_call_fields", default=None)

FIELDS = ("tool", "tenant", "portal")


def current():
    return _call.get()


def new_id():
    return "c-" + secrets.token_hex(4)


def fields():
    """``{"call": id, "tool": ..., "tenant": ..., "portal": ...}`` of the call in progress (only what is known)."""
    if not _call.get():
        return {}
    return {"call": _call.get(), **(_fields.get() or {})}


def annotate(**known):
    """Add fields (``FIELDS``) to the call in progress; empty values and calls not bound are ignored."""
    current_fields = _fields.get()
    if current_fields is not None:
        current_fields.update({key: value for key, value in known.items() if key in FIELDS and value})


@contextmanager
def bound(**known):
    """A new call id for the block, unless an outer one is already bound (the hosted Office binds before the server).

    ``known`` fields join the call's (``annotate``)."""
    if _call.get():
        annotate(**known)
        yield _call.get()
        return
    token, fields_token = _call.set(new_id()), _fields.set({})
    annotate(**known)
    try:
        yield _call.get()
    finally:
        _call.reset(token)
        _fields.reset(fields_token)
