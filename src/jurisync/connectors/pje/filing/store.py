"""Where a filing run's files live: one RunStore per run, addressed by names relative to the run.

Every reader and writer of a run (prepare, finalize, approve, the PJe connector) goes through a RunStore, so no code
outside this module joins paths onto the run folder and nothing in a run's JSON holds an absolute path. A run is
therefore self-contained: copied to another folder (or, later, kept in a bucket) it stays valid.

A run holds:

    facts.json  review.json  payload.json  approval.json  manifest.json  checkpoint.json  filing-steps.json
    documents/NN-name.pdf     the assembled PDFs the payload names, in filing order
    work/...                  downloads, conversions and splits that prepare works from

`LocalRunStore` keeps a run in a folder (0700 directories, 0600 files, atomic replaces). A cloud store implements the
same methods; `local_path` and `workspace` are its seams to tools that need a real file: a remote store would hand
back a cached copy and upload a workspace's files when it is synced.
"""

import json
import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Protocol

from jurisync.connectors.pje.filing.checkpoint import run_directory

FACTS = "facts.json"
REVIEW = "review.json"
PAYLOAD = "payload.json"
APPROVAL = "approval.json"
MANIFEST = "manifest.json"
CHECKPOINT = "checkpoint.json"
STEPS = "filing-steps.json"
DOCUMENTS = "documents"
WORK = "work"


def run_name(name):
    """Check a name relative to a run: POSIX, no absolute path, no `..`, no empty segment."""
    if not isinstance(name, str) or not name or "\\" in name:
        raise ValueError(f"Invalid run file name: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in ("", ".", "..") for part in name.split("/")):
        raise ValueError(f"Run file names must be relative to the run, without '..': {name!r}")
    return str(path)


class RunStore(Protocol):
    """The files of one filing run. Names are relative POSIX paths such as `documents/01-PETICAO INICIAL.pdf`."""

    label: str

    def exists(self, name: str) -> bool: ...
    def read_bytes(self, name: str) -> bytes: ...
    def read_json(self, name: str): ...
    def write_bytes(self, name: str, content: bytes) -> None: ...
    def write_json(self, name: str, value) -> None: ...
    def delete(self, name: str) -> None: ...
    def delete_tree(self, prefix: str) -> None: ...
    def list(self, prefix: str) -> list[str]: ...
    def local_path(self, name: str) -> Path: ...
    def workspace(self, name: str) -> Path: ...
    def name_of(self, path) -> str: ...


class LocalRunStore:
    """A run kept in one local folder."""

    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self.label = self.root.name

    def _path(self, name):
        return self.root.joinpath(*run_name(name).split("/"))

    def _mkdir(self, directory):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    def ensure(self):
        self._mkdir(self.root)
        return self

    def exists(self, name):
        return self._path(name).exists()

    def read_bytes(self, name):
        return self._path(name).read_bytes()

    def read_json(self, name):
        return json.loads(self.read_bytes(name).decode("utf-8"))

    def write_bytes(self, name, content):
        """Replace the file atomically with owner-only access, so a reader never sees half a file."""
        destination = self._path(name)
        self._mkdir(destination.parent)
        descriptor, temp = tempfile.mkstemp(dir=destination.parent, prefix=f".{destination.stem}-")
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(content)
            os.chmod(temp, 0o600)
            os.replace(temp, destination)
        except BaseException:
            Path(temp).unlink(missing_ok=True)
            raise

    def write_json(self, name, value):
        self.write_bytes(name, json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"))

    def delete(self, name):
        self._path(name).unlink(missing_ok=True)

    def delete_tree(self, prefix):
        shutil.rmtree(self._path(prefix), ignore_errors=True)

    def list(self, prefix):
        base = self._path(prefix)
        if not base.is_dir():
            return []
        return sorted(self.name_of(path) for path in base.rglob("*") if path.is_file())

    def local_path(self, name):
        """A local file holding this name's content, for tools that need a path (PDF readers, uploads)."""
        return self._path(name)

    def workspace(self, name):
        """A local folder inside the run for tools that write files (downloads, conversions, splits)."""
        directory = self._path(name)
        self._mkdir(directory)
        return directory

    def name_of(self, path):
        """The run name of a local file inside this run; files elsewhere are refused."""
        resolved = Path(path).expanduser().resolve()
        try:
            return resolved.relative_to(self.root).as_posix()
        except ValueError:
            raise ValueError(f"{resolved.name} is outside the run folder") from None


def open_run(run_id):
    """The store of a named run under the shared filing root (`JURISYNC_FILING_DIR`)."""
    return LocalRunStore(run_directory(run_id))
