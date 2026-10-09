import hashlib
import json
import os
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
                )
            )
            with self.assertRaisesRegex(ValueError, "needs review"):
                finalize_review(report, FACTS, profile=PROFILE)
            choice = {"file:0": {"role": "main", "type": "Petição inicial"}}
            result = finalize_review(report, FACTS, choice, profile=PROFILE)
            self.assertTrue(result["ready"])
            documents = json.loads(Path(result["payloadPath"]).read_text())["documents"]
            self.assertEqual(documents["main"]["path"], str(petition))
            self.assertEqual(
                documents["supporting"],
                [{"path": str(supporting), "type": "Outros Documentos", "description": "supporting"}],
            )
            self.assertEqual(oct(Path(result["payloadPath"]).stat().st_mode & 0o777), "0o600")
            petition.write_bytes(b"%PDF-1.4\nchanged")
            with self.assertRaisesRegex(ValueError, "Document changed"):
                finalize_review(report, FACTS, choice, profile=PROFILE)


if __name__ == "__main__":
    unittest.main()
