import unittest
from io import BytesIO
from unittest.mock import Mock

from curl_cffi.requests.exceptions import RequestException
from pypdf import PdfWriter

from jurisync.connectors.eproc.documents import read_selected_documents

NUMBER = "5000001-23.2026.4.04.7207"
WRAPPER = "https://eproc.jfsc.jus.br/eprocV2/controlador.php?acao=acessar_documento&doc=9&hash=h"
IMPLEMENTATION = "https://eproc.jfsc.jus.br/eprocV2/controlador.php?acao=acessar_documento_implementacao&doc=9&hash=h"


def reply(body, status=200, content_type="text/html"):
    result = Mock(status_code=status, headers={"Content-Type": content_type})
    result.iter_content.return_value = iter([body])
    return result


def process(url=WRAPPER):
    return {"success": True, "numero": NUMBER, "documentos": [{"id": "9", "titulo": "A", "url": url}]}


class EprocDocumentTests(unittest.TestCase):
    def test_iframe_and_inline_wrapper_variants_use_same_session(self):
        implementation = f"<html><p>{NUMBER}</p><p>decisão</p></html>".encode()
        for wrapper in (
            f'<iframe id="conteudoIframe" src="{IMPLEMENTATION}"></iframe>'.encode(),
            f'<script>var u="{IMPLEMENTATION}";</script>'.encode(),
        ):
            session = Mock()
            session.get.side_effect = [reply(wrapper), reply(implementation)]
            result = read_selected_documents(process(), ["9"], session=session, portal="jfsc")[0]
            self.assertEqual(result["extractionStatus"], "extracted")
            self.assertIn("decisão", result["text"])
            self.assertNotIn("url", result)
            self.assertEqual(session.get.call_count, 2)

    def test_selection_and_routes_are_validated_before_requests(self):
        session = Mock()
        for ids in ([], ["9", "9"], ["8"], [9], ["9"] * 6):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                read_selected_documents(process(), ids, session=session, portal="jfsc")
        for url in (
            WRAPPER.replace("https:", "http:"),
            WRAPPER.replace("eproc.jfsc.jus.br", "evil.test"),
            WRAPPER.replace("doc=9", "doc=8"),
            WRAPPER + "#x",
            WRAPPER.replace("https://", "https://u@"),
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                read_selected_documents(process(url), ["9"], session=session, portal="jfsc")
        session.get.assert_not_called()

    def test_denial_redirect_oversize_and_network_failures_are_safe(self):
        for wrapper, status, expected in (
            (b"Sem acesso ao documento solicitado.", 200, "access_denied"),
            (b"", 302, "redirected"),
            (b"x", 200, "wrapper_invalid"),
        ):
            session = Mock()
            response = reply(wrapper, status)
            session.get.return_value = response
            result = read_selected_documents(process(), ["9"], session=session, portal="jfsc")[0]
            self.assertEqual(result["extractionStatus"], expected)
        session = Mock()
        session.get.side_effect = RequestException("secret URL")
        result = read_selected_documents(process(), ["9"], session=session, portal="jfsc")[0]
        self.assertEqual(result["extractionStatus"], "request_failed")
        self.assertNotIn("secret", str(result))

    def test_document_body_hash_and_pdf_format_are_bounded(self):
        buffer = BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        writer.write(buffer)
        body = buffer.getvalue()
        session = Mock()
        session.get.side_effect = [
            reply(f'<iframe id="conteudoIframe" src="{IMPLEMENTATION}"></iframe>'.encode()),
            reply(body, content_type="application/pdf"),
        ]
        result = read_selected_documents(process(), ["9"], session=session, portal="jfsc")[0]
        self.assertEqual(result["format"], "pdf")
        self.assertEqual(result["extractionStatus"], "empty")
        self.assertEqual(len(result["sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
