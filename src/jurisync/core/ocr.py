"""Optional local OCR of scanned PDFs, bounded in pages and time.

The engine is the one the ADVBOX downloads use (``core.pdf.analyze_document(..., ocr=True)``): pdf-inspector's
selective OCR, which needs a PDFium library (``PDFIUM_LIB_PATH``, else the one in the ``pypdfium2`` wheel), the ONNX
runtime (``ORT_DYLIB_PATH``, else the one in the ``onnxruntime`` wheel; the ``pdf-ocr`` extra installs both) and its
models already cached (``PDF_INSPECTOR_MODEL_CACHE``, default ``~/.cache/pdf-inspector``). Models are never
downloaded during a read. Without any of them, or with ``JURISYNC_OCR=off``, ``ocr_pdf`` returns None and the
caller keeps its ``ocr_needed`` status.
"""

import importlib.util
import logging
import os
import time
from pathlib import Path

log = logging.getLogger(__name__)

MAX_PAGES = 30
SECONDS = 90
PAGES_PER_CALL = 4


def _setting(name, default):
    try:
        value = int(os.environ.get(name, ""))
    except ValueError:
        return default
    return value if value > 0 else default


def _package_library(package, pattern):
    """The shared library a wheel ships (``pypdfium2_raw/libpdfium.so``), found without importing the package."""
    try:
        spec = importlib.util.find_spec(package)
    except (ImportError, ValueError):
        return None
    for folder in spec.submodule_search_locations or () if spec else ():
        found = sorted(Path(folder).glob(pattern))
        if found:
            return str(found[0])
    return None


def _locate_runtime():
    """Point pdf-inspector at the wheels' libraries when the environment names none."""
    for variable, package, pattern in (
        ("PDFIUM_LIB_PATH", "pypdfium2_raw", "libpdfium.*"),
        ("ORT_DYLIB_PATH", "onnxruntime", "capi/libonnxruntime.so*"),
    ):
        if not os.environ.get(variable) and (path := _package_library(package, pattern)):
            os.environ[variable] = path


def enabled():
    return os.environ.get("JURISYNC_OCR", "").strip().lower() not in {"off", "0", "false", "no"}


def ocr_pdf(content, page_count, *, max_pages=None, seconds=None, clock=time.monotonic):
    """``(text, complete)`` read by OCR from the first pages of a PDF, or None when the engine is unavailable.

    At most ``max_pages`` pages (``JURISYNC_OCR_MAX_PAGES``, default 30) are read, a few per call, and no new call
    starts once ``seconds`` (``JURISYNC_OCR_SECONDS``, default 90) have passed; ``complete`` is False when either
    limit left pages unread.
    """
    if not enabled() or page_count < 1:
        return None
    try:
        import pdf_inspector
    except ImportError:
        return None
    _locate_runtime()
    max_pages = max_pages or _setting("JURISYNC_OCR_MAX_PAGES", MAX_PAGES)
    seconds = seconds or _setting("JURISYNC_OCR_SECONDS", SECONDS)
    wanted = list(range(1, min(page_count, max_pages) + 1))
    deadline = clock() + seconds
    parts, done = [], 0
    for start in range(0, len(wanted), PAGES_PER_CALL):
        if start and clock() > deadline:
            break
        pages = wanted[start : start + PAGES_PER_CALL]
        try:
            result = pdf_inspector.process_pdf_with_ocr_bytes(content, page_numbers=pages, offline=True)
        except Exception as error:  # the PDFium/ONNX runtime or a model is missing, or the PDF is unreadable
            log.info("OCR unavailable: %s", type(error).__name__)
            if not parts:
                return None
            break
        parts.append(result.markdown or "")
        done += len(pages)
    return " ".join(parts), done >= page_count
