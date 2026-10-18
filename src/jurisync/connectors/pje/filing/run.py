"""Run one filing stage or the whole fill; command-line entry point."""

import argparse
import json
import logging
import os

from jurisync.connectors.pje.auth.certificate import trf5_cert_login
from jurisync.connectors.pje.filing.approval import require_approval
from jurisync.connectors.pje.filing.checkpoint import UploadLedger, now, pid_alive
from jurisync.connectors.pje.filing.client import DraftClient
from jurisync.connectors.pje.filing.payload import payload_subjects, resolve_documents, validate_fill_payload
from jurisync.connectors.pje.filing.status import run_status
from jurisync.connectors.pje.filing.store import CHECKPOINT, PAYLOAD, LocalRunStore
from jurisync.connectors.pje.filing.timeline import StepTimeline
from jurisync.core.http import enable_request_logging
from jurisync.core.mcp import load_env_file
from jurisync.paths import REPO_ROOT

STAGES = (
    "classification",
    "create",
    "create-fill",
    "fill",
    "subject-options",
    "subject",
    "author",
    "pending",
    "pending-search",
    "public-bodies",
    "attorney",
    "characteristics",
    "main-document",
    "supporting-documents",
    "documents",
    "status",
    "status-check",
    "delete-draft",
)


WRITE_STAGES = frozenset(
    {
        "create",
        "create-fill",
        "fill",
        "subject",
        "author",
        "public-bodies",
        "attorney",
        "characteristics",
        "main-document",
        "supporting-documents",
        "documents",
        "delete-draft",
    }
)


def _load_payload(store):
    """The run's payload with its documents resolved to local files, or None when the run has no payload."""
    if store is None or not store.exists(PAYLOAD):
        return None
    return resolve_documents(store.read_json(PAYLOAD), store)


def _fill(client, checkpoint, store, payload, stage, verify_all):
    """Fill the draft, saving the checkpoint as each phase completes, then label how far it got."""

    def save(data):
        store.write_json(CHECKPOINT, data)

    holder = checkpoint.get("pid")
    if checkpoint.get("status") == "running" and holder and holder != os.getpid() and pid_alive(holder):
        raise RuntimeError(f"Another fill is already running for this draft (pid {holder})")
    timeline = StepTimeline(store, client, stage)
    timeline.close_stale()
    checkpoint.update(status="running", pid=os.getpid(), startedAt=now(), updatedAt=now(), error=None)
    save(checkpoint)
    try:
        result = client.fill_draft(checkpoint, payload, timeline, verify_all=verify_all, save=save)
    except BaseException as error:
        failed = isinstance(error, Exception)
        message = f"{type(error).__name__}: {str(error)[:300]}"
        checkpoint.update(status="failed" if failed else "interrupted", error=message, updatedAt=now())
        save(checkpoint)
        raise
    checkpoint["stage"] = (
        "documents_complete"
        if "documents" in payload
        else "characteristics_complete"
        if "characteristics" in payload
        else "parties_complete"
    )
    checkpoint.update(status="complete", updatedAt=now(), error=None)
    save(checkpoint)
    return result


def run_stage(
    stage,
    *,
    run=None,
    search=None,
    page=1,
    name=None,
    cpf=None,
    session=None,
    verify_all=False,
    draft_id=None,
):
    """Run one draft stage; pass an authenticated TRF5 session to reuse it.

    `run` is the filing run's RunStore: its payload.json, approval.json and checkpoint.json are read and written there.

    `fill` and `create-fill` skip phases the checkpoint records as done for this payload; `verify_all` re-reads and
    re-checks every phase instead.
    """
    if stage not in STAGES:
        raise ValueError(f"Unknown draft stage: {stage}")
    if stage == "status":
        if run is None or not run.exists(CHECKPOINT):
            raise ValueError("status requires a run with a checkpoint")
        try:
            payload = _load_payload(run)
        except ValueError:  # an outdated payload still lets the run report where it stands
            payload = None
        return run_status(run, payload)
    if stage == "subject-options":
        if run is None or not search or page < 1:
            raise ValueError("subject-options requires a run, search text and a positive page")
        payload = {}
    elif stage == "pending-search":
        if not (name or cpf):
            raise ValueError("pending-search requires a name or CPF")
        payload = {}
    elif stage == "delete-draft":
        if not (draft_id and name):
            raise ValueError("delete-draft requires the draft number and the author's name")
        payload = {}
    else:
        payload = _load_payload(run)
        if payload is None:
            raise ValueError("A run with a payload is required for this stage")
    if payload and payload.get("portal") != "trf5-1g":
        raise ValueError("Only trf5-1g is supported")
    if stage in WRITE_STAGES and stage != "delete-draft":
        # Every stage that writes payload content needs the user's recorded approval of this exact payload.
        validate_fill_payload(payload)
        require_approval(run)
    owned = session is None
    if owned:
        session = trf5_cert_login()
    try:
        client = DraftClient(session)
        if stage == "pending-search":
            result = client.search_pending_drafts(name=name, cpf=cpf)
        elif stage == "delete-draft":
            result = client.delete_draft(draft_id, name)
        elif stage == "classification":
            result = client.classification_options(payload["classification"])
        elif stage in ("create", "create-fill"):
            if run.exists(CHECKPOINT):
                raise FileExistsError(f"Run {run.label} already has a draft checkpoint")
            result = client.create_draft(payload["classification"])
            created = {"draftId": result["draftId"], "draftUrl": result["draftUrl"], "stage": "created"}
            run.write_json(CHECKPOINT, created)
            if stage == "create-fill":
                result["filling"] = _fill(client, created, run, payload, stage, verify_all)
        else:
            if run is None or not run.exists(CHECKPOINT):
                raise ValueError("This stage needs a run whose draft was created (no checkpoint.json yet)")
            checkpoint = run.read_json(CHECKPOINT)
            ledger = UploadLedger(checkpoint, lambda data: run.write_json(CHECKPOINT, data))
            if stage == "status-check":
                result = run_status(run, payload, client)
            elif stage == "subject-options":
                result = client.search_subjects(checkpoint["draftUrl"], search, page)
            elif stage == "subject":
                added_subjects = client.add_subjects(checkpoint["draftUrl"], payload_subjects(payload))
                result = added_subjects[0] if "subject" in payload else added_subjects
            elif stage == "fill":
                result = _fill(client, checkpoint, run, payload, stage, verify_all)
            elif stage == "pending":
                result = client.pending_draft(checkpoint["draftId"], payload["author"]["name"])
            elif stage == "public-bodies":
                result = {}
                if "passive" in payload:
                    result["passive"] = client.add_passive_body(checkpoint["draftUrl"], payload["passive"])
                if "otherParticipants" in payload:
                    result["otherParticipants"] = [
                        client.add_other_participant(checkpoint["draftUrl"], body)
                        for body in payload["otherParticipants"]
                    ]
            elif stage == "attorney":
                result = [client.add_attorney(checkpoint["draftUrl"], item) for item in payload.get("attorneys", [])]
            elif stage == "characteristics":
                result = client.save_characteristics(checkpoint["draftUrl"], payload["characteristics"])
            elif stage == "main-document":
                draft_url = client.pending_draft_url(checkpoint["draftId"], payload["author"]["name"])
                result = client.add_main_document(draft_url, payload["documents"]["main"], ledger=ledger)
            elif stage == "supporting-documents":
                draft_url = client.pending_draft_url(checkpoint["draftId"], payload["author"]["name"])
                result = client.add_supporting_documents(
                    draft_url, payload["documents"].get("supporting", []), ledger=ledger
                )
            elif stage == "documents":
                draft_url = client.pending_draft_url(checkpoint["draftId"], payload["author"]["name"])
                result = client.add_documents(draft_url, payload["documents"], ledger=ledger)
            else:
                result = client.add_author(
                    checkpoint["draftUrl"],
                    payload["authorCpf"],
                    payload["author"]["name"],
                    payload["author"].get("address"),
                )
        return result
    finally:
        if owned:
            session.close()


def main():
    load_env_file(os.environ.get("JURISYNC_ENV_FILE") or REPO_ROOT / ".env")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    parser = argparse.ArgumentParser(description="Inspect TRF5 PJe draft fields via direct requests")
    parser.add_argument(
        "--run", help="Filing run folder (payload.json, approval.json, checkpoint.json and documents/ inside it)"
    )
    parser.add_argument("--stage", choices=STAGES, default="classification")
    parser.add_argument("--search", help="Assunto text for the read-only subject-options stage")
    parser.add_argument("--name", help="Parte name for the read-only pending-search stage")
    parser.add_argument("--cpf", help="Parte CPF for the read-only pending-search stage")
    parser.add_argument("--page", type=int, default=1, help="Assunto result page (default: 1)")
    parser.add_argument("--draft-id", help="Draft number for the delete-draft stage")
    parser.add_argument(
        "--verify-all", action="store_true", help="Re-read and re-check every phase instead of trusting the checkpoint"
    )
    parser.add_argument("--mcp", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    enable_request_logging()
    try:
        result = run_stage(
            args.stage,
            run=LocalRunStore(args.run) if args.run else None,
            search=args.search,
            page=args.page,
            name=args.name,
            cpf=args.cpf,
            verify_all=args.verify_all,
            draft_id=args.draft_id,
        )
    except ValueError as error:
        if args.mcp:
            raise
        parser.error(str(error))
    print(
        json.dumps(
            {"success": True, "stage": args.stage, "result": result} if args.mcp else result,
            ensure_ascii=False,
            indent=2,
        )
    )
