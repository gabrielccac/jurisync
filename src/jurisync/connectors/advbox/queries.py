"""Request builders for the ADVBOX listings (DataTables queries and form bodies): pure functions, no HTTP."""

import re
from datetime import datetime, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

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
        extra = dict.fromkeys(("type", "state", "city", "partner_id", "date_from", "date_until"), "")
    else:
        pairs.append(("captured", "all"))
        pairs += [("steps_id[]", str(step)) for step in DEFAULT_STEPS]
        extra = dict.fromkeys(
            ("customer", "customer_type", "systems_id", "partner_id", "contingency", "date_from", "date_until"), ""
        )
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
