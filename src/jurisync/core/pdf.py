"""PDF text and metadata via pdf-inspector, plus safe local download paths."""

import os
from pathlib import Path


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


def extract_pdf_with_ocr(content, *, max_text_chars=500_000, mode="auto"):
    import pdf_inspector

    limit = _limit(max_text_chars)
    result = pdf_inspector.process_pdf_with_ocr_bytes(content, mode=mode)
    try:
        pdf_type = _pdf_type(pdf_inspector.detect_pdf_bytes(content).pdf_type)
    except Exception:
        pdf_type = None
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


def analyze_document(content, filename, *, max_text_chars=500_000, ocr=False):
    mime_type = attachment_mime_type(content, filename)
    if mime_type != "application/pdf":
        return {"mimeType": mime_type}
    if ocr:
        try:
            return {"mimeType": mime_type, **extract_pdf_with_ocr(content, max_text_chars=max_text_chars)}
        except Exception:
            pass  # OCR runtime (PDFium/ONNX) unavailable; fall back to native extraction.
    try:
        return {"mimeType": mime_type, **extract_pdf_metadata(content, max_text_chars)}
    except Exception as error:
        return {"mimeType": mime_type, "pdfExtractionError": str(error)}
