"""Named ADVBOX operations: listings, reports, workflows, and guarded task writes."""

import os
import re
from urllib.parse import quote, urlencode

from jurisync.connectors.advbox.parsers import (
    BASE_URL,
    build_create_body,
    normalize_activities,
    normalize_table,
    parse_activity_details,
    parse_create_response,
    parse_lawsuit_report,
    parse_members,
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


class AdvboxClient:
    def __init__(self, session=None):
        self.session = session or AdvboxSession()

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
        folder = folder if folder is not None else os.environ.get("ADVBOX_UPLOAD_FOLDER")
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

    def create_task(self, options, attachments=()):
        require_writes()
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
        staged = self.stage_files(list(attachments))
        if staged:
            body["file[]"] = [item["code"] for item in staged]
        return {**parse_create_response(self.session.post("/posts", body)), "staged": staged}

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
