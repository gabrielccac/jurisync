"""Synthetic, offline tests for selective joined-document context reads."""

import copy
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qsl

from curl_cffi.requests.exceptions import RequestException

from jurisync.connectors.pje import documents, parsers
from jurisync.connectors.pje import process as client
from jurisync.core.document_text import extract_document_text

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

    def test_first_instance_copy_that_does_not_open_is_reported_as_such(self):
        data = process()
        data["timeline"][0]["movements"][0]["documents"][0]["first_instance_copy"] = True
        session = Mock()
        session.get.return_value = response(status=404)
        result = documents.read_selected_documents(data, ["456"], session=session)[0]
        self.assertEqual(result["extractionStatus"], "first_instance_copy")
        self.assertEqual(result["text"], "")

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
            (f"<html><form>{NUMBER}<input type='password'></form></html>".encode(), "text/html", "session_expired"),
            (b"<html>Documento indisponivel</html>", "text/html", "unrecognized_html"),
        ):
            with self.subTest(status=status):
                actual, text = extract_document_text(body, kind, NUMBER)
                self.assertEqual(actual, status)
                self.assertEqual(text, "")
        self.assertEqual(extract_document_text(b"%PDF-1.7", "application/pdf", NUMBER)[0], "pdf_parse_error")
        body = (
            f"<html><style>secret-css</style><script>secret-js</script><p>{NUMBER}</p><p>A decisão</p></html>".encode()
        )
        status, text = extract_document_text(body, "text/html", NUMBER)
        self.assertEqual(status, "extracted")
        self.assertNotIn("secret", text)
        self.assertIn("decisão", text)

    def test_process_read_never_fetches_document_bodies(self):
        # Document texts are read only by read_documents, which checks for a pending ciência first.
        session = Mock()
        with (
            patch.object(client, "login_portal", return_value=(session, None)) as login,
            patch.object(client.search, "search_process", return_value=process()),
        ):
            result = client.fetch_process(NUMBER, scope="all", portal="trf1-1g")
            with self.assertRaises(TypeError):
                client.fetch_process(NUMBER, scope="all", document_ids=["456"], portal="trf1-1g")
        self.assertTrue(result["success"])
        self.assertNotIn("attachments", result)
        login.assert_called_once()
        session.get.assert_not_called()
        session.close.assert_called_once()
        with patch.object(client, "login_portal") as login:
            with self.assertRaises(ValueError):
                client.fetch_process(NUMBER, scope="all", fetch_documents=True, portal="trf1-1g")
            login.assert_not_called()

    def test_the_process_tool_has_no_document_argument(self):
        import asyncio

        from jurisync.connectors.pje import server

        tools = {tool.name: tool for tool in asyncio.run(server.server.list_tools())}
        self.assertNotIn("documentIds", tools["pje_get_process"].input_schema["properties"])
        self.assertIn("pje_read_documents", tools["pje_get_process"].description)
        self.assertIn("documentIds", tools["pje_read_documents"].input_schema["properties"])

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


TJMT_NUMBER = "1000001-11.2024.8.11.0041"
TJMT_DETAIL = (
    "https://pje.tjmt.jus.br/pje/Processo/ConsultaProcesso/Detalhe/listProcessoCompletoAdvogado.seam?id=77&ca=x"
)
TJMT_ROUTE = "/pje/seam/resource/rest/pje-legacy/documento/download/"
TJMT_DOWNLOAD = "https://pje.tjmt.jus.br" + TJMT_ROUTE
ACTION = "divTimeLine:j_id1:0:j_id2:1:j_id3"
TIMELINE_LINK = (
    f'<div><a href="#" id="{ACTION}" onclick="A4J.AJAX.Submit(\'divTimeLine\',event,'
    f"{{'similarityGroupingId':'{ACTION}'}});return false;\"><span>900001 - Petição Inicial</span></a>"
    '<a href="#" id="outro:link" onclick="window.open(\'x\')"><span>900002 - Procuração</span></a></div>'
)


def tjmt_process(viewer=True):
    data = {
        "success": True,
        "portal": "tjmt-1g",
        "numero_processo": TJMT_NUMBER,
        "processo_id": "77",
        "timeline": [
            {
                "date": "01 jul. 2024",
                "movements": [{"documents": parsers.extract_documents(TIMELINE_LINK, TJMT_ROUTE, TJMT_DETAIL)}],
            }
        ],
    }
    if viewer:
        data["viewer"] = {
            "url": TJMT_DETAIL,
            "fields": {"divTimeLine": "divTimeLine", "javax.faces.ViewState": "j_id4"},
        }
    return data


def page(status=200):
    return Mock(status_code=status, headers={}, url=TJMT_DETAIL, content=b"<html>ok</html>")


class ViewerSelectionChecks(unittest.TestCase):
    """TJMT serves a document on the download route only once the page's viewer shows it."""

    def pdf(self):
        return response(body=b"%PDF-1.4 fake", content_type="application/pdf")

    def test_timeline_links_carry_their_viewer_action(self):
        found = parsers.extract_documents(TIMELINE_LINK, "/d/", TJMT_DETAIL)
        self.assertEqual(found[0]["viewer_action"], ACTION)
        self.assertNotIn("viewer_action", found[1])
        self.assertEqual(found[0]["download_url"], "https://pje.tjmt.jus.br/d/900001")

    def test_a_404_shows_the_document_in_the_viewer_and_reads_it_again(self):
        # TJMT answers 404 and TJPA 204 for a document the process page is not showing.
        for status in (404, 204):
            with self.subTest(status=status):
                self.check_viewer_click(status)

    def check_viewer_click(self, status):
        session = Mock()
        session.get.side_effect = [response(status=status), self.pdf()]
        session.post.return_value = page()
        with patch.object(documents, "extract_document_text", return_value=("extracted", "Petição inicial")):
            result = documents.read_selected_documents(tjmt_process(), ["900001"], session=session)[0]
        self.assertEqual((result["status"], result["extractionStatus"]), (200, "extracted"))
        self.assertEqual([c.args[0] for c in session.get.call_args_list], [TJMT_DOWNLOAD + "900001"] * 2)
        session.post.assert_called_once()
        self.assertEqual(session.post.call_args.args[0], TJMT_DETAIL)
        sent = dict(parse_qsl(session.post.call_args.kwargs["data"]))
        self.assertEqual(sent[ACTION], ACTION)
        self.assertEqual(sent["ajaxSingle"], ACTION)
        self.assertEqual(sent["javax.faces.ViewState"], "j_id4")

    def test_a_document_served_at_once_needs_no_viewer_click(self):
        session = Mock()
        session.get.return_value = self.pdf()
        with patch.object(documents, "extract_document_text", return_value=("extracted", "texto")):
            result = documents.read_selected_documents(tjmt_process(), ["900001"], session=session)[0]
        self.assertEqual(result["extractionStatus"], "extracted")
        session.post.assert_not_called()

    def test_without_a_usable_viewer_the_404_stays_an_http_error(self):
        foreign = tjmt_process()
        foreign["viewer"]["url"] = TJMT_DETAIL.replace("pje.tjmt.jus.br", "evil.test")
        other_page = tjmt_process()
        other_page["viewer"]["url"] = "https://pje.tjmt.jus.br/pje/Painel/painel_usuario/advogado.seam"
        bad_action = tjmt_process()
        bad_action["timeline"][0]["movements"][0]["documents"][0]["viewer_action"] = "x'); alert(1"
        for name, data, doc_id in (
            ("no viewer", tjmt_process(viewer=False), "900001"),
            ("foreign host", foreign, "900001"),
            ("other page", other_page, "900001"),
            ("bad action", bad_action, "900001"),
            ("no timeline action", tjmt_process(), "900002"),
        ):
            session = Mock()
            session.get.return_value = response(status=404)
            with self.subTest(name):
                result = documents.read_selected_documents(data, [doc_id], session=session)[0]
                self.assertEqual((result["status"], result["extractionStatus"]), (404, "http_error"))
                session.post.assert_not_called()
                session.get.assert_called_once()

    def test_a_failed_viewer_click_keeps_the_first_answer(self):
        session = Mock()
        session.get.return_value = response(status=404)
        session.post.return_value = page(status=500)
        result = documents.read_selected_documents(tjmt_process(), ["900001"], session=session)[0]
        self.assertEqual((result["status"], result["extractionStatus"]), (404, "http_error"))
        session.get.assert_called_once()

    def test_the_detail_page_viewer_echoes_the_timeline_form_and_its_view_state(self):
        from jurisync.connectors.pje import search

        html = (
            '<form id="divTimeLine"><input name="divTimeLine:txtPesquisa" value="">'
            f"{TIMELINE_LINK}</form>"
            '<input type="hidden" name="javax.faces.ViewState" value="j_id9">'
        )
        viewer = search._viewer(html, TJMT_DETAIL)
        self.assertEqual(viewer["url"], TJMT_DETAIL)
        self.assertEqual(viewer["fields"]["javax.faces.ViewState"], "j_id9")
        self.assertEqual(viewer["fields"]["divTimeLine"], "divTimeLine")
        self.assertIsNone(search._viewer("<html></html>", TJMT_DETAIL))

    def test_the_viewer_and_its_actions_never_reach_the_tool_output(self):
        data = {**tjmt_process(), "documentInventory": tjmt_process()["timeline"][0]["movements"][0]["documents"]}
        redacted = client._redact(data)
        self.assertNotIn("viewer", redacted)
        self.assertNotIn("viewer_action", str(redacted))


if __name__ == "__main__":
    unittest.main()
