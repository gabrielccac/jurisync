import unittest
from unittest.mock import patch

from jurisync.core.document_text import extract_document_text


class DocumentTextTests(unittest.TestCase):
    def test_html_is_bounded_and_strips_non_evidence(self):
        body = b"<html><script>secret</script><p>1020340-18.2025.4.01.3100</p><p>decisao</p></html>"
        status, text = extract_document_text(body, "text/html", "1020340-18.2025.4.01.3100")
        self.assertEqual(status, "extracted")
        self.assertNotIn("secret", text)
        self.assertIn("decisao", text)

    def test_html_error_pages_are_not_evidence(self):
        self.assertEqual(extract_document_text(b"<p>Acesso negado</p>", "text/html")[0], "access_denied")
        self.assertEqual(
            extract_document_text(b"<form><input type=password></form>", "text/html")[0], "session_expired"
        )

    def test_scanned_pdf_reports_ocr_needed(self):
        class Image:
            def get_object(self):
                return {"/Subtype": "/Image"}

        class Page:
            def extract_text(self):
                return ""

            def get(self, key):
                return {"/Resources": {"/XObject": {"image": Image()}}}.get(key)

        class Reader:
            is_encrypted = False
            pages = [Page()]

        with patch("pypdf.PdfReader", return_value=Reader()):
            self.assertEqual(extract_document_text(b"%PDF-1.7", "application/pdf")[0], "ocr_needed")


if __name__ == "__main__":
    unittest.main()
