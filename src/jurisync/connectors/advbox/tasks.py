"""Task writes of ``AdvboxClient`` (require ADVBOX_ALLOW_WRITES=true): create, edit, complete, delete."""

import re
from urllib.parse import quote

from jurisync.connectors.advbox.parsers import (
    build_create_body,
    build_edit_body,
    parse_task_form,
)
from jurisync.connectors.advbox.session import require_writes
from jurisync.connectors.advbox.writes import UnconfirmedWriteError, _plain

DEDUPE_KEY = re.compile(r"[^\[\]\r\n]{1,80}")


MAX_DEDUPE_FORM_READS = 10


def dedupe_marker(key):
    """The line that marks a task as created for ``key`` (e.g. a DJEN communication id): ``[ref:<key>]``."""
    key = str(key or "").strip()
    if not key:
        return ""
    if not DEDUPE_KEY.fullmatch(key):
        raise ValueError("dedupeKey is one line of up to 80 characters without square brackets")
    return f"[ref:{key}]"


def _with_marker(comments, marker):
    """``comments`` with ``marker`` on its own last line (once)."""
    if marker in comments:
        return comments
    return f"{comments.rstrip()}\n{marker}" if comments.strip() else marker


class TasksMixin:
    """Guarded task writes: each checks the task first and never replays an unconfirmed write."""

    def _task_body(self, options):
        body = build_create_body(**options)
        # Optional template guard: when a workflow is named, the task type must be one of its steps.
        if options.get("workflowId"):
            workflow = self.workflow_steps(options["workflowId"])
            sequence = int(options.get("workflowSequence") or 1)
            step = workflow["steps"][sequence - 1] if 0 < sequence <= len(workflow["steps"]) else None
            if not step or step["taskId"] != int(options["tasksId"]):
                raise ValueError(
                    f"ADVBOX task {options['tasksId']} is not step {options.get('workflowSequence') or 1} "
                    f"of workflow {options['workflowId']}"
                )
            body["workflow"] = str(options["workflowId"])
            body["workflow_limit"] = str(workflow["totalSteps"])
        return body

    def find_by_marker(self, lawsuit_id, dedupe_key):
        """The pending task of ``lawsuit_id`` whose comment carries ``[ref:<dedupe_key>]``, or None.

        The listing is searched by the key. A row that names this lawsuit and shows the marker is the task; a row
        that lacks either (rows of other users carry no lawsuit ID, and the listing may shorten the comment) is
        confirmed on the task's own form, for at most ``MAX_DEDUPE_FORM_READS`` rows."""
        marker = dedupe_marker(dedupe_key)
        unsure = []
        for page in range(1, 4):
            result = self.search_tasks(str(dedupe_key).strip(), page=page, pageSize=100, detailChars=5000)
            for task in result["tasks"]:
                listed = str(task.get("lawsuitId") or "")
                if listed and listed != str(lawsuit_id):
                    continue
                if listed and marker in (task.get("detail") or ""):
                    return task
                unsure.append(task)
            if not result["hasMore"]:
                break
        for task in unsure[:MAX_DEDUPE_FORM_READS]:
            form = self.read_task_form(task["postId"])
            if marker in form["comments"] and str(form.get("lawsuitId") or "") == str(lawsuit_id):
                return task
        return None

    def preview_task(self, options, attachments=(), dedupe_key="", stage_id="", sub_stage_id=""):
        """Validate a task and its files like ``create_task`` does, but send nothing."""
        marker = dedupe_marker(dedupe_key)
        if marker:
            options = {**options, "comments": _with_marker(options.get("comments") or "", marker)}
        body = self._task_body(options)
        folder = self.upload_folder() if attachments else None
        if attachments and (not folder or not re.fullmatch(r"files/temp/\d+", folder)):
            raise ValueError(
                "Set ADVBOX_UPLOAD_FOLDER to this account's files/temp/<user-id> path before uploading attachments"
            )
        preview = {
            "dryRun": True,
            "uploadFolder": folder,
            "body": body,
            "attachments": [
                {"filename": file["filename"], "bytes": len(file["bytes"]), "contentType": file.get("contentType")}
                for file in attachments
            ],
        }
        if marker:
            existing = self.find_by_marker(options["lawsuitId"], dedupe_key)
            preview.update(dedupeMarker=marker, duplicate=bool(existing), existingTask=existing)
        if stage_id:
            preview["stage"] = self.update_lawsuit_stage(options["lawsuitId"], stage_id, sub_stage_id, dry_run=True)
        return preview

    def create_task(self, options, attachments=(), dedupe_key="", stage_id="", sub_stage_id=""):
        """Create one task. With ``dedupe_key`` a pending task of the lawsuit already marked with it is returned
        instead (``duplicate: true``), and a new task gets the marker as the last line of its comment.

        With ``stage_id`` the lawsuit is then moved to that stage (``update_lawsuit_stage``); the stage is checked
        before the task is written, and a failed move after the task exists is reported in ``stage.error`` and
        ``warnings`` instead of raising, so the caller still learns the new post ID."""
        require_writes()
        marker = dedupe_marker(dedupe_key)
        with self._exclusive():
            if stage_id:
                # Refuse an unknown stage or sub-stage before anything is written.
                self.update_lawsuit_stage(options["lawsuitId"], stage_id, sub_stage_id, dry_run=True)
            created = self._create_task(options, attachments, dedupe_key, marker)
            if stage_id:
                created["warnings"] = []
                try:
                    created["stage"] = self.update_lawsuit_stage(options["lawsuitId"], stage_id, sub_stage_id)
                except Exception as error:  # noqa: BLE001 - the task exists: report, never raise
                    created["stage"] = {"changed": False, "error": _plain(error) or type(error).__name__}
                    created["warnings"].append(
                        f"The task was created (postId {created['postId']}), but the lawsuit stage was not changed "
                        "as asked; see stage.error. Do not create the task again: call advbox_update_lawsuit_stage."
                    )
            return created

    def _create_task(self, options, attachments, dedupe_key, marker):
        """The task write itself; the caller holds the office lock."""
        if marker:
            existing = self.find_by_marker(options["lawsuitId"], dedupe_key)
            if existing:
                return {
                    "postId": existing["postId"],
                    "created": False,
                    "duplicate": True,
                    "dedupeMarker": marker,
                    "task": existing,
                    "staged": [],
                }
            options = {**options, "comments": _with_marker(options.get("comments") or "", marker)}
        body = self._task_body(options)
        staged = self.stage_files(list(attachments))
        if staged:
            body["file[]"] = [item["code"] for item in staged]
        created = {**self._send_task(body, staged, "created"), "staged": staged}
        if marker:
            created.update(duplicate=False, dedupeMarker=marker)
        return created

    def is_pending(self, post_id, guests, *, max_pages=6):
        """True when ``post_id`` is listed among the pending tasks of ``guests``.

        An edit replaces a task, but the old post stays readable; this stops an edit of a stale, completed or
        deleted post from creating a second copy.
        """
        for page in range(1, max_pages + 1):
            result = self.list_activities(
                page=page,
                pageSize=100,
                search="",
                completed="pendentes",
                responsibleIds=list(guests),
                dateFrom="",
                dateTo="",
            )
            if any(record["postId"] == str(post_id) for record in result["records"]):
                return True
            if len(result["records"]) < 100:
                return False
        return False

    def read_task_form(self, post_id):
        """The task's pre-filled edit form as plain values (read-only)."""
        return self.session.using(
            lambda s: parse_task_form(s.get_html(f"/posts/create?id={quote(str(post_id), safe='')}&edit=true"))
        )

    def _edit_body(self, post_id, changes, file_codes=(), expect_comments="", remove_files=()):
        form = self.read_task_form(post_id)
        if form["editing"] != str(post_id):
            raise ValueError(f"ADVBOX returned the form of task {form['editing']}, not {post_id}")
        if not self.is_pending(post_id, form["guests"]):
            raise ValueError(
                f"ADVBOX task {post_id} is not among the pending tasks of its guests "
                "(already replaced by an edit, completed or deleted); refusing to edit"
            )
        if expect_comments and expect_comments not in form["comments"]:
            raise ValueError("The task's current comment does not contain the expected text; refusing to edit")
        missing = [name for name in remove_files if name not in {item["name"] for item in form["files"]}]
        if missing:
            raise ValueError(f"The task has no attachment named {', '.join(missing)}")
        body = build_edit_body(form, file_codes=file_codes, account_id=self.account_id(), **changes)
        return form, body

    def preview_update(self, post_id, changes, attachments=(), expect_comments="", remove_files=()):
        """Validate an edit and show the body that would be sent, changing nothing."""
        form, body = self._edit_body(post_id, changes, expect_comments=expect_comments, remove_files=remove_files)
        return {
            "dryRun": True,
            "postId": str(post_id),
            "current": {key: form[key] for key in ("tasksId", "guests", "date", "hour", "deadline", "comments")},
            "files": [item["name"] for item in form["files"]],
            "newFiles": [file["filename"] for file in attachments],
            "removeFiles": list(remove_files),
            "body": {key: ("<token>" if key == "create_token" else value) for key, value in body.items()},
        }

    def update_task(self, post_id, changes, attachments=(), expect_comments="", remove_files=()):
        """Edit a task the way the browser does: ADVBOX replaces it, so the task gets a NEW post ID.

        Existing attachments are kept. New files are staged first; files named in ``remove_files`` are deleted
        from the replacement afterwards, using its own file codes. The check, the staging and the POST run under
        the office lock. Once the replacement exists nothing raises: the caller must learn the new post ID, so a
        failed removal is reported in ``removeErrors`` and ``warnings``.
        """
        require_writes()
        with self._exclusive():
            form, _ = self._edit_body(post_id, changes, expect_comments=expect_comments, remove_files=remove_files)
            staged = self.stage_files(list(attachments))
            body = build_edit_body(
                form, file_codes=[item["code"] for item in staged], account_id=self.account_id(), **changes
            )
            created = self._send_task(body, staged, f"replaced (task {post_id} would then no longer exist)")
            removed, errors = [], []
            if remove_files:
                try:
                    codes = {item["name"]: item["code"] for item in self.read_task_form(created["postId"])["files"]}
                except Exception as error:  # noqa: BLE001 - the replacement exists: report, never raise
                    codes = None
                    errors = [
                        {"name": name, "error": f"replacement form not read: {_plain(error)}"} for name in remove_files
                    ]
                for name in remove_files if codes is not None else ():
                    if name not in codes:
                        errors.append({"name": name, "error": "not found among the replacement's attachments"})
                        continue
                    try:
                        self.session.post(
                            "/s3", {"id": codes[name], "type": "file", "customer": "", "_method": "DELETE"}
                        )
                        removed.append(name)
                    except Exception as error:  # noqa: BLE001 - same: the new post ID must reach the caller
                        errors.append({"name": name, "error": _plain(error) or type(error).__name__})
        raw = created["raw"] if isinstance(created.get("raw"), dict) else {}
        warnings = []
        if errors:
            warnings.append(
                f"The task was replaced (new postId {created['postId']}), but {len(errors)} attachment(s) were not "
                "removed; see removeErrors. Do not repeat the edit with the old postId."
            )
        return {
            "postId": created["postId"],
            "replacedPostId": str((raw.get("kanban") or {}).get("remove") or post_id),
            "message": re.sub(r"<[^>]+>", "", str(raw.get("message") or "")).strip(),
            "staged": staged,
            "removedFiles": removed,
            "removeErrors": errors,
            "warnings": warnings,
        }

    def _guarded_task(self, post_id, expect_comments, action):
        """The task's current form, after the same guards as an edit: the form is this task's and its comment
        contains ``expect_comments``."""
        try:
            form = self.read_task_form(post_id)
        except RuntimeError as error:
            if "(403)" not in str(error):
                raise
            # ADVBOX does not open a completed task's form, and refuses to delete a completed task.
            raise ValueError(
                f"ADVBOX refused to open task {post_id} (403): it is probably completed, and ADVBOX neither opens nor "
                f"deletes a completed task. Nothing was sent; reopen the task in ADVBOX first if it must be {action}d."
            ) from None
        if form["editing"] != str(post_id):
            raise ValueError(f"ADVBOX returned the form of task {form['editing']}, not {post_id}")
        if expect_comments and expect_comments not in form["comments"]:
            raise ValueError(f"The task's current comment does not contain the expected text; refusing to {action}")
        return form

    @staticmethod
    def _affected(post_id, form, pending):
        return {
            "postId": str(post_id),
            "pending": pending,
            "current": {key: form[key] for key in ("tasksId", "guests", "date", "hour", "deadline", "comments")},
            "files": [item["name"] for item in form["files"]],
        }

    def complete_task(self, post_id, *, expect_comments="", dry_run=False):
        """Mark a pending task completed and confirm it left the pending list; ``dry_run`` only shows the task."""
        if not dry_run:
            require_writes()
        with self._exclusive():
            form = self._guarded_task(post_id, expect_comments, "complete")
            pending = self.is_pending(post_id, form["guests"])
            if dry_run:
                return {"dryRun": True, "action": "complete", **self._affected(post_id, form, pending)}
            if not pending:
                raise ValueError(
                    f"ADVBOX task {post_id} is not among the pending tasks of its guests "
                    "(already completed, replaced by an edit or deleted); refusing to complete"
                )
            response = self.session.post(
                f"/posts/status/{quote(str(post_id), safe='')}", {"field": "completed", "value": "1"}
            )
            if self.is_pending(post_id, form["guests"]):
                raise UnconfirmedWriteError(
                    f"ADVBOX answered, but task {post_id} is still listed as pending: the completion is not "
                    f"confirmed. Check the task before retrying. Response: {_plain(response)[:200] or '(empty)'}"
                )
        return {"postId": str(post_id), "completed": True, "confirmedBy": "no longer among the pending tasks"}

    def delete_task(self, post_id, *, scope="this", comments="", expect_comments="", dry_run=False):
        """Soft-delete a task and confirm it from ADVBOX's answer; ``dry_run`` only shows the task."""
        if not dry_run:
            require_writes()
        with self._exclusive():
            form = self._guarded_task(post_id, expect_comments, "delete")
            if dry_run:
                pending = self.is_pending(post_id, form["guests"])
                return {"dryRun": True, "action": "delete", "scope": scope, **self._affected(post_id, form, pending)}
            response = self.session.post(
                f"/posts/{quote(str(post_id), safe='')}", {"_method": "PUT", "type": scope, "comments": comments}
            )
        marker = re.search(r'<span class="deleted">([^<]*)</span>', str(response))
        if not marker:
            raise UnconfirmedWriteError(
                f"ADVBOX answered without its deletion marker: the deletion of task {post_id} is not confirmed. "
                f"Check the task before retrying. Response: {_plain(response)[:200] or '(empty)'}"
            )
        return {"postId": str(post_id), "deleted": True, "scope": scope, "marker": marker[1]}
