"""Named ADVBOX operations: listings, reports, workflows, and guarded task writes."""

import os
import re
from datetime import datetime, timedelta
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

from jurisync.connectors.advbox.parsers import (
    BASE_URL,
    build_create_body,
    build_edit_body,
    normalize_activities,
    normalize_publications,
    normalize_table,
    parse_activity_details,
    parse_create_response,
    parse_lawsuit_report,
    parse_members,
    parse_publication_courts,
    parse_task_form,
    parse_workflow_steps,
    parse_workflows,
)
from jurisync.connectors.advbox.session import AdvboxSession, require_writes

DEFAULT_STEPS = (9, 1, 6, 2, 3, 4, 5, 7)
TABLES = {
    "customers": {
        "columns": ["", "name", "", "city", "created_at"],
        "names": ["selection", "name", "phone", "cityState", "createdAt"],
        "sortable": [False, True, False, True, True],
        "sortColumn": 1,
    },
    "lawsuits": {
        "columns": ["names_order", "type", "process_number", "lawsuits.created_at", "", "date_last_movement"],
        "names": ["parties", "actionType", "processNumber", "createdAt", "lastMovement", "movementDate"],
        "sortable": [True, True, True, True, False, True],
        "sortColumn": 0,
    },
}
ACTIVITY_COLUMNS = ["", "important", "urgent", "", "task", "names_order", "start", "posts.date_deadline", "users.name"]
ACTIVITY_SORTABLE = [False, True, True, False, True, True, True, True, True]


def _js_bool(value):
    return "true" if value else "false"


def positive_integer(value, name, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError(f"{name} must be between 1 and {maximum}")


def table_path(kind, *, page=1, pageSize=50, search="", sortColumn=None, sortDir="asc", dateFrom="", dateUntil=""):
    positive_integer(page, "page", 2**53 - 1)
    positive_integer(pageSize, "pageSize", 100)
    table = TABLES[kind]
    sort = table["sortColumn"] if sortColumn is None else sortColumn
    if not isinstance(sort, int) or not 0 <= sort < len(table["columns"]):
        raise ValueError("sortColumn out of range")
    if sortDir not in ("asc", "desc"):
        raise ValueError("sortDir must be asc or desc")
    for value in (dateFrom, dateUntil):
        if value and not re.fullmatch(r"\d{2}/\d{2}/\d{4}", value):
            raise ValueError("dateFrom/dateUntil must be DD/MM/YYYY")
    query = {
        "sEcho": "1",
        "iColumns": str(len(table["columns"])),
        "sColumns": ",".join(table["columns"]),
        "iDisplayStart": str((page - 1) * pageSize),
        "iDisplayLength": str(pageSize),
        "sSearch": search,
        "bRegex": "false",
        "iSortCol_0": str(sort),
        "sSortDir_0": sortDir,
        "iSortingCols": "1",
        "search": search,
        "report_url": f"report/{kind}",
    }
    for index, sortable in enumerate(table["sortable"]):
        query.update(
            {
                f"mDataProp_{index}": str(index),
                f"sSearch_{index}": "",
                f"bRegex_{index}": "false",
                f"bSearchable_{index}": _js_bool(sortable),
                f"bSortable_{index}": _js_bool(sortable),
            }
        )
    pairs = list(query.items())
    if kind == "customers":
        extra = {name: "" for name in ("type", "state", "city", "partner_id", "date_from", "date_until")}
    else:
        pairs.append(("captured", "all"))
        pairs += [("steps_id[]", str(step)) for step in DEFAULT_STEPS]
        extra = {
            name: ""
            for name in (
                "customer",
                "customer_type",
                "systems_id",
                "partner_id",
                "contingency",
                "date_from",
                "date_until",
            )
        }
    if dateFrom:
        extra["date_from"] = dateFrom
    if dateUntil:
        extra["date_until"] = dateUntil
    pairs += list(extra.items())
    return f"/content/{kind}?{urlencode(pairs)}"


def build_activities_body(
    token,
    *,
    page=1,
    pageSize=50,
    search="",
    completed="pendentes",
    responsibleIds=(),
    dateFrom="",
    dateTo="",
    priorities=(1, 2, 3),
):
    positive_integer(page, "page", 2**53 - 1)
    positive_integer(pageSize, "pageSize", 100)
    body = {
        "sEcho": "1",
        "iColumns": str(len(ACTIVITY_COLUMNS)),
        "sColumns": ",".join(ACTIVITY_COLUMNS),
        "iDisplayStart": str((page - 1) * pageSize),
        "iDisplayLength": str(pageSize),
        "sSearch": search,
        "bRegex": "false",
        "iSortCol_0": "1",
        "sSortDir_0": "desc",
        "iSortCol_1": "2",
        "sSortDir_1": "desc",
        "iSortingCols": "2",
        "_token": token,
        "search": search,
        "completed": completed,
        "tasks_id": "",
        "column": "start",
        "start_from": dateFrom,
        "start_until": dateTo,
        "page": "posts",
        "steps_id": "",
        "sender": "0",
    }
    for index, sortable in enumerate(ACTIVITY_SORTABLE):
        body.update(
            {
                f"mDataProp_{index}": str(index),
                f"sSearch_{index}": "",
                f"bRegex_{index}": "false",
                f"bSearchable_{index}": "true",
                f"bSortable_{index}": _js_bool(sortable),
            }
        )
    pairs = list(body.items())
    pairs += [("multiple_users_id[]", str(user)) for user in responsibleIds]
    pairs += [("priority[]", str(priority)) for priority in priorities]
    return urlencode(pairs)


# Intimações screen presets (Hoje, últimos 3/7/30 dias). The endpoint ignores `interval`; only the dates filter.
PUBLICATION_PERIODS = {"today": 0, "3d": 3, "7d": 7, "30d": 30}
PUBLICATION_PAGE_SIZE_MAX = 200
PUBLICATION_COLUMNS = ["", "start", "", "", "users.name", "completed", ""]
PUBLICATION_SORTABLE = (1, 4, 5)
BRAZIL = ZoneInfo("America/Sao_Paulo")


def publication_dates(period=None, dateFrom="", dateTo="", today=None):
    """Resolve a preset or an explicit range into the ``dd/mm/yyyy`` pair the endpoint requires."""
    if period and (dateFrom or dateTo):
        raise ValueError("pass either period or dateFrom/dateTo, not both")
    if period:
        if period not in PUBLICATION_PERIODS:
            raise ValueError(f"period must be one of {', '.join(PUBLICATION_PERIODS)}")
        end = today or datetime.now(BRAZIL).date()
        return (end - timedelta(days=PUBLICATION_PERIODS[period])).strftime("%d/%m/%Y"), end.strftime("%d/%m/%Y")
    if not (dateFrom and dateTo):
        raise ValueError("pass period (today, 3d, 7d, 30d) or both dateFrom and dateTo")
    start, end = (datetime.strptime(value, "%d/%m/%Y") for value in (dateFrom, dateTo))
    if start > end:
        raise ValueError("dateFrom is after dateTo")
    return dateFrom, dateTo


def build_publications_query(
    token, *, page=1, pageSize=50, dateFrom, dateTo, courtId="", processNumber="", responsibleId=""
):
    """The DataTables query the Intimações screen sends; the column and sort fields are required by the server."""
    positive_integer(page, "page", 2**53 - 1)
    positive_integer(pageSize, "pageSize", PUBLICATION_PAGE_SIZE_MAX)
    columns = len(PUBLICATION_COLUMNS)
    pairs = [
        ("sEcho", "1"),
        ("iColumns", str(columns)),
        ("sColumns", ",".join(PUBLICATION_COLUMNS)),
        ("iDisplayStart", str((page - 1) * pageSize)),
        ("iDisplayLength", str(pageSize)),
    ]
    for index in range(columns):
        pairs += [
            (f"mDataProp_{index}", str(index)),
            (f"sSearch_{index}", ""),
            (f"bRegex_{index}", "false"),
            (f"bSearchable_{index}", "true"),
            (f"bSortable_{index}", "true" if index in PUBLICATION_SORTABLE else "false"),
        ]
    pairs += [
        ("sSearch", ""),
        ("bRegex", "false"),
        ("iSortCol_0", "5"),
        ("sSortDir_0", "asc"),
        ("iSortingCols", "1"),
        ("_token", token),
        ("interval", ""),
        ("user", str(responsibleId)),
        ("date_from", dateFrom),
        ("date_until", dateTo),
        ("customer", ""),
        ("courts_id", str(courtId)),
        ("process_number", processNumber),
        ("report_url", "report/publications"),
    ]
    return urlencode(pairs)


def resolve_court(courts, tribunal):
    """Match a sigla (``TRF5``) to one court option; ambiguous or unknown values list the candidates."""
    wanted = str(tribunal).strip().upper()
    exact = [court for court in courts if court["code"] == wanted]
    if len(exact) == 1:
        return exact[0]
    near = exact or [court for court in courts if wanted in court["label"].upper()]
    if len(near) == 1:
        return near[0]
    options = ", ".join(court["label"] for court in (near or courts)[:12])
    raise ValueError(f"tribunal {tribunal!r} {'is ambiguous' if near else 'was not found'}; options: {options}")


class AdvboxClient:
    def __init__(self, session=None):
        self.session = session or AdvboxSession()
        self._courts = None

    def close(self):
        self.session.close()

    def ensure_session(self):
        self.session.using(lambda s: s.get_json(table_path("customers", page=1, pageSize=1)))
        return {"authenticated": True, "url": f"{BASE_URL}/"}

    def list_customers(self, **options):
        return self.session.using(
            lambda s: normalize_table(s.get_json(table_path("customers", **options)), TABLES["customers"]["names"])
        )

    def list_lawsuits(self, **options):
        return self.session.using(
            lambda s: normalize_table(s.get_json(table_path("lawsuits", **options)), TABLES["lawsuits"]["names"])
        )

    def list_members(self):
        return self.session.using(lambda s: parse_members(s.get_html("/posts/create")))

    def list_workflows(self):
        return self.session.using(lambda s: parse_workflows(s.get_html("/posts/create")))

    def list_lawsuit_partners(self, lawsuit_id):
        payload = self.session.post("/lawsuits/network", {"id": str(lawsuit_id)})
        return [
            {"id": int(entry["id"]), "name": str(entry.get("name") or "")}
            for entry in (payload if isinstance(payload, list) else [])
        ]

    def workflow_steps(self, workflow_id):
        steps, title, total, sequence = [], "", 0, 1
        while True:
            page = parse_workflow_steps(
                self.session.post("/workflow/tasks", {"id": str(workflow_id), "sequence": str(sequence)})
            )
            title = title or page["title"]
            total = page["total"] or sequence
            steps += page["steps"]
            if sequence >= total:
                break
            if sequence > 20:
                raise RuntimeError(f"ADVBOX workflow {workflow_id} exceeded 20 steps")
            sequence += 1
        return {"workflowId": int(workflow_id), "title": title, "totalSteps": total, "steps": steps[:total]}

    def list_activities(self, *, pageSize=50, **options):
        def read(session):
            payload = session.with_csrf(
                lambda token: session.post_content(
                    "/content/posts", build_activities_body(token, pageSize=pageSize, **options), retry_429=True
                )
            )
            return normalize_activities(payload, pageSize)

        return self.session.using(read)

    def find_tasks(self, query, *, responsible=None, limit=25):
        """Find pending tasks whose title, comment, client or process number matches ``query``.

        The activities listing only returns the tasks of the responsibles it is asked for, so by default every
        member is asked. The server ignores the pending/completed switch, so completed tasks are not covered here;
        use ``lawsuit_report`` (with a task filter) for a lawsuit's full history.
        """
        responsible_ids = list(responsible or [user["id"] for user in self.list_members()["users"]])
        page = self.list_activities(
            page=1,
            pageSize=min(max(int(limit), 1), 100),
            search=query,
            completed="pendentes",
            responsibleIds=responsible_ids,
            dateFrom="",
            dateTo="",
        )
        tasks = [
            {
                "postId": record["postId"],
                "tarefa": record["task"],
                "comentario": record["detail"],
                "inicio": record["start"],
                "prazo": record["deadline"],
                "cliente": (record["customer"] or {}).get("name"),
                "responsaveis": [person["name"] for person in record["responsible"]],
            }
            for record in page["records"]
        ][:limit]
        return {"query": query, "count": len(tasks), "total": page["total"], "tasks": tasks}

    def publication_courts(self):
        """Court filter options (id, sigla, label) read once from the Intimações page."""
        if not self._courts:
            self._courts = parse_publication_courts(self.session.using(lambda s: s.get_html("/content/publications")))
        return self._courts

    def list_publications(
        self,
        *,
        period=None,
        dateFrom="",
        dateTo="",
        tribunal="",
        processNumber="",
        responsibleId="",
        page=1,
        pageSize=50,
    ):
        """Intimações received by the office, filtered by period, court, process or responsible (read-only).

        ``period`` is a screen preset (today, 3d, 7d, 30d); otherwise both dates are required. Pages are capped
        at ``PUBLICATION_PAGE_SIZE_MAX`` records, and every record keeps the post ID that opens the intimação.
        """
        date_from, date_until = publication_dates(period, dateFrom, dateTo)
        court = resolve_court(self.publication_courts(), tribunal) if tribunal else None

        def read(session):
            query = build_publications_query(
                session.csrf_token(),
                page=page,
                pageSize=pageSize,
                dateFrom=date_from,
                dateTo=date_until,
                courtId=court["id"] if court else "",
                processNumber=processNumber,
                responsibleId=responsibleId,
            )
            return session.get_json(f"/content/publications?{query}")

        try:
            payload = self.session.using(read)
        except RuntimeError as error:
            if "failed (404)" in str(error):
                raise RuntimeError("ADVBOX Intimações are not enabled for this account") from error
            raise
        return {
            "dateFrom": date_from,
            "dateUntil": date_until,
            "tribunal": court,
            **normalize_publications(payload, pageSize, page),
        }

    def activity_details(self, post_id):
        return self.session.using(
            lambda s: parse_activity_details(s.get_html(f"/posts/{quote(str(post_id), safe='')}/edit?autoread=0"))
        )

    def lawsuit_report(self, lawsuit_id, *, movements=True, publications=True, tasks=True, transactions=True):
        def flag(value):
            return "1" if value else "0"

        query = urlencode(
            {
                "uid": str(lawsuit_id),
                "show_auto_movements": flag(movements),
                "show_manual_movements": flag(movements),
                "show_publications": flag(publications),
                "show_tasks": flag(tasks),
                "show_transactions": flag(transactions),
            }
        )
        return self.session.using(lambda s: parse_lawsuit_report(s.get_html(f"/history?{query}")))

    # -- writes (require ADVBOX_ALLOW_WRITES=true) ---------------------------

    def stage_files(self, files, *, root="posts", folder=None):
        require_writes()
        folder = folder if folder is not None else (self.upload_folder() if files else None)
        if files and (not folder or not re.fullmatch(r"files/temp/\d+", folder)):
            raise ValueError(
                "Set ADVBOX_UPLOAD_FOLDER to this account's files/temp/<user-id> path before uploading attachments"
            )
        staged = []
        for file in files:
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
            staged.append({"filename": file["filename"], "code": code})
        return staged

    def upload_folder(self):
        """The account's staging folder: ADVBOX_UPLOAD_FOLDER, else the one the task form announces for this login."""
        configured = os.environ.get("ADVBOX_UPLOAD_FOLDER")
        if configured:
            return configured
        if getattr(self, "_discovered_folder", None) is None:
            html = self.session.using(lambda s: s.get_html("/posts/create"))
            found = re.search(r"files/temp/\d+", html)
            self._discovered_folder = found[0] if found else ""
        return self._discovered_folder or None

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

    def preview_task(self, options, attachments=()):
        """Validate a task and its files like ``create_task`` does, but send nothing."""
        body = self._task_body(options)
        folder = self.upload_folder() if attachments else None
        if attachments and (not folder or not re.fullmatch(r"files/temp/\d+", folder)):
            raise ValueError(
                "Set ADVBOX_UPLOAD_FOLDER to this account's files/temp/<user-id> path before uploading attachments"
            )
        return {
            "dryRun": True,
            "uploadFolder": folder,
            "body": body,
            "attachments": [
                {"filename": file["filename"], "bytes": len(file["bytes"]), "contentType": file.get("contentType")}
                for file in attachments
            ],
        }

    def create_task(self, options, attachments=()):
        require_writes()
        body = self._task_body(options)
        staged = self.stage_files(list(attachments))
        if staged:
            body["file[]"] = [item["code"] for item in staged]
        return {**parse_create_response(self.session.post("/posts", body)), "staged": staged}

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

    def account_id(self):
        folder = self.upload_folder()
        found = re.fullmatch(r"files/temp/(\d+)", folder or "")
        return int(found[1]) if found else None

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
        from the replacement afterwards, using its own file codes.
        """
        require_writes()
        form, _ = self._edit_body(post_id, changes, expect_comments=expect_comments, remove_files=remove_files)
        staged = self.stage_files(list(attachments))
        body = build_edit_body(
            form, file_codes=[item["code"] for item in staged], account_id=self.account_id(), **changes
        )
        created = parse_create_response(self.session.post("/posts", body))
        removed = []
        if remove_files:
            codes = {item["name"]: item["code"] for item in self.read_task_form(created["postId"])["files"]}
            for name in remove_files:
                if name in codes:
                    self.session.post("/s3", {"id": codes[name], "type": "file", "customer": "", "_method": "DELETE"})
                    removed.append(name)
        raw = created["raw"]
        return {
            "postId": created["postId"],
            "replacedPostId": str((raw.get("kanban") or {}).get("remove") or post_id),
            "message": re.sub(r"<[^>]+>", "", str(raw.get("message") or "")).strip(),
            "staged": staged,
            "removedFiles": removed,
        }

    def complete_task(self, post_id):
        require_writes()
        response = self.session.post(
            f"/posts/status/{quote(str(post_id), safe='')}", {"field": "completed", "value": "1"}
        )
        return {"postId": str(post_id), "response": str(response)[:200]}

    def delete_task(self, post_id, *, scope="this", comments=""):
        require_writes()
        response = self.session.post(
            f"/posts/{quote(str(post_id), safe='')}", {"_method": "PUT", "type": scope, "comments": comments}
        )
        marker = re.search(r'<span class="deleted">([^<]*)</span>', str(response))
        return {"postId": str(post_id), "deleted": bool(marker), "marker": marker[1] if marker else None}
