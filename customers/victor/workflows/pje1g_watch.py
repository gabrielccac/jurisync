#!/usr/bin/env python3
"""Poll only the 1G processes listed in a local sheet export.

This is intentionally a one-shot/loop poller: it does not send messages or
write the sheet. It returns changed movements for a later notification step.

The login is Victor's (``JURISYNC_CUSTOMER=victor`` by default): the method his portals.toml declares for
``trf1-1g``, through the PJe connector's session pool, as every PJe tool logs in.
"""

import argparse
import hashlib
import json
import os
import time
from datetime import UTC, datetime
from html import unescape
from pathlib import Path

from bs4 import BeautifulSoup

from jurisync import portals
from jurisync.connectors.pje.parsers import jsf_data, parse_process_number
from jurisync.connectors.pje.search import discover_controls, post_ajax, search_process
from jurisync.connectors.pje.server import sessions
from jurisync.core.mcp import load_env_file
from jurisync.paths import STATE_DIR

PORTAL = portals.pje("trf1-1g")
SEARCH_URL = PORTAL.search_url
BASE_URL = f"https://{PORTAL.host}"
DEFAULT_SHEET = STATE_DIR / "data" / "watched_processes_1g.json"
DEFAULT_STATE = STATE_DIR / "data" / "pje1g_watch_state.json"


def now_utc():
    return datetime.now(UTC).isoformat()


def load_rows(path):
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    result = {}
    for row in rows:
        number = str(row.get("Número do processo", "")).replace("\u00a0", " ").strip()
        if number:
            result.setdefault(number, row)
    return result


def load_json(path, default):
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def quick_check(session, process_number):
    """Search and read only the result row's latest-movement text."""
    parts = parse_process_number(process_number)
    response = session.get(
        SEARCH_URL, allow_redirects=False, headers={"Referer": BASE_URL + "/pje/"}, impersonate="chrome"
    )
    if response.status_code != 200:
        raise RuntimeError(f"search page HTTP {response.status_code}")
    page_html = response.text
    controls = discover_controls(page_html)
    state, fields = jsf_data(page_html)
    if not state:
        raise RuntimeError("search page has no JSF ViewState")

    if controls["init"]:
        first = {
            "AJAXREQUEST": "_viewRoot",
            **fields,
            "fPP": "fPP",
            "javax.faces.ViewState": state,
            controls["init"]: controls["init"],
            "ajaxSingle": controls["init"],
            "AJAX:EVENTS_COUNT": "1",
            "fPP:numeroProcesso:ramoJustica": parts[3],
            "fPP:numeroProcesso:respectivoTribunal": parts[4],
            "autoScroll": "",
        }
        response = post_ajax(session, first, SEARCH_URL)
        state, fields = jsf_data(response.text)
        if not state:
            raise RuntimeError("initialization response has no JSF ViewState")
    second = {
        "AJAXREQUEST": "_viewRoot",
        **fields,
        "fPP": "fPP",
        "javax.faces.ViewState": state,
        controls["search"]: controls["search"],
        "ajaxSingle": controls["search"],
        "fPP:numeroProcesso:numeroSequencial": parts[0],
        "fPP:numeroProcesso:numeroDigitoVerificador": parts[1],
        "fPP:numeroProcesso:Ano": parts[2],
        "fPP:numeroProcesso:ramoJustica": parts[3],
        "fPP:numeroProcesso:respectivoTribunal": parts[4],
        "fPP:numeroProcesso:NumeroOrgaoJustica": parts[5],
        "AJAX:EVENTS_COUNT": "1",
        "autoScroll": "",
    }
    response = post_ajax(session, second, SEARCH_URL)
    state, _ = jsf_data(response.text)
    if not state:
        raise RuntimeError("search response has no JSF ViewState")
    soup = BeautifulSoup(unescape(response.text), "html.parser")
    for row in soup.select("#fPP\\:processosTable tbody tr"):
        link = row.select_one("a.btn-link[title]")
        if link and link.get("title", "").replace("\u00a0", " ").strip() == process_number:
            cells = row.find_all("td")
            return {
                "success": True,
                "numero_processo": process_number,
                "latest_marker": cells[-1].get_text(" ", strip=True) if cells else "",
            }
    return {"success": False, "numero_processo": process_number, "error": "Process not found"}


def movement_key(day, movement):
    value = {
        "date": day.get("date", ""),
        "time": movement.get("time", ""),
        "movement": movement.get("movement", ""),
        "documents": [
            {k: doc.get(k, "") for k in ("id", "title", "download_url")} for doc in movement.get("documents", [])
        ],
    }
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def flatten_movements(timeline):
    result = []
    for day in timeline:
        for movement in day.get("movements", []):
            item = dict(movement)
            item["date"] = day.get("date", "")
            item["key"] = movement_key(day, movement)
            result.append(item)
    return result


def poll_once(args):
    rows = load_rows(args.sheet_json)
    state = load_json(args.state_file, {"processes": {}})
    processes = list(rows)[: args.limit] if args.limit else list(rows)
    changes, errors, baselined = [], [], []

    for number in processes:
        try:
            check = sessions.run(PORTAL.key, lambda session, number=number: quick_check(session, number))
            if not check.get("success"):
                errors.append({"numero_processo": number, "error": check.get("error", "not found")})
                continue
            marker = check.get("latest_marker", "")
            previous = state["processes"].get(number, {})
            old_marker = previous.get("latest_marker")

            if old_marker is None:
                state["processes"][number] = {
                    "latest_marker": marker,
                    "movement_keys": [],
                    "baseline": "quick",
                    "checked_at": now_utc(),
                }
                baselined.append(number)
                continue

            if marker == old_marker:
                previous["checked_at"] = now_utc()
                continue

            detail = sessions.run(
                PORTAL.key, lambda session, number=number: search_process(session, number, portal=PORTAL.key)
            )
            timeline = detail.get("timeline", [])
            movements = flatten_movements(timeline)
            old_keys = set(previous.get("movement_keys", []))
            new_movements = [m for m in movements if m["key"] not in old_keys]
            state["processes"][number] = {
                "latest_marker": marker,
                "movement_keys": [m["key"] for m in movements],
                "baseline": "detail",
                "checked_at": now_utc(),
            }
            changes.append(
                {
                    "numero_processo": number,
                    "vara": rows[number].get("Vara", ""),
                    "drive": rows[number].get("Drive", ""),
                    "previous_marker": old_marker,
                    "latest_marker": marker,
                    "new_movements": new_movements,
                    "baseline_incomplete": not bool(old_keys),
                }
            )
        except Exception as error:
            errors.append({"numero_processo": number, "error": str(error)})

    save_json(args.state_file, state)
    return {
        "success": not errors,
        "checked_at": now_utc(),
        "processes_checked": len(processes),
        "baselined": len(baselined),
        "changed": len(changes),
        "errors": errors,
        "changes": changes,
    }


def main():
    parser = argparse.ArgumentParser(description="Poll controlled 1G PJe processes")
    parser.add_argument("--sheet-json", default=str(DEFAULT_SHEET))
    parser.add_argument("--state-file", default=str(DEFAULT_STATE))
    parser.add_argument("--limit", type=int, default=0, help="Only check the first N rows while testing")
    parser.add_argument("--interval", type=int, default=0, help="Repeat every N seconds; 0 means one run")
    args = parser.parse_args()
    os.environ.setdefault("JURISYNC_CUSTOMER", "victor")
    load_env_file()
    if not Path(args.sheet_json).exists():
        raise SystemExit(f"Sheet JSON not found: {args.sheet_json}")
    while True:
        print(json.dumps(poll_once(args), ensure_ascii=False, indent=2))
        if not args.interval:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
