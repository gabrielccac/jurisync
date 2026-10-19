"""Remove a filing run and the input folder it came from: client documents are kept only while they are needed.

A run is removed when its draft has left Não Protocolados (protocolled or deleted) or, without a draft, once it is
older than the retention (`olderThanDays`, default JURISYNC_FILING_RETENTION_DAYS or 30). A run whose draft is still
in Não Protocolados is never removed without the user's explicit confirmation (`confirmPendingDraft`), and a sweep
over every run never removes one. The input folder goes with its run when no other run was prepared from it.
"""

import os
import shutil
import time

from jurisync.connectors.pje.filing.store import CHECKPOINT, LocalRunStore
from jurisync.paths import filing_folder

INBOX_RECORD = "inbox.json"


def retention_days():
    return float(os.environ.get("JURISYNC_FILING_RETENTION_DAYS", "30"))


def age_days(root):
    """Days since anything in the folder last changed."""
    newest = max((path.stat().st_mtime for path in (root, *root.rglob("*"))), default=root.stat().st_mtime)
    return (time.time() - newest) / 86400


def _input_folder(store):
    if not store.exists(INBOX_RECORD):
        return None
    name = store.read_json(INBOX_RECORD).get("inputFolder")
    return name if name and "/" not in name and name not in (".", "..") else None


def _shared_input(name, run_root):
    """Whether another run was prepared from the same input folder."""
    for other in filing_folder("runs").iterdir():
        if other.resolve() != run_root and other.is_dir():
            store = LocalRunStore(other)
            if _input_folder(store) == name:
                return True
    return False


def cleanup_run(run_id, *, draft_pending, older_than_days=None, confirm_pending=False, running=False):
    """Remove one run (and its input folder) when allowed; returns what was removed or why it was kept.

    `draft_pending(run_id)` answers whether the run's draft is still in Não Protocolados (it reads PJe; an error there
    stops the cleanup, so nothing is removed on an unknown answer).
    """
    root = filing_folder("runs") / run_id
    if not root.is_dir():
        raise ValueError(f"There is no filing run named {run_id}")
    store = LocalRunStore(root)
    limit = retention_days() if older_than_days is None else older_than_days
    age = age_days(root)
    result = {"runId": run_id, "ageDays": round(age, 1)}
    if running:
        return {**result, "removed": False, "reason": "a fill is running for this run"}
    if store.exists(CHECKPOINT):
        checkpoint = store.read_json(CHECKPOINT)
        result["draftId"] = checkpoint.get("draftId")
        if draft_pending(run_id):
            if not confirm_pending:
                return {
                    **result,
                    "removed": False,
                    "reason": "its draft is still in Não Protocolados; remove it only if the user explicitly confirms "
                    "(confirmPendingDraft=true)",
                }
        else:
            limit = 0  # the draft was protocolled or deleted: nothing left to file from this run
    if age < limit:
        return {**result, "removed": False, "reason": f"younger than {limit:g} days"}
    name = _input_folder(store)
    shutil.rmtree(root)
    result.update(removed=True)
    if name:
        folder = filing_folder("inputs") / name
        if folder.is_dir() and not _shared_input(name, root.resolve()):
            shutil.rmtree(folder)
            result["inputFolderRemoved"] = name
    return result


def cleanup_all(*, draft_pending, older_than_days=None, running=()):
    """Sweep every run with the same rules (a run whose draft is still pending is always kept), then every input
    folder no run was prepared from, once it is older than the retention."""
    results = []
    for root in sorted(path for path in filing_folder("runs").iterdir() if path.is_dir()):
        results.append(
            cleanup_run(
                root.name, draft_pending=draft_pending, older_than_days=older_than_days, running=root.name in running
            )
        )
    limit = retention_days() if older_than_days is None else older_than_days
    used = {_input_folder(LocalRunStore(root)) for root in filing_folder("runs").iterdir() if root.is_dir()}
    removed_inputs = []
    for folder in sorted(path for path in filing_folder("inputs").iterdir()):
        if folder.name not in used and age_days(folder) >= limit:
            shutil.rmtree(folder) if folder.is_dir() else folder.unlink()
            removed_inputs.append(folder.name)
    return {"runs": results, "inputsRemoved": removed_inputs}
