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

PANEL_SUFFIX = "Painel/painel_usuario/advogado.seam"
PUSH_SUFFIX = "Push/listView.seam?iframe=true"
PROCESS_RE = re.compile(r"\b\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}\b")
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


PUSH_COLUMNS = {
    "Data de inclusão": "registered_at",
    "Email alternativo": "alternate_email",
    "Observação": "observation",
}


def parse_push_items(html):
    """Parse the processes registered in PJe Push (the source of Push emails).

    The grid is the one table with a "Processo" column. TRF5/TRF3/TJAP add the inclusion date, an alternate
    e-mail and an observation; TRF1 lists only the process (1,600 of them on one page), so the other
    columns are read by header name and left empty when the portal has none. A process registered twice
    shows twice, as the portal's own count does.
    """
    soup = BeautifulSoup(html, "html.parser")
    items = []
    full, bare = [], []
    for table in soup.find_all("table"):
        if table.find("table"):
            continue
        heads = [" ".join(head.stripped_strings) for head in table.find_all("th")]
        if "Processo" in heads:
            (full if set(PUSH_COLUMNS) <= set(heads) else bare).append((table, heads))
    # The full grid wins; a table with only the process column is the grid just where no full one exists.
    grids = full or bare
    if not grids and not PROCESS_RE.search(soup.get_text(" ")):
        return []
    if len(grids) != 1:
        raise RuntimeError("Could not uniquely identify the PJe Push process grid")
    table, heads = grids[0]
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        values = [" ".join(c.stripped_strings) for c in cells]
        match = PROCESS_RE.search(" ".join(values))
        if not match:
            continue
        onclick = " ".join(a.get("onclick", "") for a in row.find_all("a"))
        process_id = re.search(r"construirLogicaAcesso\((\d+)\)", onclick)
        item = {"process_number": match.group(0)}
        for head, key in PUSH_COLUMNS.items():
            index = heads.index(head) if head in heads else None
            item[key] = values[index] if index is not None and index < len(values) else ""
        item["process_id"] = int(process_id.group(1)) if process_id else None
        items.append(item)
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
    base_path = urlsplit(base_url).path
    path_prefix = base_path.split("Push/", 1)[0] if "Push/" in base_path else "/"
    for element in soup.find_all(["a", "button", "input"]):
        label = " ".join((element.get_text(" "), element.get("title", ""), element.get("aria-label", ""))).casefold()
        if not any(marker in label for marker in ("próxim", "proxim", "next", "seguinte")):
            continue
        href = element.get("href")
        if href and href not in {"#", "javascript:void(0)"}:
            _safe_url(urljoin(base_url, href), host=base_host, path_prefix=path_prefix)
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
    complete = result_count is not None and rows == result_count and next_control is not True
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
        panel_url = _portal_url(name, f"{config.root}{PANEL_SUFFIX}")
        push_url = _portal_url(name, f"{config.root}{PUSH_SUFFIX}")
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
        _safe_url(push_url_result, host=config.host, path_exact=f"{config.root}Push/listView.seam")
        html = response_html(push)
        items = parse_push_items(html)
        pagination = parse_push_page(html, len(items), base_url=push_url)
        return {
            "success": True,
            "portal": name,
            "push_url": _safe_url(push_url, host=config.host, path_exact=f"{config.root}Push/listView.seam"),
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
