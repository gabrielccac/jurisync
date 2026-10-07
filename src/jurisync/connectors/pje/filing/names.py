"""Upload file names and how PJe stores them."""

import re
import unicodedata


def upload_name(path):
    """ASCII filename for uploads: PJe stores UTF-8 names as Latin-1 mojibake (PETIÇÃO -> PETIÃÃO)."""
    folded = unicodedata.normalize("NFKD", path.name).encode("ascii", "ignore").decode()
    return re.sub(r'[\r\n"\\]', "_", folded).strip() or "document.pdf"


def fold(name):
    return unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().casefold()


def same_name(stored, path):
    """Whether PJe's stored file name is this file, tolerating its mojibake and accent loss."""
    candidates = {stored}
    try:
        candidates.add(stored.encode("latin-1").decode("utf-8"))
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    return fold(path.name) in {fold(candidate) for candidate in candidates}
