"""Parse ADVBOX HTML pages and DataTables payloads."""

import json
import os
import re
from datetime import UTC, datetime
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
        "postId": _data_attribute(_cell(row, 0), "post"),
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
