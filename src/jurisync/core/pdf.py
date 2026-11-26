"""PDF text and metadata via pdf-inspector, plus safe local download paths."""

import os
import time
from pathlib import Path
from typing import Literal

from jurisync.core import ocr as ocr_settings


def attachment_mime_type(content, filename):
    if content[:5] == b"%PDF-":
        return "application/pdf"
    if content[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if content[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    return {".txt": "text/plain", ".csv": "text/csv", ".json": "application/json"}.get(
        Path(filename).suffix.lower(), "application/octet-stream"
    )


def available_path(directory, filename):
    """Return a new path in directory, suffixing -1, -2... instead of overwriting."""
    if (
        not isinstance(filename, str)
        or filename in ("", ".", "..")
        or any(char in filename for char in "/\\\0")
        or os.path.isabs(filename)
    ):
        raise ValueError("Download filename must be a single filename without a path")
    directory = Path(directory)
    stem, extension = os.path.splitext(filename)
    candidate, index = directory / filename, 1
    while candidate.exists():
        candidate, index = directory / f"{stem}-{index}{extension}", index + 1
    return candidate


def _pdf_type(value):
    """Match the Node binding's spelling (text_based -> TextBased) for existing callers."""
    return "".join(part.capitalize() for part in value.split("_")) if isinstance(value, str) else value


def _limit(max_text_chars):
    return max_text_chars if isinstance(max_text_chars, int) and max_text_chars > 0 else 500_000


class OcrSkipped(Exception):
    """OCR was not run on this PDF; the message says why, and the caller reads its text layer instead."""


def ocr_max_pages():
    return ocr_settings._setting("JURISYNC_OCR_MAX_PAGES", ocr_settings.MAX_PAGES)


def ocr_deadline(clock=time.monotonic):
    """When a call that OCRs several files stops starting new OCR (``JURISYNC_OCR_SECONDS``, default 90 s)."""
    return clock() + ocr_settings._setting("JURISYNC_OCR_SECONDS", ocr_settings.SECONDS)


def extract_pdf_with_ocr(
    content,
    *,
    max_text_chars=500_000,
    mode: Literal["off", "auto", "force"] = "auto",
    deadline=None,
    clock=time.monotonic,
):
    """pdf-inspector's selective OCR, bounded as ``core.ocr``: never on a PDF of more than ``ocr_max_pages()`` pages,
    never once ``deadline`` passed, never with OCR off (``JURISYNC_OCR=off``), and never downloading a model.

    Raises ``OcrSkipped`` when a limit applies. The fast detection runs first: its page count decides, and its
    ``pdfType`` (which the OCR result lacks) is the one reported.
    """
    import pdf_inspector

    limit = _limit(max_text_chars)
    if not ocr_settings.enabled():
        raise OcrSkipped("OCR is off on this server (JURISYNC_OCR)")
    if deadline is not None and clock() > deadline:
        raise OcrSkipped("this call's OCR time was spent on earlier files")
    detected = pdf_inspector.detect_pdf_bytes(content)
    if detected.page_count > ocr_max_pages():
        raise OcrSkipped(f"{detected.page_count} pages; OCR reads PDFs of at most {ocr_max_pages()} pages")
    ocr_settings._locate_runtime()
    result = pdf_inspector.process_pdf_with_ocr_bytes(content, mode=mode, offline=True)
    pdf_type = _pdf_type(detected.pdf_type)
    markdown = result.markdown or ""
    return {
        "markdown": markdown[:limit],
        "textCharacters": len(markdown),
        "textTruncated": len(markdown) > limit,
        "pdfType": pdf_type,
        "pageCount": result.page_count,
        "pagesRecommendedForOcr": list(result.pages_recommended_for_ocr),
        "pagesRoutedToOcr": list(result.pages_routed_to_ocr),
        "pagesRecommendingHosted": list(result.pages_recommending_hosted),
        "ocrReasonsByPage": [{"page": item.page, "reasons": list(item.reasons)} for item in result.ocr_reasons_by_page],
        "pagesWithTables": list(result.pages_with_tables),
        "isComplex": result.is_complex,
        "ocrTimeMs": result.ocr_time_ms,
    }


def extract_pdf_metadata(content, max_text_chars=500_000):
    import pdf_inspector

    limit = _limit(max_text_chars)
    parsed = pdf_inspector.process_pdf_bytes(content)
    markdown = parsed.markdown or ""
    return {
        "markdown": markdown[:limit],
        "textCharacters": len(markdown),
        "textTruncated": len(markdown) > limit,
        "pdfType": _pdf_type(parsed.pdf_type),
        "pageCount": parsed.page_count,
        "pagesNeedingOcr": list(parsed.pages_needing_ocr),
        "hasEncodingIssues": parsed.has_encoding_issues,
    }


def analyze_document(content, filename, *, max_text_chars=500_000, ocr=False, ocr_deadline=None):
    """MIME type, and for a PDF its text and metadata; with ``ocr`` its scanned pages are read by OCR within the
    limits of ``extract_pdf_with_ocr`` (``ocrSkipped`` says which one applied)."""
    mime_type = attachment_mime_type(content, filename)
    if mime_type != "application/pdf":
        return {"mimeType": mime_type}
    skipped = {}
    if ocr:
        try:
            return {
                "mimeType": mime_type,
                **extract_pdf_with_ocr(content, max_text_chars=max_text_chars, deadline=ocr_deadline),
            }
        except OcrSkipped as reason:
            skipped = {"ocrSkipped": str(reason)}
        except Exception:
            pass  # OCR runtime (PDFium/ONNX) or a model unavailable; fall back to native extraction.
    try:
        return {"mimeType": mime_type, **extract_pdf_metadata(content, max_text_chars), **skipped}
    except Exception as error:
        return {"mimeType": mime_type, "pdfExtractionError": str(error)}
