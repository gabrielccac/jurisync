"""Slow down password guessing on the sign-in pages: a few failures per e-mail per window, then a pause."""

import threading
import time

MAX_FAILURES = 8
WINDOW = 15 * 60


class Throttle:
    def __init__(self, max_failures=MAX_FAILURES, window=WINDOW, clock=time.monotonic):
        self._max = max_failures
        self._window = window
        self._clock = clock
        self._failures = {}
        self._lock = threading.Lock()

    def _recent(self, key):
        now = self._clock()
        recent = [moment for moment in self._failures.get(key, []) if now - moment < self._window]
        self._failures[key] = recent
        return recent

    def blocked(self, key):
        with self._lock:
            return len(self._recent(key)) >= self._max

    def fail(self, key):
        with self._lock:
            self._recent(key).append(self._clock())

    def clear(self, key):
        with self._lock:
            self._failures.pop(key, None)
