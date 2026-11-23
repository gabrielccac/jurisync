"""Optional local OCR of scanned PDFs, bounded in pages and time.

The engine is the one the ADVBOX downloads use (``core.pdf.analyze_document(..., ocr=True)``): pdf-inspector's
selective OCR, which needs a PDFium library (``PDFIUM_LIB_PATH``, else the one in the ``pypdfium2`` wheel), the ONNX
runtime (``ORT_DYLIB_PATH``, else the one in the ``onnxruntime`` wheel; the ``pdf-ocr`` extra installs both) and its
models already cached (``PDF_INSPECTOR_MODEL_CACHE``, default ``~/.cache/pdf-inspector/models``). Models are never
downloaded during a read: ``python -m jurisync.core.ocr`` fetches them ahead (deploy/install.sh does, into
``/opt/jurisync/models``). Without any of them, or with ``JURISYNC_OCR=off``, ``ocr_pdf`` returns None and the
caller keeps its ``ocr_needed`` status.
"""

import importlib.util
import io
import logging
import os
import sys
import threading
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


_runtime_lock = threading.Lock()
_runtime_located = False


def _locate_runtime():
    """Point pdf-inspector at the wheels' libraries when the environment names none.

    Once per process, under a lock: reads run in worker threads, and ``os.environ`` is the process's.
    """
    global _runtime_located
    with _runtime_lock:
        if _runtime_located:
            return
        for variable, package, pattern in (
            ("PDFIUM_LIB_PATH", "pypdfium2_raw", "libpdfium.*"),
            ("ORT_DYLIB_PATH", "onnxruntime", "capi/libonnxruntime.so*"),
        ):
            if not os.environ.get(variable) and (path := _package_library(package, pattern)):
                os.environ[variable] = path
        _runtime_located = True


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


def _sample_page():
    """A one-page PDF of a page image (no text layer), so the OCR models are loaded to read it."""
    from PIL import Image, ImageDraw, ImageFont

    try:
        face = ImageFont.truetype("DejaVuSans.ttf", 40)
    except OSError:
        face = ImageFont.load_default(size=40)
    sheet = Image.new("L", (1240, 1754), 255)
    ImageDraw.Draw(sheet).text((100, 150), "PODER JUDICIARIO 0800001-23.2025.8.14.0301", fill=0, font=face)
    buffer = io.BytesIO()
    sheet.save(buffer, "PDF", resolution=150)
    return buffer.getvalue()


def fetch_models():
    """Download the OCR models into pdf-inspector's cache (``PDF_INSPECTOR_MODEL_CACHE``) and return the text an
    offline read of a sample page then finds. Install time only: ``ocr_pdf`` never downloads."""
    import pdf_inspector

    _locate_runtime()
    page = _sample_page()
    pdf_inspector.process_pdf_with_ocr_bytes(page, mode="force", page_numbers=[1], offline=False)
    return pdf_inspector.process_pdf_with_ocr_bytes(page, mode="force", page_numbers=[1], offline=True).markdown or ""


def main():
    """``python -m jurisync.core.ocr``: fetch the models; exit 1 when the offline read finds no text."""
    cache = os.environ.get("PDF_INSPECTOR_MODEL_CACHE") or "~/.cache/pdf-inspector/models"
    text = fetch_models()
    if "JUDICIARIO" not in text.upper():
        print(f"OCR models in {cache}, but the offline check read {text!r}", file=sys.stderr)
        return 1
    print(f"OCR models ready in {cache}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
