"""One shape for tool answers: a size budget per call, texts that say how to read the rest, and compact JSON.

An answer stays within ``BUDGET`` characters (about 15k tokens, well under the 25k-token cut of Claude Code), of which
document texts take at most ``TEXT_BUDGET``. A list carries ``returned``, ``total`` (when the portal says) and
``next`` (how to get more, or None); a cut text carries ``truncated`` with the offset to continue from.
"""

import json

BUDGET = 60_000
TEXT_BUDGET = 50_000


def compact(value):
    """The answer's text copy: the same JSON as the structured content, without indentation."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def listing(items, *, key="items", total=None, more=None, **extra):
    """The common list envelope: ``{key: items, returned, total, next, ...extra}``; ``more`` says how to get the rest."""
    return {key: items, "returned": len(items), "total": total, "next": more, **extra}


def _shares(wanted, budget):
    """Split ``budget`` among texts wanting ``wanted`` characters: short ones keep all, long ones share the rest."""
    shares, left = [0] * len(wanted), budget
    order = sorted(range(len(wanted)), key=wanted.__getitem__)
    for position, index in enumerate(order):
        shares[index] = min(wanted[index], left // (len(order) - position))
        left -= shares[index]
    return shares


def fitted(documents, alone=BUDGET):
    """fit_texts for a tool without an offset, whose connector already caps each text at ALONE: the budget never cuts
    a document read by itself, so the note's advice (fewer documents per call) reaches the whole text."""
    if fit_texts(documents, resumable=False, budget=max(TEXT_BUDGET, alone)):
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
