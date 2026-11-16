"""Slow down password guessing on the sign-in pages: a few failures per key per window, then a pause.

The sign-in page keeps two of these, one keyed by e-mail and one by client address. Memory is bounded: a key with no
recent failure is not kept, and past ``max_keys`` the keys that failed longest ago are dropped.
"""

import ipaddress
import threading
import time

MAX_FAILURES = 8
IP_MAX_FAILURES = 30  # an office shares one address, so this is looser than the per-e-mail limit
WINDOW = 15 * 60
MAX_KEYS = 10_000
KEY_LENGTH = 320


class Throttle:
    def __init__(self, max_failures=MAX_FAILURES, window=WINDOW, clock=time.monotonic, max_keys=MAX_KEYS):
        self._max = max_failures
        self._window = window
        self._clock = clock
        self._max_keys = max_keys
        self._failures = {}  # key -> failure moments; dict order is the order of each key's latest failure
        self._lock = threading.Lock()

    def _recent(self, key):
        now = self._clock()
        recent = [moment for moment in self._failures.get(key, ()) if now - moment < self._window]
        if recent:
            self._failures[key] = recent
        else:
            self._failures.pop(key, None)
        return recent

    def blocked(self, key):
        key = key[:KEY_LENGTH]
        with self._lock:
            return len(self._recent(key)) >= self._max

    def fail(self, key):
        key = key[:KEY_LENGTH]
        with self._lock:
            recent = self._recent(key)
            self._failures.pop(key, None)
            # A blocked key keeps its newest failures only, so one key never grows past the limit.
            self._failures[key] = [*recent, self._clock()][-self._max :]
            if len(self._failures) > self._max_keys:
                for stale in list(self._failures):
                    self._recent(stale)
                while len(self._failures) > self._max_keys:
                    del self._failures[next(iter(self._failures))]

    def clear(self, key):
        with self._lock:
            self._failures.pop(key[:KEY_LENGTH], None)

    def __len__(self):
        with self._lock:
            return len(self._failures)


def _address(text):
    try:
        return ipaddress.ip_address(text.strip().strip("[]"))
    except ValueError:
        return None


def client_address(peer, forwarded_for=""):
    """The address to throttle a request by.

    ``X-Forwarded-For`` counts only when the direct peer is loopback (the reverse proxy runs on this host); anyone else
    could write whatever they like in it. An IPv6 client is its /64, since one subscriber holds the whole prefix.
    """
    address = _address(peer or "")
    if address is not None and address.is_loopback:
        first = _address(forwarded_for.split(",")[0])
        if first is not None:
            address = first
    if address is None:
        return (peer or "unknown")[:64]
    if address.version == 6:
        if address.ipv4_mapped is None:
            return str(ipaddress.ip_network((address, 64), strict=False))
        address = address.ipv4_mapped
    return str(address)
