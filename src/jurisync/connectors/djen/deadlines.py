"""Count DJEN deadlines from availability: a generated base calendar plus sourced court calendars.

Two layers decide whether a day counts:

- the base layer, generated for any year: weekends, national holidays, the CPC art. 220 suspension and, for the
  Federal Justice, the days of Lei 5.010/66 art. 62 and Corpus Christi. Movable dates come from Easter;
- a court's local calendar file (``calendars/<court>-*.json``), which adds that court's closures for the period it
  covers. Outside that period, or for a court without a file, the result is computed from the base layer alone and
  says so (``calendarComplete: false`` plus a warning): local holidays and suspensions still have to be checked.
"""

import argparse
import json
import re
from datetime import date, timedelta
from pathlib import Path

CALENDARS = Path(__file__).with_name("calendars")
DEFAULT_COURT = "JFPE"
# Resolução CNJ 455/2022 art. 11, as amended by Resolução CNJ 569/2024: deadlines count from the DJEN publication
# (first business day after availability) from 16/05/2025. Earlier availabilities followed each court's own rule.
DJEN_RULE_START = date(2025, 5, 16)
FEDERAL_COURT = re.compile(r"JF[A-Z]{2}|SJ[A-Z]{2}|TRF[1-6]")

# Lei 662/1949 (as amended by Lei 10.607/2002), Lei 6.802/1980 (12/10) and Lei 14.759/2023 (20/11).
NATIONAL_HOLIDAYS = {
    (1, 1): "Confraternização Universal",
    (4, 21): "Tiradentes",
    (5, 1): "Dia do Trabalho",
    (9, 7): "Independência",
    (10, 12): "Nossa Senhora Aparecida",
    (11, 2): "Finados",
    (11, 15): "Proclamação da República",
    (11, 20): "Consciência Negra",
    (12, 25): "Natal",
}
# Lei 5.010/1966 art. 62, IV.
FEDERAL_FIXED = {
    (8, 11): "11 de agosto (Lei 5.010/66 art. 62, IV)",
    (11, 1): "Todos os Santos (Lei 5.010/66 art. 62, IV)",
    (11, 2): "Finados (Lei 5.010/66 art. 62, IV)",
    (12, 8): "Dia da Justiça (Lei 5.010/66 art. 62, IV)",
}
BASE_SOURCES = [
    "CPC arts. 219, 220 e 224; Resolução CNJ 455/2022 art. 11 (redação da Resolução CNJ 569/2024)",
    "Lei 662/1949, Lei 6.802/1980, Lei 10.607/2002 e Lei 14.759/2023 (feriados nacionais)",
    "Lei 5.010/1966 art. 62 (feriados da Justiça Federal)",
]


def _iso(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("date must be YYYY-MM-DD")
    return date.fromisoformat(value)


def _br(value):
    return value.strftime("%d/%m/%Y")


def easter(year):
    """Easter Sunday in the Gregorian calendar (anonymous Gregorian algorithm, Meeus/Jones/Butcher)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    g = (b - (b + 8) // 25 + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month, day = divmod(h + m - 7 * n + 114, 31)
    return date(year, month, day + 1)


def is_federal(court):
    return bool(FEDERAL_COURT.fullmatch(court))


def in_cpc_220(day):
    """CPC art. 220: deadlines do not run from 20 December to 20 January, inclusive."""
    return (day.month == 12 and day.day >= 20) or (day.month == 1 and day.day <= 20)


def base_closure(day, federal=True):
    """Why the court is closed on ``day`` in the generated base layer, or ``None`` when it is a business day.

    The CPC art. 220 suspension is not a closure (the court may be open); see ``in_cpc_220``.
    """
    if day.weekday() >= 5:
        return "fim de semana"
    key = (day.month, day.day)
    if key in NATIONAL_HOLIDAYS:
        return NATIONAL_HOLIDAYS[key]
    sunday = easter(day.year)
    if day == sunday - timedelta(days=2):
        return "Sexta-feira Santa"
    if not federal:
        return None
    if key in FEDERAL_FIXED:
        return FEDERAL_FIXED[key]
    if (day.month == 12 and day.day >= 20) or (day.month == 1 and day.day <= 6):
        return "recesso de 20/12 a 06/01 (Lei 5.010/66 art. 62, I)"
    if day in (sunday - timedelta(days=48), sunday - timedelta(days=47)):
        return "Carnaval (Lei 5.010/66 art. 62, III)"
    if sunday - timedelta(days=4) <= day < sunday:
        return "Semana Santa (Lei 5.010/66 art. 62, II)"
    if day == sunday + timedelta(days=60):
        return "Corpus Christi"
    return None


def _court_id(court):
    court = str(court or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{2,12}", court):
        raise ValueError("court must be a court acronym such as JFPE, TRF5 or TJSP")
    return court


def local_calendars(court):
    """The sourced local calendar files of one court (``calendars/<court>-*.json``), oldest first."""
    calendars = []
    for path in sorted(CALENDARS.glob(f"{court.lower()}-*.json")):
        calendar = json.loads(path.read_text(encoding="utf-8"))
        if calendar.get("id") != path.stem:
            raise ValueError("court calendar ID mismatch")
        calendar["_start"] = _iso(calendar["coverage_start"])
        calendar["_end"] = _iso(calendar["coverage_end"])
        calendar["_closed"] = {_iso(day) for day in calendar["closed_dates"]}
        calendars.append(calendar)
    return sorted(calendars, key=lambda calendar: calendar["_start"])


def calculate_deadline(available_on, days, calendar_id=None, court=None):
    """Return the publication, first counted day, and final judicial date.

    `days` is the period in business days (CPC art. 219) already extracted from the act and checked to apply to the
    represented party. This function does not infer a period from prose. ``court`` picks the local calendar; a
    ``calendar_id`` such as ``jfpe-2026-q4`` names the court by its prefix. Without either the court is JFPE.
    """
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 365:
        raise ValueError("days must be an integer from 1 to 365")
    warnings = []
    if calendar_id is not None:
        if not re.fullmatch(r"[a-z0-9-]+", calendar_id):
            raise ValueError("invalid calendar ID")
        if not (CALENDARS / f"{calendar_id}.json").is_file():
            raise ValueError("court calendar unavailable; do not set a task deadline")
        court = court or calendar_id.split("-", 1)[0]
    if not court:
        court = DEFAULT_COURT
        warnings.append(
            f"Nenhum tribunal informado: usado o calendário de {DEFAULT_COURT}. Informe court para outro tribunal."
        )
    court = _court_id(court)
    federal = is_federal(court)
    calendars = local_calendars(court)
    available = _iso(available_on)
    if available < DJEN_RULE_START:
        raise ValueError(
            "availability is outside the verified court calendar: the DJEN counting rule "
            f"(Resolução CNJ 455/2022 art. 11) applies from {DJEN_RULE_START.isoformat()}"
        )

    uncovered = []

    def local(day):
        for calendar in calendars:
            if calendar["_start"] <= day <= calendar["_end"]:
                return calendar
        uncovered.append(day)
        return None

    def court_open(day):
        calendar = local(day)
        return base_closure(day, federal) is None and not (calendar and day in calendar["_closed"])

    def next_day(day, counts):
        day += timedelta(days=1)
        while not counts(day):
            day += timedelta(days=1)
        return day

    def runs(day):
        return not in_cpc_220(day) and court_open(day)

    # The publication is an act of the court, valid on any day it is open; the term itself only runs outside the
    # CPC art. 220 suspension, so its first and last days are never inside it.
    publication = next_day(available, court_open)
    start = next_day(publication, runs)
    counted = [start]
    while len(counted) < days:
        counted.append(next_day(counted[-1], runs))
    due = counted[-1]

    used = [calendar for calendar in calendars if calendar["_start"] <= due and calendar["_end"] >= available]
    complete = not uncovered
    if not complete:
        after = max((calendar["_end"] for calendar in calendars if calendar["_end"] < uncovered[-1]), default=None)
        before = min((calendar["_start"] for calendar in calendars if calendar["_start"] > uncovered[0]), default=None)
        if not calendars:
            period = ""
        elif after and uncovered[-1] > after:
            period = f" após {_br(after)}"
        else:
            period = f" antes de {_br(before)}"
        warnings.append(
            f"Feriados locais e suspensões de expediente de {court}{period} não estão incluídos: o cálculo usa "
            "apenas o calendário-base (fins de semana, feriados nacionais"
            + (", Lei 5.010/66 art. 62" if federal else "")
            + " e a suspensão do art. 220 do CPC). Confira o calendário do tribunal antes de fixar o prazo."
        )
    if not federal:
        warnings.append(
            f"{court} não é da Justiça Federal: Carnaval, quarta e quinta da Semana Santa, Corpus Christi e o "
            "recesso local não foram considerados dias sem expediente."
        )
    suspended = any(in_cpc_220(start + timedelta(days=n)) for n in range((due - start).days + 1))
    first = used[0] if used else None
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
            "id": first["id"] if first else "base",
            "scope": first["scope"] if first else f"Calendário-base ({'Justiça Federal' if federal else 'nacional'})",
            "sources": [source for calendar in used for source in calendar["sources"]] + BASE_SOURCES,
            "note": first.get("note", "") if first else "",
        },
        "court": court,
        "calendarComplete": complete,
        "localCalendars": [
            {"id": c["id"], "coverageStart": c["coverage_start"], "coverageEnd": c["coverage_end"]} for c in calendars
        ],
        "cpc220Suspension": suspended,
        "warnings": warnings,
    }


def main():
    parser = argparse.ArgumentParser(description="Calculate a DJEN judicial deadline")
    parser.add_argument("--available", required=True, help="DJEN data_disponibilizacao, YYYY-MM-DD")
    parser.add_argument("--days", required=True, type=int, help="Period in business days, checked against the act")
    parser.add_argument("--calendar", default=None, help="Local calendar id, e.g. jfpe-2026-q4 (names the court)")
    parser.add_argument("--court", default=None, help=f"Court acronym, e.g. JFPE, TRF5, TJSP (default {DEFAULT_COURT})")
    args = parser.parse_args()
    try:
        result = calculate_deadline(args.available, args.days, args.calendar, args.court)
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as exc:
        result = {"success": False, "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
