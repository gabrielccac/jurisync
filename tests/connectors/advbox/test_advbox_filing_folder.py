import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jurisync.connectors.advbox.filing_folder import export_filing_folder, protocol_folder
from jurisync.connectors.pje.filing.store import LocalRunStore
from jurisync.workflows.filing.contract import validate_folder
from jurisync.workflows.filing.prepare import prepare_folder
from jurisync.workflows.filing.profile import load_profile

FACTS = {
    "authorCpf": "12345678909",
    "author": {"name": "EXAMPLE"},
    "classification": {"jurisdiction": "PE / Petrolina", "judicialClass": "CLASS"},
    "subject": {"search": "TEST", "code": "1", "name": "TEST"},
    "characteristics": {"claimValue": "5.000,00"},
    "fiscalDaLei": False,
}


class Folder:
    """ADVBOX documents as the connector returns them, without the network."""

    def __init__(self, folders=("00 - PROTOCOLAR",)):
        self.folders = folders

    def list_customer_files(self, customer_id, path=None):
        if path is None:
            return {"folders": [{"path": name} for name in self.folders]}
        return {
            "files": [
                {"name": "10-PETIÇÃO INICIAL_1111.pdf", "url": "u1", "fileId": "91", "uploadedAt": "01/10/2026"},
                {"name": "2-PROCURAÇÃO_2222.pdf", "url": "u2", "fileId": "92"},
            ]
        }

    def download_file(self, url, *, filename, directory, inspect):
        target = Path(directory, filename)
        target.write_bytes(b"%PDF-1 " + url.encode())
        return {"path": str(target)}


class AdvboxFilingFolderTests(unittest.TestCase):
    def test_the_export_is_a_contract_folder_with_advbox_origins(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = export_filing_folder(Folder(), "7", Path(tmp, "input"))
            folder = Path(result["sourceFolder"])
            checked = validate_folder(folder)
            self.assertEqual(checked["files"], ["10-PETIÇÃO INICIAL.pdf", "2-PROCURAÇÃO.pdf"])
            self.assertEqual(
                checked["sources"]["10-PETIÇÃO INICIAL.pdf"],
                {
                    "origin": "advbox",
                    "originId": "91",
                    "originPath": "00 - PROTOCOLAR",
                    "originalName": "10-PETIÇÃO INICIAL_1111.pdf",
                    "modifiedAt": "01/10/2026",
                },
            )
            self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
            self.assertEqual((folder / "sources.json").stat().st_mode & 0o777, 0o600)
            with self.assertRaisesRegex(ValueError, "new or empty"):
                export_filing_folder(Folder(), "7", folder)

    def test_the_protocol_folder_is_chosen_by_the_office_convention(self):
        self.assertEqual(protocol_folder(Folder(("Outros", "00 - PROTOCOLAR JUD")), "7"), "00 - PROTOCOLAR JUD")
        with self.assertRaisesRegex(ValueError, "No 00 - PROTOCOLAR folder"):
            protocol_folder(Folder(("Outros",)), "7")

    def test_victors_flow_is_export_then_prepare_folder(self):
        profile = load_profile("victor")
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"VICTOR_PJE_ATTORNEY_CPF": "12345678909"}),
            mock.patch("jurisync.workflows.filing.prepare._petition_candidates", lambda path: ([], [])),
        ):
            source = export_filing_folder(Folder(), "7", Path(tmp, "input"))["sourceFolder"]
            store = LocalRunStore(Path(tmp, "run1")).ensure()
            result = prepare_folder(source, FACTS, store, profile=profile, jev=False)
            self.assertTrue(result["payloadWritten"], result["review"])
            rows = store.read_json("review.json")["files"]
            self.assertEqual([row["path"] for row in rows], ["inbox/10-PETIÇÃO INICIAL.pdf", "inbox/2-PROCURAÇÃO.pdf"])
            self.assertEqual(rows[0]["origin"]["kind"], "declared")
            self.assertEqual((rows[0]["origin"]["source"], rows[0]["origin"]["originId"]), ("advbox", "91"))
            self.assertEqual(rows[0]["sha256"], hashlib.sha256(b"%PDF-1 u1").hexdigest())
            documents = store.read_json("payload.json")["documents"]
            self.assertEqual(
                [documents["main"]["path"], *(item["path"] for item in documents["supporting"])],
                ["documents/01-PETICAO INICIAL.pdf", "documents/02-PROCURACAO.pdf"],
            )
            self.assertNotIn(tmp, json.dumps(store.read_json("review.json")))


if __name__ == "__main__":
    unittest.main()
