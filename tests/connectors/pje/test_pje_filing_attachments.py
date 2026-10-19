"""TRF1 attachments against HTML read from real drafts (anonymized): the draft's document grid, the Anexar panel and
the partial answer after Gravar. The registration response and the type-choice answer are synthetic (see their
headers): capturing them needs rows registered on a real draft."""

import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from curl_cffi.requests.exceptions import Timeout

from jurisync.connectors.pje.filing import DraftClient, attachment_types
from jurisync.connectors.pje.filing.checkpoint import UploadLedger

FIXTURES = Path(__file__).parent / "fixtures" / "trf1"
SAVED = (FIXTURES / "document_row_saved.html").read_text(encoding="utf-8")


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def grid_listing(rows=None):
    """The document listing the real grid gives, optionally with rows replaced (name, type, sizeKb)."""
    client = DraftClient(None, portal="trf1-1g")
    client.get = lambda url: '<input type="hidden" name="javax.faces.ViewState" value="v"/>'
    client.ajax = lambda *args: fixture("documents_grid.html")
    listed = client.review_documents("draft")
    return listed if rows is None else rows


class Upload:
    """Two supporting PDFs whose sizes match the grid (153,22 Kb and 6.642,34 Kb)."""

    def __init__(self, tmp):
        self.files = {}
        for name, size, kind in (
            ("09-relatorio_indeferimento.pdf", 6801756, "Carta de Indeferimento"),
            ("10-rg_exemplo.pdf", 156894, "Cédula de Identidade"),
        ):
            path = Path(tmp, name)
            path.write_bytes(b"%PDF-1.4\n" + b"0" * (size - 9))
            self.files[name] = {
                "path": str(path),
                "type": kind,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        self.items = list(self.files.values())


class DraftGridTests(unittest.TestCase):
    def test_the_real_grid_is_read_by_header_with_trf1_labels_and_sizes(self):
        listed = {item["name"]: item for item in grid_listing()}
        self.assertEqual(len(listed), 10)
        self.assertEqual(listed["10-rg_exemplo.pdf"]["type"], "Cédula de Identidade")
        self.assertEqual(listed["09-relatorio_indeferimento.pdf"]["type"], "Carta de Indeferimento")
        self.assertEqual(listed["09-relatorio_indeferimento.pdf"]["sizeKb"], 6642.34)
        self.assertEqual(listed["01-Peticao Judicial - Exemplo.pdf"]["type"], "Petição inicial")
        self.assertEqual(listed["07-folha_resumo_cadunico.pdf"]["description"], "07-folha_resumo_cadunico")


class AttachmentTypeTests(unittest.TestCase):
    def client(self):
        client = DraftClient(None, portal="trf1-1g")
        client.documents_panel = lambda url: fixture("documents_panel_pdf.html")
        return client

    def test_the_anexar_tab_lists_main_and_supporting_types(self):
        types = self.client().attachment_types("draft")
        self.assertIn("Petição inicial", types["main"])
        for label in ("Cédula de Identidade", "Carta de Indeferimento", "Procuração"):
            self.assertIn(label, types["supporting"])
        self.assertNotIn("Selecione o tipo em Lote", types["supporting"])

    def test_an_unknown_type_fails_before_any_row_is_registered_and_the_list_is_kept(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"JURISYNC_FILING_DIR": tmp}):
            client = self.client()
            client._upload_batch = mock.Mock(side_effect=AssertionError("nothing may be attached"))
            documents = {
                "main": {"path": "/x/01-PETICAO.pdf", "type": "Petição inicial"},
                "supporting": [{"path": "/x/02-LAUDO.pdf", "type": "Laudo Médico"}],
            }
            with self.assertRaisesRegex(
                RuntimeError, "02-LAUDO.pdf: 'Laudo Médico' is not an attachment type PJe offers"
            ):
                client.check_attachment_types("draft", documents)
            kept = attachment_types.known("trf1-1g")
            self.assertIn("Cédula de Identidade", kept["supporting"])
            self.assertEqual(Path(tmp, "attachment-types", "trf1-1g.json").stat().st_mode & 0o777, 0o600)
            documents["supporting"][0]["type"] = "Atestado médico"
            client.check_attachment_types("draft", documents)


class BatchTests(unittest.TestCase):
    """_upload_batch on TRF1: rows, actions and later fields come from the registration response."""

    def client(self, answers, multipart=None):
        client = DraftClient(None, portal="trf1-1g")
        client.documents_panel = lambda url: fixture("documents_panel_pdf.html")
        sent = []

        def ajax(url, state, fields, container, action):
            sent.append((dict(fields), dict(action)))
            return answers.pop(0)

        client.ajax = ajax
        client.multipart = multipart or (
            lambda *args, **kwargs: mock.Mock(json=lambda: {"requisicao": True, "arquivos": [{"upload": True}]})
        )
        client.sleep = lambda seconds: None
        return client, sent

    def test_partial_answers_after_a_type_choice_and_after_gravar_still_reach_every_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            upload = Upload(tmp)
            chosen, registered = fixture("document_type_chosen.html"), fixture("document_rows_registered.html")
            answers = [registered, chosen, chosen, SAVED, chosen, chosen, SAVED]
            client, sent = self.client(answers)
            ledger = UploadLedger()
            client._upload_batch("draft", upload.items, mock.MagicMock(), ledger)
        actions = [next(iter(action)) for _, action in sent]
        self.assertEqual(
            actions[1:],
            [
                "formularioUpload:listaDocs:0:j_id1201",
                "formularioUpload:listaDocs:0:commandLinkAtualizarComboTipoDocumento",
                "formularioUpload:listaDocs:0:commandLinkGravar",
                "formularioUpload:listaDocs:1:j_id1201",
                "formularioUpload:listaDocs:1:commandLinkAtualizarComboTipoDocumento",
                "formularioUpload:listaDocs:1:commandLinkGravar",
            ],
        )
        second_row = sent[4][0]
        self.assertEqual(second_row["formularioUpload:listaDocs:1:tipoDoc"], "57")
        self.assertEqual(second_row["formularioUpload:listaDocs:1:ordem"], "2")
        self.assertEqual(sent[1][0]["formularioUpload:listaDocs:0:tipoDoc"], "41")
        self.assertEqual(len(ledger.uploads), 2)


class TimeoutTests(unittest.TestCase):
    """A timed-out attachment POST that PJe saves later must not be sent again."""

    def client(self, listings):
        client = DraftClient(None, portal="trf1-1g")
        reads = []

        def review(url):
            reads.append(1)
            return listings[min(len(reads), len(listings)) - 1]

        client.review_documents = review
        client.sleep = lambda seconds: None
        return client, reads

    @staticmethod
    def listed(upload, *names, kb=None):
        sizes = {"09-relatorio_indeferimento.pdf": 6642.34, "10-rg_exemplo.pdf": 153.22}
        return [
            {"name": name, "type": upload.files[name]["type"], "sizeKb": kb or sizes[name], "description": name}
            for name in names
        ]

    def test_a_timeout_then_a_late_save_counts_as_attached_without_a_resend(self):
        with tempfile.TemporaryDirectory() as tmp:
            upload = Upload(tmp)
            names = list(upload.files)
            # PJe lists nothing at first, nothing right after the timeout, then both files once it caught up.
            client, reads = self.client([[], [], self.listed(upload, *names)])
            batches = []

            def batch(url, items, timeline, ledger):
                batches.append(len(items))
                for item in items:
                    ledger.record(item)
                raise Timeout("Operation timed out after 120000 milliseconds")

            client._upload_batch = batch
            ledger = UploadLedger()
            result = client.add_supporting_documents("draft", upload.items, ledger=ledger)
        self.assertEqual(batches, [1 + 1])
        self.assertTrue(all(item["confirmedAfterError"] for item in result))
        self.assertEqual(len(reads), 3)

    def test_a_file_sent_before_is_awaited_on_the_draft_before_any_resend(self):
        with tempfile.TemporaryDirectory() as tmp:
            upload = Upload(tmp)
            ledger = UploadLedger()
            for item in upload.items:
                ledger.record(item)
            client, reads = self.client([[], self.listed(upload, *upload.files)])
            client._upload_batch = mock.Mock(side_effect=AssertionError("resent"))
            result = client.add_supporting_documents("draft", upload.items, ledger=ledger)
        self.assertEqual([item["alreadyAdded"] for item in result], [True, True])
        self.assertEqual(len(reads), 2)

    def test_still_missing_after_the_wait_fails_without_resending(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"PJE_UPLOAD_SETTLE_SECONDS": "45"}):
            upload = Upload(tmp)
            client, reads = self.client([[]])

            def batch(url, items, timeline, ledger):
                ledger.record(items[0])
                raise Timeout("timed out")

            client._upload_batch = batch
            with self.assertRaisesRegex(RuntimeError, "not on it yet: 09-relatorio_indeferimento.pdf, 10-rg"):
                client.add_supporting_documents("draft", upload.items, ledger=UploadLedger())
        self.assertEqual(len(reads), 1 + 1 + 3, "one listing, one after the error, three settle rereads")

    def test_duplicates_and_size_differences_are_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            upload = Upload(tmp)
            ledger = UploadLedger()
            for item in upload.items:
                ledger.record(item)
            cnis = "09-relatorio_indeferimento.pdf"
            client, _ = self.client([self.listed(upload, cnis, cnis, cnis)])
            with self.assertRaisesRegex(RuntimeError, f"{cnis} is on the draft 3 times"):
                client.add_supporting_documents("draft", [upload.files[cnis]], ledger=ledger)
            client, _ = self.client([self.listed(upload, cnis, kb=12.5)])
            with self.assertRaisesRegex(RuntimeError, "another size"):
                client.add_supporting_documents("draft", [upload.files[cnis]], ledger=ledger)


if __name__ == "__main__":
    unittest.main()
