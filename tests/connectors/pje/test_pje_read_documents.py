"""Offline tests for the document-only reader: unnumbered HTML, saved files and parsed expedientes."""

import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from jurisync.connectors.pje import documents
from jurisync.connectors.pje import process as client
from jurisync.core.document_text import extract_document_text
from jurisync.core.documents_dir import resolve_saved_document, save_document

NUMBER = "0003597-10.2026.4.05.8303"
URL = "https://pje1g.trf5.jus.br/pje/seam/resource/rest/pje-legacy/documento/download/TRF5/token/123/456"
UNNUMBERED = (
    "<html><p>Por meio desta, fica V.Sa. Intimada da existência de laudo pericial anexado nos autos do "
    "processo em epígrafe. Serra Talhada, 05/10/2026.</p></html>"
).encode()
CERTIDAO = (
    f"<html><p>PROCESSO : {NUMBER} EXPEDIENTES GERADOS Parte Documento Meio Prazo Pessoal "
    "LAERCIO JOSE (456 - Intimação) Diário Eletrônico 15 dias Não "
    "INSTITUTO NACIONAL DO SEGURO SOCIAL - INSS (456 - Intimação) Sistema 15 dias Não "
    "O(s) documento(s) ficará(ão) disponível(is)</p></html>"
).encode()


def process():
    return {
        "success": True,
        "portal": "trf5-1g",
        "numero_processo": NUMBER,
        "processo_id": "123",
        "expedientes": [],
        "timeline": [
            {
                "date": "05 out. 2026",
                "movements": [{"documents": [{"id": "456", "title": "Intimação", "download_url": URL}]}],
            }
        ],
    }


def response(body, content_type="text/html;charset=UTF-8"):
    result = Mock(status_code=200, headers={"Content-Type": content_type})
    result.iter_content.return_value = iter([body])
    return result


class ExtractorTest(unittest.TestCase):
    def test_unnumbered_html_is_accepted_only_when_asked_and_substantial(self):
        self.assertEqual(extract_document_text(UNNUMBERED, "text/html", NUMBER)[0], "unrecognized_html")
        status, text = extract_document_text(UNNUMBERED, "text/html", NUMBER, allow_unnumbered=True)
        self.assertEqual(status, "extracted_unnumbered")
        self.assertIn("laudo pericial", text)

    def test_short_unnumbered_pages_are_returned_as_short_html_with_their_text(self):
        body = b'<html xmlns="http://www.w3.org/1999/xhtml"><head></head><p>PETI&Ccedil;&Atilde;O INICIAL E DOCUMENTOS ANEXADOS.</p></html>'
        self.assertEqual(extract_document_text(body, "text/html", NUMBER)[0], "unrecognized_html")
        self.assertEqual(
            extract_document_text(body, "text/html", NUMBER, allow_unnumbered=True),
            ("short_html", "PETIÇÃO INICIAL E DOCUMENTOS ANEXADOS."),
        )

    def test_unnumbered_mode_still_rejects_error_login_and_sealed_pages(self):
        for body in (
            b"<html>Documento indisponivel</html>",
            ("<html>" + "O documento não é válido ou não existe. " * 5 + "</html>").encode(),
            ("<html>PJeOffice indisponível " + "texto " * 30 + "</html>").encode(),
            b"<html>Documento protegido por sigilo - Para visualiz\xc3\xa1-lo, acesse os autos digitais.</html>",
        ):
            with self.subTest(body=body[:40]):
                self.assertEqual(
                    extract_document_text(body, "text/html", NUMBER, allow_unnumbered=True)[0], "unrecognized_html"
                )


class ExpedientesTest(unittest.TestCase):
    def test_table_becomes_one_record_per_recipient(self):
        text = (
            "EXPEDIENTES GERADOS Parte Documento Meio Prazo Pessoal D. C. F. D. S. (192188223 - Intimação) "
            "Diário Eletrônico 0 sem prazo Não INSTITUTO NACIONAL DO SEGURO SOCIAL - INSS (192188223 - Intimação) "
            "Sistema 10 dias Sim O(s) documento(s) ficará(ão)"
        )
        rows = documents.parse_expedientes(text)
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            rows[0],
            {
                "parte": "D. C. F. D. S.",
                "documentId": "192188223",
                "documento": "Intimação",
                "meio": "Diário Eletrônico",
                "prazoDias": None,
                "semPrazo": True,
                "pessoal": False,
            },
        )
        self.assertEqual((rows[1]["meio"], rows[1]["prazoDias"], rows[1]["pessoal"]), ("Sistema", 10, True))

    def test_text_without_the_table_has_no_expedientes(self):
        self.assertEqual(documents.parse_expedientes("Certidão qualquer"), [])


class SavedDocumentsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        patcher = patch.dict(os.environ, {"JURISYNC_DOCUMENTS_DIR": self.directory.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_save_and_resolve_are_confined_to_the_folder(self):
        save_document("a_1.pdf", b"%PDF-1.7")
        self.assertEqual(resolve_saved_document("a_1.pdf").read_bytes(), b"%PDF-1.7")
        for bad in ("../a_1.pdf", "/etc/passwd", "a/b.pdf", "", ".hidden", "x" * 200):
            with self.subTest(name=bad), self.assertRaises(ValueError):
                resolve_saved_document(bad)
            with self.subTest(save=bad), self.assertRaises(ValueError):
                save_document(bad, b"x")
        with self.assertRaises(FileNotFoundError):
            resolve_saved_document("missing_1.pdf")

    def test_symlink_pointing_outside_is_refused(self):
        outside = Path(tempfile.mkdtemp()) / "secret.pdf"
        outside.write_bytes(b"secret")
        (Path(self.directory.name) / "link.pdf").symlink_to(outside)
        with self.assertRaises(ValueError):
            resolve_saved_document("link.pdf")

    def test_reader_saves_original_bytes_with_a_stable_name(self):
        session = Mock()
        session.get.return_value = response(UNNUMBERED)
        result = documents.read_selected_documents(process(), ["456"], session=session, save=True)[0]
        self.assertEqual(result["extractionStatus"], "extracted_unnumbered")
        self.assertEqual(result["savedAs"], "00035971020264058303_456.html")
        self.assertEqual(resolve_saved_document(result["savedAs"]).read_bytes(), UNNUMBERED)

    def test_pdf_and_audio_get_their_own_extensions(self):
        for body, kind, extension in ((b"%PDF-1.7 x", "application/pdf", "pdf"), (b"ID3\x04data", "audio/mpeg", "mp3")):
            with self.subTest(extension=extension):
                session = Mock()
                session.get.return_value = response(body, kind)
                with patch.object(documents, "_extract_document_text", return_value=("extracted", "x")):
                    item = documents.read_selected_documents(process(), ["456"], session=session, save=True)[0]
                self.assertTrue(item["savedAs"].endswith("." + extension))

    def test_nothing_is_saved_by_default(self):
        session = Mock()
        session.get.return_value = response(UNNUMBERED)
        item = documents.read_selected_documents(process(), ["456"], session=session)[0]
        self.assertNotIn("savedAs", item)
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])


class ReadDocumentsTest(unittest.TestCase):
    def test_returns_documents_without_a_timeline_and_parses_certidoes(self):
        session = Mock()
        session.get.return_value = response(CERTIDAO)
        with patch.object(client.search, "search_process", return_value=copy.deepcopy(process())):
            result = client.read_documents(NUMBER, ["456"], portal="trf5-1g", session=session)
        self.assertTrue(result["success"])
        self.assertEqual(result["processId"], "123")
        self.assertNotIn("timeline", result)
        document = result["documents"][0]
        self.assertEqual(document["extractionStatus"], "extracted")
        self.assertEqual([row["prazoDias"] for row in document["expedientes"]], [15, 15])
        session.close.assert_not_called()

    def read_with(self, expedientes, **options):
        session = Mock()
        session.get.return_value = response(CERTIDAO)
        found = {**copy.deepcopy(process()), "expedientes": expedientes}
        with patch.object(client.search, "search_process", return_value=found):
            return session, client.read_documents(NUMBER, ["456"], portal="trf5-1g", session=session, **options)

    def test_document_of_a_pending_communication_is_not_opened_without_permission(self):
        pending = [
            {
                "expedienteId": "9",
                "documentId": "456",
                "tipo": "Intimação",
                "parte": "AUTORA",
                "prazoDias": 15,
                "ciencia": None,
            }
        ]
        session = Mock()
        found = {**copy.deepcopy(process()), "expedientes": pending}
        with patch.object(client.search, "search_process", return_value=found):
            with self.assertRaisesRegex(client.PendingNotice, "acknowledgeCiencia=true"):
                client.read_documents(NUMBER, ["456"], portal="trf5-1g", session=session)
        session.get.assert_not_called()

    def test_pending_communication_is_read_once_the_user_agreed(self):
        pending = [{"expedienteId": "9", "documentId": "456", "ciencia": None}]
        session, result = self.read_with(pending, acknowledge_ciencia=True)
        self.assertTrue(result["success"])
        session.get.assert_called()

    def test_read_of_a_pending_communication_reports_that_ciencia_may_be_registered(self):
        pending = [
            {
                "expedienteId": "9",
                "documentId": "456",
                "tipo": "Intimação",
                "parte": "AUTORA",
                "prazoDias": 15,
                "limite": "2026-10-30",
                "ciencia": None,
            }
        ]
        _, result = self.read_with(pending, acknowledge_ciencia=True)
        self.assertEqual(result["ciencia"]["pendenteAoLer"][0]["expedienteId"], "9")
        self.assertIn("informe o usuário", result["ciencia"]["aviso"])
        noticed = [{"expedienteId": "9", "documentId": "456", "ciencia": {"por": "x", "em": "2026-10-06"}}]
        self.assertNotIn("ciencia", self.read_with(noticed)[1])

    def test_documents_with_registered_notice_or_unrelated_to_expedientes_are_read(self):
        noticed = [{"expedienteId": "9", "documentId": "456", "ciencia": {"por": "O sistema", "em": "2026-10-06"}}]
        other = [{"expedienteId": "8", "documentId": "999", "ciencia": None}]
        self.assertTrue(self.read_with(noticed)[1]["success"])
        self.assertTrue(self.read_with(other)[1]["success"])

    def test_unreadable_expedientes_tab_does_not_block_but_is_reported(self):
        session, result = self.read_with(None)
        self.assertTrue(result["success"])
        self.assertIs(result["cienciaChecked"], False)
        session.get.assert_called()

    def test_failed_search_and_wrong_tribunal_do_not_read_anything(self):
        session = Mock()
        with patch.object(client.search, "search_process", return_value={"success": False}):
            result = client.read_documents(NUMBER, ["456"], portal="trf5-1g", session=session)
        self.assertEqual((result["success"], result["documents"]), (False, []))
        session.get.assert_not_called()
        with self.assertRaises(ValueError):
            client.read_documents("1020340-18.2025.4.01.3100", ["456"], portal="trf5-1g", session=session)
        with self.assertRaises(ValueError):
            client.read_documents(NUMBER, ["abc"], portal="trf5-1g", session=session)

    def test_owned_session_is_closed(self):
        session = Mock()
        session.get.return_value = response(UNNUMBERED)
        with (
            patch.object(client, "login_portal", return_value=(session, None)),
            patch.object(client.search, "search_process", return_value=copy.deepcopy(process())),
        ):
            client.read_documents(NUMBER, ["456"], portal="trf5-1g")
        session.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
