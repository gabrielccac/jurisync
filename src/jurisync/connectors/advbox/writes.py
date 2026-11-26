"""The plumbing every ADVBOX write shares: the office lock, file staging in ``/s3`` and the single task POST."""

import contextlib
import re

from curl_cffi.requests.exceptions import RequestException

from jurisync.connectors.advbox.parsers import (
    parse_create_response,
)
from jurisync.connectors.advbox.session import require_writes
from jurisync.core import credentials


class UnconfirmedWriteError(RuntimeError):
    """A write was sent and ADVBOX did not confirm it: it may or may not have happened. Never replayed here."""


def _plain(error):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", str(error))).strip()[:300]


class WritesMixin:
    """Shared write steps; the methods that use them require writes themselves."""

    def _exclusive(self):
        """The office lock for a composite write (check, stage, post), so that two calls for one task cannot both
        pass the check. Only ADVBOX requests run inside it."""
        hold = getattr(self.session, "exclusive", None)
        held = hold() if callable(hold) else None
        return held if hasattr(held, "__enter__") else contextlib.nullcontext()

    def discard_staged(self, staged):
        """Best-effort removal of files staged in /s3 for a task that was not written. Returns the codes left."""
        left = []
        for item in staged:
            try:
                self.session.post("/s3", {"id": item["code"], "type": "file", "customer": "", "_method": "DELETE"})
            except (RuntimeError, ValueError, OSError, RequestException):
                left.append(item["code"])
        return left

    def _staged_note(self, staged, *, keep=False):
        """What happened to the staged files, for an error message."""
        if not staged:
            return ""
        codes = ", ".join(item["code"] for item in staged)
        if keep:
            return f" Staged file codes kept because the task may reference them: {codes}."
        left = self.discard_staged(staged)
        if left:
            return f" Staged files could not be removed from ADVBOX; codes left: {', '.join(left)}."
        return f" The {len(staged)} staged file(s) were removed."

    def stage_files(self, files, *, root="posts", folder=None):
        require_writes()
        folder = folder if folder is not None else (self.upload_folder() if files else None)
        if files and (not folder or not re.fullmatch(r"files/temp/\d+", folder)):
            raise ValueError(
                "Set ADVBOX_UPLOAD_FOLDER to this account's files/temp/<user-id> path before uploading attachments"
            )
        staged = []
        for file in files:
            try:
                code = self.session.post_multipart(
                    "/s3",
                    {
                        "file[]": {
                            "bytes": file["bytes"],
                            "filename": file["filename"],
                            "contentType": file.get("contentType") or "application/pdf",
                        },
                        "root": root,
                        "folder": folder,
                        "path_filter": "",
                    },
                ).strip()
            except (RuntimeError, ValueError, OSError, RequestException) as error:
                # One upload failed: nothing will use the files already staged, so they do not stay behind.
                raise RuntimeError(
                    f"ADVBOX upload of {file['filename']} failed ({_plain(error) or type(error).__name__}); "
                    f"no task was written.{self._staged_note(staged)}"
                ) from error
            staged.append({"filename": file["filename"], "code": code})
        return staged

    def _send_task(self, body, staged, action):
        """POST one task body, once. A failure that leaves the outcome unknown (no answer, a 5xx, an answer without
        a post ID) is reported as such and never replayed: a second POST would create a second task."""
        unconfirmed = (
            f"ADVBOX did not confirm the request: the task MAY have been {action}. Search the lawsuit's tasks "
            "(advbox_search_tasks) before retrying; do not repeat the call without checking."
        )
        try:
            response = self.session.post("/posts", body)
        except RequestException as error:
            raise UnconfirmedWriteError(
                f"{unconfirmed} Cause: {type(error).__name__}.{self._staged_note(staged, keep=True)}"
            ) from error
        except RuntimeError as error:
            if re.search(r"failed \(5\d\d\)", str(error)):
                raise UnconfirmedWriteError(
                    f"{unconfirmed} Cause: {_plain(error)}.{self._staged_note(staged, keep=True)}"
                ) from error
            raise RuntimeError(f"{_plain(error)}. No task was {action}.{self._staged_note(staged)}") from error
        try:
            return parse_create_response(response)
        except (ValueError, TypeError, AttributeError) as error:
            raise UnconfirmedWriteError(
                f"{unconfirmed} Cause: {_plain(error)}.{self._staged_note(staged, keep=True)}"
            ) from error

    def upload_folder(self):
        """The account's staging folder: ADVBOX_UPLOAD_FOLDER, else the one the task form announces for this login."""
        configured = credentials.env("ADVBOX_UPLOAD_FOLDER")
        if configured:
            return configured
        if getattr(self, "_discovered_folder", None) is None:
            html = self.session.using(lambda s: s.get_html("/posts/create"))
            found = re.search(r"files/temp/\d+", html)
            self._discovered_folder = found[0] if found else ""
        return self._discovered_folder or None

    def account_id(self):
        folder = self.upload_folder()
        found = re.fullmatch(r"files/temp/(\d+)", folder or "")
        return int(found[1]) if found else None
