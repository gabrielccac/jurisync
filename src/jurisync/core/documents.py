"""Limits and checks every connector applies when it reads documents of a process.

A tool reads at most ``MAX_DOCUMENTS`` documents per call, each capped at ``MAX_DOCUMENT_BYTES``, and only ids that
came from a fresh inventory of the process (never a URL the caller supplied).
"""

import re

MAX_DOCUMENTS = 5
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
NUMERIC_ID = re.compile(r"[0-9]+")


def validate_document_ids(document_ids, *, pattern=NUMERIC_ID):
    """1-5 unique ids matching ``pattern`` (numeric by default); raises ``ValueError`` otherwise."""
    if (
        not isinstance(document_ids, list)
        or not 1 <= len(document_ids) <= MAX_DOCUMENTS
        or any(not isinstance(value, str) or not pattern.fullmatch(value) for value in document_ids)
        or len(set(document_ids)) != len(document_ids)
    ):
        raise ValueError(f"Select 1-{MAX_DOCUMENTS} unique numeric document ID strings")


def read_limited(response, limit=MAX_DOCUMENT_BYTES, chunk_size=64 * 1024):
    """The streamed body of ``response``, or ``None`` as soon as it passes ``limit`` bytes."""
    content, size = bytearray(), 0
    for chunk in response.iter_content(chunk_size=chunk_size):
        if not chunk:
            continue
        size += len(chunk)
        if size > limit:
            return None
        content.extend(chunk)
    return bytes(content)
