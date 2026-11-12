"""Count DJEN deadlines from availability using a sourced court calendar."""

import argparse
import json
import re
from datetime import date, timedelta
from pathlib import Path

CALENDARS = Path(__file__).with_name("calendars")


def _iso(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("date must be YYYY-MM-DD")
    return date.fromisoformat(value)


def _br(value):
    return value.strftime("%d/%m/%Y")


def calculate_deadline(available_on, days, calendar_id="jfpe-2026-q4"):
    """Return the publication, first counted day, and final judicial date.

    `days` is the period already extracted from the act and checked to apply to
    the represented party. This function does not infer a period from prose.
    """
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 365:
        raise ValueError("days must be an integer from 1 to 365")
    if not re.fullmatch(r"[a-z0-9-]+", calendar_id):
        raise ValueError("invalid calendar ID")
    path = CALENDARS / f"{calendar_id}.json"
    if not path.is_file():
        raise ValueError("court calendar unavailable; do not set a task deadline")
    calendar = json.loads(path.read_text(encoding="utf-8"))
    if calendar.get("id") != calendar_id:
        raise ValueError("court calendar ID mismatch")
    coverage_start = _iso(calendar["coverage_start"])
    coverage_end = _iso(calendar["coverage_end"])
    closed = {_iso(day) for day in calendar["closed_dates"]}
    available = _iso(available_on)
    if not coverage_start <= available <= coverage_end:
        raise ValueError("availability is outside the verified court calendar")

    def business_day(day):
        if not coverage_start <= day <= coverage_end:
            raise ValueError("deadline exceeds the verified court calendar")
        return day.weekday() < 5 and day not in closed

    def next_business_day(day):
        day += timedelta(days=1)
        while not business_day(day):
            day += timedelta(days=1)
        return day

    publication = next_business_day(available)
    start = next_business_day(publication)
    counted = [start]
    while len(counted) < days:
        counted.append(next_business_day(counted[-1]))
    due = counted[-1]
    return {
        "success": True,
        "availableOn": available.isoformat(),
        "publicationOn": publication.isoformat(),
        "startsOn": start.isoformat(),
        "days": days,
        "dueOn": due.isoformat(),
        "dueOnBr": _br(due),
        "countedDates": [day.isoformat() for day in counted],
        "calendar": {
            "id": calendar_id,
            "scope": calendar["scope"],
            "sources": calendar["sources"],
            "note": calendar.get("note", ""),
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Calculate a DJEN judicial deadline")
    parser.add_argument("--available", required=True, help="DJEN data_disponibilizacao, YYYY-MM-DD")
    parser.add_argument("--days", required=True, type=int, help="Period in business days, checked against the act")
    parser.add_argument("--calendar", default="jfpe-2026-q4")
    args = parser.parse_args()
    try:
        result = calculate_deadline(args.available, args.days, args.calendar)
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as exc:
        result = {"success": False, "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
