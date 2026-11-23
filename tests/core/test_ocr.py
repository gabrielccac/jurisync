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


def test_the_runtime_is_located_once_under_a_lock(monkeypatch):
    """Reads run in worker threads: os.environ is written once per process, not on every call."""
    import os
    import threading

    calls = []

    def library(package, pattern):
        calls.append(package)
        return f"/wheel/{package}.so"

    monkeypatch.setattr(ocr, "_package_library", library)
    monkeypatch.setattr(ocr, "_runtime_located", False)
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("PDFIUM_LIB_PATH", None)
        os.environ.pop("ORT_DYLIB_PATH", None)
        threads = [threading.Thread(target=ocr._locate_runtime) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert sorted(calls) == ["onnxruntime", "pypdfium2_raw"]
        assert os.environ["PDFIUM_LIB_PATH"] == "/wheel/pypdfium2_raw.so"
        assert os.environ["ORT_DYLIB_PATH"] == "/wheel/onnxruntime.so"
        ocr._locate_runtime()
        assert len(calls) == 2


def test_the_switch_accepts_the_usual_off_values(monkeypatch):
    for value in ("off", "OFF", "0", "false", "no"):
        monkeypatch.setenv("JURISYNC_OCR", value)
        assert not ocr.enabled()
        assert ocr.ocr_pdf(b"%PDF-", 1) is None
    for value in ("on", "1", ""):
        monkeypatch.setenv("JURISYNC_OCR", value)
        assert ocr.enabled()
    monkeypatch.delenv("JURISYNC_OCR")
    assert ocr.enabled()  # on by default


def test_install_time_fetch_downloads_once_and_checks_offline(monkeypatch):
    pytest.importorskip("PIL.Image")
    seen = []

    def process(content, *, mode, page_numbers, offline):
        seen.append((mode, page_numbers, offline))
        return SimpleNamespace(markdown="PODER JUDICIARIO" if offline else None)

    monkeypatch.setitem(sys.modules, "pdf_inspector", SimpleNamespace(process_pdf_with_ocr_bytes=process))
    monkeypatch.setattr(ocr, "_runtime_located", True)
    assert ocr.main() == 0
    assert seen == [("force", [1], False), ("force", [1], True)]  # the download, then a read that cannot download

    monkeypatch.setitem(
        sys.modules,
        "pdf_inspector",
        SimpleNamespace(process_pdf_with_ocr_bytes=lambda content, **kwargs: SimpleNamespace(markdown="")),
    )
    assert ocr.main() == 1
