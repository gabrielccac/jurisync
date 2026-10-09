"""Parse ADVBOX HTML pages and DataTables payloads."""

import json
import os
import re
from datetime import UTC, datetime
from html import unescape
from urllib.parse import parse_qs, urljoin, urlsplit

BASE_URL = os.environ.get("ADVBOX_BASE_URL", "https://app.advbox.com.br")
_ENTITIES = (
    (r"&nbsp;|&#160;", " "),
    (r"&bull;|&#8226;", "•"),
    (r"&ordm;", "º"),
    (r"&amp;", "&"),
    (r"&quot;|&#34;", '"'),
    (r"&#39;|&apos;", "'"),
    (r"&lt;", "<"),
    (r"&gt;", ">"),
)


def _group(pattern, text, index=1, flags=0):
    match = re.search(pattern, str(text or ""), flags)
    return match.group(index) if match else None


def decode_cell(value):
    text = re.sub(r"<br\s*/?>", "\n", str(value if value is not None else ""), flags=re.I)
    text = re.sub(r"<[^>]*>", " ", text)
    for pattern, replacement in _ENTITIES:
        text = re.sub(pattern, replacement, text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def _data_attribute(html, name):
    return _group(rf'data-{name}="([^"]*)"', html)


def _brazilian_date(value):
    match = re.search(r"(\d{2})/(\d{2})/(\d{4})", str(value or ""))
    if not match:
        return None
    try:
        return datetime(int(match[3]), int(match[2]), int(match[1]), tzinfo=UTC)
    except ValueError:
        return None


def _cell(row, index):
    return row[index] if index < len(row) and row[index] is not None else ""


def parse_activity_row(row):
    task = re.search(r'<a[^>]*href="posts/(\d+)/edit"[^>]*title="([^"]*)"[^>]*>([\s\S]*?)</a>', str(_cell(row, 4)))
    customer = re.search(r'<a[^>]*href="customers/(\d+)/edit"[^>]*>([\s\S]*?)</a>', str(_cell(row, 5)))
    responsible = [
        {"id": match[1], "name": match[2]}
        for match in re.finditer(r'<img[^>]*profiles/(\d+)\.\w+[^>]*title="([^"]+)"', str(_cell(row, 8)))
    ]

    def cell_date(html):
        label = decode_cell(html)
        absolute = _group(r'title="([^"]*\d{2}/\d{2}/\d{4}[^"]*)"', html)
        return (decode_cell(absolute) if absolute else label), label

    start, start_label = cell_date(_cell(row, 6))
    deadline, deadline_label = cell_date(_cell(row, 7))
    deadline_date = _brazilian_date(deadline)
    return {
        "postId": _data_attribute(_cell(row, 0), "post") or (task[1] if task else None),
        "activityId": _data_attribute(_cell(row, 0), "id"),
        "lawsuitId": _data_attribute(_cell(row, 0), "lawsuit"),
        "taskTemplateId": _data_attribute(_cell(row, 0), "task"),
        "task": decode_cell(task[3] if task else _cell(row, 4)),
        "detail": task[2] if task else "",
        "important": _data_attribute(_cell(row, 1), "value") == "1",
        "urgent": _data_attribute(_cell(row, 2), "value") == "1",
        "viewed": _data_attribute(_cell(row, 3), "value") == "1",
        "customer": {"id": customer[1], "name": decode_cell(customer[2])} if customer else None,
        "start": start,
        "startLabel": start_label,
        "deadline": deadline,
        "deadlineLabel": deadline_label,
        "overdue": bool(deadline_date and deadline_date < datetime.now(UTC)),
        "responsible": responsible,
    }


def _rows(payload, label):
    data = (payload or {}).get("data") or []
    if isinstance(data, str):
        data = json.loads(data)
    if not isinstance(data, list):
        raise ValueError(f"ADVBOX {label} response did not contain an array")
    return data


def _number(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0


def normalize_activities(payload, page_size):
    data = _rows(payload, "activities")
    total = _number(payload.get("iTotalDisplayRecords", 0))
    return {
        "total": total,
        "totalPages": -(-total // page_size) or 1,
        "records": [parse_activity_row(row) for row in data],
    }


def parse_publication_row(row):
    """One row of the Intimações (publications) table: customer, date, court, process, responsible, status, links."""
    post = _group(r'href="posts/(\d+)/edit"', _cell(row, 0))
    responsible = re.search(r'<img[^>]*profiles/(\d+)\.\w+[^>]*title="([^"]*)"', str(_cell(row, 4)))
    links = str(_cell(row, 6))
    return {
        "postId": post,
        "customer": {"id": _group(r"customers/(\d+)/edit", links), "name": decode_cell(_cell(row, 0))},
        "lawsuitId": _group(r"lawsuits/(\d+)/edit", links),
        "date": decode_cell(_cell(row, 1)),
        "court": decode_cell(_cell(row, 2)) or None,
        "processNumber": decode_cell(_cell(row, 3)),
        "responsible": {"id": responsible[1], "name": decode_cell(responsible[2])} if responsible else None,
        "status": "concluida" if "conclu" in decode_cell(_cell(row, 5)).lower() else "pendente",
    }


def normalize_publications(payload, page_size, page=1):
    total = _number(payload.get("iTotalDisplayRecords", 0))
    return {
        "total": total,
        "page": page,
        "pageSize": page_size,
        "totalPages": -(-total // page_size) or 1,
        "hasMore": page * page_size < total,
        "records": [parse_publication_row(row) for row in _rows(payload, "publications")],
    }


def parse_publication_courts(html):
    """Court filter options of the Intimações page: ``[{"id", "code", "label"}]`` (``code`` is the sigla)."""
    select = _group(r'<select[^>]*name="courts_id"[^>]*>([\s\S]*?)</select>', html)
    courts = []
    for match in re.finditer(r'<option[^>]*value="(\d+)"[^>]*>([^<]*)</option>', select or ""):
        label = decode_cell(match[2])
        courts.append({"id": int(match[1]), "code": label.split(" - ", 1)[0].strip().upper(), "label": label})
    return courts


def normalize_table(payload, names):
    data = _rows(payload, "table")
    records = []
    for row in data:
        record = {name: decode_cell(_cell(row, index)) for index, name in enumerate(names)}
        links = list(
            dict.fromkeys(
                match[1] for cell in row for match in re.finditer(r"""href=["']([^"']+)["']""", str(cell or ""), re.I)
            )
        )
        if links:
            record["links"] = links
        records.append(record)
    return {
        "total": _number(payload.get("iTotalRecords", 0)),
        "filteredTotal": _number(payload.get("iTotalDisplayRecords", 0)),
        "totalPages": _number(payload.get("iTotalPage", 0)),
        "records": records,
    }


def _table_rows(table_html, headers):
    rows = []
    for tr in re.finditer(r"<tr[^>]*>([\s\S]*?)</tr>", str(table_html)):
        cells = [decode_cell(cell[1]) for cell in re.finditer(r"<td[^>]*>([\s\S]*?)</td>", tr[1])]
        if not cells or len(cells) < len(headers) - 1:
            continue
        rows.append({name: cells[index] if index < len(cells) else "" for index, name in enumerate(headers)})
    return rows


def parse_lawsuit_report(html):
    source = str(html)
    text = decode_cell(re.sub(r"<script[\s\S]*?</script>", " ", source, flags=re.I))
    info = {}
    for match in re.finditer(r'<td[^>]*align="right"[^>]*>\s*([^<]+?)\s*</td>\s*<td[^>]*>([\s\S]*?)</td>', source):
        label = decode_cell(match[1])
        if label:
            info[label] = decode_cell(match[2])
    sections = {}
    for match in re.finditer(r"<h3[^>]*>\s*([^<]+?)\s*</h3>\s*<table[^>]*>([\s\S]*?)</table>", source):
        title = decode_cell(match[1])
        headers = [
            header
            for header in (decode_cell(th[1]) for th in re.finditer(r"<th[^>]*>([\s\S]*?)</th>", match[2]))
            if header
        ]
        parts = match[2].split("</thead>")
        body = parts[1] if len(parts) > 1 else match[2]
        if title and headers:
            sections[title] = _table_rows(body, headers)
    office = _group(r"Página de impressão\s+Imprimir\s+([^\d]{3,80}?)(?:\s*\(\d{2}\)|\s+\d{2}\s)", text)
    issued = _group(r"Relatório emitido em\s+([0-9/: ]+)", text)
    group = _group(
        r"Processo\s*Nº\s*[\d.-]+\s*-\s*([A-ZÁÉÍÓÚÂÊÔÃÕÇ/-][A-ZÁÉÍÓÚÂÊÔÃÕÇ /-]*?)(?=\s+[A-Z][a-záéíóúâêôãõç]|\s*$)",
        text,
    )
    return {
        "office": office.strip() if office else None,
        "reportIssuedAt": issued.strip() if issued else None,
        "processNumber": _group(r"Processo\s*Nº\s*([\d.-]+)", text),
        "actionGroup": group.strip() if group else None,
        "info": info,
        "sections": sections,
    }


def parse_file_tree(html):
    folders, files = [], []
    for chunk in re.split(r"<li\b", str(html))[1:]:
        folder = re.search(r'class="item folder open-folder[^"]*"[^>]*data-path="([^"]*)"', chunk)
        meta = decode_cell(_group(r'<p class="datetime">([\s\S]*?)</p>', chunk) or "")
        name = _group(r'<p class="name">\s*([^<]*?)\s*</p>', chunk)
        if folder:
            size = _group(r"itens?\s*•\s*([^•]+)$", meta)
            folders.append(
                {
                    "path": folder[1],
                    "name": decode_cell(name if name is not None else folder[1]),
                    "itemCount": int(_group(r"(\d+)\s*itens?", meta) or 0),
                    "size": size.strip() if size else "",
                }
            )
            continue
        file = re.search(r'<a[^>]*href="((?:files|posts-file)/[^"]+)"[^>]*class="item open-new-page"', chunk)
        if file:
            parts = [part.strip() for part in meta.split("•")]
            url = urljoin(BASE_URL + "/", file[1])
            file_id = _group(r'data-type="file"[^>]*data-item="(\d+)"', chunk)
            if file_id is None:
                file_id = (parse_qs(urlsplit(url).query).get("f") or [None])[0]
            files.append(
                {
                    "name": decode_cell(name or ""),
                    "fileId": file_id,
                    "sourcePostId": file[1].split("/")[1] if file[1].startswith("posts-file/") else None,
                    "url": url,
                    "downloadUrl": f"{url}{'&' if '?' in url else '?'}download=true",
                    "uploadedAt": parts[0] if parts else "",
                    "size": parts[1] if len(parts) > 1 else "",
                }
            )
    return {"folders": folders, "files": files}


def parse_activity_details(html):
    source = str(html)
    fields = {}
    for match in re.finditer(
        r'<p class="column-label">\s*([^<]+?)\s*</p>\s*<p class="column-description[^"]*">([\s\S]*?)</p>', source
    ):
        label, value = decode_cell(match[1]), decode_cell(match[2])
        if label and value:
            fields[label] = value
    return {
        "title": decode_cell(_group(r'<h1 class="task-title">([\s\S]*?)(?:<span|</h1>)', source) or ""),
        "points": _group(r'<span class="reward">\s*(\d+)\s*pts', source),
        "lawsuitId": _group(r'href="lawsuits/(\d+)/edit', source),
        "processNumber": _group(r'data-copy="([\d.-]{20,25})"', source),
        "fields": fields,
    }


def parse_hidden_inputs(html):
    fields = {}
    for match in re.finditer(r"""<input[^>]+type=["']?hidden["']?[^>]*>""", str(html), re.I):
        tag = match[0]
        name = _group(r"""name=["']([^"']+)["']""", tag, flags=re.I)
        value = _group(r"""value=["']([^"']*)["']""", tag, flags=re.I) or ""
        if name:
            for entity, character in (("&amp;", "&"), ("&quot;", '"'), ("&#39;", "'"), ("&lt;", "<"), ("&gt;", ">")):
                value = value.replace(entity, character)
            fields[name] = value
    return fields


def parse_csrf_token(html):
    return _group(r"""name=["']csrf-token["'][^>]*content=["']([^"']+)["']""", html, flags=re.I) or _group(
        r"""content=["']([^"']+)["'][^>]*name=["']csrf-token["']""", html, flags=re.I
    )


def parse_members(html):
    users = [
        {"id": int(match[1]), "name": match[2].strip()}
        for match in re.finditer(
            r'<option value="(\d+)">([^<]*)</option>', _group(r'name="user"[^>]*>([\s\S]*?)</select>', html) or ""
        )
    ]
    usernames = {}
    mentions = _group(r'data-users="([^"]*)"', html) or _group(r"data-users='([^']*)'", html)
    if mentions:
        try:
            decoded = mentions.replace("&quot;", '"').replace("&#039;", "'").replace("&amp;", "&")
            for entry in json.loads(decoded):
                if isinstance(entry, dict) and isinstance(entry.get("id"), int) and not isinstance(entry["id"], bool):
                    usernames[entry["id"]] = entry.get("username") or ""
        except (ValueError, TypeError):
            pass  # The mentions block is best-effort.
    for user in users:
        user["username"] = usernames.get(user["id"], "")
    groups = []
    squad = _group(r'(name="squad"[\s\S]*?</select>)', html) or ""
    for match in re.finditer(r'<option value="(\d+)" data-members="([^"]*)">([^<]*)</option>', squad):
        groups.append(
            {
                "id": int(match[1]),
                "name": match[3].strip(),
                "memberIds": [int(item) for item in match[2].split(",") if item],
            }
        )
    return {"users": users, "groups": groups}


def parse_workflows(html):
    select = _group(r'name="workflow"[^>]*>([\s\S]*?)</select>', html) or ""
    return [
        {"id": int(match[1]), "name": re.sub(r"&#0?39;", "'", match[2]).strip()}
        for match in re.finditer(r'<option value="(\d+)">([^<]*)</option>', select)
    ]


def parse_workflow_steps(html):
    flat = re.sub(r"\s+", " ", str(html))
    steps = []
    for match in re.finditer(r'data-task="(\d+)" data-user="(\d*)"[^>]*>(.*?)</a>', flat):
        title = re.sub(r"<[^>]+>", "", match[3]).strip()
        title = re.sub(r"^(?:check\s+)?(?:check_box_outline_blank\s+)?\d+\.\s*", "", title).strip()[:120]
        steps.append({"taskId": int(match[1]), "defaultUserId": int(match[2]) if match[2] else None, "title": title})
    return {
        "title": (_group(r'<h1 class="title">([^<]*)', flat) or "").strip(),
        "sequence": int(_group(r"Tarefa <span[^>]*>(\d+)</span> de (\d+)", flat) or 0),
        "total": int(_group(r"Tarefa <span[^>]*>\d+</span> de (\d+)", flat) or 0),
        "steps": steps,
    }


def parse_create_response(response):
    payload = json.loads(response) if isinstance(response, str) else response
    post_id = ((payload or {}).get("kanban") or {}).get("add")
    if not post_id:
        raise ValueError("ADVBOX task creation returned no post ID")
    return {"postId": str(post_id), "created": payload.get("create") is True, "raw": payload}


def build_create_body(
    *,
    lawsuitId=None,
    tasksId=None,
    guests=(),
    workflowId="",
    workflowSequence="1",
    workflowLimit="",
    date="",
    hour="",
    deadline="",
    comments="",
):
    if not lawsuitId:
        raise ValueError("lawsuitId is required")
    if not tasksId:
        raise ValueError("tasksId is required")
    if not guests:
        raise ValueError("at least one guest (responsible) is required")
    return {
        "lawsuits_id": str(lawsuitId),
        "user": "",
        "squad": "",
        "guests[]": [str(guest) for guest in guests],
        "has_partner": "0",
        "tasks_id": str(tasksId),
        "workflow_id": str(workflowId or 0),
        "workflow_sequence": str(workflowSequence),
        "workflow_limit": str(workflowLimit or 0),
        "workflow": "",
        "steps_id": "",
        "date": date,
        "hour": hour,
        "date_deadline": deadline,
        "date_end": "",
        "hour_end": "",
        "local": "",
        "comments": comments,
        "recurrence": "",
        "repeat_on_week": "all",
        "date_completed": "",
        "create": "1",
        "ai_suggestion": "",
        "create_token": "",
        "editing": "",
        "v2": "1",
    }


def compact_lawsuit_report(report, *, task_search="", max_tasks=0, max_notices=0, max_text=0):
    """Return a bounded view of a lawsuit report: filter task history by text, cap lists, shorten long texts.

    With every option at its default the report is returned unchanged. Totals before bounding are kept in
    ``compact`` so a caller can tell that the list was cut.
    """
    if not (task_search or max_tasks or max_notices or max_text):
        return report
    sections = dict(report.get("sections") or {})
    summary = {}

    def shorten(record, field):
        if max_text and len(record.get(field, "")) > max_text:
            return {**record, field: record[field][:max_text] + "…"}
        return record

    tasks = sections.get("Histórico de tarefas")
    if tasks is not None:
        needle = task_search.casefold().strip()
        if needle:
            tasks = [t for t in tasks if needle in f"{t.get('Descrição', '')} {t.get('Responsável', '')}".casefold()]
        summary["tasksMatched"] = len(tasks)
        if max_tasks:
            tasks = tasks[:max_tasks]
        sections["Histórico de tarefas"] = [shorten(t, "Descrição") for t in tasks]
        summary["tasksReturned"] = len(sections["Histórico de tarefas"])
    notices = sections.get("Intimações")
    if notices is not None:
        summary["noticesTotal"] = len(notices)
        if max_notices:
            notices = notices[:max_notices]
        sections["Intimações"] = [shorten(n, "Intimação") for n in notices]
        summary["noticesReturned"] = len(sections["Intimações"])
    return {**report, "sections": sections, "compact": summary}


TASK_FORM_FLAGS = (
    "important",
    "urgent",
    "display_date",
    "display_schedule",
    "private",
    "toggle_end",
    "all_day",
    "is_recurring",
)
DATE_PATTERN = re.compile(r"\d{2}/\d{2}/\d{4}")
HOUR_PATTERN = re.compile(r"\d{2}:\d{2}")


def _tag(html, element, name):
    found = re.search(rf"""<{element}\b[^>]*\bname=["']{re.escape(name)}["'][^>]*>""", html, re.I)
    return found[0] if found else ""


def _attribute(tag, name):
    found = re.search(rf"""\b{re.escape(name)}=["']([^"']*)["']""", tag, re.I)
    return unescape(found[1]) if found else ""


def _selected_value(html, name):
    block = re.search(rf"""<select\b[^>]*\bname=["']{re.escape(name)}["'][^>]*>([\s\S]*?)</select>""", html, re.I)
    if not block:
        return ""
    chosen = re.search(r"""<option\b[^>]*value=["']([^"']*)["'][^>]*selected""", block[1], re.I)
    return unescape(chosen[1]) if chosen else ""


def parse_task_form(html):
    """Read the pre-filled task form (``GET /posts/create?id=<id>&edit=true``) into plain values."""
    source = str(html)
    editing = _attribute(_tag(source, "input", "editing"), "value")
    token = _attribute(_tag(source, "input", "create_token"), "value")
    lawsuit = re.search(
        r"""<select\b[^>]*name=["']lawsuits_id["'][^>]*>\s*<option value="(\d+)"[^>]*selected""", source
    )
    user = _tag(source, "select", "user")
    guests = [int(item) for item in re.findall(r"\d+", _attribute(user, "data-guests"))]
    task = _attribute(_tag(source, "select", "tasks_id"), "data-task")
    if not (editing and token and lawsuit and guests and task):
        raise ValueError("ADVBOX edit form was not recognised (missing task, lawsuit, guests or token)")
    comments = re.search(r"""<textarea\b[^>]*name=["']comments["'][^>]*>([\s\S]*?)</textarea>""", source, re.I)

    def value(name):
        return _attribute(_tag(source, "input", name), "value")

    def checked(name):
        return any(
            re.search(r"\bchecked\b", tag, re.I)
            for tag in re.findall(rf"""<input\b[^>]*\bname=["']{re.escape(name)}["'][^>]*>""", source, re.I)
        )

    files = [
        {"name": unescape(item[0]), "code": item[1]}
        for item in re.findall(r'class="delete-file[^"]*"[^>]*data-file="([^"]*)"[^>]*data-item="(\d+)"', source)
    ]
    return {
        "editing": editing,
        "createToken": token,
        "lawsuitId": lawsuit[1],
        "tasksId": task,
        "guests": guests,
        "hasPartner": value("has_partner") or "0",
        "workflowId": value("workflow_id") or "0",
        "workflowSequence": value("workflow_sequence") or "1",
        "workflowLimit": value("workflow_limit") or "0",
        "date": value("date"),
        "hour": value("hour"),
        "deadline": value("date_deadline"),
        "dateEnd": value("date_end"),
        "hourEnd": value("hour_end"),
        "local": value("local"),
        "comments": unescape(comments[1]) if comments else "",
        "recurrence": _selected_value(source, "recurrence"),
        "repeatOnWeek": _selected_value(source, "repeat_on_week") or "all",
        "flags": {name: checked(name) for name in TASK_FORM_FLAGS},
        "files": files,
    }


def build_edit_body(
    form,
    *,
    comments=None,
    append_comments=None,
    date=None,
    hour=None,
    deadline=None,
    guests=None,
    file_codes=(),
    account_id=None,
):
    """Rebuild the browser's edit POST from the current form values plus the requested changes.

    ``None`` leaves a field as it is; an empty string clears ``hour`` or ``deadline``.
    """
    text = form["comments"] if comments is None else comments
    if append_comments:
        text = f"{text.rstrip()}\n\n{append_comments}" if text.strip() else append_comments
    members = list(form["guests"] if guests is None else guests)
    if guests is not None and account_id and account_id not in members:
        members.append(account_id)  # the integration account must stay a guest or the task leaves its listings
    if not members:
        raise ValueError("at least one guest (responsible) is required")
    new_date = form["date"] if date is None else date
    new_hour = form["hour"] if hour is None else hour
    new_deadline = form["deadline"] if deadline is None else deadline
    for label, found, pattern in (
        ("date", new_date, DATE_PATTERN),
        ("deadline", new_deadline, DATE_PATTERN),
        ("hour", new_hour, HOUR_PATTERN),
    ):
        if found and not pattern.fullmatch(found):
            raise ValueError(f"{label} must be DD/MM/YYYY" if pattern is DATE_PATTERN else "hour must be HH:MM")
    if not new_date:
        raise ValueError("date is required")
    body = {
        "lawsuits_id": form["lawsuitId"],
        "user": "",
        "squad": "",
        "guests[]": [str(member) for member in members],
        "has_partner": form["hasPartner"],
        "tasks_id": form["tasksId"],
        "workflow_id": form["workflowId"],
        "workflow_sequence": form["workflowSequence"],
        "workflow_limit": form["workflowLimit"],
        "workflow": "",
        "date": new_date,
        "hour": new_hour,
        "date_deadline": new_deadline,
        "date_end": form["dateEnd"],
        "hour_end": form["hourEnd"],
        "local": form["local"],
        "comments": text,
        "recurrence": form["recurrence"],
        "repeat_on_week": form["repeatOnWeek"],
        "create": "1",
        "ai_suggestion": "",
        "create_token": form["createToken"],
        "editing": form["editing"],
        "v2": "1",
    }
    for flag, on in form["flags"].items():
        if on:
            body[flag] = "1"
    if file_codes:
        body["file[]"] = [str(code) for code in file_codes]
    return body
