import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from bs4 import BeautifulSoup

from jurisync.connectors.pje import filing
from jurisync.connectors.pje.filing import DraftClient, validate_fill_payload
from jurisync.connectors.pje.filing.store import LocalRunStore
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

    def test_extra_attorneys_are_a_list_and_the_old_single_key_is_refused(self):
        payload = {
            "subject": {"search": "test", "code": "1", "name": "TEST"},
            "authorCpf": "12345678909",
            "author": {"name": "EXAMPLE", "representative": {"cpf": "99988877766", "name": "M", "kind": "mae"}},
            "attorneys": [{"cpf": "11122233344", "name": "G", "oabNumber": "1", "oabState": "PE"}],
        }
        validate_fill_payload(payload)
        attorney = payload["attorneys"][0]
        with self.assertRaisesRegex(ValueError, "Use attorneys"):
            validate_fill_payload({**payload, "attorney": attorney})
        with self.assertRaisesRegex(ValueError, "attorneys\\[0\\] needs"):
            validate_fill_payload({**payload, "attorneys": [{"cpf": "11122233344"}]})
        with self.assertRaisesRegex(ValueError, "representative needs"):
            validate_fill_payload({**payload, "author": {"name": "E", "representative": {"cpf": "1"}}})

    def test_document_path_is_checked_before_login_or_creation(self):
        payload = {
            "subject": {"search": "test", "code": "1", "name": "TEST"},
            "authorCpf": "12345678909",
            "author": {"name": "EXAMPLE"},
            "documents": {"main": {"path": "/missing/petition.docx"}},
        }
        with self.assertRaisesRegex(ValueError, "existing PDF"):
            validate_fill_payload(payload)

    def test_documents_must_carry_the_approved_sha256_and_still_match_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            petition = Path(tmp, "petition.pdf")
            petition.write_bytes(b"%PDF-1 approved")
            main = {"path": str(petition), "type": "Petição inicial"}
            payload = {
                "subject": {"search": "test", "code": "1", "name": "TEST"},
                "authorCpf": "12345678909",
                "author": {"name": "EXAMPLE"},
                "documents": {"main": main},
            }
            with self.assertRaisesRegex(ValueError, "no sha256.*filing_finalize again"):
                validate_fill_payload(payload)
            main["sha256"] = hashlib.sha256(b"%PDF-1 approved").hexdigest()
            validate_fill_payload(payload)
            petition.write_bytes(b"%PDF-1 edited later")
            with self.assertRaisesRegex(ValueError, "changed since approval: petition.pdf"):
                validate_fill_payload(payload)


class StepTimelineTest(unittest.TestCase):
    def test_records_each_step_with_requests_and_appends_across_runs(self):
        class Client:
            requests = 0

        with tempfile.TemporaryDirectory() as tmp:
            store = LocalRunStore(tmp)
            path = Path(tmp) / "filing-steps.json"
            client = Client()
            timeline = filing.StepTimeline(store, client, "fill")
            with timeline.step("author"):
                client.requests += 3
            with timeline.step("other participant", name="PARTY"):
                pass
            with self.assertRaises(RuntimeError):
                with timeline.step("subjects"):
                    raise RuntimeError("boom")
            again = filing.StepTimeline(store, client, "fill")
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


def test_party_matches_minor_shown_by_initials():
    """O TRF1 lista autor menor só pelas iniciais; CPF + iniciais identificam a parte."""
    from jurisync.connectors.pje.filing.parsing import party_matches

    row = "D. L. J. - CPF: 104.739.182-18 (AUTOR)"
    assert party_matches(row, "DAVI LEÃO JARDIM", "10473918218")
    assert not party_matches(row, "DAVI LEÃO JARDIM", "00000000000")  # CPF diferente
    assert not party_matches(row, "MARIA LEÃO JARDIM", "10473918218")  # iniciais diferentes
    assert party_matches("JOSÉ DA SILVA - CPF: 111", "José da Silva")  # nome completo continua valendo
    assert party_matches("J. S. - CPF: 123.456.789-00", "JOSÉ DOS SANTOS", "12345678900")  # partículas ignoradas
