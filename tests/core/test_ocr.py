"""Local OCR of scanned PDFs: used when the engine is installed, bounded, and never required."""

import io
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from jurisync.core import ocr
from jurisync.core.document_text import extract_document_text

LINES = ("PODER JUDICIARIO", "PETICAO INICIAL", "Processo 0800001-23.2025.8.14.0301")


def scanned_pdf(pages=1):
    """A PDF of page images only (no text layer), as a court scanner produces."""
    image = pytest.importorskip("PIL.Image")
    draw = pytest.importorskip("PIL.ImageDraw")
    font = pytest.importorskip("PIL.ImageFont")
    try:
        face = font.truetype("DejaVuSans.ttf", 40)
    except OSError:
        face = font.load_default(size=40)
    sheets = []
    for _ in range(pages):
        sheet = image.new("L", (1240, 1754), 255)
        pen = draw.Draw(sheet)
        for row, line in enumerate(LINES):
            pen.text((100, 150 + row * 80), line, fill=0, font=face)
        sheets.append(sheet)
    buffer = io.BytesIO()
    sheets[0].save(buffer, "PDF", resolution=150, save_all=True, append_images=sheets[1:])
    return buffer.getvalue()


def fake_engine(pages_seen, text="TEXTO"):
    def process(content, *, page_numbers, offline):
        assert offline  # models are never downloaded during a read
        pages_seen.append(list(page_numbers))
        return SimpleNamespace(markdown=" ".join(f"{text} {page}" for page in page_numbers))

    return SimpleNamespace(process_pdf_with_ocr_bytes=process)


def test_a_scanned_pdf_is_read_by_the_installed_engine():
    content = scanned_pdf()
    if ocr.ocr_pdf(content, 1) is None:
        pytest.skip("the OCR runtime (pdf-ocr extra and cached models) is not installed here")
    status, text = extract_document_text(content, "application/pdf")
    assert status == "extracted_ocr"
    assert "PETICAO INICIAL" in text and "0800001-23.2025.8.14.0301" in text


def test_without_the_engine_a_scanned_pdf_stays_ocr_needed(monkeypatch):
    content = scanned_pdf()

    def missing(*args, **kwargs):
        raise ValueError("failed to load PDFium")

    monkeypatch.setitem(sys.modules, "pdf_inspector", SimpleNamespace(process_pdf_with_ocr_bytes=missing))
    assert extract_document_text(content, "application/pdf") == ("ocr_needed", "")
    monkeypatch.setitem(sys.modules, "pdf_inspector", None)  # not importable at all
    assert extract_document_text(content, "application/pdf") == ("ocr_needed", "")


def test_ocr_can_be_switched_off(monkeypatch):
    seen = []
    monkeypatch.setitem(sys.modules, "pdf_inspector", fake_engine(seen))
    monkeypatch.setenv("JURISYNC_OCR", "off")
    assert extract_document_text(scanned_pdf(), "application/pdf") == ("ocr_needed", "")
    assert seen == []


def test_pages_and_time_are_bounded_and_a_partial_read_says_so(monkeypatch):
    seen = []
    monkeypatch.setitem(sys.modules, "pdf_inspector", fake_engine(seen))
    monkeypatch.delenv("JURISYNC_OCR", raising=False)
    text, complete = ocr.ocr_pdf(b"%PDF-", 100, max_pages=10, seconds=60)
    assert seen == [[1, 2, 3, 4], [5, 6, 7, 8], [9, 10]] and not complete
    assert text.startswith("TEXTO 1") and "TEXTO 11" not in text

    seen.clear()
    clock = iter([0.0, 61.0])
    assert ocr.ocr_pdf(b"%PDF-", 8, seconds=60, clock=lambda: next(clock)) == ("TEXTO 1 TEXTO 2 TEXTO 3 TEXTO 4", False)
    assert seen == [[1, 2, 3, 4]]

    seen.clear()
    assert ocr.ocr_pdf(b"%PDF-", 3)[1] is True


def test_a_partial_ocr_has_its_own_status(monkeypatch):
    monkeypatch.setitem(sys.modules, "pdf_inspector", fake_engine([]))
    monkeypatch.setenv("JURISYNC_OCR_MAX_PAGES", "1")
    with patch.object(ocr, "PAGES_PER_CALL", 1):
        status, text = extract_document_text(scanned_pdf(pages=2), "application/pdf")
    assert (status, text) == ("extracted_ocr_partial", "TEXTO 1")
