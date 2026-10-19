import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from jurisync.workflows.filing.cleanup import cleanup_all, cleanup_run


class CleanupTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {"JURISYNC_FILING_DIR": tmp.name, "JURISYNC_FILING_RETENTION_DAYS": "30"})
        env.start()
        self.addCleanup(env.stop)
        self.root = Path(tmp.name)

    def make_run(self, run_id, *, input_folder=None, draft=False, days_old=0):
        root = self.root / "runs" / run_id
        root.mkdir(parents=True)
        inbox = {"files": [], **({"inputFolder": input_folder} if input_folder else {})}
        root.joinpath("inbox.json").write_text(json.dumps(inbox))
        if draft:
            root.joinpath("checkpoint.json").write_text(json.dumps({"draftId": "15508678"}))
        if input_folder:
            self.root.joinpath("inputs", input_folder).mkdir(parents=True, exist_ok=True)
        self.age(root, days_old)
        return root

    @staticmethod
    def age(folder, days):
        stamp = time.time() - days * 86400
        for path in (folder, *folder.rglob("*")):
            os.utime(path, (stamp, stamp))

    def test_a_pending_draft_is_kept_until_the_user_confirms(self):
        root = self.make_run("case1", input_folder="drive-1", draft=True, days_old=90)
        kept = cleanup_run("case1", draft_pending=lambda run_id: True)
        self.assertFalse(kept["removed"])
        self.assertIn("confirmPendingDraft", kept["reason"])
        self.assertTrue(root.exists())
        removed = cleanup_run("case1", draft_pending=lambda run_id: True, confirm_pending=True)
        self.assertEqual((removed["removed"], removed["inputFolderRemoved"]), (True, "drive-1"))
        self.assertFalse(root.exists() or self.root.joinpath("inputs", "drive-1").exists())

    def test_a_protocolled_draft_goes_at_once_and_a_run_without_draft_after_the_retention(self):
        self.make_run("filed", draft=True)
        self.assertTrue(cleanup_run("filed", draft_pending=lambda run_id: False)["removed"])
        self.make_run("fresh", days_old=3)
        self.assertFalse(cleanup_run("fresh", draft_pending=None)["removed"])
        self.assertTrue(cleanup_run("fresh", draft_pending=None, older_than_days=2)["removed"])

    def test_a_running_fill_and_a_shared_input_are_left_alone(self):
        self.make_run("busy", draft=True, days_old=90)
        self.assertEqual(
            cleanup_run("busy", draft_pending=None, running=True)["reason"], "a fill is running for this run"
        )
        self.make_run("a", input_folder="shared", days_old=90)
        self.make_run("b", input_folder="shared")
        removed = cleanup_run("a", draft_pending=None)
        self.assertTrue(removed["removed"])
        self.assertNotIn("inputFolderRemoved", removed)
        self.assertTrue(self.root.joinpath("inputs", "shared").exists())

    def test_the_sweep_never_removes_a_pending_draft_and_drops_old_unused_inputs(self):
        self.make_run("pending", draft=True, days_old=90)
        self.make_run("old", days_old=90)
        orphan = self.root / "inputs" / "never-prepared"
        orphan.mkdir(parents=True)
        self.age(orphan, 60)
        self.root.joinpath("inputs", "recent").mkdir()
        result = cleanup_all(draft_pending=lambda run_id: True)
        self.assertEqual({item["runId"]: item["removed"] for item in result["runs"]}, {"old": True, "pending": False})
        self.assertEqual(result["inputsRemoved"], ["never-prepared"])
        with self.assertRaisesRegex(ValueError, "no filing run named nope"):
            cleanup_run("nope", draft_pending=None)


if __name__ == "__main__":
    unittest.main()
