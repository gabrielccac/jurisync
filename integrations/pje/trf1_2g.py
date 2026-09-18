#!/usr/bin/env python3
"""Search one TRF1 2G process and validate its detail/document endpoints."""

from html import unescape
import json
import re
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup

from .auth import APP_HOST


SEARCH_URL = f"https://{APP_HOST}/pje/Processo/ConsultaProcesso/listView.seam"
SEARCH_BUTTON_ID = "fPP:j_id495"  # 2G server-side action from the browser capture


def parse_process_number(value):
    match = re.fullmatch(r"(\d{7})-(\d{2})\.(\d{4})\.(\d)\.(\d{2})\.(\d{4})", value.strip())
    if not match:
        raise ValueError("Process number must use NNNNNNN-DD.AAAA.J.TR.OOOO format")
    return match.groups()


def jsf_data(html):
    soup = BeautifulSoup(html, "html.parser")
    state = soup.find("input", attrs={"name": "javax.faces.ViewState"})
    fields = {}
    for element in soup.find_all(["input", "select"]):
        name = element.get("name")
        if not name or name == "javax.faces.ViewState":
            continue
        if element.name == "input" and element.get("type") in {"checkbox", "radio"} and not element.has_attr("checked"):
            continue
        if element.name == "select":
            selected = element.find("option", selected=True)
            fields[name] = (selected or element.find("option")).get("value", "") if element.find("option") else ""
        else:
            fields[name] = element.get("value", "")
    return (state.get("value") if state else None), fields


def controls(html):
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form", id="fPP")
    if not form:
        return []
    result = []
    for element in form.find_all(["input", "button"]):
        element_id = element.get("id", "")
        if not element_id.startswith("fPP:"):
            continue
        label = element.get("value") or element.get_text(" ")
        if label or "A4J.AJAX.Submit" in element.get("onclick", ""):
            result.append({"id": element_id, "label": clean_text(label)[:80]})
    return result


def find_search_button(html):
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form", id="fPP")
    candidates = []
    if form:
        for element in form.find_all(["input", "button"]):
            if (element.get("value") or element.get_text(" ")).strip().lower() == "pesquisar":
                candidates.append((element.get("id"), element.get("onclick", "")))
    if not candidates:
        raise RuntimeError("Could not find the 2G Pesquisar control on the JSF page")
    # The visible button invokes reCAPTCHA, which then submits this hidden action.
    return SEARCH_BUTTON_ID


def post_ajax(session, payload):
    response = session.post(
        SEARCH_URL,
        data=urlencode(payload),
        headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Referer": SEARCH_URL,
        },
        impersonate="chrome",
    )
    print(f"POST {urlsplit(SEARCH_URL).path} -> {response.status_code}")
    return response


def clean_text(value):
    return re.sub(r"\s+", " ", BeautifulSoup(value or "", "html.parser").get_text(" ")).strip()


def response_html(response):
    content = getattr(response, "content", b"")
    if not isinstance(content, (bytes, bytearray)):
        return response.text
    content_type = response.headers.get("Content-Type", "").lower()
    header_match = re.search(r"charset\s*=\s*[\"']?([a-z0-9_-]+)", content_type, re.I)
    meta_match = re.search(rb"charset\s*=\s*[\"']?([a-z0-9_-]+)", content[:4096], re.I)
    charset = (header_match.group(1).lower() if header_match else
               meta_match.group(1).decode("ascii", errors="ignore").lower() if meta_match else "")
    if charset in {"iso-8859-1", "latin1", "latin-1"}:
        return bytes(content).decode("iso-8859-1")
    try:
        return bytes(content).decode("utf-8")
    except UnicodeDecodeError:
        # PJe has served Latin-1 bytes while declaring UTF-8.
        return bytes(content).decode("cp1252")


def _coded_value(value):
    value = clean_text(value)
    match = re.fullmatch(r"(.*?)\s*\(([^()]+)\)", value)
    return {"name": match.group(1).strip(), "code": match.group(2).strip()} if match else {"name": value, "code": None}


def _coded_values(value):
    parts, current, depth = [], [], 0
    for char in clean_text(value):
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        if char == "/" and depth == 0:
            item = clean_text("".join(current))
            if item:
                parts.append(item)
            current = []
        else:
            current.append(char)
    item = clean_text("".join(current))
    if item:
        parts.append(item)
    return [_coded_value(item) for item in parts]


def _yes_no(value):
    value = clean_text(value).upper()
    return {"SIM": True, "NÃO": False, "NAO": False}.get(value, value or None)


def parse_process_details(html):
    soup = BeautifulSoup(html, "html.parser")
    details = soup.find(id="maisDetalhes")
    if not details:
        return {}
    values = {}
    for label in details.find_all("dt"):
        key = clean_text(label.get_text(" "))
        siblings = []
        for sibling in label.next_siblings:
            if getattr(sibling, "name", None) == "dt" and clean_text(sibling.get_text(" ")):
                break
            if getattr(sibling, "name", None) == "dd":
                text = clean_text(sibling.get_text(" "))
                if text:
                    siblings.append(text)
        if key and siblings:
            values[key] = siblings

    mapped = {
        "Classe judicial": ("judicial_class", lambda v: _coded_value(v[0])),
        "Assunto": ("subjects", lambda v: _coded_values(" / ".join(v))),
        "Jurisdição": ("jurisdiction", lambda v: v[0]),
        "Autuação": ("filed_at", lambda v: v[0]),
        "Última distribuição": ("last_distribution", lambda v: v[0]),
        "Valor da causa": ("case_value", lambda v: v[0]),
        "Segredo de justiça?": ("confidential", lambda v: _yes_no(v[0])),
        "Juízo 100% digital?": ("digital_court", lambda v: _yes_no(v[0])),
        "Justiça gratuita?": ("legal_aid", lambda v: _yes_no(v[0])),
        "Tutela/liminar?": ("injunction", lambda v: _yes_no(v[0])),
        "Prioridade?": ("priority", lambda v: v[0]),
        "Órgão julgador": ("judicial_body", lambda v: v[0]),
        "Cargo judicial": ("judicial_role", lambda v: v[0]),
        "Competência": ("competence", lambda v: v[0]),
        "Contato órgão julgador": ("judicial_body_contacts", lambda v: {
            "phone": v[0] if v else None,
            "email": v[1] if len(v) > 1 else None,
        }),
    }
    normalized = {label.casefold(): value for label, value in values.items()}
    return {key: transform(normalized[label.casefold()]) for label, (key, transform) in mapped.items()
            if label.casefold() in normalized}


def parse_parties(html):
    soup = BeautifulSoup(html, "html.parser")
    groups = {
        "active": ("poloAtivo", "Polo ativo"),
        "passive": ("poloPassivo", "Polo passivo"),
        "other_interested": ("outrosInteressados", "Outros interessados"),
    }
    result = {}
    for key, (element_id, heading) in groups.items():
        node = soup.find(id=element_id)
        entries = []
        if node:
            for span in node.find_all("span"):
                text = clean_text(span.get_text(" "))
                if text and text.casefold() != heading.casefold() and "sr-only" not in (span.get("class") or []) and text not in entries:
                    entries.append(text)
            for item in node.find_all("li"):
                text = clean_text(item.get_text(" "))
                if text and text not in entries:
                    entries.append(text)
        result[key] = entries
    return result


def parse_search_results(html):
    soup = BeautifulSoup(unescape(html), "html.parser")
    table = soup.find(id="fPP:processosTable")
    if not table:
        return {"count": 0, "items": []}
    count_match = re.search(r"(\d+)\s+resultados encontrados", clean_text(table.get_text(" ")), re.I)
    items = []
    for row in table.find_all("tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 9:
            continue
        process_number = clean_text(cells[1].get_text(" "))
        if not re.fullmatch(r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}", process_number):
            continue
        process_link = cells[1].find("a")
        process_id_match = re.search(r"idProcessoSelecionado['\"]?\s*:\s*(\d+)", str(row))
        characteristic = cells[2].find(attrs={"title": True})
        items.append({
            "process_id": process_id_match.group(1) if process_id_match else None,
            "process_number": process_number,
            "priority": clean_text(characteristic.get("title")) if characteristic else None,
            "judicial_body": clean_text(cells[3].get_text(" ")),
            "filed_at": clean_text(cells[4].get_text(" ")),
            "judicial_class": clean_text(cells[5].get_text(" ")),
            "active_party": clean_text(cells[6].get_text(" ")),
            "passive_party": clean_text(cells[7].get_text(" ")),
            "last_movement": clean_text(cells[8].get_text(" ")),
            "selection_control": process_link.get("id") if process_link else None,
        })
    return {"count": int(count_match.group(1)) if count_match else len(items), "items": items}


def extract_documents(block_html, download_pattern, base_url=SEARCH_URL):
    documents = []
    seen = set()
    soup = BeautifulSoup(block_html, "html.parser")
    for anchor in soup.find_all("a"):
        span = anchor.find("span")
        label = clean_text(span.decode_contents() if span else anchor.get_text(" "))
        match = re.match(r"(\d+)\s*-\s*(.*)", label)
        if not match:
            continue
        doc_id, title = match.groups()
        if doc_id in seen:
            continue
        seen.add(doc_id)
        documents.append({
            "id": doc_id,
            "title": title.strip(),
            "download_url": urljoin(base_url, f"{download_pattern}{doc_id}") if download_pattern else None,
        })
    return documents


def parse_timeline(html, download_pattern, base_url=SEARCH_URL):
    dates = list(re.finditer(
        r'<div class="media data">\s*<div class="media-body">\s*<span class="text-muted data-interna">(.*?)</span>',
        html,
        re.DOTALL,
    ))
    timeline = []
    for index, date_match in enumerate(dates):
        end = dates[index + 1].start() if index + 1 < len(dates) else len(html)
        chunk = html[date_match.end():end]
        movements = []
        for movement in re.finditer(
            r'<div class="media ([^"]*tipo-[^"]*)">(.*?)<div class="col-sm-12">\s*<small class="text-muted pull-right">(.*?)</small>',
            chunk,
            re.DOTALL,
        ):
            classes = movement.group(1).split()
            type_code = next((item.split("-", 1)[1] for item in classes if item.startswith("tipo-")), None)
            block = movement.group(2)
            block_soup = BeautifulSoup(block, "html.parser")
            labels = [clean_text(node.decode_contents()) for node in block_soup.select("span.text-upper.texto-movimento")]
            icon = block_soup.select_one(".media-left .icon-auto")
            origin_code = next((item for item in (icon.get("class", []) if icon else [])
                                if item not in {"icon-auto", "tip-right"}), None)
            movements.append({
                "time": clean_text(movement.group(3)),
                "movement": labels[0] if labels else "",
                "movement_texts": labels,
                "documents": extract_documents(block, download_pattern, base_url),
                "entry_type": {"M": "movement", "D": "document"}.get(type_code, type_code),
                "type_code": type_code,
                "origin": clean_text(icon.get("title")) if icon and icon.get("title") else None,
                "origin_code": origin_code,
            })
        timeline.append({"date": clean_text(date_match.group(1)), "movements": movements})
    return timeline


def parse_document_inventory(html, timeline, download_pattern, base_url=SEARCH_URL):
    visible = {
        document["id"]: document
        for day in timeline
        for movement in day.get("movements", [])
        for document in movement.get("documents", [])
    }
    match = re.search(r"var\s+listaIdsDocumentos\s*=\s*\[([^\]]*)\]", html)
    ids = re.findall(r"\d+", match.group(1)) if match else list(visible)
    result = []
    for doc_id in dict.fromkeys(ids):
        item = dict(visible.get(doc_id, {"id": doc_id, "title": None, "download_url": None}))
        if download_pattern:
            item["download_url"] = urljoin(base_url, f"{download_pattern}{doc_id}")
        item["visible_in_timeline"] = doc_id in visible
        result.append(item)
    return result


def parse_pagination(html, timeline):
    return {
        "page": 1,
        "mode": "infinite_scroll" if "novaPagina=" in html else "single_page",
        "loaded_days": len(timeline),
        "loaded_movements": sum(len(day.get("movements", [])) for day in timeline),
        "loaded_documents": sum(len(movement.get("documents", [])) for day in timeline for movement in day.get("movements", [])),
        "more_pages_possible": "novaPagina=" in html,
    }


def download_documents(session, timeline, output_dir=None, fetch=False, referer_url=SEARCH_URL):
    results = []
    if not output_dir and not fetch:
        return results
    destination = Path(output_dir) if output_dir else None
    if destination:
        destination.mkdir(parents=True, exist_ok=True)
    for day in timeline:
        for movement in day["movements"]:
            for document in movement["documents"]:
                url = document.get("download_url")
                if not url:
                    continue
                response = session.get(
                    url,
                    headers={"Accept": "application/pdf,*/*", "Referer": referer_url},
                    impersonate="chrome",
                )
                content_type = response.headers.get("Content-Type", "")
                item = {
                    "id": document["id"],
                    "title": document["title"],
                    "download_url": url,
                    "status": response.status_code,
                    "content_type": content_type,
                    "bytes": len(response.content),
                    "html_response": "html" in content_type.lower() or response.content[:100].lower().find(b"<html") >= 0,
                }
                if destination:
                    path = destination / f"{document['id']}.bin"
                    path.write_bytes(response.content)
                    item["saved_to"] = str(path.resolve())
                results.append(item)
    return results


def search_process(session, process_number, output_dir=None):
    parts = parse_process_number(process_number)
    response = session.get(SEARCH_URL, headers={"Referer": f"https://{APP_HOST}/pje/"}, impersonate="chrome")
    print(f"GET  {urlsplit(SEARCH_URL).path} -> {response.status_code}")
    if response.status_code != 200:
        raise RuntimeError(f"Search page returned HTTP {response.status_code}")
    page_controls = controls(response_html(response))
    state, fields = jsf_data(response_html(response))
    if not state:
        raise RuntimeError("Search page did not contain javax.faces.ViewState")
    search_button_id = find_search_button(response_html(response))
    search_payload = {
        "AJAXREQUEST": "_viewRoot",
        **fields,
        "fPP": "fPP",
        "javax.faces.ViewState": state,
        search_button_id: search_button_id,
        "ajaxSingle": search_button_id,
        "autoScroll": "",
        "fPP:numeroProcesso:numeroSequencial": parts[0],
        "fPP:numeroProcesso:numeroDigitoVerificador": parts[1],
        "fPP:numeroProcesso:Ano": parts[2],
        "fPP:numeroProcesso:ramoJustica": parts[3],
        "fPP:numeroProcesso:respectivoTribunal": parts[4],
        "fPP:numeroProcesso:NumeroOrgaoJustica": parts[5],
        "AJAX:EVENTS_COUNT": "1",
    }
    response = post_ajax(session, search_payload)
    decoded_response = unescape(response_html(response))
    search_results = parse_search_results(decoded_response)
    process_match = re.search(r"['\"]?idProcessoSelecionado['\"]?\s*:\s*(\d+)", decoded_response)
    if not process_match:
        component_ids = sorted(set(re.findall(r'id="(fPP:[^"]+)"', decoded_response)))
        result_table = BeautifulSoup(decoded_response, "html.parser").find(id="fPP:processosTable")
        row_ids = sorted(set(re.findall(r'id="(fPP:processosTable:\d+:[^"]+)"', decoded_response)))
        result_text = clean_text(result_table.get_text(" ") if result_table else "")
        return {
            "success": False,
            "error": "Process not found or 2G JSF component IDs differ",
                "search_results": search_results,
                "search_debug": {
                "response_bytes": len(response.content),
                "has_process_marker": "idProcessoSelecionado" in decoded_response,
                "has_no_results_text": any(text in decoded_response.lower() for text in ("nenhum processo", "não encontrado", "nao encontrado")),
                "fpp_component_ids_sample": component_ids[:20],
                "search_page_controls": page_controls,
                "search_button_id": search_button_id,
                "result_table_found": bool(result_table),
                "result_table_text_length": len(result_text),
                "result_row_id_sample": row_ids[:20],
                "required_field_presence": {
                    name: name in fields
                    for name in (
                        "fPP:numeroProcesso:numeroSequencial",
                        "fPP:numeroProcesso:numeroDigitoVerificador",
                        "fPP:numeroProcesso:Ano",
                        "fPP:numeroProcesso:ramoJustica",
                        "fPP:numeroProcesso:respectivoTribunal",
                        "fPP:numeroProcesso:NumeroOrgaoJustica",
                    )
                },
            },
        }
    process_id = process_match.group(1)
    state, fields = jsf_data(response_html(response))
    result_soup = BeautifulSoup(decoded_response, "html.parser")
    select_id = None
    for anchor in result_soup.select('a[onclick*="idProcessoSelecionado"]'):
        if re.search(rf"idProcessoSelecionado['\"]?\s*:\s*{re.escape(process_id)}", anchor.get("onclick", "")):
            select_id = anchor.get("name") or anchor.get("id")
            break
    if not select_id:
        raise RuntimeError("Process row found but its 2G selection control was not found")
    third = {
        "AJAXREQUEST": "_viewRoot",
        **fields,
        "fPP": "fPP",
        "javax.faces.ViewState": state or "",
        "idProcessoSelecionado": process_id,
        select_id: select_id,
        "ajaxSingle": select_id,
        "AJAX:EVENTS_COUNT": "2",
    }
    response = post_ajax(session, third)
    ca_match = re.search(r"ca=([a-fA-F0-9]{32,})", response_html(response))
    if not ca_match:
        return {"success": False, "error": "Process selected but CA token was not returned", "processo_id": process_id, "search_results": search_results}

    detail_url = f"https://{APP_HOST}/pje/Processo/ConsultaProcesso/Detalhe/listProcessoCompletoAdvogado.seam?id={process_id}&ca={ca_match.group(1)}"
    response = session.get(detail_url, headers={"Referer": SEARCH_URL}, impersonate="chrome")
    print(f"GET  {urlsplit(detail_url).path} -> {response.status_code}")
    detail_html = response_html(response)
    pattern_match = re.search(r"window\.open\('([^']*/documento/download/TRF1/[^']*/\d+/)'", detail_html)
    download_pattern = urljoin(detail_url, pattern_match.group(1)) if pattern_match else None
    timeline = parse_timeline(detail_html, download_pattern)
    documents = [doc for day in timeline for movement in day["movements"] for doc in movement["documents"]]
    result = {
        "success": response.status_code == 200,
        "processo_id": process_id,
        "numero_processo": process_number,
        "detail_status": response.status_code,
        "download_pattern_found": bool(download_pattern),
        "days": len(timeline),
        "movements": sum(len(day["movements"]) for day in timeline),
        "documents": len(documents),
        "search_results": search_results,
        "process_details": parse_process_details(detail_html),
        "parties": parse_parties(detail_html),
        "timeline": timeline,
        "document_inventory": parse_document_inventory(detail_html, timeline, download_pattern),
        "pagination": parse_pagination(detail_html, timeline),
        "latest_update": timeline[0] if timeline else None,
    }
    result["downloads"] = download_documents(session, timeline, output_dir, fetch=bool(output_dir))
    return result
