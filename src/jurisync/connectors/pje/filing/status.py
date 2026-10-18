"""Where a filing run stands: read from its files, optionally confirmed against PJe.

A run is only `complete` when its checkpoint says so. Whether the *draft* is complete is a separate question that only
PJe can answer, so `check_pje` reads the draft's documents and compares them with the payload.
"""

import json
from pathlib import Path

from jurisync.connectors.pje.filing.checkpoint import UploadLedger, phase_fingerprints, pid_alive
from jurisync.connectors.pje.filing.documents import expected_documents

LEGACY_COMPLETE = {"documents_complete", "characteristics_complete", "parties_complete"}


def run_state(checkpoint):
    """running / interrupted / failed / complete, from what the run last recorded about itself."""
    status = checkpoint.get("status")
    if status == "running":
        return "running" if pid_alive(checkpoint.get("pid")) else "interrupted"
    if status in ("complete", "failed", "interrupted"):
        return status
    # Checkpoints written before runs recorded a status: only the end-of-run label tells.
    return "complete" if checkpoint.get("stage") in LEGACY_COMPLETE else "unknown"


def _steps(checkpoint_path):
    path = Path(checkpoint_path).with_name("filing-steps.json")
    return json.loads(path.read_text(encoding="utf-8")).get("steps", []) if path.exists() else []


def run_status(checkpoint_path, payload=None, client=None):
    """Status of one run. Pass a payload to compare phases with the ledger, and a client to check PJe live."""
    checkpoint = json.loads(Path(checkpoint_path).read_text(encoding="utf-8"))
    state = run_state(checkpoint)
    steps = _steps(checkpoint_path)
    last_run = steps[-1]["run"] if steps else None
    current = [step for step in steps if step["run"] == last_run]
    result = {
        "draftId": checkpoint.get("draftId"),
        "state": state,
        "stage": checkpoint.get("stage"),
        "startedAt": checkpoint.get("startedAt"),
        "updatedAt": checkpoint.get("updatedAt"),
        "error": checkpoint.get("error"),
        "pid": checkpoint.get("pid"),
        "recentSteps": [
            {key: step.get(key) for key in ("step", "document", "status", "seconds", "requests", "startedAt")}
            for step in current[-4:]
        ],
    }
    running = [step for step in current if step["status"] in ("running", "interrupted")]
    if running:
        result["currentStep"] = running[-1]["step"]
    if payload is not None:
        ledger = checkpoint.get("completed", {})
        result["phases"] = {
            name: "done" if ledger.get(name) == key else "pending" for name, key in phase_fingerprints(payload).items()
        }
    if client is not None and payload is not None:
        result["pje"] = _check_pje(client, checkpoint, payload)
        phases_done = all(value == "done" for value in result["phases"].values())
        if result["pje"]["verified"]:
            result["conclusion"] = "verified" if phases_done else "complete_in_pje_record_incomplete"
        else:
            result["conclusion"] = "incomplete"
    else:
        result["conclusion"] = "not_checked"
    return result


def _check_pje(client, checkpoint, payload):
    row = client._pending_row(checkpoint["draftId"], payload["author"]["name"])
    check = {"inNaoProtocolados": row is not None}
    expected = expected_documents(payload["documents"]) if "documents" in payload else []
    if row is None:
        check.update(documents={"expected": len(expected), "missing": [Path(i["path"]).name for i in expected]})
        check["verified"] = False
        return check
    listed = client.review_documents(row["draftUrl"]) if expected else []
    missing = client._missing_documents(listed, expected) if expected else []
    # A listed name proves nothing about its bytes: the run's upload ledger must hold this document's sha256 for it.
    ledger = UploadLedger(checkpoint)
    unconfirmed = [
        Path(item["path"]).name for item in expected if Path(item["path"]).name not in missing and ledger.mismatch(item)
    ]
    check["documents"] = {"expected": len(expected), "listed": len(listed), "missing": missing}
    if unconfirmed:
        check["documents"]["contentNotConfirmed"] = unconfirmed
    check["verified"] = not missing and not unconfirmed
    return check
