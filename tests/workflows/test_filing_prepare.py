import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jurisync.workflows.filing.classify import (
    ask_jev,
    claim_value_candidates,
    classify_document,
    cpf_candidates,
    decision_outcome,
    document_kind,
)
from jurisync.workflows.filing.prepare import (
    _stage_upload,
    apply_supporting_fallback,
    build_payload,
    finalize_review,
    prepare_folder,
    upload_filename,
)
from jurisync.workflows.filing.profile import load_profile

PROFILE = load_profile("victor")
FACTS = {
    "authorCpf": "12345678909",
    "author": {"name": "EXAMPLE"},
    "classification": {"jurisdiction": "PE / Petrolina", "judicialClass": "CLASS"},
    "subject": {"search": "TEST", "code": "1", "name": "TEST"},
    "characteristics": {"claimValue": "5.000,00"},
    "fiscalDaLei": False,
}


class DocumentClassificationTest(unittest.TestCase):
    def test_real_advbox_filename_variants_and_decision_text(self):
        expected = {
            "2-LAUDOS MÉDCIOS E RECEITUARIOS_9352.pdf": "medical",
            "3-PROVAS RURAIS_9436.pdf": "Documento Comprobatório",
            "0-PA_3489.pdf": "Processo Administrativo",
            "11-PETÇÃO INICIAL_8308.docx": "petition",
            "1-INDEFERIMENTO_9328.pdf": "Carta de Indeferimento",
            "2-CERTIDÃO DE ÓBITO E DOCUMENTOS_6017.pdf": "Documento Comprobatório",
            "3-PROVAS DA DEPENDENCIA ECONÔMICA_5512.pdf": "Documento Comprobatório",
            "71- DOCUMENTO DE IDENTIFICIAÇÃO DA REPRESENTANDE_6267.pdf": "Documento Comprobatório",
            "Quesitos Auxilio Doença_8587.pdf": "Outros Documentos",
        }
        for name, kind in expected.items():
            self.assertEqual(document_kind(name), kind, name)
        self.assertEqual(
            decision_outcome(
                "A perícia médica não reconheceu a incapacidade; o benefício não foi prorrogado. A cessação permanece."
            ),
            "cessation",
        )
        self.assertEqual(
            decision_outcome(
                "Decisão: Indeferimento do Pedido. Não foi reconhecido o direito ao benefício. "
                + "Razões da decisão. " * 3
            ),
            "denied",
        )
        self.assertEqual(
            decision_outcome("Decisão: Deferimento do Pedido. Foi reconhecido o direito. " + "Razões da decisão. " * 3),
            "granted",
        )
        self.assertEqual(decision_outcome("**Decisão:** NEGADO **Motivo:** " + "Texto da decisão. " * 5), "denied")
        self.assertEqual(
            classify_document("1-COMUNICADO DE DECISÃO.pdf", "Decisão: NEGADO. " + "Razões do indeferimento. " * 5)[
                "type"
            ],
            "Carta de Indeferimento",
        )
        self.assertEqual(
            claim_value_candidates("Valor da Causa: R$ 28.432,90\nDá-se à causa o valor de R$ 28.432,90"), ["28.432,90"]
        )
        self.assertEqual(cpf_candidates("CPF 123.456.789-09; CPF 123.456.789-09"), ["12345678909"])

    def test_jev_uses_typed_choice_api_and_holds_unknown_files_for_review(self):
        requests = []

        def post(url, headers, body):
            requests.append((url, body))
            return 200, {"answers": {"kind": {"choice": "supporting_evidence", "confidence": 0.98}}}

        self.assertEqual(
            ask_jev("DESPACHO.pdf", "Content " * 20, api_key="test", openrouter_key="", post=post),
            {"choice": "supporting_evidence", "confidence": 0.98},
        )
        url, body = requests[-1]
        self.assertEqual(
            (url, body["model"], body["questions"]["kind"]["type"]),
            ("https://api.typesafe.ai/v1/systemone", "jev-latest", "choice"),
        )
        proposed = classify_document("DESPACHO.pdf", "Content " * 20, api_key="test", openrouter_key="", post=post)
        self.assertEqual(proposed["type"], "Documento Comprobatório")
        self.assertIn("review", proposed)
        ask_jev("DESPACHO.pdf", "Content " * 20, openrouter_key="test", api_key="", post=post)
        self.assertEqual(
            (requests[-1][0], requests[-1][1]["model"]),
            ("https://openrouter.ai/api/alpha/decisions", "typesafe/jev-1.13"),
        )

    def test_jev_retries_rate_limits_then_fails_clearly(self):
        statuses = iter([429, 529, 500])
        with self.assertRaisesRegex(RuntimeError, "HTTP 500"):
            ask_jev(
                "x.pdf",
                "Content " * 20,
                api_key="test",
                openrouter_key="",
                post=lambda *args: (next(statuses), None),
                sleep=lambda seconds: None,
            )

    def test_text_sent_to_jev_is_bounded(self):
        captured = {}

        def post(url, headers, body):
            captured.update(body)
            return 200, {"answers": {"kind": {"choice": "other", "confidence": 0.5}}}

        ask_jev("x.pdf", "a" * 50_000, api_key="test", openrouter_key="", post=post)
        self.assertEqual(len(captured["state"]["extracted_text"]), 12_000)


class UploadNameTest(unittest.TestCase):
    def test_names_lose_accents_and_advbox_suffix_but_keep_order_and_volumes(self):
        expected = {
            "10-PETIÇÃO INICIAL_6630.pdf": "10-PETICAO INICIAL.pdf",
            "2-LAUDOS MÉDICOS E EXAMES_1524.pdf": "2-LAUDOS MEDICOS E EXAMES.pdf",
            "6-DOCUMENTO DE IDENTIFICAÇÃO_7949.pdf": "6-DOCUMENTO DE IDENTIFICACAO.pdf",
            "0-PA_3489.pdf": "0-PA.pdf",
            "3-PROVAS DA ATIVIDADE RURAL_4492-vol-02.pdf": "3-PROVAS DA ATIVIDADE RURAL-vol-02.pdf",
            "8-PROCURAÇÃO #1 & final_9722.docx": "8-PROCURACAO 1 final.pdf",
            "çãõ.pdf": "cao.pdf",
            "???.pdf": "documento.pdf",
        }
        for name, clean in expected.items():
            self.assertEqual(upload_filename(name), clean, name)

    def test_staged_copy_keeps_content_and_never_collides(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, second = root / "A_1111.pdf", root / "Ã_2222.pdf"
            first.write_bytes(b"%PDF-1")
            second.write_bytes(b"%PDF-2")
            a, b = Path(_stage_upload(first, root)), Path(_stage_upload(second, root))
            self.assertEqual((a.name, b.name), ("A.pdf", "A.pdf"))
            self.assertNotEqual(a.parent, b.parent)
            self.assertEqual((a.read_bytes(), b.read_bytes()), (b"%PDF-1", b"%PDF-2"))


class PayloadTest(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.dict(os.environ, {"VICTOR_PJE_ATTORNEY_CPF": "12345678909"})
        patch.start()
        self.addCleanup(patch.stop)
        os.environ.pop("VICTOR_PJE_GRAZIELE_CPF", None)
        found = mock.patch(
            "jurisync.workflows.filing.prepare._petition_candidates", return_value=(["5.000,00"], ["12345678909"])
        )
        found.start()
        self.addCleanup(found.stop)

    def test_uncertain_supporting_files_fall_back_without_blocking(self):
        flagged = apply_supporting_fallback(
            {
                "role": "supporting",
                "type": "Laudo Médico",
                "method": "jev",
                "confidence": 0.63,
                "review": "Jev classification needs review",
            }
        )
        self.assertEqual(
            flagged,
            {
                "role": "supporting",
                "type": "Outros Documentos",
                "method": "fallback",
                "confidence": 0.63,
                "fallbackReason": "Jev classification needs review",
                "suggestedType": "Laudo Médico",
            },
        )
        main = {"role": "main", "type": "Petição inicial", "review": "Choose main"}
        self.assertEqual(apply_supporting_fallback(main), main)

    def test_defaults_merge_only_with_explicit_case_facts(self):
        payload = build_payload(
            FACTS, {"main": {"path": "/tmp/test.pdf", "type": "Petição inicial"}, "supporting": []}, PROFILE.defaults()
        )
        self.assertEqual(payload["attorney"]["name"], "GRAZIELE ALVES DA SILVA")
        self.assertTrue(payload["characteristics"]["legalAid"])
        self.assertEqual(payload["characteristics"]["claimValue"], "5.000,00")
        self.assertEqual(payload["classification"]["jurisdiction"], "PE / Petrolina")
        with self.assertRaisesRegex(ValueError, "claimValue"):
            build_payload({**FACTS, "characteristics": {"claimValue": "0"}}, {}, PROFILE.defaults())

    def test_fiscal_da_lei_is_conditional_and_ceab_is_fixed(self):
        def bodies(facts):
            payload = build_payload(facts, {}, PROFILE.defaults())
            return [(body["role"], "when" in body) for body in payload["otherParticipants"]]

        self.assertEqual(bodies(FACTS), [("ÓRGÃO DE CUMPRIMENTO", False)])
        self.assertEqual(
            bodies({**FACTS, "fiscalDaLei": True}),
            [("ÓRGÃO DE CUMPRIMENTO", False), ("FISCAL DA LEI", False)],
        )
        missing = {key: value for key, value in FACTS.items() if key != "fiscalDaLei"}
        with self.assertRaisesRegex(ValueError, "fiscalDaLei"):
            build_payload(missing, {}, PROFILE.defaults())

    def test_review_finalizer_requires_resolution_and_verifies_pdf_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            petition, supporting = directory / "petition.pdf", directory / "supporting.pdf"
            petition.write_bytes(b"%PDF-1.4\nfixture")
            supporting.write_bytes(b"%PDF-1.4\nsupporting fixture")
            digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
            report = directory / "review.json"
            report.write_text(
                json.dumps(
                    {
                        "files": [
                            {
                                "id": "file:0",
                                "name": "petition.pdf",
                                "path": str(petition),
                                "sha256": digest(petition),
                                "uploadSha256": digest(petition),
                                "role": "main",
                                "type": "Petição inicial",
                                "review": "Choose main",
                            },
                            {
                                "id": "file:1",
                                "name": "supporting.pdf",
                                "path": str(supporting),
                                "sha256": digest(supporting),
                                "uploadSha256": digest(supporting),
                                "role": "supporting",
                                "type": "Outros Documentos",
                                "fallbackReason": "No readable document text",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "needs review"):
                finalize_review(report, FACTS, profile=PROFILE)
            choice = {"file:0": {"role": "main", "type": "Petição inicial"}}
            result = finalize_review(report, FACTS, choice, profile=PROFILE)
            self.assertTrue(result["ready"])
            documents = json.loads(Path(result["payloadPath"]).read_text(encoding="utf-8"))["documents"]
            self.assertEqual(documents["main"]["path"], str(petition))
            self.assertEqual(
                documents["supporting"],
                [{"path": str(supporting), "type": "Outros Documentos", "description": "supporting"}],
            )
            self.assertEqual(oct(Path(result["payloadPath"]).stat().st_mode & 0o777), "0o600")
            petition.write_bytes(b"%PDF-1.4\nchanged")
            with self.assertRaisesRegex(ValueError, "Document changed"):
                finalize_review(report, FACTS, choice, profile=PROFILE)


class PrepareFolderTest(unittest.TestCase):
    """The preparation works on any folder of pulled documents, whatever system they came from."""

    def setUp(self):
        patch = mock.patch.dict(os.environ, {"VICTOR_PJE_ATTORNEY_CPF": "12345678909"})
        patch.start()
        self.addCleanup(patch.stop)

    def folder(self, files):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        for name, content in files.items():
            Path(directory.name, name).write_bytes(content)
        return directory.name

    def test_a_pulled_folder_becomes_a_review_and_a_payload_without_touching_the_source(self):
        source = self.folder({"PETIÇÃO INICIAL.pdf": b"%PDF-1.4\npetition", "0-PA.pdf": b"%PDF-1.4\nadministrative"})
        files = sorted(Path(source).iterdir())
        with tempfile.TemporaryDirectory() as out:
            result = prepare_folder(files, FACTS, out, profile=PROFILE, source="docs", jev=False)
            self.assertEqual((result["fileCount"], result["source"]), (2, "docs"))
            report = json.loads(Path(result["reviewPath"]).read_text(encoding="utf-8"))
            self.assertEqual(report["source"], "docs")
            self.assertEqual(sorted(row["role"] for row in report["files"]), ["main", "supporting"])
            for row in report["files"]:
                self.assertTrue(Path(row["path"]).is_relative_to(Path(out).resolve()))
        self.assertEqual(sorted(p.name for p in Path(source).iterdir()), ["0-PA.pdf", "PETIÇÃO INICIAL.pdf"])
        self.assertEqual(Path(source, "0-PA.pdf").read_bytes(), b"%PDF-1.4\nadministrative")

    def test_an_empty_folder_and_a_non_pdf_file_are_reported(self):
        with tempfile.TemporaryDirectory() as out:
            with self.assertRaisesRegex(ValueError, "has no files"):
                prepare_folder([], FACTS, out, profile=PROFILE, source="empty", jev=False)
            source = self.folder({"PETIÇÃO INICIAL.pdf": b"%PDF-1.4\np", "nota.txt": b"hello"})
            result = prepare_folder(sorted(Path(source).iterdir()), FACTS, out, profile=PROFILE, jev=False)
        self.assertFalse(result["ready"])
        self.assertTrue(any("not a PDF" in item["reason"] for item in result["review"]))


FOUND = (["5.000,00"], ["12345678909"])


def candidates(values=FOUND):
    return mock.patch("jurisync.workflows.filing.prepare._petition_candidates", return_value=values)


class RunFolder(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.dict(os.environ, {"VICTOR_PJE_ATTORNEY_CPF": "12345678909"})
        patch.start()
        self.addCleanup(patch.stop)
        for holder in ("source", "out"):
            directory = tempfile.TemporaryDirectory()
            self.addCleanup(directory.cleanup)
            setattr(self, holder, Path(directory.name))
        self.write({"PETIÇÃO INICIAL.pdf": b"%PDF-1.4\npetition", "0-PA.pdf": b"%PDF-1.4\nadministrative"})
        self.payload = self.out / "payload.json"

    def write(self, files):
        for name, content in files.items():
            (self.source / name).write_bytes(content)

    def prepare(self, facts=FACTS, found=FOUND):
        with candidates(found):
            return prepare_folder(sorted(self.source.iterdir()), facts, self.out, profile=PROFILE, source="docs")


class StalePayloadTest(RunFolder):
    def test_a_second_prepare_with_review_items_removes_the_earlier_payload(self):
        self.assertTrue(self.prepare()["ready"])
        self.assertTrue(self.payload.exists())
        second = self.prepare({**FACTS, "authorCpf": ""})
        self.assertFalse(second["ready"])
        self.assertIsNone(second["payloadPath"])
        self.assertFalse(self.payload.exists())

    def test_a_prepare_that_raises_leaves_no_payload_and_no_review_from_the_earlier_run(self):
        self.prepare()
        with self.assertRaisesRegex(ValueError, "has no files"):
            prepare_folder([], FACTS, self.out, profile=PROFILE, source="empty")
        self.assertFalse(self.payload.exists())
        self.assertFalse((self.out / "review.json").exists())

    def test_a_finalize_that_raises_removes_the_earlier_payload(self):
        self.prepare()
        review = self.out / "review.json"
        with candidates():
            self.assertTrue(finalize_review(review, FACTS, profile=PROFILE)["ready"])
            self.assertTrue(self.payload.exists())
            with self.assertRaisesRegex(ValueError, "authorCpf"):
                finalize_review(review, {**FACTS, "authorCpf": ""}, profile=PROFILE)
        self.assertFalse(self.payload.exists())

    def test_the_payload_is_written_whole_and_private(self):
        self.prepare()
        self.assertEqual(oct(self.payload.stat().st_mode & 0o777), "0o600")
        self.assertEqual(json.loads(self.payload.read_text(encoding="utf-8"))["authorCpf"], "12345678909")
        self.assertEqual(sorted(path.name for path in self.out.glob("*.json*")), ["payload.json", "review.json"])


class OverrideTest(RunFolder):
    def finalize(self, overrides):
        with candidates():
            return finalize_review(self.out / "review.json", FACTS, overrides, profile=PROFILE)

    def test_an_override_changes_only_role_and_type(self):
        self.prepare()
        report = json.loads((self.out / "review.json").read_text(encoding="utf-8"))
        supporting = next(row for row in report["files"] if row["role"] == "supporting")
        other = self.source / "0-PA.pdf"
        for extra in ({"path": str(other)}, {"uploadSha256": "0" * 64}, {"parts": []}, {"name": "x.pdf"}):
            with self.assertRaisesRegex(ValueError, "only set role and type"):
                self.finalize({supporting["id"]: {"role": "supporting", "type": "Planilha", **extra}})
        for bad in ({"role": "principal"}, {"type": 3}, "skip"):
            with self.assertRaisesRegex(ValueError, "Override"):
                self.finalize({supporting["id"]: bad})
        with self.assertRaisesRegex(ValueError, "not in the review"):
            self.finalize({"nope:9": {"role": "skip"}})
        self.finalize({supporting["id"]: {"role": "supporting", "type": "Planilha"}})
        documents = json.loads(self.payload.read_text(encoding="utf-8"))["documents"]
        self.assertEqual(documents["supporting"][0]["type"], "Planilha")
        self.assertEqual(documents["supporting"][0]["path"], supporting["path"])
        self.finalize({supporting["id"]: {"role": "skip"}})
        self.assertEqual(json.loads(self.payload.read_text(encoding="utf-8"))["documents"]["supporting"], [])


class PetitionCrossCheckTest(RunFolder):
    def reasons(self, result):
        return {item["name"]: item["reason"] for item in result["review"]}

    def test_a_petition_without_claim_value_or_cpf_passes(self):
        result = self.prepare(found=([], []))
        self.assertTrue(result["ready"])
        self.assertTrue(self.payload.exists())

    def test_claim_values_are_compared_as_amounts(self):
        facts = {**FACTS, "characteristics": {"claimValue": "5000,00"}}
        self.assertTrue(self.prepare(facts)["ready"])
        with candidates():
            self.assertTrue(finalize_review(self.out / "review.json", facts, profile=PROFILE)["ready"])
        different = self.prepare(found=(["5.000,01", "x"], FOUND[1]))
        self.assertIn("differs", self.reasons(different)["claimValue"])
        self.assertIn("not found", self.reasons(self.prepare(found=(FOUND[0], ["11122233344"])))["authorCpf"])

    def test_finalize_refuses_only_a_claim_value_or_cpf_that_differs(self):
        self.prepare()
        review = self.out / "review.json"
        for found in (([], FOUND[1]), (FOUND[0], []), ([], [])):
            with candidates(found):
                self.assertTrue(finalize_review(review, FACTS, profile=PROFILE)["ready"])
        for found, message in (
            ((["9.000,00"], FOUND[1]), "claimValue differs"),
            ((FOUND[0], ["11122233344"]), "CPF was not found"),
        ):
            with candidates(found), self.assertRaisesRegex(ValueError, message):
                finalize_review(review, FACTS, profile=PROFILE)
            self.assertFalse(self.payload.exists())


class ReviewReasonTest(RunFolder):
    def test_a_failed_conversion_or_classifier_never_puts_commands_or_paths_in_the_review(self):
        self.write({"LAUDO.docx": b"not a real docx", "EXAME.pdf": b"%PDF-1.4\nexam"})
        failure = subprocess.CalledProcessError(1, ["/usr/bin/libreoffice", "--outdir", "/tmp/jurisync-secret/x"])
        with (
            mock.patch("jurisync.workflows.filing.prepare.convert_word_to_pdf", side_effect=failure),
            mock.patch(
                "jurisync.workflows.filing.prepare.classify_document",
                side_effect=OSError(2, "No such file", "/home/office/private/model.bin"),
            ),
        ):
            result = self.prepare()
        text = json.dumps(result["review"])
        for hidden in ("libreoffice", "jurisync-secret", "/home/office", "model.bin", "--outdir"):
            self.assertNotIn(hidden, text)
        reasons = " | ".join(item["reason"] for item in result["review"])
        self.assertIn("PDF conversion failed: the converter exited with an error", reasons)

    def test_jev_off_is_reported_as_off_not_as_a_missing_key(self):
        off = classify_document("DESPACHO.pdf", "Content " * 20, jev=False)
        self.assertIn("off", off["review"])
        self.assertNotIn("OPENROUTER", off["review"])
        missing = classify_document("DESPACHO.pdf", "Content " * 20, openrouter_key="", api_key="")
        self.assertIn("OPENROUTER_API_KEY", missing["review"])

    def test_prepare_keeps_jev_off_unless_asked(self):
        self.write({"DESPACHO.pdf": b"%PDF-1.4\nnote"})
        seen = []

        def classify(name, text, *, rules=None, **options):
            seen.append(options)
            return {"role": "main" if "PETI" in name else "supporting", "type": "Petição inicial", "method": "x"}

        with mock.patch("jurisync.workflows.filing.prepare.classify_document", classify):
            self.prepare()
        self.assertTrue(seen and all(options == {"jev": False} for options in seen))


if __name__ == "__main__":
    unittest.main()
