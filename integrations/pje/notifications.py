#!/usr/bin/env python3
"""Read TRF1 PJe lawyer-panel notification counters and populated buckets."""

import argparse
import json
import re
from html import unescape
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .auth import login_session
from .client import credentials
from .trf1_2g import response_html

PORTALS = {
    "1g": {"host": "pje1g.trf1.jus.br", "app_url": "https://pje1g.trf1.jus.br/pje/login.seam"},
    "2g": {"host": "pje2g.trf1.jus.br", "app_url": "https://pje2g.trf1.jus.br/pje/login.seam"},
}

PANEL_PATH = "/pje/Painel/painel_usuario/advogado.seam"
PUSH_PATH = "/pje/Push/listView.seam?iframe=true"
BUCKET_RE = re.compile(r"formAbaExpediente:listaAgrSitExp:(\d+):linhaN1$")
PROCESS_RE = re.compile(r"\b\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}\b")
PUSH_PROCESS_CELL_RE = re.compile(r"j_id217:j_id221:\d+:j_id226$")


def _count(text):
    match = re.search(r"(\d+)\s*$", text.strip())
    return int(match.group(1)) if match else 0


def parse_buckets(html):
    soup = BeautifulSoup(html, "html.parser")
    buckets = []
    for node in soup.find_all(id=BUCKET_RE):
        match = BUCKET_RE.fullmatch(node["id"])
        label = node.select_one(".nomeTarefa")
        count_node = node.select_one(".pull-right")
        label_text = " ".join(label.stripped_strings) if label else " ".join(node.stripped_strings)
        count_text = " ".join(count_node.stripped_strings) if count_node else label_text
        buckets.append({
            "index": int(match.group(1)),
            "label": unescape(label_text).strip(),
            "count": _count(count_text),
        })
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
    soup = BeautifulSoup(form_html, "html.parser")
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
        impersonate="chrome",
    )
    if response.status_code != 200:
        raise RuntimeError(f"Expedientes action returned HTTP {response.status_code}")
    return response.content


def expand_bucket(session, panel_url, html, index):
    control = f"formAbaExpediente:listaAgrSitExp:{index}:j_id179"
    expanded = submit_control(session, panel_url, html, control)
    seen = {control}
    # Expedientes is a tree: bucket -> section -> individual entries.
    for _ in range(6):
        soup = BeautifulSoup(expanded, "html.parser")
        prefix = f"formAbaExpediente:listaAgrSitExp:{index}:"
        candidates = []
        for element in soup.find_all(True):
            onclick = element.get("onclick", "")
            if "Clique para fechar" in element.get("title", ""):
                continue
            candidate = element.get("name") or element.get("id")
            if candidate and not candidate.startswith(prefix):
                continue
            is_tree_anchor = candidate and candidate.endswith(":jNp")
            if "A4J.AJAX.Submit" not in onclick and not is_tree_anchor:
                continue
            if candidate and candidate not in seen:
                candidates.append((candidate, "ajaxSingle" in onclick or is_tree_anchor))
        if not candidates:
            break
        control, ajax_single = candidates[0]
        seen.add(control)
        expanded = submit_control(session, panel_url, html, control, ajax_single=ajax_single)
    return expanded


def parse_expanded_items(html, index, base_url):
    if isinstance(html, bytes):
        probe = html.decode("utf-8", errors="replace")
    else:
        probe = html
    if "<partial-response" in probe.lower():
        updates = re.findall(
            r"<update[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</update>",
            probe,
            flags=re.IGNORECASE | re.DOTALL,
        )
        html = "".join(unescape(update) for update in updates)
    soup = BeautifulSoup(html, "html.parser")
    items = []
    seen = set()
    nodes = soup.find_all("tr") or soup.find_all("li")
    for node in nodes:
        text = " ".join(node.stripped_strings)
        processes = sorted(set(PROCESS_RE.findall(text)))
        if not processes:
            continue
        links = [
            urljoin(base_url, a["href"])
            for a in node.find_all("a", href=True)
            if a["href"] not in {"#", "javascript:void(0)"}
        ]
        key = (text, tuple(links))
        if key in seen:
            continue
        seen.add(key)
        items.append({"text": text, "process_numbers": processes, "links": links})
    return items


def read_portal(name, expand=True):
    config = PORTALS[name]
    session, response = login_session(
        *credentials(name),
        config["app_url"],
        config["host"],
    )
    panel_url = f"https://{config['host']}{PANEL_PATH}"
    panel = session.get(panel_url, headers={"Referer": response.url}, impersonate="chrome")
    if panel.status_code != 200:
        raise RuntimeError(f"Panel returned HTTP {panel.status_code}")

    panel_html = response_html(panel)
    buckets = parse_buckets(panel_html)
    result = {
        "success": True,
        "portal": name,
        "panel_url": panel_url,
        "buckets": buckets,
        "notifications": [],
    }
    if expand:
        for bucket in buckets:
            if bucket["count"] <= 0:
                continue
            expanded = expand_bucket(session, panel_url, panel_html, bucket["index"])
            items = parse_expanded_items(
                expanded,
                bucket["index"],
                f"https://{config['host']}",
            )
            result["notifications"].append({
                "bucket": bucket["label"],
                "count": bucket["count"],
                "rows_returned": len(items),
                "page_may_be_partial": len(items) < bucket["count"],
                "items": items,
            })
    return result


def parse_push_items(html):
    """Parse the processes registered in PJe Push (the source of Push emails)."""
    soup = BeautifulSoup(html, "html.parser")
    items = []
    seen = set()
    for cell in soup.find_all(id=PUSH_PROCESS_CELL_RE):
        process = " ".join(cell.stripped_strings)
        match = PROCESS_RE.search(process)
        if not match or match.group(0) in seen:
            continue
        seen.add(match.group(0))
        row = cell.find_parent("tr")
        cells = row.find_all("td") if row else []
        values = [" ".join(c.stripped_strings) for c in cells]
        onclick = " ".join(a.get("onclick", "") for a in row.find_all("a")) if row else ""
        process_id = re.search(r"construirLogicaAcesso\((\d+)\)", onclick)
        items.append({
            "process_number": match.group(0),
            "registered_at": values[2] if len(values) > 2 else "",
            "alternate_email": values[3] if len(values) > 3 else "",
            "observation": values[4] if len(values) > 4 else "",
            "process_id": int(process_id.group(1)) if process_id else None,
        })
    return items


def read_push(name):
    config = PORTALS[name]
    session, response = login_session(
        *credentials(name),
        config["app_url"],
        config["host"],
    )
    panel_url = f"https://{config['host']}{PANEL_PATH}"
    push_url = f"https://{config['host']}{PUSH_PATH}"
    push = session.get(push_url, headers={"Referer": panel_url}, impersonate="chrome")
    if push.status_code != 200:
        raise RuntimeError(f"Push list returned HTTP {push.status_code}")
    html = response_html(push)
    items = parse_push_items(html)
    result_count = re.search(r"(\d+)\s+resultados encontrados", html, re.I)
    return {
        "success": True,
        "portal": name,
        "push_url": push_url,
        "results_count": int(result_count.group(1)) if result_count else None,
        "rows_returned": len(items),
        "items": items,
        # Push stores watched processes; movement text arrives in the email.
        "content_note": "This page lists watched processes, not the individual movement email text.",
    }


def self_test():
    sample = """
    <form id='formAbaExpediente'>
      <div id='formAbaExpediente:listaAgrSitExp:0:linhaN1'>
        <span class='nomeTarefa'>Apenas pendentes de ciência</span><span class='pull-right'>3</span>
      </div>
    </form>
    """
    assert parse_buckets(sample) == [{"index": 0, "label": "Apenas pendentes de ciência", "count": 3}]
    assert PROCESS_RE.findall("1016572-38.2026.4.01.0000") == ["1016572-38.2026.4.01.0000"]
    assert len(parse_expanded_items(
        "<tr><td>Intimação 1016572-38.2026.4.01.0000</td></tr>",
        0,
        "https://pje1g.trf1.jus.br",
    )) == 1
    push_sample = """
    <table><tr class='rich-table-row'>
      <td></td><td id='j_id217:j_id221:0:j_id226'>1016572-38.2026.4.01.0000</td>
      <td>03/09/2026 10:00</td><td></td><td>teste</td>
    </tr></table>
    """
    assert parse_push_items(push_sample)[0]["process_number"] == "1016572-38.2026.4.01.0000"
    print("self-test: ok")


def main():
    parser = argparse.ArgumentParser(description="Read TRF1 PJe Expedientes notifications")
    parser.add_argument("--portal", choices=("1g", "2g", "both"), default="both")
    parser.add_argument("--no-expand", action="store_true", help="Only read counters")
    parser.add_argument("--push", action="store_true", help="Read the Push watched-process list instead of Expedientes")
    parser.add_argument("--self-test", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return

    names = ("1g", "2g") if args.portal == "both" else (args.portal,)
    output = []
    for name in names:
        try:
            output.append(read_push(name) if args.push else read_portal(name, expand=not args.no_expand))
        except Exception as error:
            output.append({"success": False, "portal": name, "error": str(error)})
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
