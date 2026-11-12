import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jurisync.connectors.advbox.documents import AdvboxDocuments
from jurisync.core.documents_dir import resolve_saved_folder, save_folder_file


class FakeSession:
    def using(self, handler):
        return handler(self)


class FakeClient:
    session = FakeSession()


def listing(files=(), folders=()):
    return {
        "files": [{"name": name, "url": f"https://app.advbox.com.br/files/{name}"} for name in files],
        "folders": [{"path": path} for path in folders],
    }


class SaveFolderTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        env = mock.patch.dict(os.environ, {"JURISYNC_DOCUMENTS_DIR": folder.name})
        env.start()
        self.addCleanup(env.stop)
        self.documents = AdvboxDocuments(FakeClient())
        self.tree = {
            "": listing(folders=["00-PROTOCOLAR", "Outros"]),
            "00-PROTOCOLAR": listing(["Petição inicial.docx", "Procuração.pdf"], ["00-PROTOCOLAR/Anexos"]),
            "00-PROTOCOLAR/Anexos": listing(["Laudo.pdf"]),
        }
        patcher = mock.patch.object(
            self.documents, "list_customer_files", side_effect=lambda customer, path="": self.tree[path]
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        fetch = mock.patch.object(self.documents, "_fetch_bytes", side_effect=lambda url, label, limit: url.encode())
        fetch.start()
        self.addCleanup(fetch.stop)

    def test_files_are_saved_unparsed_with_their_names(self):
        result = self.documents.save_folder("1", "00-PROTOCOLAR", "run1")
        self.assertEqual([f["name"] for f in result["files"]], ["Petição inicial.docx", "Procuração.pdf", "Laudo.pdf"])
        self.assertEqual((result["savedInto"], result["truncated"]), ("run1", False))
        _, files = resolve_saved_folder("run1")
        self.assertEqual(sorted(p.name for p in files), ["Laudo.pdf", "Petição inicial.docx", "Procuração.pdf"])

    def test_a_repeated_name_is_suffixed_not_overwritten(self):
        self.documents.save_folder("1", "00-PROTOCOLAR", "run1")
        again = self.documents.save_folder("1", "00-PROTOCOLAR", "run1")
        self.assertIn("Laudo-1.pdf", [f["name"] for f in again["files"]])
        self.assertEqual(len(resolve_saved_folder("run1")[1]), 6)

    def test_limits_and_unknown_folder(self):
        result = self.documents.save_folder("1", "00-PROTOCOLAR", "run2", max_files=1)
        self.assertEqual((len(result["files"]), result["truncated"]), (1, True))
        with self.assertRaisesRegex(ValueError, "folder not found"):
            self.documents.save_folder("1", "Inexistente", "run3")


class SavedFolderTest(unittest.TestCase):
    def test_names_are_confined_to_the_shared_folder(self):
        with tempfile.TemporaryDirectory() as root, mock.patch.dict(os.environ, {"JURISYNC_DOCUMENTS_DIR": root}):
            for folder in ("../x", "a/b", ".hidden", "", "x" * 200):
                with self.subTest(folder=folder), self.assertRaises(ValueError):
                    save_folder_file(folder, "a.pdf", b"x")
            for name in ("../a.pdf", ".env", "a/b.pdf", "", "a\x00.pdf"):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    save_folder_file("run", name, b"x")
            save_folder_file("run", "Procuração (1).pdf", b"x")
            (Path(root) / "link").symlink_to(Path(root) / "run")
            with self.assertRaises(FileNotFoundError):
                resolve_saved_folder("link")
            with self.assertRaises(FileNotFoundError):
                resolve_saved_folder("missing")
            (Path(root) / "run" / "evil.pdf").symlink_to("/etc/passwd")
            self.assertEqual([p.name for p in resolve_saved_folder("run")[1]], ["Procuração (1).pdf"])


if __name__ == "__main__":
    unittest.main()
