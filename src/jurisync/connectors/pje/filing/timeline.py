"""Per-step timing and outcome of a filing run."""

import logging
import time
import uuid
from contextlib import contextmanager

from jurisync.connectors.pje.filing.checkpoint import now
from jurisync.connectors.pje.filing.store import STEPS

log = logging.getLogger("jurisync.pje.filing")


class StepTimeline:
    """Timing and outcome of each filing step, saved in the run's filing-steps.json after every step.

    Entries accumulate across runs, so a re-run appends instead of replacing the history. Without a store (tests,
    one-off calls) nothing is saved.
    """

    def __init__(self, store, client, stage):
        self.store = store
        self.client = client
        self.stage = stage
        self.run = uuid.uuid4().hex[:8]
        self.steps = []
        if self.store is not None and self.store.exists(STEPS):
            self.steps = self.store.read_json(STEPS).get("steps", [])

    @contextmanager
    def step(self, step_name, /, **details):
        entry = {
            "run": self.run,
            "stage": self.stage,
            "step": step_name,
            **details,
            "startedAt": now(),
            "status": "running",
            "seconds": None,
        }
        entry.update(requests=0, error=None)
        self.steps.append(entry)
        self._save()
        label = " ".join(str(value) for value in (step_name, *details.values()) if value not in (None, ""))
        log.info("START %s", label)
        started, requests = time.monotonic(), self.client.requests
        try:
            yield entry
            entry["status"] = "ok"
        except BaseException as error:
            entry["status"] = "failed" if isinstance(error, Exception) else "interrupted"
            entry["error"] = f"{type(error).__name__}: {str(error)[:300]}"
            raise
        finally:
            entry["seconds"] = round(time.monotonic() - started, 2)
            entry["requests"] = self.client.requests - requests
            log.info("%s %s %.2fs %dreq", entry["status"].upper(), label, entry["seconds"], entry["requests"])
            self._save()

    def close_stale(self):
        """Mark steps left `running` by a run that no longer exists."""
        changed = False
        for entry in self.steps:
            if entry.get("status") == "running":
                entry["status"], entry["error"] = "interrupted", entry.get("error") or "run ended without finishing"
                changed = True
        if changed:
            self._save()

    def skip(self, step_name, reason, /, **details):
        """Record a step that was not needed, so the history shows why it cost nothing."""
        self.steps.append(
            {
                "run": self.run,
                "stage": self.stage,
                "step": step_name,
                **details,
                "status": "skipped",
                "seconds": 0,
                "requests": 0,
                "error": None,
                "reason": reason,
            }
        )
        self._save()

    def _save(self):
        if self.store is not None:
            self.store.write_json(STEPS, {"steps": self.steps})
