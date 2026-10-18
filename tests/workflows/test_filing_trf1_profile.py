import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jurisync.connectors.pje.filing.store import LocalRunStore
from jurisync.workflows.filing.prepare import finalize_review, prepare_folder
from jurisync.workflows.filing.profile import load_profile

PROFILE = load_profile("jean")
FACTS = {
    "authorCpf": "12345678909",
    "author": {"name": "EXAMPLE"},
    "classification": {
        "jurisdiction": "Seção Judiciária do Amapá",
        "judicialClass": "PROCEDIMENTO DO JUIZADO ESPECIAL CÍVEL (436)",
    },
    "subject": {"search": "Pessoa com Deficiência", "code": "11946", "name": "Pessoa com Deficiência"},
    "characteristics": {"claimValue": "5.000,00"},
}


class Trf1PrepareTests(unittest.TestCase):
    def test_types_become_trf1_labels_and_the_payload_targets_trf1(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch("jurisync.workflows.filing.prepare._petition_candidates", lambda path: ([], [], [])),
        ):
            folder = Path(tmp, "input")
            folder.mkdir()
            names = (
                "10-PETIÇÃO INICIAL.pdf",
                "2-PROCURAÇÃO.pdf",
                "3-COMPROVANTE DE RESIDÊNCIA.pdf",
                "4-FOLHA RESUMO CADÚNICO.pdf",
                "5-PA.pdf",
                "6-DOCUMENTO DE IDENTIFICAÇÃO.pdf",
                "7-LAUDOS MÉDICOS.pdf",
            )
            for name in names:
                (folder / name).write_bytes(b"%PDF-1.4\n" + name.encode())
            store = LocalRunStore(Path(tmp, "run1")).ensure()
            result = prepare_folder(folder, FACTS, store, profile=PROFILE, jev=False)
            self.assertTrue(result["payloadWritten"], result["review"])
            payload = store.read_json("payload.json")
            self.assertEqual(payload["portal"], "trf1-1g")
            self.assertEqual(
                payload["classification"],
                {
                    "area": "DIREITO PREVIDENCIÁRIO",
                    "jurisdiction": "Seção Judiciária do Amapá",
                    "judicialClass": "PROCEDIMENTO DO JUIZADO ESPECIAL CÍVEL (436)",
                },
            )
            self.assertEqual([subject["code"] for subject in payload["subjects"]], ["11946"])
            self.assertEqual(
                [item["type"] for item in payload["documents"]["supporting"]],
                [
                    "Procuração",
                    "Comprovante de residência",
                    "Cadastro Nacional de Informações Sociais - CNIS",
                    "Processo administrativo",
                    "Cédula de Identidade",  # rótulo da lista de tipos de anexo do TRF1
                    "Atestado médico",
                ],
            )
            self.assertNotIn("otherParticipants", payload)
            self.assertNotIn("attorneys", payload)
            self.assertEqual(
                payload["characteristics"],
                {
                    "legalAid": True,
                    "injunction": True,
                    "secrecy": False,
                    "digitalCourt": True,
                    "claimValue": "5.000,00",
                    "priority": ["Pessoa com deficiência"],
                },
            )
            rows = {row["name"]: row["id"] for row in store.read_json("review.json")["files"]}
            with self.assertRaisesRegex(ValueError, "Invalid PJe type"):
                finalize_review(store, FACTS, {rows["5-PA.pdf"]: {"type": "Processo Administrativo"}}, profile=PROFILE)


if __name__ == "__main__":
    unittest.main()


class Trf1SecondDegreeProfileTests(unittest.TestCase):
    def test_class_reference_process_and_party_roles_come_with_the_case(self):
        from jurisync.workflows.filing.prepare import build_payload

        profile = load_profile("jean-trf1-2g")
        facts = {
            **FACTS,
            "classification": {
                "judicialClass": "AGRAVO DE INSTRUMENTO (202)",
                "referenceProcess": "1001234-56.2026.4.01.3100",
            },
            "subject": {"search": "Pessoa com Deficiência", "code": "11946", "name": "Pessoa com Deficiência"},
            "partyRoles": {"active": "AGRAVANTE", "passive": "AGRAVADO"},
        }
        payload = build_payload(facts, {}, profile.defaults())
        self.assertEqual(payload["portal"], "trf1-2g")
        self.assertEqual(
            payload["classification"],
            {
                "area": "DIREITO PREVIDENCIÁRIO",
                "jurisdiction": "Turmas Recursais dos Juizados Especiais Federais da SJAP e da SJPA",
                "judicialClass": "AGRAVO DE INSTRUMENTO (202)",
                "referenceProcess": "1001234-56.2026.4.01.3100",
            },
        )
        self.assertEqual(payload["partyRoles"], {"active": "AGRAVANTE", "passive": "AGRAVADO"})
        self.assertNotIn("digitalCourt", payload["characteristics"])
        for bad in (
            {"classification": {**facts["classification"], "referenceProcess": "123"}},
            {"partyRoles": {"autor": "X"}},
        ):
            with self.subTest(bad), self.assertRaises(ValueError):
                build_payload({**facts, **bad}, {}, profile.defaults())
        with self.assertRaisesRegex(ValueError, "judicialClass"):
            build_payload({**facts, "classification": {}}, {}, profile.defaults())
