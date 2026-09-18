"""Read-only JFSC smoke test: panel -> first process -> first events page."""

import argparse
import hashlib
import html as html_lib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup
from curl_cffi import requests

from integrations.eproc.auth import env_account, load_env, login, required_env
from integrations.eproc.parsers import parse_process_detail, parse_process_list
from scripts.pje_2fa_from_qr import accounts_from_image


BASE_URL = "https://eproc.jfsc.jus.br/eprocV2/"
LIST_ACTIONS = {
    "citacao_intimacao_prazo_aberto_listar",
    "citacao_intimacao_pendente_listar",
}


def endpoint_links(panel_html):
    soup = BeautifulSoup(panel_html, "html.parser")
    entries = []
    for anchor in soup.select("table.infraTable tbody tr td a[href]"):
        href = html_lib.unescape(anchor.get("href", "")).strip()
        action = parse_qs(urlsplit(href).query).get("acao", [""])[0]
        if action not in LIST_ACTIONS or parse_qs(urlsplit(href).query).get("urgente") == ["true"]:
            continue
        entries.append({
            "action": action,
            "url": urljoin(BASE_URL, href),
            "quantity": int(anchor.get_text(strip=True)) if anchor.get_text(strip=True).isdigit() else None,
        })
    return entries


def request(session, url, method="GET", data=None):
    response = session.request(
        method,
        url,
        data=data,
        headers={"Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8"},
        allow_redirects=False,
        impersonate="chrome",
        timeout=30,
    )
    return response.status_code, response.content.decode("iso-8859-1", errors="replace")


def download_document(session, href, expected_type, run_dir, index):
    target = urljoin(BASE_URL, href)
    parsed = urlsplit(target)
    if parsed.scheme != "https" or parsed.netloc != "eproc.jfsc.jus.br" or "acao=acessar_documento" not in parsed.query:
        raise RuntimeError(f"Refusing document URL outside JFSC eproc: {target}")
    response = session.get(
        target,
        headers={"Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8"},
        allow_redirects=False,
        impersonate="chrome",
        timeout=30,
    )
    content = response.content
    actual = response.headers.get("content-type", "").split(";", 1)[0].lower()
    wrapper_path = None
    if expected_type == "pdf" and not content.startswith(b"%PDF-"):
        wrapper_path = run_dir / f"{index:02d}-document-wrapper.html"
        wrapper_path.write_bytes(content)
        frame = BeautifulSoup(content, "html.parser").select_one("#conteudoIframe[src]")
        if frame:
            frame_url = urljoin(BASE_URL, html_lib.unescape(frame["src"]))
            frame_parts = urlsplit(frame_url)
            if frame_parts.scheme != "https" or frame_parts.netloc != "eproc.jfsc.jus.br" or "acao=acessar_documento_implementacao" not in frame_parts.query:
                raise RuntimeError(f"Refusing document iframe outside JFSC eproc: {frame_url}")
            response = session.get(frame_url, headers={"Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8"}, allow_redirects=False, impersonate="chrome", timeout=30)
            content = response.content
            actual = response.headers.get("content-type", "").split(";", 1)[0].lower()
    suffix = ".pdf" if content.startswith(b"%PDF-") else ".html"
    path = run_dir / f"{index:02d}-document{suffix}"
    path.write_bytes(content)
    return {
        "status": response.status_code,
        "expectedType": expected_type,
        "contentType": actual,
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "path": str(path),
        "wrapperPath": str(wrapper_path) if wrapper_path else None,
        "format": "pdf" if content.startswith(b"%PDF-") else "html",
    }


def process_links(list_html):
    soup = BeautifulSoup(list_html, "html.parser")
    links = []
    for anchor in soup.select("a[href]"):
        href = html_lib.unescape(anchor.get("href", "")).strip()
        query = parse_qs(urlsplit(href).query)
        if query.get("acao") == ["processo_selecionar"] and query.get("num_processo"):
            links.append(urljoin(BASE_URL, href))
    return list(dict.fromkeys(links))


def save_response(run_dir, name, status, body):
    path = run_dir / name
    path.write_text(body, encoding="utf-8")
    return {"status": status, "path": str(path), "bytes": len(body.encode("utf-8"))}


def main():
    parser = argparse.ArgumentParser(description="Validate JFSC eproc endpoints without writes")
    parser.add_argument("qr_image", type=Path)
    parser.add_argument("--account", type=int, default=1)
    args = parser.parse_args()

    load_env()
    accounts = accounts_from_image(args.qr_image)
    if not 1 <= args.account <= len(accounts):
        parser.error(f"account must be between 1 and {len(accounts)}")

    phpsessid, panel_html, panel_url = login(
        required_env("EPROC_USERNAME"),
        required_env("EPROC_PASSWORD"),
        env_account(accounts[args.account - 1], args.account),
    )

    run_dir = Path("data") / f"eproc-jfsc-validation-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    run_dir.mkdir(parents=True, exist_ok=False)
    panel_file = save_response(run_dir, "00-panel.html", 200, panel_html)

    session = requests.Session()
    session.cookies.set("PHPSESSID", phpsessid, domain="eproc.jfsc.jus.br", path="/")
    endpoint_results = []
    selected = None
    parsed_lists = []
    for index, endpoint in enumerate(endpoint_links(panel_html), 1):
        status, body = request(session, endpoint["url"])
        result = {**endpoint, "response": save_response(run_dir, f"{index:02d}-{endpoint['action']}.html", status, body)}
        result["processLinkCount"] = len(process_links(body))
        parsed = parse_process_list(body)
        result["parsedProcessCount"] = len(parsed)
        result["firstProcess"] = parsed[0] if parsed else None
        endpoint_results.append(result)
        parsed_lists.extend(parsed)
        if selected is None and result["processLinkCount"]:
            selected = process_links(body)[0]

    if selected is None:
        raise RuntimeError("No process link was found in the authenticated panel lists")

    detail_status, detail_html = request(session, selected)
    detail_file = save_response(run_dir, "10-process.html", detail_status, detail_html)
    event_status, event_html = request(session, selected, method="POST", data={"pagina": "0"})
    event_file = save_response(run_dir, "11-events-page-0.html", event_status, event_html)
    parsed_detail = parse_process_detail(detail_html)
    document_results = []
    seen_docs = set()
    for document in parsed_detail["documentos"]:
        if document["id"] in seen_docs:
            continue
        seen_docs.add(document["id"])
        document_results.append(download_document(
            session, document["url"], document.get("tipo"), run_dir, len(document_results) + 12
        ))
        if len(document_results) == 2:
            break

    print(json.dumps({
        "authenticated": True,
        "otpAccount": args.account,
        "panelUrl": panel_url.split("&hash=", 1)[0],
        "panel": panel_file,
        "endpoints": endpoint_results,
        "process": {
            "url": selected.split("&hash=", 1)[0],
            "detail": detail_file,
            "eventsPage0": event_file,
            "eventRowCount": len(BeautifulSoup(event_html, "html.parser").select("tr[id^='trEvento']")),
            "parsed": {
                "numero": parsed_detail["numero"],
                "parties": len(parsed_detail["partes"]),
                "events": parsed_detail["eventCount"],
                "documents": parsed_detail["documentCount"],
                "partyDetails": parsed_detail["partes"],
                "documentInventory": parsed_detail["documentos"],
                "eventsDetail": parsed_detail["eventos"],
                "firstEvent": parsed_detail["eventos"][0] if parsed_detail["eventos"] else None,
            },
            "documentsDownloaded": document_results,
        },
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
