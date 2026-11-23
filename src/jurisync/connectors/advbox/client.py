"""Named ADVBOX operations: listings, reports, workflows, and guarded task writes."""

import contextlib
import re
from datetime import datetime, timedelta
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

from curl_cffi.requests.exceptions import RequestException

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
    parse_task_filters,
    parse_task_form,
    parse_workflow_steps,
    parse_workflows,
)
from jurisync.connectors.advbox.session import AdvboxSession, require_writes
from jurisync.core import credentials


class UnconfirmedWriteError(RuntimeError):
    """A write was sent and ADVBOX did not confirm it: it may or may not have happened. Never replayed here."""


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


def _plain(error):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", str(error))).strip()[:300]


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


# The Tarefas screen's "Situação" select: the server ignores any other value and returns the pending tasks.
COMPLETED_VALUES = {"pendentes": "pendentes", "concluidas": "finalizados"}
# "Prioridade" checkboxes of the Tarefas filters.
PRIORITIES = {"normal": 1, "urgente": 2, "importante": 3}


def _compact_task(record, detail_chars):
    detail = record.get("detail") or ""
    flags = {key: record[key] for key in ("important", "urgent", "viewed") if record.get(key) is not None}
    return {
        "postId": record["postId"],
        "task": record["task"],
        "detail": detail[:detail_chars] + ("…" if len(detail) > detail_chars else ""),
        "customer": record.get("customer"),
        "lawsuitId": record.get("lawsuitId"),
        "taskTypeId": record.get("taskTemplateId"),
        "start": record.get("start"),
        "deadline": record.get("deadline") or None,
        "overdue": record.get("overdue"),
        "responsible": [person["name"] for person in record.get("responsible") or []],
        **flags,
    }


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
    taskTypeId="",
    stepId="",
    groupIds=(),
    lawsuitTypeIds=(),
):
    """Form body of the Tarefas listing (``POST /content/posts``), as the screen sends it.

    ``taskTypeId`` is a task template id, or ``publications``/``schedule`` for the screen's two built-in types;
    ``stepId`` is a lawsuit stage (Etapa); ``groupIds`` are the lawsuit groups; ``lawsuitTypeIds`` the action types.
    """
    positive_integer(page, "page", 2**53 - 1)
    positive_integer(pageSize, "pageSize", 100)
    if completed not in COMPLETED_VALUES:
        raise ValueError(f"completed must be one of {', '.join(COMPLETED_VALUES)}")
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
        "completed": COMPLETED_VALUES[completed],
        "tasks_id": str(taskTypeId or ""),
        "column": "start",
        "start_from": dateFrom,
        "start_until": dateTo,
        "page": "posts",
        "steps_id": str(stepId or ""),
        "sender": "0",
        "future": "1",
        "sOrigin": "posts",
        "report_url": "report/tasks",
    }
    if responsibleIds:
        body["enable_multiple"] = "1"
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
    pairs += [("groups_id[]", str(group)) for group in groupIds]
    pairs += [("type_lawsuits_id[]", str(kind)) for kind in lawsuitTypeIds]
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
        """One page of the Tarefas listing.

        Without ``responsibleIds`` the server lists only the logged-in user's tasks. The server refuses (HTTP 413)
        a filter that matches several thousand tasks, such as every member's pending tasks or completed tasks
        without a date range; that is reported as a ValueError asking for narrower filters.
        """

        def read(session):
            try:
                payload = session.with_csrf(
                    lambda token: session.post_content(
                        "/content/posts", build_activities_body(token, pageSize=pageSize, **options), retry_429=True
                    )
                )
            except RuntimeError as error:
                if "(413)" not in str(error):
                    raise
                raise ValueError(
                    "ADVBOX refused this task listing because it matches too many tasks (HTTP 413). Narrow it with "
                    "fewer responsibles, a date range (required for completed tasks), a search text, a task type, a "
                    "stage, a group or a lawsuit type."
                ) from None
            return normalize_activities(payload, pageSize)

        return self.session.using(read)

    def task_filters(self):
        """Options of the Tarefas filters: task types, stages, groups, lawsuit types, situations and priorities."""
        if getattr(self, "_task_filters", None) is None:
            self._task_filters = parse_task_filters(self.session.using(lambda s: s.get_html("/content/home")))
        return self._task_filters

    def search_tasks(
        self,
        search="",
        *,
        completed="pendentes",
        responsible=None,
        allResponsibles=None,
        dateFrom="",
        dateTo="",
        taskType="",
        stage="",
        groups=(),
        lawsuitTypes=(),
        priorities=(),
        page=1,
        pageSize=25,
        detailChars=400,
    ):
        """Tasks matching a free text and/or the Tarefas filters, as compact records.

        ``search`` matches the title, comment, customer and process number. Without ``responsible`` a text search
        covers every member and a listing without text only the logged-in user (ADVBOX's own default);
        ``allResponsibles`` overrides that. Completed tasks need a date range: ADVBOX refuses them otherwise.
        """
        if completed == "concluidas" and not (dateFrom and dateTo):
            raise ValueError("Completed tasks need dateFrom and dateTo; ADVBOX refuses them otherwise")
        everyone = bool(search) if allResponsibles is None else allResponsibles
        responsible_ids = list(
            responsible or ([user["id"] for user in self.list_members()["users"]] if everyone else [])
        )
        result = self.list_activities(
            page=page,
            pageSize=pageSize,
            search=search,
            completed=completed,
            responsibleIds=responsible_ids,
            dateFrom=dateFrom,
            dateTo=dateTo,
            taskTypeId=taskType,
            stepId=stage,
            groupIds=list(groups or ()),
            lawsuitTypeIds=list(lawsuitTypes or ()),
            priorities=[PRIORITIES[name] for name in priorities] if priorities else (1, 2, 3),
        )
        tasks = [_compact_task(record, detailChars) for record in result["records"]]
        return {
            "search": search,
            "scope": "responsible" if responsible else ("everyone" if everyone else "logged-in user"),
            "total": result["total"],
            "page": page,
            "pageSize": pageSize,
            "hasMore": page * pageSize < result["total"],
            "tasks": tasks,
        }

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

    def preview_task(self, options, attachments=(), dedupe_key=""):
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
        return preview

    def create_task(self, options, attachments=(), dedupe_key=""):
        """Create one task. With ``dedupe_key`` a pending task of the lawsuit already marked with it is returned
        instead (``duplicate: true``), and a new task gets the marker as the last line of its comment."""
        require_writes()
        marker = dedupe_marker(dedupe_key)
        with self._exclusive():
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
        form = self.read_task_form(post_id)
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
