import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path

from jurisync.connectors.pje.filing.approval import approve, require_approval
from jurisync.connectors.pje.filing.payload import resolve_documents, validate_fill_payload
from jurisync.connectors.pje.filing.store import LocalRunStore
from jurisync.workflows.filing.classify import (
    ask_jev,
    claim_value_candidates,
    classify_document,
    cpf_candidates,
    decision_outcome,
    document_kind,
)
from jurisync.workflows.filing.prepare import (
    apply_supporting_fallback,
    assemble,
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
            "0-PA.pdf": "Processo Administrativo",
            "11-PETÇÃO INICIAL_8308.docx": "petition",
            "1-INDEFERIMENTO_9328.pdf": "Carta de Indeferimento",
            "2-CERTIDÃO DE ÓBITO E DOCUMENTOS_6017.pdf": "Documento Comprobatório",
            "3-PROVAS DA DEPENDENCIA ECONÔMICA_5512.pdf": "Documento Comprobatório",
            "71- DOCUMENTO DE IDENTIFICIAÇÃO DA REPRESENTANDE_6267.pdf": "Documento Comprobatório",
            "Quesitos Auxilio Doença_8587.pdf": "Outros Documentos",
            "Inicial BPC - Francisley Damasceno.pdf": "petition",
            "Emenda a Inicial.docx": "unknown",
            "receitas.pdf": "medical",
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
        self.assertEqual(
            claim_value_candidates("Atribui-se à causa o valor de R$ 41.631 (quarenta e um mil"), ["41.631,00"]
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
    def test_names_lose_accents_and_unsafe_characters_but_keep_order_and_volumes(self):
        expected = {
            "10-PETIÇÃO INICIAL.pdf": "10-PETICAO INICIAL.pdf",
            "2-LAUDOS MÉDICOS E EXAMES.pdf": "2-LAUDOS MEDICOS E EXAMES.pdf",
            "6-DOCUMENTO DE IDENTIFICAÇÃO.pdf": "6-DOCUMENTO DE IDENTIFICACAO.pdf",
            "0-PA.pdf": "0-PA.pdf",
            "3-PROVAS DA ATIVIDADE RURAL-vol-02.pdf": "3-PROVAS DA ATIVIDADE RURAL-vol-02.pdf",
            "8-PROCURAÇÃO #1 & final.docx": "8-PROCURACAO 1 final.pdf",
            "çãõ.pdf": "cao.pdf",
            "???.pdf": "documento.pdf",
        }
        for name, clean in expected.items():
            self.assertEqual(upload_filename(name), clean, name)

    def test_assembly_numbers_files_in_filing_order_with_unique_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LocalRunStore(tmp)

            def work(name, content):
                store.write_bytes(f"work/files/{name}", content)
                return {"path": f"work/files/{name}", "uploadSha256": hashlib.sha256(content).hexdigest()}

            rows = [
                {
                    "id": "a",
                    "name": "3-A.pdf",
                    "role": "supporting",
                    "type": "Outros Documentos",
                    **work("a", b"%PDF-1"),
                },
                {
                    "id": "b",
                    "name": "Ã.pdf",
                    "role": "supporting",
                    "type": "Outros Documentos",
                    **work("b", b"%PDF-2"),
                },
                {
                    "id": "p",
                    "name": "10-PETIÇÃO INICIAL.pdf",
                    "role": "main",
                    "type": "Petição inicial",
                    **work("p", b"%PDF-p"),
                },
                {
                    "id": "c",
                    "name": "B.pdf",
                    "role": "supporting",
                    "type": "Laudo Médico",
                    **work("c", b"%PDF-big"),
                    "parts": [work("c1", b"%PDF-vol1"), work("c2", b"%PDF-vol2")],
                },
            ]
            store.write_bytes("documents/99-stale.pdf", b"%PDF-old")
            documents = assemble(store, rows)
            items = [documents["main"], *documents["supporting"]]
            self.assertEqual(
                [item["path"] for item in items],
                [
                    "documents/01-PETICAO INICIAL.pdf",
                    "documents/02-A.pdf",
                    "documents/03-A.pdf",
                    "documents/04-B-vol-01.pdf",
                    "documents/05-B-vol-02.pdf",
                ],
            )
            self.assertEqual(store.list("documents"), sorted(item["path"] for item in items))
            self.assertEqual(store.read_bytes("documents/03-A.pdf"), b"%PDF-2")
            self.assertEqual((items[3]["number"], items[3]["description"]), ("1/2", "B - volume 1/2"))
            manifest = store.read_json("manifest.json")
            self.assertEqual([entry["reviewId"] for entry in manifest["documents"]], ["p", "a", "b", "c", "c"])
            self.assertEqual(manifest["documents"][0]["sha256"], items[0]["sha256"])
            self.assertEqual(Path(tmp, "documents").stat().st_mode & 0o777, 0o700)
            self.assertEqual(Path(tmp, "documents/02-A.pdf").stat().st_mode & 0o777, 0o600)
            store.write_bytes("work/files/b", b"%PDF-2 edited")
            with self.assertRaisesRegex(ValueError, "Document changed: Ã.pdf"):
                assemble(store, rows)


class PayloadTest(unittest.TestCase):
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
        self.assertNotIn("attorneys", payload, "without extra attorneys only the logged-in one files")
        self.assertTrue(payload["characteristics"]["legalAid"])
        self.assertEqual(payload["characteristics"]["claimValue"], "5.000,00")
        self.assertEqual(payload["classification"]["jurisdiction"], "PE / Petrolina")
        with self.assertRaisesRegex(ValueError, "claimValue"):
            build_payload({**FACTS, "characteristics": {"claimValue": "0"}}, {}, PROFILE.defaults())

    def test_extra_attorneys_come_from_the_case_facts(self):
        graziele = {"cpf": "111.222.333-44", "name": "GRAZIELE ALVES DA SILVA", "oabNumber": "48647", "oabState": "PE"}
        other = {"cpf": "55566677788", "name": "OUTRA ADVOGADA", "oabNumber": "1234", "oabState": "PB"}
        payload = build_payload({**FACTS, "attorneys": [graziele, other]}, {}, PROFILE.defaults())
        self.assertEqual(payload["attorneys"], [{**graziele, "cpf": "11122233344"}, other])
        bad = {
            "needs exactly cpf": [{"cpf": "11122233344", "name": "X"}],
            "11 digits": [{**graziele, "cpf": "123"}],
            "two-letter UF": [{**graziele, "oabState": "Pernambuco"}],
            "repeats": [graziele, graziele],
        }
        for message, attorneys in bad.items():
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                build_payload({**FACTS, "attorneys": attorneys}, {}, PROFILE.defaults())
        with self.assertRaisesRegex(ValueError, "Use attorneys"):
            build_payload({**FACTS, "attorney": graziele}, {}, PROFILE.defaults())

    def test_the_legal_representative_goes_with_the_author(self):
        mother = {"cpf": "999.888.777-66", "name": "MARIA MAE", "kind": "mae"}
        facts = {**FACTS, "author": {"name": "EXAMPLE", "birthDate": "01/02/2015", "representative": mother}}
        payload = build_payload(facts, {}, PROFILE.defaults())
        self.assertEqual(payload["author"], {"name": "EXAMPLE", "representative": {**mother, "cpf": "99988877766"}})
        with self.assertRaisesRegex(ValueError, "kind must be one of"):
            build_payload(
                {**facts, "author": {"name": "X", "representative": {**mother, "kind": "avó"}}}, {}, PROFILE.defaults()
            )

    def test_priorities_are_the_profiles_fixed_ones_plus_the_cases_or_the_default(self):
        defaults = {
            **PROFILE.defaults(),
            "characteristics": {"legalAid": True, "injunction": False, "priority": "Juízo 100% Digital"},
        }
        self.assertEqual(build_payload(FACTS, {}, defaults)["characteristics"]["priority"], ["Juízo 100% Digital"])
        defaults["characteristics"]["defaultPriority"] = ["Pessoa com deficiência"]
        self.assertEqual(
            build_payload(FACTS, {}, defaults)["characteristics"]["priority"],
            ["Juízo 100% Digital", "Pessoa com deficiência"],
        )
        idoso = {**FACTS, "characteristics": {**FACTS["characteristics"], "priority": ["Idoso(a)"]}}
        self.assertEqual(
            build_payload(idoso, {}, defaults)["characteristics"]["priority"], ["Juízo 100% Digital", "Idoso(a)"]
        )
        self.assertNotIn("defaultPriority", build_payload(idoso, {}, defaults)["characteristics"])
        for bad in ({"priority": [""]}, {"secrecy": True}, {"digitalCourt": "sim"}):
            facts = {**FACTS, "characteristics": {**FACTS["characteristics"], **bad}}
            with self.subTest(bad), self.assertRaises(ValueError):
                build_payload(facts, {}, defaults)

    def test_the_representative_may_carry_an_address(self):
        mother = {"cpf": "99988877766", "name": "M", "kind": "mae", "address": {"cep": "68900-000", "numero": "12"}}
        payload = build_payload({**FACTS, "author": {"name": "E", "representative": mother}}, {}, PROFILE.defaults())
        self.assertEqual(payload["author"]["representative"]["address"], {"cep": "68900-000", "numero": "12"})
        for address in ({"cep": "1"}, {"cep": "68900-000"}):
            with self.subTest(address), self.assertRaisesRegex(ValueError, "representative.address"):
                build_payload(
                    {**FACTS, "author": {"name": "E", "representative": {**mother, "address": address}}},
                    {},
                    PROFILE.defaults(),
                )

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

    def run_with_review(self, tmp):
        store = LocalRunStore(Path(tmp, "run1")).ensure()
        petition, supporting = b"%PDF-1.4\nfixture", b"%PDF-1.4\nsupporting fixture"
        store.write_bytes("work/files/petition.pdf", petition)
        store.write_bytes("work/files/supporting.pdf", supporting)
        digest = lambda content: hashlib.sha256(content).hexdigest()
        store.write_json(
            "review.json",
            {
                "files": [
                    {
                        "id": "file:0",
                        "name": "petition.pdf",
                        "path": "work/files/petition.pdf",
                        "sha256": digest(petition),
                        "uploadSha256": digest(petition),
                        "role": "main",
                        "type": "Petição inicial",
                        "review": "Choose main",
                    },
                    {
                        "id": "file:1",
                        "name": "supporting.pdf",
                        "path": "work/files/supporting.pdf",
                        "sha256": digest(supporting),
                        "uploadSha256": digest(supporting),
                        "role": "supporting",
                        "type": "Outros Documentos",
                        "fallbackReason": "No readable document text",
                    },
                ]
            },
        )
        return store, digest(petition), digest(supporting)

    def test_review_finalizer_requires_resolution_and_verifies_pdf_contents(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, petition_sha, supporting_sha = self.run_with_review(tmp)
            with self.assertRaisesRegex(ValueError, "needs review"):
                finalize_review(store, FACTS, profile=PROFILE)
            choice = {"file:0": {"role": "main", "type": "Petição inicial"}}
            result = finalize_review(store, FACTS, choice, profile=PROFILE)
            self.assertTrue(result["ready"])
            documents = store.read_json("payload.json")["documents"]
            self.assertEqual(
                documents["main"],
                {
                    "path": "documents/01-petition.pdf",
                    "type": "Petição inicial",
                    "description": "Petição inicial",
                    "sha256": petition_sha,
                },
            )
            self.assertEqual(
                documents["supporting"],
                [
                    {
                        "path": "documents/02-supporting.pdf",
                        "type": "Outros Documentos",
                        "description": "supporting",
                        "sha256": supporting_sha,
                    }
                ],
            )
            store.write_json("approval.json", {})
            described = {**choice, "file:1": {"type": "Laudo Médico", "description": "Laudo do CAPS"}}
            finalize_review(store, FACTS, described, profile=PROFILE)
            supporting_item = store.read_json("payload.json")["documents"]["supporting"][0]
            self.assertEqual(
                (supporting_item["type"], supporting_item["description"]), ("Laudo Médico", "Laudo do CAPS")
            )
            self.assertFalse(store.exists("approval.json"), "a new payload needs a new approval")
            for forbidden in ({"path": "/tmp/other.pdf"}, {"uploadSha256": "0" * 64}, {"role": "main", "sha256": "x"}):
                with self.assertRaisesRegex(ValueError, "cannot be changed"):
                    finalize_review(store, FACTS, {**choice, "file:1": forbidden}, profile=PROFILE)
            with self.assertRaisesRegex(ValueError, "main, supporting or skip"):
                finalize_review(store, FACTS, {**choice, "file:1": {"role": "other"}}, profile=PROFILE)
            with self.assertRaisesRegex(ValueError, "nope"):
                finalize_review(store, FACTS, {"nope": {"role": "skip"}}, profile=PROFILE)
            finalize_review(store, FACTS, {**choice, "file:1": {"role": "skip"}}, profile=PROFILE)
            self.assertEqual(store.list("documents"), ["documents/01-petition.pdf"], "skipped files leave documents/")
            self.assertEqual(Path(tmp, "run1", "payload.json").stat().st_mode & 0o777, 0o600)
            store.write_bytes("work/files/petition.pdf", b"%PDF-1.4\nchanged")
            with self.assertRaisesRegex(ValueError, "Document changed"):
                finalize_review(store, FACTS, choice, profile=PROFILE)
            self.assertFalse(store.exists("payload.json"), "a failed finalize leaves no payload behind")

    def test_a_run_with_a_draft_may_only_change_case_facts(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, _, _ = self.run_with_review(tmp)
            choice = {"file:0": {"role": "main", "type": "Petição inicial"}}
            finalize_review(store, FACTS, choice, profile=PROFILE)
            approve(store)
            store.write_json("checkpoint.json", {"draftId": "1"})
            assembled = {name: store.read_bytes(name) for name in store.list("documents")}
            finalize_review(store, {**FACTS, "author": {"name": "EXAMPLE FIXED"}}, choice, profile=PROFILE)
            self.assertEqual(store.read_json("payload.json")["author"]["name"], "EXAMPLE FIXED")
            self.assertFalse(store.exists("approval.json"))
            with self.assertRaisesRegex(ValueError, "documents can no longer change"):
                finalize_review(store, FACTS, {**choice, "file:1": {"role": "skip"}}, profile=PROFILE)
            self.assertEqual({name: store.read_bytes(name) for name in store.list("documents")}, assembled)
            with self.assertRaisesRegex(ValueError, "cannot be prepared again"):
                prepare_folder(Path(tmp), FACTS, store, profile=PROFILE)

    def test_a_run_copied_to_another_folder_stays_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, _, _ = self.run_with_review(tmp)
            finalize_review(store, FACTS, {"file:0": {"role": "main", "type": "Petição inicial"}}, profile=PROFILE)
            approve(store)
            payload_text = Path(tmp, "run1", "payload.json").read_text()
            self.assertNotIn(tmp, payload_text, "no absolute path in the payload")
            self.assertNotIn(tmp, Path(tmp, "run1", "manifest.json").read_text())
            shutil.copytree(Path(tmp, "run1"), Path(tmp, "elsewhere", "copied"))
            shutil.rmtree(Path(tmp, "run1"))
            copied = LocalRunStore(Path(tmp, "elsewhere", "copied"))
            require_approval(copied)
            payload = resolve_documents(copied.read_json("payload.json"), copied)
            validate_fill_payload(payload)
            self.assertEqual(
                Path(payload["documents"]["main"]["path"]),
                Path(tmp, "elsewhere", "copied").resolve() / "documents/01-petition.pdf",
            )
            copied.write_bytes("documents/02-supporting.pdf", b"%PDF-1.4\nswapped")
            with self.assertRaisesRegex(ValueError, "changed since approval: 02-supporting.pdf"):
                validate_fill_payload(resolve_documents(copied.read_json("payload.json"), copied))


if __name__ == "__main__":
    unittest.main()
