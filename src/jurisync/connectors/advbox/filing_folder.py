"""Export an ADVBOX customer's protocol folder as a filing input folder.

    python -m jurisync.connectors.advbox.filing_folder --customer ID [--folder "00 - PROTOCOLAR"] [--out DIR]

This is the ADVBOX side of filing: it knows ADVBOX (which customer folder holds the documents to protocol, how its
files are named and downloaded) and writes a folder in the shape of the filing input contract
(`jurisync.workflows.filing.contract`): the files plus a `sources.json` declaring each one's ADVBOX origin. The filing
then prepares that folder like any other. The dependency runs one way: this module uses the contract's validator; the
filing never imports ADVBOX.
"""

import argparse
import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from jurisync.connectors.advbox.client import AdvboxClient
from jurisync.connectors.advbox.documents import AdvboxDocuments
from jurisync.paths import filing_folder
from jurisync.workflows.filing.contract import write_sources

# The office convention for "documents ready to protocol": `00 - PROTOCOLAR`, sometimes `00 - PROTOCOLAR JUD`.
PROTOCOL_FOLDER_PATTERNS = (r"(?:^|/)00\s*-\s*PROTOCOLAR$", r"(?:^|/)00\s*-\s*PROTOCOLAR\s+JUD$")
# ADVBOX appends a random number to uploaded file names: "LAUDO MÉDICO_1524.pdf".
RANDOM_SUFFIX = re.compile(r"_\d{3,}(?=\.[^.]+$)")


def protocol_folder(documents, customer_id, patterns=PROTOCOL_FOLDER_PATTERNS):
    """The customer's protocol folder path, by the office naming convention."""
    folders = documents.list_customer_files(customer_id)["folders"]
    selected = next(
        (entry["path"] for pattern in patterns for entry in folders if re.search(pattern, entry["path"].strip(), re.I)),
        None,
    )
    if not selected:
        raise ValueError(f"No 00 - PROTOCOLAR folder found for customer {customer_id}")
    return selected


def default_destination(customer_id):
    """A new folder under the filing inputs folder (<filing root>/inputs)."""
    root = filing_folder("inputs")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    return root / f"advbox-{customer_id}-{stamp}"


def export_filing_folder(documents, customer_id, destination, *, folder=None):
    """Download one ADVBOX customer folder into `destination` (new or empty) as a filing input folder."""
    destination = Path(destination).expanduser().resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("The destination folder must be new or empty")
    selected = folder or protocol_folder(documents, customer_id)
    files = documents.list_customer_files(customer_id, selected)["files"]
    if not files:
        raise ValueError(f"The selected folder has no files: {selected}")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    entries = []
    for entry in files:
        saved = documents.download_file(
            entry.get("downloadUrl") or entry["url"],
            filename=RANDOM_SUFFIX.sub("", entry["name"]),
            directory=destination,
            inspect=False,
        )
        path = Path(saved["path"])
        path.chmod(0o600)
        source = {
            "file": path.name,
            "origin": "advbox",
            "originPath": selected,
            "originalName": entry["name"],
            "verifiedBy": "jurisync.advbox",
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        if entry.get("fileId"):
            source["originId"] = str(entry["fileId"])
        if entry.get("uploadedAt"):
            source["modifiedAt"] = entry["uploadedAt"]
        entries.append(source)
    write_sources(destination, entries)
    return {
        "sourceFolder": str(destination),
        "inputFolder": destination.name,
        "customerId": str(customer_id),
        "advboxFolder": selected,
        "files": [{"name": item["file"], "originalName": item["originalName"]} for item in entries],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Export an ADVBOX customer folder as a filing input folder")
    parser.add_argument("--customer", required=True)
    parser.add_argument("--folder", help="ADVBOX folder path (default: the customer's 00 - PROTOCOLAR folder)")
    parser.add_argument("--out", type=Path, help="New or empty destination folder")
    args = parser.parse_args(argv)
    documents = AdvboxDocuments(AdvboxClient())
    result = export_filing_folder(
        documents, args.customer, args.out or default_destination(args.customer), folder=args.folder
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    from jurisync.core.mcp import load_env_file
    from jurisync.paths import REPO_ROOT

    load_env_file(os.environ.get("JURISYNC_ENV_FILE") or REPO_ROOT / ".env")
    main()
