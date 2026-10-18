import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jurisync.connectors.pje.filing.store import LocalRunStore
from jurisync.workflows.filing.prepare import finalize_review, prepare_folder
from jurisync.workflows.filing.profile import load_profile

PROFILE = load_profile("victor-trf1")
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
            for name in ("10-PETIÇÃO INICIAL.pdf", "2-PROCURAÇÃO.pdf", "5-PA.pdf", "6-DOCUMENTO DE IDENTIFICAÇÃO.pdf"):
                (folder / name).write_bytes(b"%PDF-1.4\n" + name.encode())
            store = LocalRunStore(Path(tmp, "run1")).ensure()
            result = prepare_folder(folder, FACTS, store, profile=PROFILE, jev=False)
            self.assertTrue(result["payloadWritten"], result["review"])
            payload = store.read_json("payload.json")
            self.assertEqual(payload["portal"], "trf1-1g")
            self.assertEqual(payload["classification"]["area"], "DIREITO ASSISTENCIAL")
            self.assertEqual(
                [item["type"] for item in payload["documents"]["supporting"]],
                ["Procuração", "Processo administrativo", "Documentos Diversos"],
            )
            self.assertNotIn("otherParticipants", payload)
            self.assertIs(payload["characteristics"]["priority"], False)
            rows = {row["name"]: row["id"] for row in store.read_json("review.json")["files"]}
            with self.assertRaisesRegex(ValueError, "Invalid PJe type"):
                finalize_review(store, FACTS, {rows["5-PA.pdf"]: {"type": "Processo Administrativo"}}, profile=PROFILE)


if __name__ == "__main__":
    unittest.main()
