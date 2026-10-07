"""Run one filing stage or the whole fill; command-line entry point."""

import argparse
import json
import logging
import os
from pathlib import Path

from jurisync.connectors.pje.auth.certificate import trf5_cert_login
from jurisync.connectors.pje.filing.checkpoint import write_checkpoint
from jurisync.connectors.pje.filing.client import DraftClient
from jurisync.connectors.pje.filing.payload import payload_subjects, validate_fill_payload
from jurisync.connectors.pje.filing.timeline import StepTimeline
from jurisync.core.http import enable_request_logging

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
    }
)


def _timeline(checkpoint_path, client, stage):
    return StepTimeline(Path(checkpoint_path).with_name("filing-steps.json"), client, stage)


def _fill(client, checkpoint, checkpoint_path, payload, stage, verify_all):
    """Fill the draft, saving the checkpoint as each phase completes, then label how far it got."""

    def save(data):
        write_checkpoint(checkpoint_path, data)

    result = client.fill_draft(
        checkpoint, payload, _timeline(checkpoint_path, client, stage), verify_all=verify_all, save=save
    )
    checkpoint["stage"] = (
        "documents_complete"
        if "documents" in payload
        else "characteristics_complete"
        if "characteristics" in payload
        else "parties_complete"
    )
    save(checkpoint)
    return result


def run_stage(
    stage,
    *,
    payload_path=None,
    checkpoint_path=None,
    search=None,
    page=1,
    name=None,
    cpf=None,
    session=None,
    verify_all=False,
):
    """Run one draft stage; pass an authenticated TRF5 session to reuse it.

    `fill` and `create-fill` skip phases the checkpoint records as done for this payload; `verify_all` re-reads and
    re-checks every phase instead.
    """
    if stage not in STAGES:
        raise ValueError(f"Unknown draft stage: {stage}")
    if stage == "subject-options":
        if not checkpoint_path or not search or page < 1:
            raise ValueError("subject-options requires a checkpoint, search text and a positive page")
        payload = {}
    elif stage == "pending-search":
        if not (name or cpf):
            raise ValueError("pending-search requires a name or CPF")
        payload = {}
    else:
        if not payload_path:
            raise ValueError("A payload is required for this stage")
        with open(payload_path, encoding="utf-8") as source:
            payload = json.load(source)
    if payload and payload.get("portal") != "trf5-1g":
        raise ValueError("Only trf5-1g is supported")
    if stage in ("create-fill", "fill"):
        validate_fill_payload(payload)
    owned = session is None
    if owned:
        session = trf5_cert_login()
    try:
        client = DraftClient(session)
        if stage == "pending-search":
            result = client.search_pending_drafts(name=name, cpf=cpf)
        elif stage == "classification":
            result = client.classification_options(payload["classification"])
        elif stage in ("create", "create-fill"):
            if not checkpoint_path:
                raise ValueError("--checkpoint is required to save the new draft ID")
            destination = Path(checkpoint_path)
            if destination.exists():
                raise FileExistsError(f"Checkpoint already exists: {destination}")
            result = client.create_draft(payload["classification"])
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(
                    {"draftId": result["draftId"], "draftUrl": result["draftUrl"], "stage": "created"},
                    output,
                    ensure_ascii=False,
                    indent=2,
                )
            if stage == "create-fill":
                created = {"draftId": result["draftId"], "draftUrl": result["draftUrl"], "stage": "created"}
                result["filling"] = _fill(client, created, destination, payload, stage, verify_all)
        else:
            if not checkpoint_path:
                raise ValueError("--checkpoint is required after classification")
            with open(checkpoint_path, encoding="utf-8") as source:
                checkpoint = json.load(source)
            if stage == "subject-options":
                result = client.search_subjects(checkpoint["draftUrl"], search, page)
            elif stage == "subject":
                added_subjects = client.add_subjects(checkpoint["draftUrl"], payload_subjects(payload))
                result = added_subjects[0] if "subject" in payload else added_subjects
            elif stage == "fill":
                result = _fill(client, checkpoint, checkpoint_path, payload, stage, verify_all)
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
                result = client.add_attorney(checkpoint["draftUrl"], payload["attorney"])
            elif stage == "characteristics":
                result = client.save_characteristics(checkpoint["draftUrl"], payload["characteristics"])
            elif stage == "main-document":
                draft_url = client.pending_draft_url(checkpoint["draftId"], payload["author"]["name"])
                result = client.add_main_document(draft_url, payload["documents"]["main"])
            elif stage == "supporting-documents":
                draft_url = client.pending_draft_url(checkpoint["draftId"], payload["author"]["name"])
                result = client.add_supporting_documents(draft_url, payload["documents"].get("supporting", []))
            elif stage == "documents":
                draft_url = client.pending_draft_url(checkpoint["draftId"], payload["author"]["name"])
                result = client.add_documents(draft_url, payload["documents"])
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
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    parser = argparse.ArgumentParser(description="Inspect TRF5 PJe draft fields via direct requests")
    parser.add_argument("--payload")
    parser.add_argument("--stage", choices=STAGES, default="classification")
    parser.add_argument("--checkpoint")
    parser.add_argument("--search", help="Assunto text for the read-only subject-options stage")
    parser.add_argument("--name", help="Parte name for the read-only pending-search stage")
    parser.add_argument("--cpf", help="Parte CPF for the read-only pending-search stage")
    parser.add_argument("--page", type=int, default=1, help="Assunto result page (default: 1)")
    parser.add_argument(
        "--verify-all", action="store_true", help="Re-read and re-check every phase instead of trusting the checkpoint"
    )
    parser.add_argument("--mcp", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    enable_request_logging()
    try:
        result = run_stage(
            args.stage,
            payload_path=args.payload,
            checkpoint_path=args.checkpoint,
            search=args.search,
            page=args.page,
            name=args.name,
            cpf=args.cpf,
            verify_all=args.verify_all,
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
