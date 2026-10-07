#!/usr/bin/env python3
"""Read PJe lawyer-panel notification counters and populated buckets."""

import argparse
import json
import re
import sys
from contextlib import redirect_stdout
from html import unescape
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from jurisync import portals
from jurisync.connectors.pje import jsf
from jurisync.connectors.pje.parsers import response_html
from jurisync.connectors.pje.process import login_portal
from jurisync.core.http import IMPERSONATE, UnsafeUrlError, enable_request_logging, require_url
from jurisync.core.redaction import url_shape

PANEL_PATH = "/pje/Painel/painel_usuario/advogado.seam"
PUSH_PATH = "/pje/Push/listView.seam?iframe=true"
BUCKET_RE = re.compile(r"formAbaExpediente:listaAgrSitExp:(\d+):linhaN1$")
PROCESS_RE = re.compile(r"\b\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}\b")
PANEL_PREFIX = "/pje/Painel/painel_usuario/advogado.seam"
PUSH_PREFIX = "/pje/Push/listView.seam"
REQUEST_TIMEOUT = 20
PUSH_TIMEOUT = 90


def _count(text):
    match = re.search(r"(\d+)\s*$", text.strip())
    return int(match.group(1)) if match else 0


def _text(value):
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)


parse_partial_response = jsf.parse_partial_response
apply_partial_updates = jsf.apply_partial_updates


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


def parse_buckets(html):
    soup = BeautifulSoup(html, "html.parser")
    buckets = []
    for node in soup.find_all(id=BUCKET_RE):
        match = BUCKET_RE.fullmatch(node["id"])
        label = node.select_one(".nomeTarefa")
        count_node = node.select_one(".pull-right")
        label_text = " ".join(label.stripped_strings) if label else " ".join(node.stripped_strings)
        count_text = " ".join(count_node.stripped_strings) if count_node else label_text
        buckets.append(
            {
                "index": int(match.group(1)),
                "label": unescape(label_text).strip(),
                "count": _count(count_text),
            }
        )
    return buckets


def form_data(soup):
    form = soup.find("form", id="formAbaExpediente")
    if not form:
        raise RuntimeError("Expedientes form was not returned")
    data = []
    for field in form.find_all(["input", "select", "textarea"]):
        name = field.get("name")
        if not name:
            continue
        if field.name == "select":
            option = field.find("option", selected=True) or field.find("option")
            value = option.get("value", "") if option else ""
        elif field.get("type") in {"checkbox", "radio"} and not field.has_attr("checked"):
            continue
        else:
            value = field.get("value", "")
        data.append((name, value))
    return data


def submit_control(session, panel_url, form_html, control, ajax_single=False):
    _safe_url(panel_url, host=urlsplit(panel_url).hostname, path_exact=PANEL_PREFIX)
    soup = BeautifulSoup(form_html, "html.parser")
    state = soup.find("input", attrs={"name": "javax.faces.ViewState"})
    if not state or not state.get("value"):
        raise RuntimeError("Expedientes form has no current JSF ViewState")
    data = [("AJAXREQUEST", "_viewRoot"), *form_data(soup)]
    data.append((control, control))
    if ajax_single:
        data.append(("ajaxSingle", control))
    response = session.post(
        panel_url,
        data=data,
        headers={
            "Referer": panel_url,
            "Faces-Request": "partial/ajax",
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        },
        allow_redirects=False,
        timeout=REQUEST_TIMEOUT,
        impersonate=IMPERSONATE,
    )
    response_url = getattr(response, "url", panel_url)
    if response.status_code != 200:
        raise RuntimeError(f"Expedientes action returned HTTP {response.status_code}")
    _safe_url(response_url, host=urlsplit(panel_url).hostname, path_exact=PANEL_PREFIX)
    payload = _text(response.content)
    parsed = parse_partial_response(payload)
    if "<partial-response" in payload.lower():
        if not parsed["view_state"]:
            raise RuntimeError("Expedientes action returned no refreshed JSF ViewState")
        return apply_partial_updates(form_html, response.content)
    full = BeautifulSoup(payload, "html.parser")
    state = full.find("input", attrs={"name": "javax.faces.ViewState"})
    if not state or not state.get("value"):
        raise RuntimeError("Expedientes action returned no refreshed JSF ViewState")
    if full.find("form", id="formAbaExpediente"):
        return payload
    # RichFaces can return a complete HTML tree fragment without the source form.
    # Retain its form fields, refresh the state, and expose only its new tree nodes.
    previous = BeautifulSoup(form_html, "html.parser")
    for field in previous.find_all("input", attrs={"name": "javax.faces.ViewState"}):
        field["value"] = state["value"]
    return str(previous) + payload


def _control_candidates(html, prefix, *, seen=()):
    soup = BeautifulSoup(html, "html.parser")
    result = []
    found = set(seen)
    for element in soup.find_all(["a", "button", "input", "span", "img"]):
        candidate = element.get("name") or element.get("id")
        if not candidate or not candidate.startswith(prefix) or candidate in found:
            continue
        onclick = element.get("onclick", "")
        title = " ".join((element.get("title", ""), element.get("aria-label", ""))).casefold()
        if any(marker in title for marker in ("fechar", "close")):
            continue
        is_ajax = "A4J.AJAX.Submit" in onclick
        is_tree = candidate.endswith(":jNp")
        is_expand = any(marker in title for marker in ("abrir", "expand", "detalh", "open"))
        if not (is_ajax or is_tree or is_expand):
            continue
        found.add(candidate)
        result.append((candidate, is_tree or "ajaxSingle" in onclick, 0 if is_expand else 1))
    return [(candidate, ajax_single) for candidate, ajax_single, _ in sorted(result, key=lambda item: item[2])]


def discover_bucket_control(html, index):
    prefix = f"formAbaExpediente:listaAgrSitExp:{index}:"
    soup = BeautifulSoup(html, "html.parser")
    row = soup.find(id=f"{prefix}linhaN1")
    candidates = _control_candidates(str(row), prefix) if row else []
    if len(candidates) != 1:
        raise RuntimeError("Could not discover the Expedientes bucket control")
    return candidates[0]


def discover_child_control(html, index, seen=()):
    prefix = f"formAbaExpediente:listaAgrSitExp:{index}:"
    # Only expand tree toggles. Other AJAX controls may open/acknowledge a notice.
    candidates = [item for item in _control_candidates(html, prefix, seen=seen) if item[0].endswith(":jNp")]
    if len(candidates) > 1:
        raise RuntimeError("Ambiguous Expedientes child control")
    if not candidates:
        return None
    return candidates[0]


def expand_bucket(session, panel_url, html, index):
    control, ajax_single = discover_bucket_control(html, index)
    expanded = submit_control(session, panel_url, html, control, ajax_single=ajax_single)
    seen = {control}
    # Expedientes is a tree: bucket -> section -> individual entries.
    for _ in range(6):
        child = discover_child_control(expanded, index, seen)
        if not child:
            break
        control, ajax_single = child
        seen.add(control)
        expanded = submit_control(session, panel_url, expanded, control, ajax_single=ajax_single)
    return expanded


def parse_expanded_items(html, index, base_url):
    base_host = _known_host(base_url)
    if isinstance(html, bytes):
        probe = html.decode("utf-8", errors="replace")
    else:
        probe = html
    if "<partial-response" in probe.lower():
        html = "".join(parse_partial_response(probe)["updates"].values())
    soup = BeautifulSoup(html, "html.parser")
    items = []
    seen = set()
    tables = soup.find_all("table", id="formExpedientes:tbExpedientes")
    nodes = tables[-1].find_all("tr") if tables else []
    for node in nodes:
        text = " ".join(node.stripped_strings)
        processes = sorted(set(PROCESS_RE.findall(text)))
        if not processes:
            continue
        links = [
            _safe_url(urljoin(base_url, a["href"]), host=base_host)
            for a in node.find_all("a", href=True)
            if a["href"] not in {"#", "javascript:void(0)"}
        ]
        key = (text, tuple(links))
        if key in seen:
            continue
        seen.add(key)
        items.append({"text": text, "process_numbers": processes, "links": links})
    return items


def read_portal(name, expand=False, *, session=None):
    config = portals.pje(name)
    owned_session = session is None
    login_response = None
    try:
        if owned_session:
            session, login_response = login_portal(name)
        panel_url = _portal_url(name, PANEL_PATH)
        panel = session.get(
            panel_url,
            headers={"Referer": login_response.url if login_response else panel_url},
            allow_redirects=False,
            timeout=REQUEST_TIMEOUT,
            impersonate=IMPERSONATE,
        )
        panel_url_result = getattr(panel, "url", panel_url)
        if panel.status_code != 200:
            raise RuntimeError(f"Panel returned HTTP {panel.status_code}")
        _safe_url(panel_url_result, host=config.host, path_exact=PANEL_PREFIX)

        panel_html = response_html(panel)
        buckets = parse_buckets(panel_html)
        result = {
            "success": True,
            "portal": name,
            "panel_url": _safe_url(panel_url, host=config.host, path_exact=PANEL_PREFIX),
            "buckets": buckets,
            "notifications": [],
        }
        if expand:
            current_html = panel_html
            for bucket in buckets:
                if bucket["count"] <= 0:
                    continue
                expanded = expand_bucket(session, panel_url, current_html, bucket["index"])
                current_html = expanded
                items = parse_expanded_items(
                    expanded,
                    bucket["index"],
                    f"https://{config.host}",
                )
                result["notifications"].append(
                    {
                        "bucket": bucket["label"],
                        "count": bucket["count"],
                        "rows_returned": len(items),
                        "page_may_be_partial": len(items) < bucket["count"],
                        "items": items,
                    }
                )
        return result
    finally:
        if owned_session and session is not None:
            session.close()


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
    parser = argparse.ArgumentParser(description="Read PJe Expedientes notifications")
    parser.add_argument("--portal", choices=portals.keys("pje"))
    parser.add_argument("--expand", action="store_true", help="Expand Expedientes (exploratory; may be partial)")
    parser.add_argument("--no-expand", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--push", action="store_true", help="Read the Push watched-process list instead of Expedientes")
    args = parser.parse_args()
    if not args.portal:
        parser.error("--portal is required")
    enable_request_logging()

    try:
        with redirect_stdout(sys.stderr):
            output = (
                read_push(args.portal)
                if args.push
                else read_portal(args.portal, expand=args.expand and not args.no_expand)
            )
    except Exception as error:
        output = {"success": False, "portal": args.portal, "error": str(error)}
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
