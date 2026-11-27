"""Log lines an agent can read: one JSON object per line on stderr, with the call they belong to.

``{"at", "level", "logger", "message", "call", "tool", "tenant", "portal", ...}``; a call's last line also carries
``event: "call"``, ``ok``, ``error_code`` and ``duration_ms``. Servers log this way; CLIs keep plain text.
``JURISYNC_LOG_FORMAT=text`` (or ``json``) overrides either. Messages follow docs/padroes.md, section 7: no arguments,
results, credentials, CPF or process numbers, and URLs only by their shape.
"""

import datetime
import json
import logging
import os

from jurisync.core import calls

# Fields a log call may pass with ``extra=``; anything else on the record stays out of the line.
EXTRA = ("event", "ok", "error_code", "duration_ms")


class JsonFormatter(logging.Formatter):
    def format(self, record):
        entry = {
            "at": datetime.datetime.fromtimestamp(record.created, datetime.UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            **calls.fields(),
            **{key: getattr(record, key) for key in EXTRA if hasattr(record, key)},
        }
        if record.exc_info:
            entry["exception"] = record.exc_info[0].__name__ if record.exc_info[0] else ""
            entry["trace"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


def formatter(structured):
    """JSON lines for a server (``structured``), the bare message for a CLI; ``JURISYNC_LOG_FORMAT`` decides first."""
    chosen = os.environ.get("JURISYNC_LOG_FORMAT", "").strip().lower()
    if chosen in {"json", "text"}:
        structured = chosen == "json"
    return JsonFormatter() if structured else logging.Formatter("%(message)s")
