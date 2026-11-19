import unittest
from io import BytesIO
from unittest.mock import Mock

from curl_cffi.requests.exceptions import RequestException
from pypdf import PdfWriter

from jurisync.connectors.eproc import documents
from jurisync.core.http import IMPERSONATE, TIMEOUT

NUMBER = "5000001-23.2026.4.04.7207"
WRAPPER = "https://eproc.jfsc.jus.br/eprocV2/controlador.php?acao=acessar_documento&doc=9&hash=h"
IMPLEMENTATION = "https://eproc.jfsc.jus.br/eprocV2/controlador.php?acao=acessar_documento_implementacao&doc=9&hash=h"


def reply(body, status=200, content_type="text/html"):
    result = Mock(status_code=status, headers={"Content-Type": content_type})
    result.iter_content.return_value = iter([body])
    return result


def read_selected_documents(*args, **kwargs):
    """The reader as ``client.fetch_process`` calls it, after its ciência gate."""
    return documents.read_selected_documents(*args, ciencia_checked=True, **kwargs)


def process(url=WRAPPER):
    return {"success": True, "number": NUMBER, "documents": [{"id": "9", "title": "A", "url": url}]}


class EprocDocumentTests(unittest.TestCase):
    def test_iframe_and_inline_wrapper_variants_use_same_session(self):
        implementation = f"<html><p>{NUMBER}</p><p>decisão</p></html>".encode()
        for wrapper in (
            f'<iframe id="conteudoIframe" src="{IMPLEMENTATION}"></iframe>'.encode(),
            f'<script>var u="{IMPLEMENTATION}";</script>'.encode(),
        ):
            session = Mock()
            session.request.side_effect = [reply(wrapper), reply(implementation)]
            result = read_selected_documents(process(), ["9"], session=session, portal="jfsc")[0]
            self.assertEqual(result["extractionStatus"], "extracted")
            self.assertIn("decisão", result["text"])
            self.assertNotIn("url", result)
            self.assertEqual(session.request.call_count, 2)
            session.get.assert_not_called()

    def test_document_requests_go_through_the_guarded_client_request(self):
        """Same fingerprint, timeout, no redirects and route allowlist as every other eproc request."""
        session = Mock()
        session.request.side_effect = [
            reply(f'<iframe id="conteudoIframe" src="{IMPLEMENTATION}"></iframe>'.encode()),
            reply(f"<html><p>{NUMBER}</p></html>".encode()),
        ]
        with self.assertLogs("jurisync.http", level="INFO") as logs:
            read_selected_documents(process(), ["9"], session=session, portal="jfsc")
        self.assertEqual(len(logs.output), 2)
        self.assertNotIn("hash=h", "".join(logs.output))
        for call, url in zip(session.request.call_args_list, (WRAPPER, IMPLEMENTATION), strict=True):
            self.assertEqual(call.args, ("GET", url))
            self.assertEqual(
                {key: call.kwargs[key] for key in ("allow_redirects", "impersonate", "timeout", "stream")},
                {"allow_redirects": False, "impersonate": IMPERSONATE, "timeout": TIMEOUT, "stream": True},
            )
        self.assertEqual(session.request.call_args.kwargs["headers"]["Referer"], WRAPPER)

    def test_nothing_is_opened_without_the_ciencia_check(self):
        session = Mock()
        for kwargs in ({}, {"ciencia_checked": False}, {"ciencia_checked": "yes"}):
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(ValueError, "ciência check"):
                documents.read_selected_documents(process(), ["9"], session=session, portal="jfsc", **kwargs)
        session.request.assert_not_called()

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
        session.request.assert_not_called()

    def test_a_document_cited_by_two_events_opens_through_its_first_link(self):
        """TJSC links a document from the event that holds it and from a later one, with that event's hash."""
        first = WRAPPER + "&evento=193"
        cited = WRAPPER.replace("hash=h", "hash=other") + "&evento=196"
        record = {
            "success": True,
            "number": NUMBER,
            "documents": [{"id": "9", "title": "A", "url": cited}],
            "events": [
                {"id": "196", "documents": [{"id": "9", "url": cited}]},
                {"id": "193", "documents": [{"id": "9", "url": first}]},
            ],
        }
        session = Mock()
        session.request.side_effect = [
            reply(f'<iframe id="conteudoIframe" src="{IMPLEMENTATION}"></iframe>'.encode()),
            reply(f"<html><p>{NUMBER}</p></html>".encode()),
        ]
        result = read_selected_documents(record, ["9"], session=session, portal="jfsc")[0]
        self.assertEqual(result["extractionStatus"], "extracted")
        self.assertEqual(session.request.call_args_list[0].args, ("GET", cited))

    def test_links_that_differ_beyond_the_event_still_conflict(self):
        session = Mock()
        for other in (
            WRAPPER.replace("acao=acessar_documento", "acao=acessar_documento_implementacao"),
            WRAPPER + "&key=k",
            WRAPPER.replace("/eprocV2/", "/eproc/"),
        ):
            record = {**process(), "events": [{"id": "2", "documents": [{"id": "9", "url": other}]}]}
            with self.subTest(other=other), self.assertRaisesRegex(ValueError, "Conflicting"):
                read_selected_documents(record, ["9"], session=session, portal="jfsc")
        session.request.assert_not_called()

    def test_denial_redirect_oversize_and_network_failures_are_safe(self):
        for wrapper, status, expected in (
            (b"Sem acesso ao documento solicitado.", 200, "access_denied"),
            (b"", 302, "redirected"),
            (b"x", 200, "wrapper_invalid"),
        ):
            session = Mock()
            response = reply(wrapper, status)
            session.request.return_value = response
            result = read_selected_documents(process(), ["9"], session=session, portal="jfsc")[0]
            self.assertEqual(result["extractionStatus"], expected)
        session = Mock()
        session.request.side_effect = RequestException("secret URL")
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
        session.request.side_effect = [
            reply(f'<iframe id="conteudoIframe" src="{IMPLEMENTATION}"></iframe>'.encode()),
            reply(body, content_type="application/pdf"),
        ]
        result = read_selected_documents(process(), ["9"], session=session, portal="jfsc")[0]
        self.assertEqual(result["format"], "pdf")
        self.assertEqual(result["extractionStatus"], "empty")
        self.assertEqual(len(result["sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
