from unittest import mock

from jurisync.core import html_pdf


def test_no_browser_returns_none(monkeypatch):
    monkeypatch.setattr(html_pdf, "chrome_path", lambda: None)
    assert html_pdf.html_to_pdf(b"<html>x</html>") is None


def test_browser_failure_returns_none(monkeypatch):
    monkeypatch.setattr(html_pdf, "chrome_path", lambda: "/usr/bin/false")
    assert html_pdf.html_to_pdf(b"<html>x</html>", "text/html; charset=ISO-8859-1") is None


def test_saved_html_falls_back_to_html_without_a_browser(tmp_path, monkeypatch):
    from jurisync.connectors.eproc import documents

    monkeypatch.setenv("JURISYNC_DOCUMENTS_DIR", str(tmp_path))
    with mock.patch.object(documents, "html_to_pdf", return_value=None):
        result = {"format": "html", "content_type": "text/html"}
        name = documents._save("5004020-17.2026.4.04.7104", "99", b"<html>ato</html>", result)
    assert name.endswith("_99.html") and result["savedFormat"] == "html"
    assert (tmp_path / name).read_bytes() == b"<html>ato</html>"
