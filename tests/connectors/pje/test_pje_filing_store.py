import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jurisync.connectors.pje.filing import run_stage
from jurisync.connectors.pje.filing.approval import approve
from jurisync.connectors.pje.filing.payload import resolve_documents
from jurisync.connectors.pje.filing.store import LocalRunStore, open_run

PAYLOAD = {
    "portal": "trf5-1g",
    "subject": {"search": "s", "code": "1", "name": "S"},
    "authorCpf": "12345678909",
    "author": {"name": "EXAMPLE"},
}


class LocalRunStoreTests(unittest.TestCase):
    def test_names_stay_inside_the_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LocalRunStore(Path(tmp, "run")).ensure()
            for name in ("/etc/passwd", "../other/payload.json", "documents/../../x", "a//b", "", "a\\b"):
                with self.subTest(name), self.assertRaises(ValueError):
                    store.write_bytes(name, b"x")
            with self.assertRaisesRegex(ValueError, "outside the run"):
                store.name_of(Path(tmp, "elsewhere.pdf"))
            self.assertEqual(store.name_of(store.workspace("work/files") / "a.pdf"), "work/files/a.pdf")

    def test_files_are_private_and_written_whole(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LocalRunStore(Path(tmp, "run")).ensure()
            store.write_bytes("documents/01-A.pdf", b"%PDF-1")
            store.write_json("payload.json", {"a": 1})
            root = Path(tmp, "run")
            self.assertEqual(root.stat().st_mode & 0o777, 0o700)
            self.assertEqual(root.joinpath("documents").stat().st_mode & 0o777, 0o700)
            self.assertEqual(root.joinpath("documents/01-A.pdf").stat().st_mode & 0o777, 0o600)
            self.assertEqual(store.list("documents"), ["documents/01-A.pdf"])
            self.assertEqual(store.list("missing"), [])
            self.assertEqual(sorted(path.name for path in root.iterdir()), ["documents", "payload.json"])
            store.delete_tree("documents")
            store.delete("payload.json")
            self.assertEqual(list(root.iterdir()), [])

    def test_open_run_uses_the_shared_filing_root(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict("os.environ", {"JURISYNC_FILING_DIR": tmp}):
            self.assertEqual(open_run("run1").root, Path(tmp).resolve() / "run1")


class RelativePayloadTests(unittest.TestCase):
    def run_store(self, tmp, path):
        store = LocalRunStore(Path(tmp, "run")).ensure()
        store.write_bytes("documents/01-PETICAO.pdf", b"%PDF-1")
        sha = hashlib.sha256(b"%PDF-1").hexdigest()
        main = {"path": path, "type": "Petição inicial", "sha256": sha}
        store.write_json("payload.json", {**PAYLOAD, "documents": {"main": main}})
        return store

    def test_relative_paths_resolve_inside_the_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.run_store(tmp, "documents/01-PETICAO.pdf")
            resolved = resolve_documents(store.read_json("payload.json"), store)
            self.assertEqual(resolved["documents"]["main"]["path"], str(store.root / "documents/01-PETICAO.pdf"))
            for bad in ("work/files/a.pdf", "documents/../payload.json"):
                payload = {"documents": {"main": {"path": bad}}}
                with self.subTest(bad), self.assertRaises(ValueError):
                    resolve_documents(payload, store)

    def test_an_old_payload_with_absolute_paths_is_refused_clearly_but_status_still_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            absolute = str(Path(tmp, "run", "documents", "01-PETICAO.pdf"))
            store = self.run_store(tmp, absolute)
            message = "absolute.*written before filing runs were self-contained.*filing_finalize again"
            with self.assertRaisesRegex(ValueError, message):
                approve(store)
            login = mock.patch("jurisync.connectors.pje.filing.run._login", side_effect=AssertionError)
            with login, self.assertRaisesRegex(ValueError, message):
                run_stage("fill", run=store)
            store.write_json("checkpoint.json", {"draftId": "1", "status": "complete"})
            status = run_stage("status", run=store)
            self.assertEqual((status["draftId"], status["state"]), ("1", "complete"))
            self.assertNotIn("phases", status)
            self.assertNotIn(tmp, json.dumps(store.read_json("checkpoint.json")))


if __name__ == "__main__":
    unittest.main()
