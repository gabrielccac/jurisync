import hashlib
import io
import json
import os
import random
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from jurisync.connectors.pje.filing.approval import approve
from jurisync.connectors.pje.filing.store import LocalRunStore
from jurisync.workflows.filing import server as filing_server
from jurisync.workflows.filing.contract import load_sources
from jurisync.workflows.filing.images import image_to_pdf
from jurisync.workflows.filing.inbox import fill_from_folder, read_inbox
from jurisync.workflows.filing.prepare import finalize_review, prepare_folder
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
PETITION = b"%PDF-1.4\npetition"


def sha(content):
    return hashlib.sha256(content).hexdigest()


def png(mode="RGBA", size=(40, 30)):
    buffer = io.BytesIO()
    Image.new(mode, size, (200, 10, 10, 128) if mode == "RGBA" else (200, 10, 10)).save(buffer, "PNG")
    return buffer.getvalue()


class SourceFolderTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.folder = self.tmp / "gathered"
        (self.folder / "laudos").mkdir(parents=True)
        (self.folder / "10-PETIÇÃO INICIAL.pdf").write_bytes(PETITION)
        (self.folder / "laudos" / "LAUDO MÉDICO.pdf").write_bytes(b"%PDF-1.4\nlaudo")
        (self.folder / ".DS_Store").write_bytes(b"ignored")
        self.store = LocalRunStore(self.tmp / "run1").ensure()

    def sources(self, files, version=1):
        (self.folder / "sources.json").write_text(json.dumps({"version": version, "files": files}))

    def test_declared_and_local_origins_with_the_hash_of_each_copy(self):
        drive = {
            "file": "laudos/LAUDO MÉDICO.pdf",
            "origin": "drive",
            "originId": "1AbC",
            "originPath": "Clientes/Maria/Laudos",
            "originalName": "Laudo CAPS.pdf",
            "modifiedAt": "2026-10-01T12:00:00Z",
            "link": "https://drive.google.com/file/d/1AbC",
        }
        self.sources([drive])
        inbox = fill_from_folder(self.store, self.folder)
        by_name = {entry["name"]: entry for entry in inbox["files"]}
        self.assertEqual(set(by_name), {"10-PETIÇÃO INICIAL.pdf", "LAUDO MÉDICO.pdf"})
        laudo = by_name["LAUDO MÉDICO.pdf"]
        declared = {key: value for key, value in drive.items() if key != "origin"}
        self.assertEqual(laudo["origin"], {"kind": "declared", "source": "drive", **declared})
        self.assertEqual(laudo["sha256"], sha(b"%PDF-1.4\nlaudo"))
        self.assertEqual(self.store.read_bytes(laudo["path"]), b"%PDF-1.4\nlaudo")
        petition = by_name["10-PETIÇÃO INICIAL.pdf"]
        self.assertEqual(petition["origin"], {"kind": "local", "source": "local", "file": "10-PETIÇÃO INICIAL.pdf"})
        self.assertNotIn(str(self.tmp), json.dumps(self.store.read_json("inbox.json")), "no local path recorded")
        self.assertEqual((self.tmp / "run1" / "inbox.json").stat().st_mode & 0o777, 0o600)
        self.store.write_bytes(petition["path"], b"%PDF-1.4\nswapped")
        with self.assertRaisesRegex(ValueError, "changed after it was copied: 10-PETIÇÃO INICIAL.pdf"):
            read_inbox(self.store)

    def test_invalid_sources_are_refused_before_anything_is_copied(self):
        good = {"file": "10-PETIÇÃO INICIAL.pdf", "origin": "gerid"}
        cases = {
            "version must be one of 1, 2": ([good], 3),
            "unknown fields: verifiedBy": ([{**good, "verifiedBy": "x"}], 1),
            "sha256 must be": ([{**good, "sha256": "abc"}], 2),
            "unknown fields: owner": ([{**good, "owner": "x"}], 1),
            "inside the folder": ([{**good, "file": "../secret.pdf"}], 1),
            "inside the folder,": ([{**good, "file": "/etc/passwd"}], 1),
            "described twice": ([good, good], 1),
            "not in the folder": ([{**good, "file": "missing.pdf"}], 1),
            "link must be": ([{**good, "link": "file:///etc/passwd"}], 1),
            "origin must be": ([{"file": good["file"]}], 1),
        }
        for message, (files, version) in cases.items():
            with self.subTest(message):
                self.sources(files, version)
                with self.assertRaisesRegex(ValueError, message.rstrip(",")):
                    fill_from_folder(self.store, self.folder)
                self.assertFalse(self.store.exists("inbox.json"))
        (self.folder / "sources.json").write_text("{not json")
        with self.assertRaisesRegex(ValueError, "not valid JSON"):
            load_sources(self.folder)

    def test_an_exported_file_must_still_be_what_its_exporter_wrote(self):
        laudo = {"file": "laudos/LAUDO MÉDICO.pdf", "origin": "drive", "verifiedBy": "jurisync.gdrive"}
        self.sources([{**laudo, "sha256": sha(b"%PDF-1.4\nlaudo")}], version=2)
        inbox = fill_from_folder(self.store, self.folder)
        entry = next(item for item in inbox["files"] if item["name"] == "LAUDO MÉDICO.pdf")
        self.assertEqual((entry["origin"]["kind"], entry["origin"]["verifiedBy"]), ("exported", "jurisync.gdrive"))
        self.sources([{**laudo, "sha256": sha(b"other bytes")}], version=2)
        with self.assertRaisesRegex(ValueError, "differs from the file its exporter wrote"):
            fill_from_folder(self.store, self.folder)

    def test_symlinks_and_relative_folders_are_refused(self):
        (self.folder / "link.pdf").symlink_to(self.folder / "10-PETIÇÃO INICIAL.pdf")
        with self.assertRaisesRegex(ValueError, "Symbolic links are not accepted"):
            fill_from_folder(self.store, self.folder)
        (self.folder / "link.pdf").unlink()
        with self.assertRaisesRegex(ValueError, "absolute path"):
            fill_from_folder(self.store, "gathered")


class ImageTests(unittest.TestCase):
    def test_png_with_transparency_and_jpg_become_one_page_pdfs(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, content in (("a.png", png()), ("b.jpg", None)):
                source = Path(tmp, name)
                if content is None:
                    Image.new("RGB", (50, 20), "blue").save(source, "JPEG")
                else:
                    source.write_bytes(content)
                target = image_to_pdf(source, Path(tmp, name + ".pdf"), 10_000_000)
                self.assertTrue(target.read_bytes().startswith(b"%PDF-"))

    def test_a_photo_over_the_limit_is_scaled_down_instead_of_split(self):
        with tempfile.TemporaryDirectory() as tmp:
            noise = random.Random(1).randbytes(600 * 600 * 3)
            source = Path(tmp, "photo.png")
            Image.frombytes("RGB", (600, 600), noise).save(source, "PNG")
            target = image_to_pdf(source, Path(tmp, "photo.pdf"), 150_000)
            self.assertLessEqual(target.stat().st_size, 150_000)
            with self.assertRaisesRegex(ValueError, "10 MB limit"):
                image_to_pdf(source, Path(tmp, "tiny.pdf"), 300)


class FolderToPayloadTests(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.dict(os.environ, {})
        patch.start()
        self.addCleanup(patch.stop)
        candidates = mock.patch("jurisync.workflows.filing.prepare._petition_candidates", lambda path: ([], [], []))
        candidates.start()
        self.addCleanup(candidates.stop)

    def test_every_assembled_document_traces_back_to_its_inbox_hash_and_origin(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp, "gathered")
            folder.mkdir()
            (folder / "10-PETIÇÃO INICIAL.pdf").write_bytes(PETITION)
            identity = png("RGB")
            (folder / "6-DOCUMENTO DE IDENTIFICAÇÃO.png").write_bytes(identity)
            (folder / "8-PROCURAÇÃO.heic").write_bytes(b"not decodable here")
            (folder / "sources.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "files": [{"file": "6-DOCUMENTO DE IDENTIFICAÇÃO.png", "origin": "gerid", "originId": "T-9"}],
                    }
                )
            )
            store = LocalRunStore(Path(tmp, "run1")).ensure()
            result = prepare_folder(folder, FACTS, store, profile=PROFILE, jev=False)
            self.assertFalse(result["payloadWritten"])
            rows = {row["name"]: row for row in store.read_json("review.json")["files"]}
            heic = rows["8-PROCURAÇÃO.heic"]
            self.assertIn("HEIC images need the optional pillow-heif package", heic["review"])
            image = rows["6-DOCUMENTO DE IDENTIFICAÇÃO.png"]
            self.assertEqual((image["derivedBy"], image["sha256"]), ("image-to-pdf", sha(identity)))
            self.assertNotEqual(image["uploadSha256"], image["sha256"])
            self.assertEqual(image["origin"]["source"], "gerid")

            finalize_review(store, FACTS, {heic["id"]: {"role": "skip"}}, profile=PROFILE)
            approval = approve(store)
            payload = store.read_json("payload.json")
            manifest = {entry["path"]: entry for entry in store.read_json("manifest.json")["documents"]}
            inbox = {entry["sha256"]: entry for entry in store.read_json("inbox.json")["files"]}
            approved = {item["sha256"] for item in approval["documents"]}
            for document in (payload["documents"]["main"], *payload["documents"]["supporting"]):
                entry = manifest[document["path"]]
                self.assertEqual(entry["sha256"], document["sha256"])
                self.assertEqual(sha(store.read_bytes(document["path"])), document["sha256"])
                self.assertIn(document["sha256"], approved)
                source = inbox[entry["inputSha256"]]
                self.assertEqual(source["origin"], entry["origin"])
            traced = {manifest[path]["inputSha256"]: manifest[path]["origin"]["source"] for path in manifest}
            self.assertEqual(traced, {sha(PETITION): "local", sha(identity): "gerid"})

            view = filing_server._payload_view(payload, store.read_json("manifest.json"))
            self.assertEqual([item["origin"]["source"] for item in view["documents"]], ["local", "gerid"])
            self.assertNotIn(tmp, json.dumps(view))
            self.assertNotIn("sha256", json.dumps(view))


if __name__ == "__main__":
    unittest.main()
