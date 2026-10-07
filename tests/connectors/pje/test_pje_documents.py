"""Synthetic, offline tests for selective joined-document context reads."""

import copy
import unittest
from unittest.mock import Mock, patch

from curl_cffi.requests.exceptions import RequestException

from jurisync.connectors.pje import documents, parsers
from jurisync.connectors.pje import process as client

NUMBER = "1020340-18.2025.4.01.3100"
URL = "https://pje1g.trf1.jus.br/pje/seam/resource/rest/pje-legacy/documento/download/TRF1/token/123/456"


def process():
    return {
        "success": True,
        "portal": "trf1-1g",
        "numero_processo": NUMBER,
        "processo_id": "123",
        "timeline": [
            {
                "date": "07 set. 2026",
                "movements": [
                    {
                        "time": "16:42",
                        "movement": "Decisão",
                        "documents": [{"id": "456", "title": "Decisão", "download_url": URL}],
                    }
                ],
            }
        ],
    }


def response(body=None, status=200, content_type="text/html"):
    body = (
        body if body is not None else f"<html><p>PROCESSO: {NUMBER}</p><p>Intime-se a parte autora.</p></html>".encode()
    )
    result = Mock(status_code=status, headers={"Content-Type": content_type})
    result.iter_content.return_value = iter([body])
    return result


class SelectedDocumentsChecks(unittest.TestCase):
    def test_only_selected_ids_reuse_session_and_omit_urls(self):
        data = process()
        data["timeline"][0]["movements"].append(copy.deepcopy(data["timeline"][0]["movements"][0]))
        reply = response()
        session = Mock()
        session.get.return_value = reply
        result = documents.read_selected_documents(data, ["456"], session=session)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["extractionStatus"], "extracted")
        self.assertIn("Intime-se", result[0]["text"])
        self.assertNotIn("download_url", result[0])
        self.assertEqual(len(result[0]["sha256"]), 64)
        session.get.assert_called_once()
        self.assertFalse(session.get.call_args.kwargs["allow_redirects"])
        session.close.assert_not_called()
        reply.close.assert_called_once()

    def test_invalid_selection_and_urls_fail_before_any_request(self):
        session = Mock()
        for selection in ([], ["456", "456"], ["456", "999"], [456], "456", [str(n) for n in range(6)]):
            with self.subTest(selection=selection), self.assertRaises(ValueError):
                documents.read_selected_documents(process(), selection, session=session)
        for url in (
            URL.replace("https:", "http:"),
            URL.replace("pje1g.trf1.jus.br", "evil.test"),
            URL.replace("/123/", "/987/"),
            URL.replace("/456", "/999"),
            URL + "?x=token",
            URL + "#fragment",
            URL.replace("https://", "https://user@"),
            "https://pje1g.trf1.jus.br/pje/Painel/painel_usuario/expedientes.seam",
            URL.replace("token/", "../"),
        ):
            data = process()
            data["timeline"][0]["movements"][0]["documents"][0]["download_url"] = url
            with self.subTest(url=url), self.assertRaises(ValueError):
                documents.read_selected_documents(data, ["456"], session=session)
        for field, value in (
            ("portal", "3g"),
            ("numero_processo", NUMBER.replace(".4.01.", ".4.02.")),
            ("success", False),
            ("processo_id", "bad"),
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                documents.read_selected_documents({**process(), field: value}, ["456"], session=session)
        # Tribunal must match the portal: TRF5 numbers need trf5-1g and vice versa.
        with self.subTest(field="cross-tribunal"), self.assertRaises(ValueError):
            documents.read_selected_documents(
                {**process(), "numero_processo": NUMBER.replace(".4.01.", ".4.05.")}, ["456"], session=session
            )
        with self.subTest(field="cross-portal"), self.assertRaises(ValueError):
            documents.read_selected_documents({**process(), "portal": "trf5-1g"}, ["456"], session=session)
        session.get.assert_not_called()
        trf5 = process()
        trf5.update(portal="trf5-1g", numero_processo=NUMBER.replace(".4.01.", ".4.05."))
        trf5["timeline"][0]["movements"][0]["documents"][0]["download_url"] = URL.replace(
            "pje1g.trf1.jus.br", "pje1g.trf5.jus.br"
        ).replace("/download/TRF1/", "/download/TRF5/")
        reply = response(body=f"<html><p>PROCESSO: {trf5['numero_processo']}</p><p>Intime-se.</p></html>".encode())
        session.get.return_value = reply
        with self.subTest(field="trf5-ok"):
            result = documents.read_selected_documents(trf5, ["456"], session=session)
        self.assertEqual(result[0]["extractionStatus"], "extracted")

    def test_http_errors_redirects_and_network_failures_are_not_text(self):
        for status in (302, 401, 403, 404, 429, 500):
            session = Mock()
            reply = response(status=status)
            session.get.return_value = reply
            result = documents.read_selected_documents(process(), ["456"], session=session)[0]
            self.assertEqual(result["extractionStatus"], "http_error")
            self.assertEqual(result["text"], "")
            reply.iter_content.assert_not_called()
            reply.close.assert_called_once()
        session = Mock()
        session.get.side_effect = RequestException("secret-bearing URL")
        result = documents.read_selected_documents(process(), ["456"], session=session)[0]
        self.assertEqual(result["extractionStatus"], "request_failed")
        self.assertNotIn("secret", str(result))

    def test_body_limit_and_interrupted_stream(self):
        for chunks, expected in (([b"123", b"456"], "too_large"), (None, "request_failed")):
            session = Mock()
            reply = response()
            if chunks is None:
                reply.iter_content.side_effect = RequestException("interrupted")
            else:
                reply.iter_content.return_value = iter(chunks)
            session.get.return_value = reply
            with patch.object(documents, "MAX_DOCUMENT_BYTES", 5):
                result = documents.read_selected_documents(process(), ["456"], session=session)[0]
            self.assertEqual(result["extractionStatus"], expected)
            self.assertEqual(result["text"], "")
            reply.close.assert_called_once()

    def test_extraction_reports_unsupported_and_login_pages(self):
        for body, kind, status in (
            (b"", "text/html", "empty"),
            (b"image", "image/png", "unsupported_content"),
            (b"<html><title>Error</title>Unavailable</html>", "text/html", "unrecognized_html"),
            (f"<html><form>{NUMBER}<input type='password'></form></html>".encode(), "text/html", "unrecognized_html"),
            (b"<html>Documento indisponivel</html>", "text/html", "unrecognized_html"),
        ):
            with self.subTest(status=status):
                actual, text = documents.extract_document_text(body, kind, NUMBER)
                self.assertEqual(actual, status)
                self.assertEqual(text, "")
        with patch.object(documents, "_extract_document_text", return_value=("pdf_parse_error", "")):
            self.assertEqual(
                documents.extract_document_text(b"%PDF-1.7", "application/pdf", NUMBER)[0], "pdf_extractor_unavailable"
            )
        body = (
            f"<html><style>secret-css</style><script>secret-js</script><p>{NUMBER}</p><p>A decisão</p></html>".encode()
        )
        status, text = documents.extract_document_text(body, "text/html", NUMBER)
        self.assertEqual(status, "extracted")
        self.assertNotIn("secret", text)
        self.assertIn("decisão", text)

    def test_client_selective_read_keeps_owned_session_until_finished(self):
        session = Mock()
        session.get.return_value = response()
        with (
            patch.object(client, "login_portal", return_value=(session, None)) as login,
            patch.object(client.search, "search_process", return_value=process()),
        ):
            result = client.fetch_process(NUMBER, scope="all", document_ids=["456"], portal="trf1-1g")
        self.assertEqual(result["attachments"][0]["extractionStatus"], "extracted")
        login.assert_called_once()
        session.get.assert_called_once()
        session.close.assert_called_once()
        with patch.object(client, "login_portal") as login:
            for kwargs in ({"scope": "latest"}, {"scope": "all", "fetch_documents": True}):
                with self.assertRaises(ValueError):
                    client.fetch_process(NUMBER, document_ids=["456"], portal="trf1-1g", **kwargs)
            for invalid in ([], ["456", "456"], [456], "456"):
                with self.assertRaises(ValueError):
                    client.fetch_process(NUMBER, scope="all", document_ids=invalid, portal="trf1-1g")
            login.assert_not_called()

    def test_client_scope_page_uses_first_page_and_all_merges_bounded_pages(self):
        first = {"date": "07 set. 2026", "movements": []}
        second = {"date": "06 set. 2026", "movements": []}
        parsed = {
            "success": True,
            "numero_processo": NUMBER,
            "timeline": [first],
            "first_timeline": [first],
            "timeline_pages": [{"timeline": [first]}, {"timeline": [second]}],
            "pagination": {"loadedPages": 2, "complete": True},
        }
        session = Mock()
        with patch.object(client.search, "search_process", return_value=parsed):
            page = client.fetch_process(NUMBER, scope="page", session=session, portal="trf1-1g")
            all_pages = client.fetch_process(NUMBER, scope="all", session=session, portal="trf1-1g")
        self.assertEqual(page["timeline"], [first])
        self.assertEqual(all_pages["timeline"], [first, second])
        self.assertEqual(all_pages["scopeSemantics"], "bounded_timeline_pages")

    def test_parser_preserves_compound_and_empty_labels(self):
        html = """<div class="media data"><div class="media-body"><span class="text-muted data-interna">07 set. 2026</span>
        <div class="media interno tipo-M"><span class="text-upper texto-movimento">Julgado procedente</span>
        <span class="texto-movimento text-upper">Concedida a tutela</span><a><span>456 - Sentença</span></a>
        <div class="col-sm-12"><small class="text-muted pull-right">16:42</small>
        <div class="media interno tipo-D"><a><span>457 - Intimação</span></a>
        <div class="col-sm-12"><small class="text-muted pull-right">16:43</small>"""
        timeline = parsers.parse_timeline(html, "/documents/", "https://pje1g.trf1.jus.br")
        first, second = timeline[0]["movements"]
        self.assertEqual(first["movement"], "Julgado procedente")
        self.assertEqual(first["movement_texts"], ["Julgado procedente", "Concedida a tutela"])
        self.assertEqual(second["movement_texts"], [])
        self.assertEqual(second["documents"][0]["id"], "457")
        self.assertEqual(
            client.movement_candidates({"timeline": timeline})[0]["movement_texts"], first["movement_texts"]
        )


if __name__ == "__main__":
    unittest.main()
