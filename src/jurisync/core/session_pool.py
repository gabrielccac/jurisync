"""Authenticated portal sessions kept in memory by a long-running MCP server.

Each key (portal) holds at most one session per tenant: a hosted server keeps each office's
login apart (``credentials.tenant()``), a local install has the single tenant "". Calls for the
same tenant and key are serialized because JSF/PHP view state is not safe to share concurrently.
A session idle longer than ``revalidate_after`` seconds is checked with
``refresh`` before reuse; a failed check or a failed call triggers a new login.
"""

import threading
import time
from contextlib import contextmanager

from jurisync.core import credentials


class SessionExpired(RuntimeError):
    """Raised by a connector when the portal sent the session back to its login page.

    ``SessionPool.run`` answers it with one fresh login and a repeat of the call.
    """


class SessionPool:
    def __init__(self, login, refresh, close, *, revalidate_after=120):
        self._login = login
        self._refresh = refresh
        self._close = close
        self._revalidate_after = revalidate_after
        self._entries = {}
        self._locks = {}
        self._guard = threading.Lock()

    def _lock(self, key):
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())

    def _discard(self, key):
        entry = self._entries.pop(key, None)
        if entry is not None:
            try:
                self._close(entry[0])
            except Exception:
                pass  # Closing a stale session must not block a fresh login.

    @contextmanager
    def session(self, portal):
        key = (credentials.tenant(), portal)
        with self._lock(key):
            entry = self._entries.get(key)
            if entry is not None and time.monotonic() - entry[1] > self._revalidate_after:
                try:
                    value = self._refresh(portal, entry[0])
                except Exception:
                    value = None
                if value is None:
                    self._discard(key)
                else:
                    self._entries[key] = entry = (value, time.monotonic())
            if key not in self._entries:
                self._entries[key] = (self._login(portal), time.monotonic())
            value = self._entries[key][0]
            try:
                yield value
            except BaseException:
                # The failure may be an expired session: verify before next reuse.
                self._entries[key] = (value, float("-inf"))
                raise
            self._entries[key] = (value, time.monotonic())

    def run(self, portal, read):
        """``read(session)`` on the pooled session; if it raises ``SessionExpired``, log in again once and repeat.

        Reads must be safe to repeat (every connector read is). A second expiry is reported to the caller.
        """
        for attempt in (1, 2):
            try:
                with self.session(portal) as session:
                    return read(session)
            except SessionExpired:
                self.forget(portal)
                if attempt == 2:
                    raise
        raise AssertionError("unreachable")  # pragma: no cover

    def forget(self, portal):
        """Drop the current tenant's login for this portal, e.g. after its credentials changed."""
        key = (credentials.tenant(), portal)
        with self._lock(key):
            self._discard(key)

    def close(self):
        with self._guard:
            keys = list(self._entries)
        for key in keys:
            with self._lock(key):
                self._discard(key)
