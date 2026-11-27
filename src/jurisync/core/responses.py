"""One shape for tool answers: a size budget per call, texts that say how to read the rest, and compact JSON.

An answer stays within ``BUDGET`` characters (about 15k tokens, well under the 25k-token cut of Claude Code), of which
document texts take at most ``TEXT_BUDGET``. A list carries ``returned``, ``total`` (when the portal says) and
``next`` (the arguments that read more, or None); a cut text carries ``truncated`` with the offset to continue from.
Dates go out as ISO 8601 and an empty value as None (``iso``).
"""

import json
import re
from datetime import datetime

BUDGET = 60_000
TEXT_BUDGET = 50_000
ENVELOPE = (
    "returned counts the items given, total is the portal's count (null when it shows none or a local filter makes it "
    "unknown) and next the arguments to replace in this same call, keeping every other argument as it was, to read "
    "more (null when this tool can read no more); the new call reads again from the first page."
)
BR_DATE = re.compile(r"(\d{2})/(\d{2})/(\d{4})(?: (\d{2}):(\d{2})(?::(\d{2}))?)?")


def compact(value):
    """The answer's text copy: the same JSON as the structured content, without indentation."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def listing(items, *, key="items", total=None, more=None, **extra):
    """The common list envelope: ``{key: items, returned, total, next, ...extra}``.

    ``total`` is the count the portal reports, or None when it does not say. ``more`` is the arguments to replace in
    the same call, every other argument kept, to read the rest (``{"maxPages": 10}``), or None when nothing is left.
    """
    return {key: items, "returned": len(items), "total": total, "next": more, **extra}


def iso(value):
    """A portal date as ISO 8601: ``DD/MM/AAAA`` -> ``AAAA-MM-DD``, with time -> ``AAAA-MM-DDTHH:MM:SS``; empty -> None.

    Text already in ISO, in a shape this does not know, or naming a day or time that does not exist (31/02, 24:61) is
    returned as is (trimmed), so nothing the portal showed is lost.
    """
    text = " ".join(str(value or "").split())
    if not text:
        return None
    match = BR_DATE.fullmatch(text)
    if not match:
        return text
    day, month, year, hour, minute, second = match.groups()
    try:
        moment = datetime(int(year), int(month), int(day), int(hour or 0), int(minute or 0), int(second or 0))
    except ValueError:
        return text
    return moment.date().isoformat() if hour is None else moment.isoformat()


def _shares(wanted, budget):
    """Split ``budget`` among texts wanting ``wanted`` characters: short ones keep all, long ones share the rest."""
    shares, left = [0] * len(wanted), budget
    order = sorted(range(len(wanted)), key=wanted.__getitem__)
    for position, index in enumerate(order):
        shares[index] = min(wanted[index], left // (len(order) - position))
        left -= shares[index]
    return shares


def fitted(documents):
    """fit_texts for a tool without an offset. A document read by itself keeps the connector's own cap, so the note's
    advice (fewer documents per call) reaches the whole text; several share TEXT_BUDGET."""
    if len(documents) == 1:
        return {}
    if fit_texts(documents, resumable=False):
        return {"note": "Texts were cut to fit the answer: read fewer documents per call to see more of each one."}
    return {}


def fit_texts(reads, *, offset=0, limit=None, budget=TEXT_BUDGET, resumable=True):
    """Cut the ``text`` of each read (dict) from ``offset`` so all fit ``budget`` together, each at most ``limit``.

    A cut text gets ``truncated: {shown, total, nextOffset}`` (``nextOffset`` only when the tool takes an offset)
    and ``textTruncated`` mirrors it for older clients. Returns whether any text was cut.
    """
    texts = [read.get("text") or "" for read in reads]
    wanted = [len(text[offset:]) if limit is None else min(len(text[offset:]), limit) for text in texts]
    cut_any = False
    for read, text, share in zip(reads, texts, _shares(wanted, budget), strict=True):
        if "text" not in read:
            continue
        read["text"] = text[offset : offset + share]
        cut = offset + share < len(text)
        read["textTruncated"] = cut or bool(read.get("textTruncated"))
        if cut:
            read["truncated"] = {"shown": share, "total": len(text)}
            if resumable:
                read["truncated"]["nextOffset"] = offset + share
        cut_any = cut_any or cut
    return cut_any
