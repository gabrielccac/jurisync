"""Parsers shared by the TRF1 PJe 1G and 2G search flows."""

import re
from html import unescape
from urllib.parse import urljoin

from bs4 import BeautifulSoup


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


def clean_text(value):
    return re.sub(r"\s+", " ", BeautifulSoup(value or "", "html.parser").get_text(" ")).strip()


def response_html(response):
    content = getattr(response, "content", b"")
    if not isinstance(content, (bytes, bytearray)):
        return response.text
    content_type = response.headers.get("Content-Type", "").lower()
    header_match = re.search(r"charset\s*=\s*[\"']?([a-z0-9_-]+)", content_type, re.I)
    meta_match = re.search(rb"charset\s*=\s*[\"']?([a-z0-9_-]+)", content[:4096], re.I)
    charset = (
        header_match.group(1).lower()
        if header_match
        else meta_match.group(1).decode("ascii", errors="ignore").lower()
        if meta_match
        else ""
    )
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
                if key == "Assunto" and sibling.find("li"):
                    siblings.extend(clean_text(item.get_text(" ")) for item in sibling.find_all("li"))
                    continue
                text = clean_text(sibling.get_text(" "))
                if text:
                    siblings.append(text)
        if key and siblings:
            values[key] = siblings

    mapped = {
        "Classe judicial": ("judicial_class", lambda v: _coded_value(v[0])),
        "Assunto": ("subjects", lambda v: [_coded_value(item) for item in v] if len(v) > 1 else _coded_values(v[0])),
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
        "Contato órgão julgador": (
            "judicial_body_contacts",
            lambda v: {
                "phone": v[0] if v else None,
                "email": v[1] if len(v) > 1 else None,
            },
        ),
    }
    normalized = {label.casefold(): value for label, value in values.items()}
    return {
        key: transform(normalized[label.casefold()])
        for label, (key, transform) in mapped.items()
        if label.casefold() in normalized
    }


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
                if (
                    text
                    and text.casefold() != heading.casefold()
                    and "sr-only" not in (span.get("class") or [])
                    and text not in entries
                ):
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
        return {"count": None, "items": [], "table_found": False}
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
        items.append(
            {
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
            }
        )
    return {"count": int(count_match.group(1)) if count_match else len(items), "items": items, "table_found": True}


def matching_process_id(html, process_number):
    parts = parse_process_number(process_number)
    canonical = "{}-{}.{}.{}.{}.{}".format(*parts)
    results = parse_search_results(html)
    matches = [item for item in results["items"] if item["process_number"] == canonical]
    return (matches[0]["process_id"] if len(matches) == 1 else None), results


def detail_matches_process_number(html, process_number):
    parts = parse_process_number(process_number)
    canonical = "{}-{}.{}.{}.{}.{}".format(*parts)
    return canonical in set(re.findall(r"\b\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}\b", html))


def recaptcha_search_action(html):
    """Resolve the hidden A4J action invoked by PJe's visible Pesquisar button."""
    script = re.search(r"executarPesquisaReCaptcha\s*=\s*function\s*\([^)]*\)\s*\{(.*?)\};", html, re.S)
    actions = (
        set(re.findall(r"similarityGroupingId['\"]\s*:\s*['\"](fPP:[^'\"]+)", script.group(1))) if script else set()
    )
    if len(actions) != 1:
        raise RuntimeError("Could not uniquely discover the PJe reCAPTCHA search action")
    return actions.pop()


def submit_function_search_action(html):
    """The A4J action behind TJPE's Pesquisar button, which only calls the JS function ``submeterPesquisa``.

    The button's own AJAX call is never reached (its onclick returns first), so the id to post is the one the
    function declares. A page that already marks reCAPTCHA as active (``captchaAtivo = true``) is refused: the
    search then waits for a token from the user's browser.
    """
    if re.search(r"captchaAtivo\s*=\s*true", html):
        raise RuntimeError("This PJe asks for reCAPTCHA before searching; open the process in the browser")
    script = re.search(r"submeterPesquisa\s*=\s*function\s*\([^)]*\)\s*\{(.*?)\};", html, re.S)
    actions = (
        set(re.findall(r"similarityGroupingId['\"]\s*:\s*['\"](fPP:[^'\"]+)", script.group(1))) if script else set()
    )
    if len(actions) != 1:
        raise RuntimeError("Could not uniquely discover the PJe submeterPesquisa search action")
    return actions.pop()


def extract_documents(block_html, download_pattern, base_url):
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
        documents.append(
            {
                "id": doc_id,
                "title": title.strip(),
                "download_url": urljoin(base_url, f"{download_pattern}{doc_id}") if download_pattern else None,
            }
        )
    return documents


def parse_timeline(html, download_pattern, base_url):
    soup = BeautifulSoup(html, "html.parser")
    dates = [node for node in soup.select("div.media.data") if node.select_one(".data-interna")]
    timeline = []
    for date in dates:
        movements = []
        for node in date.find_all_next("div"):
            if node is not date and {"media", "data"}.issubset(node.get("class", [])):
                break
            classes = node.get("class", [])
            if "media" not in classes or not any(item.startswith("tipo-") for item in classes):
                continue
            type_code = next((item.split("-", 1)[1] for item in classes if item.startswith("tipo-")), None)
            labels = [clean_text(label.decode_contents()) for label in node.select("span.text-upper.texto-movimento")]
            icon = node.select_one(".media-left .icon-auto")
            origin_code = next(
                (item for item in (icon.get("class", []) if icon else []) if item not in {"icon-auto", "tip-right"}),
                None,
            )
            clock = node.select_one(".col-sm-12 small")
            movements.append(
                {
                    "time": clean_text(clock.get_text(" ")) if clock else "",
                    "movement": labels[0] if labels else "",
                    "movement_texts": labels,
                    "documents": extract_documents(str(node), download_pattern, base_url),
                    "entry_type": {"M": "movement", "D": "document"}.get(type_code, type_code),
                    "type_code": type_code,
                    "origin": clean_text(icon.get("title")) if icon and icon.get("title") else None,
                    "origin_code": origin_code,
                }
            )
        timeline.append({"date": clean_text(date.select_one(".data-interna").get_text(" ")), "movements": movements})
    return timeline


def parse_document_inventory(html, timeline, download_pattern, base_url):
    visible = {
        document["id"]: document
        for day in timeline
        for movement in day.get("movements", [])
        for document in movement.get("documents", [])
    }
    # A second-instance document titled "... - ID de origem N" points to a copy of document N in the first
    # instance, which that instance's portal serves but this one does not.
    origin_ids = {
        found
        for document in visible.values()
        for found in re.findall(r"ID de origem (\d+)", document.get("title") or "")
    }
    match = re.search(r"var\s+listaIdsDocumentos\s*=\s*\[([^\]]*)\]", html)
    ids = (re.findall(r"\d+", match.group(1)) if match else []) + list(visible)
    result = []
    for doc_id in dict.fromkeys(ids):
        item = dict(visible.get(doc_id, {"id": doc_id, "title": None, "download_url": None}))
        if download_pattern:
            item["download_url"] = urljoin(base_url, f"{download_pattern}{doc_id}")
        item["visible_in_timeline"] = doc_id in visible
        if doc_id in origin_ids:
            item["first_instance_copy"] = True
        result.append(item)
    return result


def parse_pagination(html, timeline):
    return {
        "page": 1,
        "mode": "infinite_scroll" if "novaPagina=" in html else "single_page",
        "loaded_days": len(timeline),
        "loaded_movements": sum(len(day.get("movements", [])) for day in timeline),
        "loaded_documents": sum(
            len(movement.get("documents", [])) for day in timeline for movement in day.get("movements", [])
        ),
        "more_pages_possible": "novaPagina=" in html,
    }
