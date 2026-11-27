"""The process number filter of the Eproc lists, which no list form takes and is applied here over the rows read.

Pure functions; the requests stay in ``client``.
"""

import re
from datetime import date

from jurisync.core.errors import InputError


def process_digits(value):
    """The 20 digits of a CNJ number given with or without punctuation; None stays None."""
    if value is None:
        return None
    digits = re.sub(r"[\s.-]", "", value)
    if not re.fullmatch(r"\d{20}", digits):
        raise InputError("processNumber must be a CNJ number, e.g. 5000001-00.2026.4.04.7200")
    return digits


def intimation_dates(date_from=None, date_to=None):
    """``AAAA-MM-DD`` bounds as the portal's ``DD/MM/AAAA`` (None stays None), checked before anything is read."""
    days = []
    for name, value in (("intimationDateFrom", date_from), ("intimationDateTo", date_to)):
        try:
            day = None if value is None else date.fromisoformat(value) if len(value) == 10 else ""
        except ValueError:
            day = ""
        if day == "":
            raise InputError(f"{name} must be a date as YYYY-MM-DD")
        days.append(day)
    if None not in days and days[0] > days[1]:
        raise InputError("intimationDateFrom must not be after intimationDateTo")
    return tuple(day and day.strftime("%d/%m/%Y") for day in days)


def by_process(result, process_number=None):
    """``result`` with ``processNumber`` beside ``number`` in every item (kept for one cycle) and, with ``process_number``
    (``process_digits``), only that process' rows; the second item is the list total: the portal's count, or with the
    filter the rows found when every row was read, else None."""
    items = [{**item, "processNumber": item.get("number")} for item in result.get("items") or []]
    pagination = result.get("pagination") or {}
    if not process_number:
        return {**result, "items": items}, pagination.get("totalItems")
    items = [item for item in items if re.sub(r"\D", "", item["processNumber"] or "") == process_number]
    complete = bool(pagination.get("complete"))
    note = "processNumber was filtered here over the rows read (the portal's list has no number filter)"
    if not complete:
        note += "; rows remain unread, so it may be on a later page: raise maxPages"
    notes = [*result.get("notes", []), note]
    return {**result, "items": items, "notes": notes}, len(items) if complete else None
