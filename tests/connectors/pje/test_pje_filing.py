import json
import tempfile
import unittest
from pathlib import Path

from bs4 import BeautifulSoup

from jurisync.connectors.pje import filing
from jurisync.connectors.pje.filing import DraftClient, validate_fill_payload
from jurisync.connectors.pje.filing.subjects import has_subject_code, subject_rows


class DraftPayloadTests(unittest.TestCase):
    def test_subject_result_code_is_distinct_from_ancestor_code(self):
        table = BeautifulSoup(
            """<table><tr><td></td><td>6101</td><td>Auxílio por Incapacidade Temporária (6101)</td><td>Não</td></tr>
          <tr><td></td><td>14808</td><td>Auxílio por Incapacidade Temporária (6101) | Rural (14808)</td><td>Não</td></tr></table>""",
            "html.parser",
        ).table
        matches = [row for row, code, _, _ in subject_rows(table) if code == "6101"]
        self.assertEqual(len(matches), 1)
        attached = BeautifulSoup(
            """<table><tr><td></td><td>14808</td><td></td>
          <td>Auxílio por Incapacidade Temporária (6101) | Rural (14808)</td><td>Não</td></tr></table>""",
            "html.parser",
        ).table
        self.assertFalse(has_subject_code(attached, "6101"))
        self.assertTrue(has_subject_code(attached, "14808"))

    def test_optional_parties_do_not_require_customer_defaults(self):
        payload = {
            "subject": {"search": "test", "code": "1", "name": "TEST"},
            "authorCpf": "12345678909",
            "author": {"name": "EXAMPLE"},
        }
        validate_fill_payload(payload)
        client = DraftClient(None)
        client.add_subjects = lambda *_: [{"subjectCode": "1"}]
        client.party_panel = lambda *_: "<html></html>"
        client.add_author = lambda *_: {"authorNameMatched": True}
        client._pending_row = lambda *_: {"draftId": "1"}
        result = client.fill_draft({"draftUrl": "draft", "draftId": "1"}, payload)
        self.assertEqual(set(result), {"subject", "author", "pending"})

    def test_document_path_is_checked_before_login_or_creation(self):
        payload = {
            "subject": {"search": "test", "code": "1", "name": "TEST"},
            "authorCpf": "12345678909",
            "author": {"name": "EXAMPLE"},
            "documents": {"main": {"path": "/missing/petition.docx"}},
        }
        with self.assertRaisesRegex(ValueError, "existing PDF"):
            validate_fill_payload(payload)


class StepTimelineTest(unittest.TestCase):
    def test_records_each_step_with_requests_and_appends_across_runs(self):
        class Client:
            requests = 0

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "timeline.json"
            client = Client()
            timeline = filing.StepTimeline(path, client, "fill")
            with timeline.step("author"):
                client.requests += 3
            with timeline.step("other participant", name="PARTY"):
                pass
            with self.assertRaises(RuntimeError):
                with timeline.step("subjects"):
                    raise RuntimeError("boom")
            again = filing.StepTimeline(path, client, "fill")
            with again.step("pending"):
                pass
            saved = json.loads(path.read_text())["steps"]
        self.assertEqual([step["step"] for step in saved], ["author", "other participant", "subjects", "pending"])
        self.assertEqual(saved[1]["name"], "PARTY")
        self.assertEqual((saved[0]["status"], saved[0]["requests"]), ("ok", 3))
        self.assertEqual((saved[2]["status"], saved[2]["error"]), ("failed", "RuntimeError: boom"))


class UploadNameTests(unittest.TestCase):
    def test_uploads_use_ascii_names_and_checks_tolerate_pje_mojibake(self):
        path = Path("/tmp/10-PETIÇÃO INICIAL_6630.pdf")
        self.assertEqual(filing.upload_name(path), "10-PETICAO INICIAL_6630.pdf")
        # PJe stores UTF-8 names as Latin-1 characters: PETIÇÃO is listed as PETIÃ\x87Ã\x83O.
        self.assertTrue(filing.same_name("10-PETIÃ\x87Ã\x83O INICIAL_6630.pdf", path))
        self.assertTrue(filing.same_name("10-PETICAO INICIAL_6630.pdf", path))
        self.assertTrue(filing.same_name("10-PETIÇÃO INICIAL_6630.pdf", path))
        self.assertFalse(filing.same_name("10-PETICAO INICIAL_9999.pdf", path))
        self.assertEqual(filing.upload_name(Path('/tmp/a"b.pdf')), "a_b.pdf")


if __name__ == "__main__":
    unittest.main()
