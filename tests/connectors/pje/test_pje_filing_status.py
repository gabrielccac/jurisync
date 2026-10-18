import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jurisync.connectors.pje.filing import DraftClient, StepTimeline
from jurisync.connectors.pje.filing.approval import approve
from jurisync.connectors.pje.filing.checkpoint import phase_fingerprints
from jurisync.connectors.pje.filing.run import _fill
from jurisync.connectors.pje.filing.status import run_state, run_status
from jurisync.connectors.pje.filing.store import LocalRunStore

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
            timeline = StepTimeline(LocalRunStore(tmp), Stub(), "fill")
            timeline.close_stale()
            saved = json.loads(path.read_text())["steps"][0]
        self.assertEqual((saved["status"], saved["error"]), ("interrupted", "run ended without finishing"))


class FillStatusTests(unittest.TestCase):
    def run_fill(self, client, checkpoint=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name, "checkpoint.json")
        checkpoint = checkpoint or {"draftId": "1", "draftUrl": "u", "stage": "created"}
        store = LocalRunStore(tmp.name)
        return checkpoint, path, lambda: _fill(client, checkpoint, store, PAYLOAD, "fill", False)

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
        Path(tmp, "checkpoint.json").write_text(json.dumps(checkpoint))
        if steps is not None:
            Path(tmp, "filing-steps.json").write_text(json.dumps({"steps": steps}))
        return LocalRunStore(tmp)

    def test_state_comes_from_the_run_record_and_process_liveness(self):
        self.assertEqual(run_state({"status": "running", "pid": os.getpid()}), "running")
        self.assertEqual(run_state({"status": "running", "pid": dead_pid()}), "interrupted")
        self.assertEqual(run_state({"status": "failed"}), "failed")
        self.assertEqual(run_state({"stage": "documents_complete"}), "complete")
        self.assertEqual(run_state({"stage": "created"}), "unknown")

    def test_manual_steps_are_reported_and_keep_a_draft_from_counting_as_done(self):
        class Pje:
            def _pending_row(self, draft_id, name):
                return {"draftId": draft_id, "draftUrl": "review"}

        step = {"step": "legal representative", "name": "MARIA MAE", "cpf": "999.***.***-66", "kind": "mae"}
        with tempfile.TemporaryDirectory() as tmp:
            prints = phase_fingerprints(PAYLOAD)
            store = self.write(tmp, {"draftId": "1", "status": "complete", "completed": prints, "manualSteps": [step]})
            result = run_status(store, PAYLOAD, Pje())
        self.assertEqual(result["manualSteps"], [step])
        self.assertEqual(result["conclusion"], "verified_manual_steps_pending")

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
            sha = hashlib.sha256(b"%PDF-1").hexdigest()
            main = {"path": str(pdf), "type": "Petição inicial", "sha256": sha}
            payload = {**PAYLOAD, "documents": {"main": main}}
            uploads = {sha: {"name": "0-A.pdf", "type": "Petição inicial"}}
            path = self.write(tmp, {"draftId": "1", "status": "complete", "completed": {}, "uploads": uploads}, [])
            on_draft = [{"name": "0-A.pdf", "type": "Petição inicial"}]
            stale = run_status(path, payload, Pje(on_draft))
            missing = run_status(path, payload, Pje([]))
            other = {"f" * 64: {"name": "0-A.pdf", "type": "Petição inicial"}}
            path = self.write(tmp, {"draftId": "1", "status": "complete", "completed": {}, "uploads": other}, [])
            changed = run_status(path, payload, Pje(on_draft))
        self.assertEqual(stale["conclusion"], "complete_in_pje_record_incomplete")
        self.assertTrue(stale["pje"]["verified"])
        self.assertEqual(missing["conclusion"], "incomplete")
        self.assertEqual(missing["pje"]["documents"]["missing"], ["0-A.pdf"])
        # The name is on the draft, but the run uploaded other bytes under it: not verified.
        self.assertFalse(changed["pje"]["verified"])
        self.assertEqual(changed["pje"]["documents"]["contentNotConfirmed"], ["0-A.pdf"])


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
            approve(LocalRunStore(run_dir))
            env = {"JURISYNC_FILING_DIR": tmp, "PJE_ALLOW_DRAFT_WRITES": "true"}
            with mock.patch.dict(os.environ, env), mock.patch.object(server.filing, "run_stage", slow_run_stage):
                first = server._start_fill("run1", "create-fill")
                started.wait(5)
                again = server._start_fill("run1", "create-fill")
                status = server.draft_status("run1")
                release.set()
                server.fill_jobs["run1"].join(5)
        self.assertEqual(first["state"], "started")
        self.assertEqual(again["state"], "already-running")
        self.assertEqual(status["result"]["state"], "starting")

    def test_draft_write_is_refused_without_an_approval_matching_the_payload(self):
        from mcp.server.mcpserver.exceptions import ToolError

        from jurisync.connectors.pje import server

        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp, "run1")
            run_dir.mkdir()
            payload = run_dir / "payload.json"
            payload.write_text(json.dumps(PAYLOAD))
            env = {"JURISYNC_FILING_DIR": tmp, "PJE_ALLOW_DRAFT_WRITES": "true"}
            run = lambda stage, **options: calls.append(stage)
            with mock.patch.dict(os.environ, env), mock.patch.object(server.filing, "run_stage", run):
                with self.assertRaisesRegex(ToolError, "not been approved"):
                    server.draft_write("run1", "create-fill")
                approve(LocalRunStore(run_dir))
                payload.write_text(json.dumps({**PAYLOAD, "author": {"name": "SOMEONE ELSE"}}))
                with self.assertRaisesRegex(ToolError, "changed after it was approved"):
                    server.draft_write("run1", "create-fill")
        self.assertEqual(calls, [])

    def test_every_payload_write_stage_needs_approval_before_login(self):
        from jurisync.connectors.pje.filing import WRITE_STAGES, run_stage

        with tempfile.TemporaryDirectory() as tmp:
            payload = Path(tmp, "payload.json")
            payload.write_text(json.dumps({**PAYLOAD, "portal": "trf5-1g"}))
            login = mock.patch("jurisync.connectors.pje.filing.run.trf5_cert_login", side_effect=AssertionError)
            with login:
                for stage in sorted(WRITE_STAGES - {"delete-draft"}):
                    with self.subTest(stage), self.assertRaisesRegex(ValueError, "not been approved"):
                        run_stage(stage, run=LocalRunStore(tmp))

    def test_writes_stay_disabled_without_the_flag(self):
        from mcp.server.mcpserver.exceptions import ToolError

        from jurisync.connectors.pje import server

        with mock.patch.dict(os.environ, {"PJE_ALLOW_DRAFT_WRITES": ""}):
            with self.assertRaises(ToolError):
                server._start_fill("run1", "create-fill")


if __name__ == "__main__":
    unittest.main()
