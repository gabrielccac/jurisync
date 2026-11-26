"""State files rewritten on every poll or run: an exclusive lock between processes, and records that age out."""

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from jurisync.connectors.djen import poll, store
from jurisync.core.file_lock import LockBusy, locked
from jurisync.workflows.publications import pipeline


class FileLockTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "state.json"

    def test_a_second_holder_waits_then_gives_up(self):
        clock, slept = [0.0], []

        def sleep(seconds):
            slept.append(seconds)
            clock[0] += seconds

        with locked(self.path):
            with self.assertRaises(LockBusy):
                with locked(self.path):
                    pass
            with self.assertRaises(LockBusy):
                with locked(self.path, wait=1.0, poll=0.25, clock=lambda: clock[0], sleep=sleep):
                    pass
        self.assertEqual(slept, [0.25] * 4)
        with locked(self.path):  # released: free again
            pass


class DjenStateTest(unittest.TestCase):
    def test_ids_older_than_the_window_are_forgotten_and_old_states_get_dates(self):
        state = {"ids_vistos": ["1", "2"], "ids_cancelados": ["2"], "vistos_em": {}}
        store.mark_seen(state, [{"id": 3, "data_disponibilizacao": "2026-10-20"}], today="2026-11-20")
        self.assertEqual(state["vistos_em"], {"1": "2026-11-20", "2": "2026-11-20", "3": "2026-10-20"})
        store.prune(state, "2026-11-25")  # cutoff 2026-10-25
        self.assertEqual((state["ids_vistos"], state["ids_cancelados"], state["count_total"]), (["1", "2"], ["2"], 2))
        store.prune(state, "2026-12-22")  # cutoff 2026-11-21
        self.assertEqual((state["ids_vistos"], state["ids_cancelados"], state["vistos_em"]), ([], [], {}))

    def test_a_poll_runs_under_the_state_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = store.state_path(tmp, "14663", "PB", "TRF5")
            fetched = {"count": 1, "unique": 1, "items": [{"id": 9, "data_disponibilizacao": "2026-11-20"}]}
            with (
                mock.patch.object(poll, "LOCK_WAIT_SECONDS", 0),
                mock.patch.object(poll, "fetch_all_variants", return_value=fetched) as fetch,
            ):
                with locked(path), self.assertRaises(LockBusy):
                    poll.poll_window("14663", "PB", "TRF5", state_dir=tmp, inicio="2026-11-20", fim="2026-11-20")
                fetch.assert_not_called()
                result = poll.poll_window("14663", "PB", "TRF5", state_dir=tmp, inicio="2026-11-20", fim="2026-11-20")
            self.assertEqual(result["new"], 1)
            self.assertEqual(store.load_state(path)["vistos_em"], {"9": "2026-11-20"})


class PipelineStateTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "state.json"

    def test_old_completed_and_read_publications_are_dropped(self):
        now = datetime(2026, 11, 26, tzinfo=UTC)
        old = (now - timedelta(days=pipeline.RETAIN_DAYS + 1)).isoformat().replace("+00:00", "Z")
        recent = (now - timedelta(days=1)).isoformat().replace("+00:00", "Z")
        done = {"status": "completed", "markedRead": True}
        publications = {
            "old-read": {**done, "updatedAt": old},
            "recent-read": {**done, "updatedAt": recent},
            "old-error": {"status": "error", "updatedAt": old},
            "old-pending": {"status": "pending_read", "updatedAt": old},
        }
        self.path.write_text(json.dumps({"publications": publications, "events": {"k": {}}}), encoding="utf-8")
        state = pipeline._State(self.path)
        state.prune(now)
        state.save()
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(sorted(saved["publications"]), ["old-error", "old-pending", "recent-read"])
        self.assertEqual(saved["events"], {"k": {}})

    def test_a_second_run_on_the_same_state_fails_at_once(self):
        agent = mock.Mock()
        runner = pipeline.PublicationPipeline(
            agent=agent, profile={"id": "x", "taskTemplates": {}}, state_file=self.path, api=mock.Mock()
        )
        with locked(self.path), self.assertRaises(LockBusy):
            runner.run()
        runner.api.all_unread_publications.assert_not_called()


if __name__ == "__main__":
    unittest.main()
