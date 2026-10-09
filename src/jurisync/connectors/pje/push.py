#!/usr/bin/env python3
"""Read the PJe Push registry: the processes the lawyer asked PJe to e-mail movements for."""

import argparse
import json
import re
import sys
from contextlib import redirect_stdout
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from jurisync import portals
from jurisync.connectors.pje.parsers import response_html
from jurisync.connectors.pje.process import login_portal
from jurisync.core.http import IMPERSONATE, UnsafeUrlError, enable_request_logging, require_url
from jurisync.core.redaction import url_shape

PANEL_PATH = "/pje/Painel/painel_usuario/advogado.seam"
PUSH_PATH = "/pje/Push/listView.seam?iframe=true"
PROCESS_RE = re.compile(r"\b\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}\b")
PUSH_PREFIX = "/pje/Push/listView.seam"
PUSH_TIMEOUT = 90


def _safe_url(value, *, host=None, path_prefix="/pje/", path_exact=None):
    """Validate an internal PJe URL and return its token-free shape."""
    try:
        require_url(
            value,
            hosts={host or urlsplit(value).hostname},
            path=path_exact,
            path_prefix=None if path_exact else path_prefix,
            label="PJe",
        )
    except UnsafeUrlError:
        raise RuntimeError("Unsafe PJe URL rejected") from None
    return url_shape(value)


def _portal_url(name, path):
    config = portals.pje(name)
    value = f"https://{config.host}{path}"
    _safe_url(value, host=config.host, path_exact=urlsplit(value).path)
    return value


def _known_host(value):
    host = urlsplit(value).hostname
    if host not in portals.hosts("pje"):
        raise RuntimeError("Unsafe PJe URL rejected")
    return host


def parse_push_items(html):
    """Parse the processes registered in PJe Push (the source of Push emails)."""
    soup = BeautifulSoup(html, "html.parser")
    items = []
    seen = set()
    grids = [
        table
        for table in soup.find_all("table")
        if not table.find("table")
        and {"Processo", "Data de inclusão", "Email alternativo", "Observação"}.issubset(
            {" ".join(head.stripped_strings) for head in table.find_all("th")}
        )
    ]
    if not grids and not PROCESS_RE.search(soup.get_text(" ")):
        return []
    if len(grids) != 1:
        raise RuntimeError("Could not uniquely identify the PJe Push process grid")
    for row in grids[0].find_all("tr"):
        cells = row.find_all("td")
        values = [" ".join(c.stripped_strings) for c in cells]
        match = PROCESS_RE.search(" ".join(values))
        if not match or match.group(0) in seen:
            continue
        seen.add(match.group(0))
        onclick = " ".join(a.get("onclick", "") for a in row.find_all("a"))
        process_id = re.search(r"construirLogicaAcesso\((\d+)\)", onclick)
        items.append(
            {
                "process_number": match.group(0),
                "registered_at": values[2] if len(values) > 2 else "",
                "alternate_email": values[3] if len(values) > 3 else "",
                "observation": values[4] if len(values) > 4 else "",
                "process_id": int(process_id.group(1)) if process_id else None,
            }
        )
    return items


def parse_push_page(html, rows_returned=None, base_url="https://pje1g.trf1.jus.br/pje/Push/listView.seam"):
    """Parse Push counts/navigation without replaying an unverified page action."""
    text = " ".join(BeautifulSoup(html, "html.parser").stripped_strings)
    result_count_match = re.search(r"(\d+)\s+resultados?\s+encontrados", text, re.I)
    if result_count_match:
        result_count = int(result_count_match.group(1))
    else:
        range_match = re.search(r"\b\d+\s*-\s*\d+\s+de\s+(\d+)\b", text, re.I)
        result_count = int(range_match.group(1)) if range_match else None
    soup = BeautifulSoup(html, "html.parser")
    base_host = _known_host(base_url)
    current_page = None
    total_pages = None
    page_match = re.search(r"(?:p[aá]gina\s*)?(\d+)\s*(?:de|/|of)\s*(\d+)", text, re.I)
    if page_match:
        candidate_page, candidate_total = map(int, page_match.groups())
        if 1 <= candidate_page <= candidate_total:
            current_page, total_pages = candidate_page, candidate_total
    next_control = None
    for element in soup.find_all(["a", "button", "input"]):
        label = " ".join((element.get_text(" "), element.get("title", ""), element.get("aria-label", ""))).casefold()
        if not any(marker in label for marker in ("próxim", "proxim", "next", "seguinte")):
            continue
        href = element.get("href")
        if href and href not in {"#", "javascript:void(0)"}:
            _safe_url(urljoin(base_url, href), host=base_host, path_prefix="/pje/")
        disabled = element.has_attr("disabled") or "disabled" in (element.get("class") or [])
        next_control = not disabled
        break
    if total_pages is None:
        page_numbers = []
        for element in soup.select(".rich-datascr-button, .rf-ds-btn, [class*=pagination] a"):
            match = re.fullmatch(r"\s*(\d+)\s*", element.get_text(" "))
            if match:
                page_numbers.append(int(match.group(1)))
        if page_numbers:
            total_pages = max(page_numbers)
    if current_page is None:
        current = soup.select_one(".rich-datascr-button.rf-ds-btn-on, .rf-ds-btn-on, [aria-current='page']")
        if current and current.get_text(strip=True).isdigit():
            current_page = int(current.get_text(strip=True))
    if current_page and total_pages and current_page > total_pages:
        current_page = total_pages = None
    rows = rows_returned if rows_returned is not None else len(parse_push_items(html))
    complete = result_count is not None and rows == result_count and next_control is False
    return {
        "results_count": result_count,
        "current_page": current_page,
        "total_pages": total_pages,
        "has_next_page": next_control,
        "complete": complete,
    }


def read_push(name, *, session=None):
    config = portals.pje(name)
    owned_session = session is None
    try:
        if owned_session:
            session, _ = login_portal(name)
        panel_url = _portal_url(name, PANEL_PATH)
        push_url = _portal_url(name, PUSH_PATH)
        push = session.get(
            push_url,
            headers={"Referer": panel_url},
            allow_redirects=False,
            timeout=PUSH_TIMEOUT,
            impersonate=IMPERSONATE,
        )
        push_url_result = getattr(push, "url", push_url)
        if push.status_code != 200:
            raise RuntimeError(f"Push list returned HTTP {push.status_code}")
        _safe_url(push_url_result, host=config.host, path_exact=PUSH_PREFIX)
        html = response_html(push)
        items = parse_push_items(html)
        pagination = parse_push_page(html, len(items), base_url=push_url)
        return {
            "success": True,
            "portal": name,
            "push_url": _safe_url(push_url, host=config.host, path_exact=PUSH_PREFIX),
            "pagination": pagination,
            **pagination,
            "rows_returned": len(items),
            "items": items,
            # Push stores watched processes; movement text arrives in the email.
            "content_note": "This page lists watched processes, not the individual movement email text.",
        }
    finally:
        if owned_session and session is not None:
            session.close()


def main():
    parser = argparse.ArgumentParser(description="Read the PJe Push watched-process list")
    parser.add_argument("--portal", choices=portals.keys("pje"))
    args = parser.parse_args()
    if not args.portal:
        parser.error("--portal is required")
    enable_request_logging()

    try:
        with redirect_stdout(sys.stderr):
            output = read_push(args.portal)
    except Exception as error:
        output = {"success": False, "portal": args.portal, "error": str(error)}
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
