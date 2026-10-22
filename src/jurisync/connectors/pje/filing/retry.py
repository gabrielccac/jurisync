"""Retry for PJe requests that failed in a way worth repeating, and the errors that must stop a run instead."""

import time

from curl_cffi.requests.exceptions import RequestException

# Waits before each repeat: three attempts in all, 2 s and 5 s apart.
DELAYS = (2, 5)

TRANSIENT_MARKERS = (
    "timed out",
    "timeout",
    "errorunexpected.seam",
    "returned http 5",
    "connection",
    "no current jsf viewstate",
)


class DocumentsNeedReview(RuntimeError):
    """The draft holds documents a rerun cannot fix; the message names each one and what PJe has."""

    def __init__(self, problems):
        self.problems = problems
        lines = [
            f"{p['name']}: PJe has {p['found']!r}, the payload asks for {p['expected']!r}"
            if p["kind"] == "wrongType"
            else f"{p['name']}: {p['found']} copies on the draft"
            for p in problems
        ]
        super().__init__("These documents need review in PJe: " + "; ".join(lines))


def is_transient(error):
    """Whether repeating the same request can succeed: timeouts, dropped connections, PJe 5xx and error pages."""
    if isinstance(error, DocumentsNeedReview):
        return False
    if isinstance(error, (TimeoutError, ConnectionError, RequestException)):
        return True
    text = str(error).lower()
    return isinstance(error, RuntimeError) and any(marker in text for marker in TRANSIENT_MARKERS)


def retrying(call, *, delays=DELAYS, sleep=time.sleep, on_retry=None):
    """Run `call`; on a transient error wait and run it again, up to len(delays) more times, then raise."""
    for attempt in range(len(delays) + 1):
        try:
            return call()
        except Exception as error:
            if attempt == len(delays) or not is_transient(error):
                raise
            if on_retry:
                on_retry(attempt + 1, error)
            sleep(delays[attempt])
