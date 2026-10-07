"""Per-step timing and outcome of a filing run."""

import json
import logging
import os
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger("jurisync.pje.filing")


class StepTimeline:
    """Timing and outcome of each filing step, saved next to the checkpoint after every step.

    Entries accumulate across runs, so a re-run appends instead of replacing the history.
    """

    def __init__(self, path, client, stage):
        self.path = Path(path) if path else None
        self.client = client
        self.stage = stage
        self.run = uuid.uuid4().hex[:8]
        self.steps = []
        if self.path and self.path.exists():
            self.steps = json.loads(self.path.read_text(encoding="utf-8")).get("steps", [])

    @contextmanager
    def step(self, step_name, /, **details):
        entry = {
            "run": self.run,
            "stage": self.stage,
            "step": step_name,
            **details,
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
        except Exception as error:
            entry["status"] = "failed"
            entry["error"] = f"{type(error).__name__}: {str(error)[:300]}"
            raise
        finally:
            entry["seconds"] = round(time.monotonic() - started, 2)
            entry["requests"] = self.client.requests - requests
            log.info("%s %s %.2fs %dreq", entry["status"].upper(), label, entry["seconds"], entry["requests"])
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
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp = tempfile.mkstemp(dir=self.path.parent, prefix=".timeline-")
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump({"steps": self.steps}, output, ensure_ascii=False, indent=2)
        os.chmod(temp, 0o600)
        os.replace(temp, self.path)
