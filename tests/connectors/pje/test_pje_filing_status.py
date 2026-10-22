import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jurisync.connectors.pje.filing import DraftClient, StepTimeline
from jurisync.connectors.pje.filing.checkpoint import phase_fingerprints
from jurisync.connectors.pje.filing.run import _fill
from jurisync.connectors.pje.filing.status import run_state, run_status

PAYLOAD = {
    "subject": {"search": "s", "code": "1", "name": "S"},
    "authorCpf": "12345678909",
    "author": {"name": "EXAMPLE"},
}


def dead_pid():
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


class Stub:
    """Stands in for DraftClient in _fill: fill_draft either finishes or raises what it is told to."""

    requests = 0

    def __init__(self, outcome=None):
        self.outcome = outcome

    def fill_draft(self, checkpoint, payload, timeline, verify_all=False, save=None):
        if self.outcome:
            raise self.outcome
        checkpoint.setdefault("completed", {}).update(phase_fingerprints(payload))
        return {"ok": True}


class StepStateTests(unittest.TestCase):
    def test_cancelled_steps_are_interrupted_and_real_errors_are_failed(self):
        client = Stub()
        timeline = StepTimeline(None, client, "fill")
        for error, expected in ((KeyboardInterrupt(), "interrupted"), (RuntimeError("boom"), "failed")):
            with self.assertRaises(type(error)):
                with timeline.step("work"):
                    raise error
            self.assertEqual(timeline.steps[-1]["status"], expected)

    def test_steps_left_running_by_a_dead_run_are_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "filing-steps.json")
            path.write_text(json.dumps({"steps": [{"step": "x", "status": "running", "error": None}]}))
            timeline = StepTimeline(path, Stub(), "fill")
            timeline.close_stale()
            saved = json.loads(path.read_text())["steps"][0]
        self.assertEqual((saved["status"], saved["error"]), ("interrupted", "run ended without finishing"))


class FillStatusTests(unittest.TestCase):
    def run_fill(self, client, checkpoint=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name, "checkpoint.json")
        checkpoint = checkpoint or {"draftId": "1", "draftUrl": "u", "stage": "created"}
        return checkpoint, path, lambda: _fill(client, checkpoint, str(path), PAYLOAD, "fill", False)

    def test_a_finished_fill_records_complete_with_its_process_id(self):
        checkpoint, path, run = self.run_fill(Stub())
        run()
        saved = json.loads(path.read_text())
        self.assertEqual((saved["status"], saved["pid"], saved["error"]), ("complete", os.getpid(), None))
        self.assertEqual(saved["stage"], "parties_complete")

    def test_a_failed_fill_keeps_the_error_and_a_cancelled_one_is_interrupted(self):
        for outcome, status in ((RuntimeError("PJe said no"), "failed"), (KeyboardInterrupt(), "interrupted")):
            checkpoint, path, run = self.run_fill(Stub(outcome))
            with self.assertRaises(type(outcome)):
                run()
            saved = json.loads(path.read_text())
            self.assertEqual(saved["status"], status)
            self.assertIn(type(outcome).__name__, saved["error"])

    def test_a_second_fill_is_refused_while_another_process_is_filling_the_same_draft(self):
        live_other = os.getppid()
        checkpoint = {"draftId": "1", "draftUrl": "u", "status": "running", "pid": live_other}
        _, _, run = self.run_fill(Stub(), checkpoint)
        with self.assertRaisesRegex(RuntimeError, "already running"):
            run()
        stale = {"draftId": "1", "draftUrl": "u", "status": "running", "pid": dead_pid()}
        _, path, run = self.run_fill(Stub(), stale)
        run()
        self.assertEqual(json.loads(path.read_text())["status"], "complete")


class RunStatusTests(unittest.TestCase):
    def write(self, tmp, checkpoint, steps=None):
        path = Path(tmp, "checkpoint.json")
        path.write_text(json.dumps(checkpoint))
        if steps is not None:
            Path(tmp, "filing-steps.json").write_text(json.dumps({"steps": steps}))
        return path

    def test_state_comes_from_the_run_record_and_process_liveness(self):
        self.assertEqual(run_state({"status": "running", "pid": os.getpid()}), "running")
        self.assertEqual(run_state({"status": "running", "pid": dead_pid()}), "interrupted")
        self.assertEqual(run_state({"status": "failed"}), "failed")
        self.assertEqual(run_state({"stage": "documents_complete"}), "complete")
        self.assertEqual(run_state({"stage": "created"}), "unknown")

    def test_phases_compare_the_ledger_with_the_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            prints = phase_fingerprints(PAYLOAD)
            path = self.write(tmp, {"draftId": "1", "completed": {"subjects": prints["subjects"]}}, [])
            result = run_status(path, PAYLOAD)
        self.assertEqual(result["phases"], {"subjects": "done", "parties": "pending"})
        self.assertEqual(result["conclusion"], "not_checked")

    def test_the_live_check_decides_completeness_not_the_local_files(self):
        class Pje:
            def __init__(self, listed):
                self.listed = listed

            def _pending_row(self, draft_id, name):
                return {"draftId": draft_id, "draftUrl": "review"}

            def review_documents(self, url):
                return self.listed

            _missing_documents = staticmethod(DraftClient._missing_documents)

        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp, "0-A.pdf")
            pdf.write_bytes(b"%PDF-1")
            payload = {**PAYLOAD, "documents": {"main": {"path": str(pdf), "type": "Petição inicial"}}}
            path = self.write(tmp, {"draftId": "1", "status": "complete", "completed": {}}, [])
            on_draft = [{"name": "0-A.pdf", "type": "Petição inicial"}]
            stale = run_status(path, payload, Pje(on_draft))
            missing = run_status(path, payload, Pje([]))
        self.assertEqual(stale["conclusion"], "complete_in_pje_record_incomplete")
        self.assertTrue(stale["pje"]["verified"])
        self.assertEqual(missing["conclusion"], "incomplete")
        self.assertEqual(missing["pje"]["documents"]["missing"], ["0-A.pdf"])


class BackgroundFillTests(unittest.TestCase):
    def test_create_fill_returns_at_once_and_the_job_runs_on(self):
        import threading

        from jurisync.connectors.pje import server

        release, started = threading.Event(), threading.Event()

        def slow_run_stage(stage, **options):
            started.set()
            release.wait(5)

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp, "run1")
            run_dir.mkdir()
            run_dir.joinpath("payload.json").write_text(json.dumps(PAYLOAD))
            env = {"JURISYNC_FILING_DIR": tmp, "PJE_ALLOW_DRAFT_WRITES": "true"}
            with (
                mock.patch.dict(os.environ, env),
                mock.patch.object(server.filing, "run_stage", slow_run_stage),
                mock.patch.object(server, "sessions", mock.MagicMock()),  # the job borrows a pooled session
            ):
                first = server._start_fill("run1", "create-fill")
                started.wait(5)
                again = server._start_fill("run1", "create-fill")
                status = server.draft_status("run1")
                release.set()
                server.fill_jobs["run1"].join(5)
        self.assertEqual(first["state"], "started")
        self.assertEqual(again["state"], "already-running")
        self.assertEqual(status["result"]["state"], "starting")

    def test_writes_stay_disabled_without_the_flag(self):
        from mcp.server.mcpserver.exceptions import ToolError

        from jurisync.connectors.pje import server

        with mock.patch.dict(os.environ, {"PJE_ALLOW_DRAFT_WRITES": ""}):
            with self.assertRaises(ToolError):
                server._start_fill("run1", "create-fill")


if __name__ == "__main__":
    unittest.main()
