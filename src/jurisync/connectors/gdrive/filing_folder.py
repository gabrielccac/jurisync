"""Export a Google Drive folder as a filing input folder.

    python -m jurisync.connectors.gdrive check [--folder ID_OR_URL]
    python -m jurisync.connectors.gdrive export --folder ID_OR_URL [--subfolders] [--exclude GLOB ...] [--out DIR]

This is the Drive side of filing, like the ADVBOX exporter: it reads a folder shared with the service account
(read-only), downloads each file straight to a new local folder, exports Google Docs/Sheets/Slides to PDF, checks
Drive's md5 against the bytes, and writes a `sources.json` in the filing input contract
(`jurisync.workflows.filing.contract`). It depends on the contract's validator only; the filing never imports it.
"""

import argparse
import fnmatch
import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from jurisync.connectors.gdrive.auth import SCOPE, ServiceAccount, key_path
from jurisync.connectors.gdrive.client import EXPORTABLE, FOLDER, DriveClient, DriveError, TooLarge
from jurisync.paths import STATE_DIR
from jurisync.workflows.filing.contract import MAX_FILES, write_sources

EXPORTER = "jurisync.gdrive"
DEFAULT_MAX_MB = 50
# Folders a desktop sync client mirrors to the cloud: never write exported client documents there.
SYNCED = re.compile(r"google ?drive|cloudstorage|dropbox|onedrive|icloud|mobile documents|box sync", re.I)


def folder_id(reference):
    """The folder id from an id or a Drive URL (…/folders/<id>, ?id=<id>)."""
    text = str(reference or "").strip()
    match = re.search(r"/folders/([\w-]{10,})", text) or re.search(r"[?&]id=([\w-]{10,})", text)
    if match:
        return match.group(1)
    if re.fullmatch(r"[\w-]{10,}", text):
        return text
    raise ValueError("Give a Google Drive folder id or its URL (https://drive.google.com/drive/folders/<id>)")


def default_destination(folder):
    root = Path(os.environ.get("JURISYNC_FILING_INPUT_DIR") or STATE_DIR / "filing-input")
    return root / f"drive-{folder[:12]}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}"


def _check_destination(destination):
    if SYNCED.search(str(destination)):
        raise ValueError(f"Refusing to write client documents inside a synced folder: {destination}")
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("The destination folder must be new or empty")


def _safe(name):
    """A file or folder name the input contract accepts: no separators, no leading dot."""
    clean = re.sub(r'[\x00-\x1f/\\:*?"<>|]', "_", name).strip().lstrip(".").strip()
    return clean or "documento"


def _walk(client, folder, path, include_subfolders, out, skipped):
    for item in client.children(folder):
        if item["mimeType"] == FOLDER:
            if include_subfolders:
                _walk(client, item["id"], (*path, item["name"]), True, out, skipped)
            else:
                skipped.append({"name": "/".join((*path, item["name"])), "reason": "subfolder (includeSubfolders off)"})
        elif item["mimeType"].startswith("application/vnd.google-apps.") and item["mimeType"] not in EXPORTABLE:
            skipped.append(
                {"name": "/".join((*path, item["name"])), "reason": f"cannot be exported ({item['mimeType']})"}
            )
        else:
            out.append((path, item))


def export_filing_folder(
    client, reference, destination, *, include_subfolders=False, exclude=(), max_mb=DEFAULT_MAX_MB
):
    """Download a shared Drive folder into `destination` (new or empty) as a filing input folder."""
    limit = int(max_mb * 1_000_000)
    root_id = folder_id(reference)
    destination = Path(destination).expanduser().resolve()
    _check_destination(destination)
    root = client.metadata(root_id)
    if root.get("mimeType") != FOLDER:
        raise ValueError(f"{root.get('name')} is not a folder")
    found, skipped = [], []
    _walk(client, root_id, (), include_subfolders, found, skipped)
    chosen = []
    for path, item in found:
        relative = "/".join((*path, item["name"]))
        if any(fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(item["name"], pattern) for pattern in exclude):
            skipped.append({"name": relative, "reason": "excluded"})
            continue
        if item.get("size") and int(item["size"]) > limit:
            raise TooLarge(f"{relative} has {int(item['size']) / 1e6:.1f} MB, more than the {max_mb} MB limit")
        chosen.append((path, item))
    if not chosen:
        raise ValueError(f"The Drive folder {root['name']} has no files to export")
    if len(chosen) > MAX_FILES:
        raise ValueError(f"The Drive folder has more than {MAX_FILES} files")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    entries, taken = [], set()
    try:
        for path, item in chosen:
            folder = destination.joinpath(*(_safe(part) for part in path))
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            name = _safe(item["name"]) + (".pdf" if item["mimeType"] in EXPORTABLE else "")
            stem, suffix, count = Path(name).stem, Path(name).suffix, 1
            while (folder / name).as_posix().casefold() in taken:
                count += 1
                name = f"{stem}-{count}{suffix}"
            target = folder / name
            taken.add(target.as_posix().casefold())
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as sink:
                md5 = client.download(item, sink, limit)
            if item.get("md5Checksum") and item["md5Checksum"] != md5:
                target.unlink()
                raise DriveError(f"{item['name']} arrived different from Drive's copy (md5 mismatch); try again")
            entry = {
                "file": target.relative_to(destination).as_posix(),
                "origin": "drive",
                "originId": item["id"],
                "originPath": "/".join((root["name"], *path)),
                "originalName": item["name"],
                "verifiedBy": EXPORTER,
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            }
            if item.get("modifiedTime"):
                entry["modifiedAt"] = item["modifiedTime"]
            if item.get("webViewLink"):
                entry["link"] = item["webViewLink"]
            entries.append(entry)
        write_sources(destination, entries)
    except BaseException:
        # Leave nothing half-exported behind: a partial folder would look like a complete input.
        for file in sorted(destination.rglob("*"), reverse=True):
            file.unlink() if file.is_file() else file.rmdir()
        destination.rmdir()
        raise
    return {
        "sourceFolder": str(destination),
        "driveFolder": {"id": root_id, "name": root["name"]},
        "serviceAccount": client.account.email,
        "files": [{"name": entry["file"], "originalName": entry["originalName"]} for entry in entries],
        "skipped": skipped,
        "next": "Call pje_filing_prepare with this sourceFolder.",
    }


def check_access(client, reference=None):
    """Prove the key works (a token for drive.readonly) and, given a folder, that it is shared with the account."""
    client.account.token()
    result = {"serviceAccount": client.account.email, "scope": SCOPE, "keyFile": str(key_path())}
    if reference:
        meta = client.metadata(folder_id(reference))
        result["folder"] = {"id": meta["id"], "name": meta["name"], "isFolder": meta.get("mimeType") == FOLDER}
    return result


def make_client():
    from jurisync.connectors.gdrive.client import urllib_http

    return DriveClient(ServiceAccount(urllib_http), urllib_http)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Google Drive (service account, read-only) to filing input folder")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="Test the service-account key, and optionally access to a folder")
    check.add_argument("--folder", help="Folder id or URL to check it is shared with the service account")
    export = commands.add_parser("export", help="Export a folder as a filing input folder")
    export.add_argument("--folder", required=True, help="Folder id or URL")
    export.add_argument("--subfolders", action="store_true", help="Include subfolders")
    export.add_argument("--exclude", action="append", default=[], help="Glob of names/paths to leave out (repeatable)")
    export.add_argument("--max-mb", type=float, default=DEFAULT_MAX_MB, help="Largest file accepted (MB)")
    export.add_argument("--out", type=Path, help="New or empty destination folder")
    args = parser.parse_args(argv)
    client = make_client()
    if args.command == "check":
        result = check_access(client, args.folder)
    else:
        result = export_filing_folder(
            client,
            args.folder,
            args.out or default_destination(folder_id(args.folder)),
            include_subfolders=args.subfolders,
            exclude=args.exclude,
            max_mb=args.max_mb,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
