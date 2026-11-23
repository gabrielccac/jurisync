"""JPe reads: home counters, comunicações by status, the account's processes, one process and its documents.

Every page is reached the way a person would reach it: a menu entry of the home page (``client.open_menu``), then a
control on that page (``client.click``). The process window opens only from a row of "Consulta" as it first loads,
which the portal fills with the active processes the account represents (its OAB is pre-filled); the connector never
changes that filter, so it never opens a process the lawyer does not represent (that would log a third-party access
and ask for a justification, see ``client.ThirdPartyAccess``).

Ciência. Opening a comunicação's documents or its "dados da comunicação" registers its reading, so nothing in a
comunicação row is ever clicked. A document of a process with a comunicação "Pendente de Leitura" is refused
(``CienciaPending``) unless the caller acknowledges, and so is any document read while that list could not be read in
full (``CienciaUnchecked``): the act behind the comunicação is usually among the process documents.

The authenticated pages have not been seen yet (see ``parsers``); when one does not have the expected shape the read
fails with ``NotRecognized`` instead of returning a guess.
"""

import hashlib
import re

from jurisync.connectors.jpe import client, parsers
from jurisync.core import ciencia
from jurisync.core.document_text import extract_document_text
from jurisync.core.documents import validate_document_ids
from jurisync.core.documents_dir import save_document

MAX_TEXT_CHARACTERS = 60_000
STATUSES = tuple(parsers.COMMUNICATION_MENU)
CIENCIA_GIVEN = (
    "Este processo tinha comunicação pendente de leitura no JPe: abrir o documento pode ter registrado a ciência e "
    "iniciado o prazo."
)
CIENCIA_UNKNOWN = (
    "Não foi possível conferir se este processo tinha comunicação pendente de leitura no JPe: abrir o documento pode "
    "ter registrado a ciência e iniciado o prazo."
)


class NotRecognized(client.JpeError):
    """A JPe page did not have the shape the parsers expect: nothing was guessed."""


class CienciaPending(ValueError):
    """A document of a process with a comunicação pending reading: refused so the read cannot count as ciência."""


class CienciaUnchecked(ValueError):
    """The "Pendentes de Leitura" list could not be read in full, so a pending ciência cannot be ruled out."""


class ProcessNotFound(LookupError):
    pass


def digits(value):
    return re.sub(r"\D", "", value or "")


def _key(number):
    """The digits that identify a process number given by the caller (CNJ: 20 digits; TJ number: 16)."""
    value = digits(number)
    if len(value) not in (16, 20):
        raise ValueError("process_number must be a CNJ number (0000000-00.0000.8.13.0000) or a TJ number")
    return value


def _matches(item, key):
    return key in (digits(item.get("number")), digits(item.get("tjNumber")))


# --- Counters ---------------------------------------------------------------------------------------------------------


def counters(session):
    """The home totals. Collapsed panels are expanded with their own toggle (a display action, nothing else)."""
    page = client.reload_home(session)
    result = parsers.counters(page.html)
    if result is None or not (result["communications"] and result["processes"]):
        for _, element in parsers.toggle_panels(page.html).items():
            answer = client.click(session, page, element, kind="panel")
            found = parsers.counters(answer.html) or {}
            result = result or {"communications": {}, "processes": {}}
            for group in ("communications", "processes"):
                result[group].update(found.get(group) or {})
    if not result or not (result["communications"] or result["processes"]):
        raise NotRecognized("JPe home page has no counters the connector recognizes")
    return result


# --- Comunicações -----------------------------------------------------------------------------------------------------


def communications(session, status="pendentes_leitura"):
    """One status' comunicações as listed (first page). Listing them never opens or reads any of them."""
    if status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    page = client.open_menu(session, parsers.COMMUNICATION_MENU[status])
    table = parsers.find_grid(page.html, ("processo cnj",)) or parsers.find_grid(page.html, ("processo",))
    if table is None:
        if re.search(r"nenhum (resultado|registro)", parsers.norm(parsers.text_of(parsers.soup_of(page.html)))):
            return {"status": status, "count": 0, "total": 0, "complete": True, "communications": []}
        raise NotRecognized(f"JPe '{status}' page has no comunicações grid the connector recognizes")
    items = []
    for values, _row in parsers.grid_rows(table):
        item = parsers.keyed(values)
        if isinstance(item.get("urgent"), str):
            item["urgent"] = parsers.norm(item["urgent"]) in ("sim", "s", "true")
        items.append(item)
    total = parsers.result_total(table)
    return {
        "status": status,
        "count": len(items),
        "total": total if total is not None else len(items),
        "complete": total is None or total <= len(items),
        "communications": items,
    }


def pending_reading(session):
    """``(items, unchecked reason or None)`` of the comunicações still pending reading."""
    try:
        listed = communications(session, "pendentes_leitura")
    except (client.JpeError, ValueError) as error:
        return [], f"the Pendentes de Leitura list could not be read ({str(error)[:120]})"
    if not listed["complete"]:
        return listed["communications"], (
            f"the Pendentes de Leitura list shows {listed['count']} of {listed['total']} comunicações"
        )
    return listed["communications"], None


def check_ciencia(session, number, acknowledge=False):
    """Refuse (``CienciaPending``/``CienciaUnchecked``) unless acknowledged; return the ``ciencia`` block of the read."""
    key = digits(number)
    items, unchecked = pending_reading(session)
    pending = [item for item in items if _matches(item, key)]
    if not acknowledge:
        if unchecked:
            raise CienciaUnchecked(
                f"ciência not checked: {unchecked}, so it is unknown whether this process has a comunicação pending "
                "reading. Nothing was opened. Opening a document anyway may register the ciência and start the "
                "deadline: only with the user's explicit permission, repeat the call with acknowledgeCiencia=true."
            )
        if pending:
            shown = "; ".join(" ".join(filter(None, (item.get("class"), item.get("sentAt")))) for item in pending[:5])
            raise CienciaPending(
                f"this process has {len(pending)} comunicação(ões) pending reading ({shown}). Nothing was opened. "
                "Opening its documents may register the ciência and start the deadline. Ask the user for explicit "
                "permission; if they agree, repeat the call with acknowledgeCiencia=true."
            )
    if pending:
        return ciencia.report(ciencia.GIVEN, CIENCIA_GIVEN, pending)
    if unchecked:
        return ciencia.report(ciencia.UNKNOWN, CIENCIA_UNKNOWN)
    return ciencia.report(ciencia.NONE)


# --- Processes --------------------------------------------------------------------------------------------------------


def _consulta(session):
    entries = parsers.menu_entries(session.home.html)
    label = next((label for label in parsers.CONSULTA_MENU if label in entries), None)
    if label is None:
        raise NotRecognized("JPe home page has no Consulta menu entry")
    return client.open_menu(session, label)


def _process_grid(page):
    for required in (("processo cnj",), ("processo",), ("numero",)):
        table = parsers.find_grid(page.html, required)
        if table is not None:
            return table
    return None


def my_processes(session):
    """``(items, rows, page, total)`` of the Consulta list as it first loads: the account's active processes."""
    page = _consulta(session)
    table = _process_grid(page)
    if table is None:
        raise NotRecognized("JPe Consulta page has no process grid the connector recognizes")
    items, rows = [], []
    for values, row in parsers.grid_rows(table):
        item = parsers.keyed(values)
        if not item.get("number") and not item.get("tjNumber"):
            continue
        items.append(item)
        rows.append(row)
    return items, rows, page, parsers.result_total(table)


def list_processes(session, query=None):
    items, _, _, total = my_processes(session)
    complete = total is None or total <= len(items)
    if query:
        needle, wanted = parsers.norm(query), digits(query)
        items = [
            item
            for item in items
            if (wanted and (wanted in digits(item.get("number")) or wanted in digits(item.get("tjNumber"))))
            or needle in parsers.norm(" ".join(str(value) for value in item.values()))
        ]
    # "complete" is about the portal's list: a filter applied here narrows a complete list, it does not cut it.
    return {"count": len(items), "total": total, "complete": complete, "query": query or None, "processes": items}


def _view_control(row):
    """The row's "view process" control: the only one whose label reads as viewing, and nothing forbidden."""
    candidates = [
        element
        for element in parsers.row_controls(row)
        if not parsers.is_forbidden(element)
        and any(word in parsers.control_label(element) for word in parsers.VIEW_PROCESS_WORDS)
    ]
    return candidates[0] if len(candidates) == 1 else None


def open_process(session, process_number):
    """``(item, process window page)`` of one of the account's processes; never a process outside its list."""
    key = _key(process_number)
    items, rows, page, _ = my_processes(session)
    for item, row in zip(items, rows, strict=True):
        if _matches(item, key):
            control = _view_control(row)
            if control is None:
                raise NotRecognized("JPe Consulta row has no view-process control the connector recognizes")
            return item, client.click(session, page, control, kind="view_process")
    raise ProcessNotFound(
        "process not in the account's active processes in JPe Consulta (only processes the account represents are "
        "opened; archived ones are not listed there)"
    )


def _tab_contents(session, window):
    """``{tab label: fragment html}`` of the process window, opening (display-only) the tabs not yet rendered."""
    contents = {}
    found = parsers.tabs(window.html)
    for label, (header, content) in found.items():
        if content is not None and parsers.text_of(content):
            contents[label] = str(content)
        elif header.get("onclick") and "A4J.AJAX.Submit" in header.get("onclick"):
            answer = client.click(session, window, header, kind="tab")
            contents[label] = answer.html
    if not found:
        contents[""] = window.html  # no tab structure: the whole window
    return contents


def _movements(fragment):
    result = []
    for headers, rows in parsers.tables(fragment):
        if not any(parsers.COLUMNS.get(header) == "date" for header in headers):
            continue
        if not any(parsers.COLUMNS.get(header) == "description" for header in headers):
            continue
        result.extend(parsers.keyed(values) for values, _ in rows)
    return result


def _documents(fragment):
    """``[(public entry, control or None)]`` of a documents grid; ids come from the control, else the position."""
    entries = []
    for headers, rows in parsers.tables(fragment):
        if not any(parsers.COLUMNS.get(header) == "name" for header in headers):
            continue
        for values, row in rows:
            item = parsers.keyed(values)
            controls = [
                element
                for element in parsers.row_controls(row)
                if not parsers.is_forbidden(element, ignore_text=True)
                and any(word in parsers.control_label(element) for word in parsers.DOWNLOAD_WORDS)
            ]
            entries.append((item, controls[0] if controls else None))
    ids = [next(iter(parsers.numeric_ids(control)), None) if control is not None else None for _, control in entries]
    if None in ids or len(set(ids)) != len(ids):
        ids = [str(position) for position in range(1, len(entries) + 1)]
    result = []
    for (item, control), identifier in zip(entries, ids, strict=True):
        item = {"documentId": identifier, **item, "readable": control is not None}
        result.append((item, control))
    return result


def load_process(session, process_number):
    """``(data, documents with their controls, window page)`` of one of the account's processes."""
    item, window = open_process(session, process_number)
    contents = _tab_contents(session, window)
    details, parties, movements, documents = {}, [], [], []
    for label, fragment in contents.items():
        details.update(parsers.labelled_values(fragment))
        if label in (parsers.MOVEMENT_TAB, ""):
            movements.extend(_movements(fragment))
        if label in (parsers.DOCUMENT_TAB, ""):
            documents.extend(_documents(fragment))
        if label == parsers.PARTIES_TAB:
            for _, rows in parsers.tables(fragment):
                parties.extend(parsers.keyed(values) for values, _ in rows)
    if not details and not movements and not documents:
        raise NotRecognized("JPe process window has no content the connector recognizes")
    data = {
        "number": item.get("number") or parsers.process_title(window.html) or item.get("tjNumber"),
        "tjNumber": item.get("tjNumber"),
        "summary": item,
        "details": details,
        "parties": parties,
        "movements": movements,
        "tabs": sorted(label for label in contents if label),
    }
    return data, documents, window


def get_process(session, process_number, include_documents=True):
    data, documents, _ = load_process(session, process_number)
    result = {key: value for key, value in data.items() if value not in (None, "", [], {})}
    result["movementCount"] = len(data["movements"])
    if include_documents:
        result["documentCount"] = len(documents)
        result["documents"] = [entry for entry, _ in documents]
    return result


# --- Documents --------------------------------------------------------------------------------------------------------


def file_format(content, content_type):
    if content.lstrip().startswith(b"%PDF-") or "pdf" in (content_type or "").lower():
        return "pdf"
    if "html" in (content_type or "").lower() or content.lstrip()[:15].lower().startswith((b"<html", b"<!doc")):
        return "html"
    return "other"


def _selected(documents, document_ids):
    by_id = {entry["documentId"]: (entry, control) for entry, control in documents}
    missing = [value for value in document_ids if value not in by_id]
    if missing:
        raise ValueError(f"unknown document id(s) for this process: {', '.join(missing)}")
    chosen = [by_id[value] for value in document_ids]
    unreadable = [entry["documentId"] for entry, control in chosen if control is None]
    if unreadable:
        raise NotRecognized(f"JPe shows no download control for document(s) {', '.join(unreadable)}")
    return chosen


def read_documents(session, process_number, document_ids, *, acknowledge_ciencia=False):
    """``(number, documents, ciencia block)``: text of 1-5 documents of one of the account's processes."""
    validate_document_ids(document_ids)
    read = check_ciencia(session, process_number, acknowledge_ciencia)
    data, documents, window = load_process(session, process_number)
    results = []
    for entry, control in _selected(documents, document_ids):
        result = {"documentId": entry["documentId"], "name": entry.get("name")}
        results.append(result)
        content, content_type = client.fetch_file(session, window, control)
        if content is None:
            result["extractionStatus"] = content_type
            continue
        status, text = extract_document_text(content, content_type, data["number"] or "", allow_unnumbered=True)
        if status == "session_expired":
            raise client.JpeSessionExpired("JPe answered its login page instead of a document")
        result.update(
            {
                "extractionStatus": status,
                "format": file_format(content, content_type),
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "text": text[:MAX_TEXT_CHARACTERS],
                "textTruncated": len(text) > MAX_TEXT_CHARACTERS,
            }
        )
    return data["number"], results, read


def download_document(session, process_number, document_id, *, acknowledge_ciencia=False):
    validate_document_ids([document_id])
    read = check_ciencia(session, process_number, acknowledge_ciencia)
    data, documents, window = load_process(session, process_number)
    ((entry, control),) = _selected(documents, [document_id])
    result = {"documentId": entry["documentId"], "name": entry.get("name"), "ciencia": read}
    content, content_type = client.fetch_file(session, window, control)
    if content is None:
        return {**result, "saved": False, "reason": content_type}
    kind = file_format(content, content_type)
    status, _ = extract_document_text(content, content_type, data["number"] or "", allow_unnumbered=True)
    if status == "session_expired":
        raise client.JpeSessionExpired("JPe answered its login page instead of a document")
    result.update(
        {
            "format": kind,
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            "extractionStatus": status,
        }
    )
    if status == "access_denied":
        return {**result, "saved": False}
    extension = {"pdf": "pdf", "html": "html"}.get(kind, "bin")
    result["savedAs"] = save_document(f"{digits(data['number'])}_{entry['documentId']}.{extension}", content)
    result["saved"] = True
    return result
