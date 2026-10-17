"""Filing MCP server: prepare and finalize a PJe draft payload without a shell.

The PJe MCP fills a draft from `payload.json` in a filing run, but something has to build that file from the ADVBOX
`00-PROTOCOLAR` folder. That used to be a command-line step; these tools make it callable by an agent that only has
MCP access. They wrap `prepare.py`, so the agent never passes file paths and never writes
`review.json` or `payload.json` itself.
"""

import json
import os
import re
from pathlib import Path
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field, StringConstraints

from jurisync.connectors.pje.filing.checkpoint import run_directory
from jurisync.core.mcp import READ_ONLY, load_env_file, run_stdio, tool_errors
from jurisync.paths import REPO_ROOT
from jurisync.workflows.filing.prepare import _write_private, finalize_review, prepare_customer
from jurisync.workflows.filing.profile import load_profile, profile_names

ERRORS = (ValueError, RuntimeError, OSError, KeyError)
LOCAL_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True)

RunId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")]
Digits = Annotated[str, StringConstraints(pattern=r"^\d{1,12}$")]

ProfileName = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{0,40}$")]

server = MCPServer("filing", version="1.0.0")


def _file_view(row):
    view = {key: row[key] for key in ("id", "name", "role", "type", "method", "pages", "review") if row.get(key)}
    if row.get("suggestedType"):
        view["suggestedType"] = row["suggestedType"]
    if row.get("parts"):
        view["volumes"] = len(row["parts"])
    return view


def _review_view(run_id, report, payload_ready):
    """The review without local paths or hashes: the agent needs ids, names, types and problems."""
    return {
        "runId": run_id,
        "customerId": report.get("customerId"),
        "folder": report.get("folder"),
        "ready": bool(report.get("ready")),
        "payloadReady": payload_ready,
        "files": [_file_view(row) for row in report.get("files", [])],
        "review": report.get("review", []),
        "candidates": report.get("candidates", {}),
        "fallbacks": report.get("fallbacks", []),
    }


def _mask_cpf(value):
    digits = re.sub(r"\D", "", str(value or ""))
    return f"{digits[:3]}.***.***-{digits[-2:]}" if len(digits) == 11 else None


def _payload_view(payload):
    documents = payload.get("documents") or {}
    items = [documents["main"], *documents.get("supporting", [])] if documents.get("main") else []
    return {
        "classification": payload.get("classification"),
        "subjects": [{"code": item["code"], "name": item["name"]} for item in payload.get("subjects", [])],
        "author": {
            "name": (payload.get("author") or {}).get("name"),
            "cpf": _mask_cpf(payload.get("authorCpf")),
            "address": (payload.get("author") or {}).get("address"),
        },
        "characteristics": payload.get("characteristics"),
        "documents": [
            {
                "name": Path(item["path"]).name,
                "type": item.get("type"),
                "description": item.get("description"),
                **({"volume": item["number"]} if item.get("number") else {}),
            }
            for item in items
        ],
    }


def _read_json(path, missing):
    if not path.exists():
        raise ToolError(missing)
    return json.loads(path.read_text(encoding="utf-8"))


def _require_no_draft(directory):
    if (directory / "checkpoint.json").exists():
        raise ToolError("This run already has a PJe draft, so its payload can no longer change; use a new runId")


@server.tool(
    name="filing_prepare",
    title="Prepare a TRF5 filing from the ADVBOX protocol folder",
    description="Download the customer's 00-PROTOCOLAR folder from ADVBOX, convert Word files to PDF, split PDFs over 10 MB, classify every file and write the run's review. Facts may be partial: anything missing becomes a review item, and `candidates` lists the claim values and CPFs found in the petition. Returns each file's id, suggested PJe type and problems. Jev (document text sent to an outside classifier) is off unless useJev is true. Needs a new runId per filing; takes about 20 seconds.",
    annotations=LOCAL_WRITE,
)
def prepare(
    profile: Annotated[ProfileName, Field(description="Profile name from filing_profiles, e.g. 'victor'.")],
    runId: Annotated[
        RunId,
        Field(description="New run id, letters digits _ or -, up to 64 characters, e.g. 'ana-2026-10-16'."),
    ],
    customerId: Annotated[
        Digits, Field(description="ADVBOX customer id (digits) whose 00-PROTOCOLAR folder is read, e.g. '10473994'.")
    ],
    facts: Annotated[
        dict[str, Any],
        Field(
            description="Partial JSON object with what is known: authorCpf (11 digits), subject or subjects (benefit labels from filing_subjects), author (object), classification (object), characteristics (object, e.g. claimValue and boolean flags). Anything missing becomes a review item."
        ),
    ],
    folder: Annotated[
        str | None,
        Field(
            max_length=200,
            description="Optional ADVBOX folder path inside the customer, as listed by advbox_list_files.",
        ),
    ] = None,
    useJev: Annotated[
        bool, Field(description="true sends document text to an outside classifier. Default false.")
    ] = False,
) -> dict[str, Any]:
    directory = run_directory(runId)
    _require_no_draft(directory)
    with tool_errors("Filing", *ERRORS):
        selected = load_profile(profile)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        _write_private(directory / "facts.json", facts)
        prepare_customer(customerId, facts, directory, profile=selected, folder=folder, jev=useJev)
    # prepare returns a short summary; the review file holds the files and the petition's candidates.
    report = _read_json(directory / "review.json", "Prepare did not write a review")
    return _review_view(runId, report, (directory / "payload.json").exists())


@server.tool(
    name="filing_review",
    title="Read a prepared filing review",
    description="Return the review of a prepared run again: files with their ids, roles, PJe types and problems, the petition's candidates, and whether a payload exists. Reads local files only.",
    annotations=READ_ONLY,
)
def review(
    runId: Annotated[RunId, Field(description="runId of a run that already has filing_prepare output.")],
) -> dict[str, Any]:
    directory = run_directory(runId)
    report = _read_json(directory / "review.json", "This run has no review yet; call filing_prepare first")
    return _review_view(runId, report, (directory / "payload.json").exists())


@server.tool(
    name="filing_finalize",
    title="Write the filing payload for a prepared run",
    description="Apply the reviewed choices and write payload.json for pje_draft_write. Refuses unless every file is classified, exactly one main petition remains, and the claim value and author CPF appear in the petition. `overrides` maps a file id from the review to {role, type}, or {role: 'skip'} to leave a file out. Omit `facts` to reuse the facts given to prepare. Returns a summary of what the draft will contain.",
    annotations=LOCAL_WRITE,
)
def finalize(
    profile: Annotated[ProfileName, Field(description="Same profile used in filing_prepare.")],
    runId: Annotated[RunId, Field(description="runId of a prepared run.")],
    facts: Annotated[
        dict[str, Any] | None,
        Field(
            description="Full facts object, same shape as filing_prepare. Omit to reuse the facts stored at prepare time."
        ),
    ] = None,
    overrides: Annotated[
        dict[str, dict[str, str]] | None,
        Field(
            description='JSON object keyed by file id from filing_review, e.g. {"<id>": {"role": "main", "type": "<PJe type>"}} or {"<id>": {"role": "skip"}} to leave the file out.'
        ),
    ] = None,
) -> dict[str, Any]:
    directory = run_directory(runId)
    _require_no_draft(directory)
    report = _read_json(directory / "review.json", "This run has no review yet; call filing_prepare first")
    if facts is None:
        facts = _read_json(directory / "facts.json", "This run has no stored facts; pass facts")
    else:
        _write_private(directory / "facts.json", facts)
    unknown = sorted(set(overrides or {}) - {row["id"] for row in report.get("files", [])})
    if unknown:
        raise ToolError(f"Overrides name files that are not in the review: {', '.join(unknown)}")
    with tool_errors("Filing", *ERRORS):
        result = finalize_review(directory / "review.json", facts, overrides or {}, profile=load_profile(profile))
    payload = _read_json(directory / "payload.json", "The payload was not written")
    return {
        "runId": runId,
        "ready": bool(result.get("ready")),
        "payload": _payload_view(payload),
        "next": "Show this to the user. When they approve, call pje_draft_write with stage create-fill, then follow it with pje_draft_status.",
    }


def _subject_table(path):
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines() if path and path.exists() else []:
        match = re.match(r"\|\s*([^|`]+?)\s*\|\s*`(\d+)`\s*([^|]+?)\s*\|\s*`([^`]+)`\s*\|", line)
        if match:
            rows.append({"benefit": match[1], "code": match[2], "name": match[3], "search": match[4]})
    return rows


@server.tool(
    name="filing_profiles",
    title="List the available filing profiles",
    description="List the customer filing profiles this server can use (name, portal, description). A profile holds one office's defaults and conventions; the customer's skill says which one to pass to the other filing tools.",
    annotations=READ_ONLY,
)
def profiles() -> dict[str, Any]:
    with tool_errors("Filing", *ERRORS):
        return {
            "profiles": [
                {"name": name, "portal": load_profile(name).portal, "description": load_profile(name).description}
                for name in profile_names()
            ]
        }


@server.tool(
    name="filing_subjects",
    title="Candidate PJe subjects (assuntos) for a filing",
    description="Return the profile's reference table of benefit labels and the PJe subject codes seen for them, plus, when lawsuitRef (a lawsuit ID or CNJ number) is given, the paragraphs of the profile's history notes that record what was filed for that exact case. These are candidates for the user to confirm, never defaults.",
    annotations=READ_ONLY,
)
def subjects(
    profile: Annotated[ProfileName, Field(description="Profile name from filing_profiles.")],
    lawsuitRef: Annotated[
        str | None,
        Field(
            min_length=4,
            max_length=40,
            description="Optional lawsuit id or CNJ number to look up what was filed for that case in the profile's history notes.",
        ),
    ] = None,
) -> dict[str, Any]:
    with tool_errors("Filing", *ERRORS):
        selected = load_profile(profile)
        history_path = selected.reference("history")
        history = []
        if lawsuitRef and history_path and history_path.exists():
            paragraphs = re.split(r"\n\s*\n", history_path.read_text(encoding="utf-8"))
            history = [" ".join(text.split())[:900] for text in paragraphs if lawsuitRef in text][:4]
        return {"candidates": _subject_table(selected.reference("subjects")), "history": history}


def main():
    load_env_file(os.environ.get("JURISYNC_ENV_FILE") or REPO_ROOT / ".env")
    run_stdio(server)


if __name__ == "__main__":
    main()
