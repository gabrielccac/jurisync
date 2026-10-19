"""The attachment types a portal's Anexar tab offers, as last read from a real draft.

The fill reads them right after the draft exists and before any attachment row is registered, and fails on a payload
type the portal does not list. The list is also kept per portal (<filing root>/attachment-types/<portal>.json), so
pje_filing_build can check a new payload's types before any draft is created.
"""

import difflib
import json
import os
import tempfile
from datetime import UTC, datetime

from jurisync.paths import filing_folder


def _file(portal):
    return filing_folder("attachment-types") / f"{portal}.json"


def remember(portal, types):
    """Keep the types just read from `portal` (owner-only, replaced atomically)."""
    record = {**types, "portal": portal, "readAt": datetime.now(UTC).isoformat(timespec="seconds")}
    target = _file(portal)
    descriptor, temp = tempfile.mkstemp(dir=target.parent, prefix=".types-")
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump(record, output, ensure_ascii=False, indent=2)
    os.chmod(temp, 0o600)
    os.replace(temp, target)
    return record


def known(portal):
    """The types last read from `portal`, or None when no draft of that portal was read yet."""
    target = _file(portal)
    return json.loads(target.read_text(encoding="utf-8")) if target.exists() else None


def unknown_types(types, main, supporting):
    """Why the payload's types do not fit the portal's lists: one message per type it does not offer.

    `main` is the main petition's type; `supporting` maps each supporting file name to its type. A list that could not
    be read (None) is not checked.
    """
    problems = []
    checks = [("main petition", main, types.get("main"))]
    checks += [(name, kind, types.get("supporting")) for name, kind in supporting.items()]
    for name, kind, offered in checks:
        if offered is None or kind in offered:
            continue
        close = difflib.get_close_matches(kind, offered, n=3, cutoff=0.5)
        hint = f" (closest: {', '.join(close)})" if close else ""
        problems.append(f"{name}: '{kind}' is not an attachment type PJe offers{hint}")
    return problems
