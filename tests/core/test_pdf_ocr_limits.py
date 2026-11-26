"""OCR of downloaded PDFs (ADVBOX) is bounded in pages and time, offline, and detects each PDF once."""

import sys
from types import SimpleNamespace

from jurisync.core import pdf


def engine(page_count, calls):
    def detect(content):
        calls.append("detect")
        return SimpleNamespace(pdf_type="scanned", page_count=page_count)

    def process(content, *, mode, offline):
        assert offline  # models are never downloaded during a read
        calls.append("ocr")
        return SimpleNamespace(
            markdown="TEXTO",
            page_count=page_count,
            pages_recommended_for_ocr=[1],
            pages_routed_to_ocr=[1],
            pages_recommending_hosted=[],
            ocr_reasons_by_page=[],
            pages_with_tables=[],
            is_complex=False,
            ocr_time_ms=5,
        )

    def native(content):
        calls.append("native")
        return SimpleNamespace(
            markdown="", pdf_type="scanned", page_count=page_count, pages_needing_ocr=[1], has_encoding_issues=False
        )

    return SimpleNamespace(detect_pdf_bytes=detect, process_pdf_with_ocr_bytes=process, process_pdf_bytes=native)


def analyze(monkeypatch, page_count, **options):
    calls = []
    monkeypatch.setitem(sys.modules, "pdf_inspector", engine(page_count, calls))
    monkeypatch.setattr(pdf.ocr_settings, "_locate_runtime", lambda: None)
    return pdf.analyze_document(b"%PDF-1.4 x", "a.pdf", ocr=True, **options), calls


def test_a_short_scanned_pdf_is_read_by_ocr_after_one_detection(monkeypatch):
    result, calls = analyze(monkeypatch, 3)
    assert calls == ["detect", "ocr"]
    assert (result["markdown"], result["pdfType"], result["pagesRoutedToOcr"]) == ("TEXTO", "Scanned", [1])
    assert "ocrSkipped" not in result


def test_a_long_pdf_keeps_its_text_layer_and_says_why_ocr_was_skipped(monkeypatch):
    monkeypatch.setenv("JURISYNC_OCR_MAX_PAGES", "10")
    result, calls = analyze(monkeypatch, 11)
    assert calls == ["detect", "native"]
    assert result["ocrSkipped"] == "11 pages; OCR reads PDFs of at most 10 pages"
    assert result["pagesNeedingOcr"] == [1]


def test_no_ocr_once_the_call_spent_its_time_or_with_ocr_off(monkeypatch):
    result, calls = analyze(monkeypatch, 1, ocr_deadline=-1.0)
    assert calls == ["native"]
    assert result["ocrSkipped"] == "this call's OCR time was spent on earlier files"
    monkeypatch.setenv("JURISYNC_OCR", "off")
    result, calls = analyze(monkeypatch, 1)
    assert calls == ["native"]
    assert "JURISYNC_OCR" in result["ocrSkipped"]


def test_the_call_deadline_follows_the_ocr_seconds_setting(monkeypatch):
    monkeypatch.setenv("JURISYNC_OCR_SECONDS", "40")
    assert pdf.ocr_deadline(clock=lambda: 100.0) == 140.0
