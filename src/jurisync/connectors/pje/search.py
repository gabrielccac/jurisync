"""Find a process in any PJe deployment and read its detail page and timeline.

The flow is the same for 1st and 2nd instance (and for TRF1, TRF5 and TJAP): open the
consulta page, submit the CNJ number, select the matching row, open the detail
page with the returned CA token, then read bounded timeline pages. Live pages
on trf1-1g, trf1-2g, trf5-1g and tjap-1g expose the same controls; the only
per-instance difference is the detail URL suffix below. TJPE (tjpe-1g/2g) differs in one control: its
Pesquisar button only calls the JS function submeterPesquisa(), so the action to post is the one that function
declares (parsers.submit_function_search_action).
"""

import re
from html import unescape
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from jurisync import portals
from jurisync.connectors.pje import jsf
from jurisync.connectors.pje.expedientes import ato_item, fetch_ato, fetch_expedientes
from jurisync.connectors.pje.parsers import (
    detail_matches_process_number,
    matching_process_id,
    parse_document_inventory,
    parse_pagination,
    parse_parties,
    parse_process_details,
    parse_process_number,
    parse_timeline,
    recaptcha_search_action,
    response_html,
    submit_function_search_action,
)

MAX_TIMELINE_PAGES = 5
DETAIL_PATH = "Processo/ConsultaProcesso/Detalhe/listProcessoCompletoAdvogado.seam"
DETAIL_SUFFIX = {"1g": "&aba=", "2g": ""}
DOWNLOAD_PATTERN = re.compile(r"window\.open\('([^']*/documento/download/[A-Za-z0-9]+/[^']*/\d+/)'")


def _control_id(element):
    return element.get("id") or element.get("name")


def discover_controls(html):
    """Discover the consulta page's optional init control and its search control; ambiguity is fatal."""
    soup = BeautifulSoup(unescape(html), "html.parser")
    form = soup.find("form", id="fPP")
    if not form:
        raise RuntimeError("PJe search form fPP was not found")

    controls = []
    for element in form.find_all(["input", "button", "select", "a"]):
        element_id = _control_id(element)
        ids = jsf.ajax_ids(element)
        if (not element_id or not element_id.startswith("fPP:")) and not ids:
            continue
        label = " ".join((element.get("value") or element.get_text(" ")).split()).casefold()
        controls.append({"element": element, "id": element_id, "label": label, "ajax_ids": ids})

    # Search controls are either visible labels or a hidden action named by the
    # visible reCAPTCHA button. All compatible declarations must agree.
    search = [item for item in controls if "pesquisar" in item["label"]]
    if any("executarReCaptcha()" in item["element"].get("onclick", "") for item in search):
        search_ids = {recaptcha_search_action(html)}
    elif any(item["element"].get("onclick", "").lstrip().startswith("return pesquisar()") for item in search):
        search_ids = {submit_function_search_action(html)}  # TJPE: the button calls submeterPesquisa()
    else:
        search_refs = {ref for item in search for ref in item["ajax_ids"]}
        search_ids = search_refs or {item["id"] for item in search if item["id"]}
    if len(search_ids) != 1:
        raise RuntimeError("Could not uniquely discover the PJe Pesquisar control")

    # The initial ramo/tribunal change is the only AJAX control that references
    # either dependent field. This remains stable when JSF regenerates j_id IDs.
    init = [
        item
        for item in controls
        if any(token in str(item["element"]).lower() for token in ("ramojustica", "respectivotribunal"))
        and item["ajax_ids"]
    ]
    init_ids = {ref for item in init for ref in item["ajax_ids"]}
    if len(init_ids) > 1:
        raise RuntimeError("Could not uniquely discover the PJe initialization control")
    return {"init": next(iter(init_ids), None), "search": next(iter(search_ids))}


def post_ajax(session, payload, search_url):
    return jsf.post(session, search_url, payload, label="PJe search response", require_state=True)


def select_control(html, process_id):
    soup = BeautifulSoup(unescape(html), "html.parser")
    for anchor in soup.select('a[onclick*="idProcessoSelecionado"]'):
        if re.search(rf"idProcessoSelecionado['\"]?\s*:\s*{re.escape(process_id)}", anchor.get("onclick", "")):
            return anchor.get("name") or anchor.get("id")
    raise RuntimeError("Process row found but its selection control was not found")


def _require_state(response, label):
    return jsf.page_state(response_html(response), label)


def _safe_page_url(candidate, detail_url):
    url = urljoin(detail_url, candidate)
    parts = urlsplit(url)
    detail_parts = urlsplit(detail_url)
    allowed_hosts = portals.hosts("pje")
    if (
        detail_parts.hostname not in allowed_hosts
        or (parts.scheme, parts.netloc) != ("https", detail_parts.netloc)
        or parts.path != detail_parts.path
    ):
        raise RuntimeError("Unsafe PJe timeline pagination URL")
    return url


def search_process(
    session,
    process_number,
    raw_html_path=None,
    *,
    portal,
    ato_expediente=None,
    ato_text_limit=20000,
    ato_allow_pending=False,
):
    config = portals.pje(portal)
    search_url = config.search_url
    parts = parse_process_number(process_number)
    response = jsf.get(session, search_url, label=f"{config.key} search page", referer=config.base_url)
    page_html = response_html(response)
    controls = discover_controls(page_html)
    state, fields = _require_state(response, "Search page")

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
        response = post_ajax(session, first, search_url)
        state, fields = _require_state(response, "PJe search init response")

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
    response = post_ajax(session, second, search_url)
    decoded = unescape(response_html(response))
    process_id, search_results = matching_process_id(decoded, process_number)
    if not process_id:
        error = (
            "Search result table was not returned"
            if not search_results["table_found"]
            else "Process not found or returned process number did not match"
        )
        return {"success": False, "error": error, "search_results": search_results, "timeline": []}
    state, fields = _require_state(response, "PJe search response")
    select_id = select_control(decoded, process_id)
    third = {
        "AJAXREQUEST": "_viewRoot",
        **fields,
        "fPP": "fPP",
        "javax.faces.ViewState": state or "",
        "idProcessoSelecionado": process_id,
        select_id: select_id,
        "ajaxSingle": select_id,
        "AJAX:EVENTS_COUNT": "2",
        "autoScroll": "",
    }
    response = post_ajax(session, third, search_url)
    selected_html = unescape(response_html(response))
    ca_match = re.search(r"ca=([a-fA-F0-9]{32,})", selected_html)
    if not ca_match:
        return {
            "success": False,
            "error": "Process selected but CA token was not returned",
            "processo_id": process_id,
            "search_results": search_results,
            "timeline": [],
        }

    detail_url = config.url(f"{DETAIL_PATH}?id={process_id}&ca={ca_match.group(1)}{DETAIL_SUFFIX[config.instance]}")
    response = jsf.get(session, detail_url, label=f"{config.key} detail page", referer=search_url)
    detail_html = response_html(response)
    canonical_number = "{}-{}.{}.{}.{}.{}".format(*parts)
    if not detail_matches_process_number(detail_html, canonical_number):
        return {
            "success": False,
            "error": "Detail page process number did not match requested process",
            "processo_id": process_id,
            "search_results": search_results,
            "timeline": [],
        }
    if raw_html_path:
        capture_path = Path(raw_html_path)
        capture_path.parent.mkdir(parents=True, exist_ok=True)
        capture_path.touch(mode=0o600, exist_ok=True)
        capture_path.chmod(0o600)
        capture_path.write_bytes(response.content)
    pattern_match = DOWNLOAD_PATTERN.search(detail_html)
    download_pattern = pattern_match.group(1) if pattern_match else None
    first_timeline = parse_timeline(detail_html, download_pattern, search_url)
    timeline, pagination = fetch_timeline_pages(session, detail_html, detail_url, download_pattern, search_url)
    documents = [doc for day in timeline for movement in day["movements"] for doc in movement["documents"]]
    diagnostics = []
    try:
        expedientes = fetch_expedientes(session, detail_html, detail_url)
    except RuntimeError as error:
        expedientes = None
        diagnostics.append({"code": "expedientes_unavailable", "message": str(error)[:200]})
    ato = None
    if ato_expediente and expedientes is not None:
        ato = {"expedienteId": str(ato_expediente)}
        try:
            ato.update(
                fetch_ato(
                    session,
                    ato_item(expedientes, ato_expediente),
                    detail_url,
                    ato_text_limit,
                    allow_pending=ato_allow_pending,
                )
            )
        except (RuntimeError, ValueError) as error:
            ato["error"] = str(error)[:300]
    return {
        "success": response.status_code == 200,
        "processo_id": process_id,
        "numero_processo": canonical_number,
        "detail_status": response.status_code,
        "download_pattern_found": bool(download_pattern),
        "days": len(timeline),
        "movements": sum(len(day["movements"]) for day in timeline),
        "documents": len(documents),
        "search_results": search_results,
        "process_details": parse_process_details(detail_html),
        "parties": parse_parties(detail_html),
        "timeline": timeline,
        "first_timeline": first_timeline,
        "document_inventory": parse_document_inventory(detail_html, timeline, download_pattern, search_url),
        "pagination": pagination,
        "latest_update": timeline[0] if timeline else None,
        "expedientes": expedientes,
        "ato": ato,
        "diagnostics": diagnostics,
    }


def parse_timeline_contract(html, detail_url):
    """Find the live novaPagina action without trusting arbitrary links."""
    soup = BeautifulSoup(unescape(html), "html.parser")
    ajax_scripts = [
        script for script in soup.find_all("script") if re.search(r"novaPagina\s*=\s*function", script.get_text())
    ]
    if ajax_scripts:
        actions = set()
        for script in ajax_scripts:
            match = re.search(r"similarityGroupingId['\"]\s*:\s*['\"](divTimeLine:[^'\"]+)", script.get_text())
            if match and script.get("id") == match.group(1):
                actions.add(match.group(1))
        if len(actions) != 1:
            raise RuntimeError("Ambiguous PJe timeline AJAX action")
        return {"ajax_action": actions.pop()}
    candidates = []
    for element in soup.find_all(["a", "form", "script"]):
        text = str(element)
        if "novaPagina" not in text:
            continue
        if element.name == "a" and element.get("href"):
            candidates.append(element["href"])
        candidates.extend(re.findall(r"(?:url|href|action)\s*[:=]\s*['\"]([^'\"]+)['\"]", text, re.I))
        candidates.extend(re.findall(r"novaPagina\s*\(\s*['\"]([^'\"]+)['\"]", text, re.I))
    candidates = list(dict.fromkeys(candidates))
    if not candidates:
        return None
    if len(candidates) != 1:
        raise RuntimeError("Ambiguous PJe timeline pagination contract")
    return _safe_page_url(candidates[0], detail_url)


def _merge_timelines(existing, incoming):
    days = {day.get("date", ""): day for day in existing}
    for day in incoming:
        target = days.setdefault(day.get("date", ""), {"date": day.get("date", ""), "movements": []})
        seen = {repr(item) for item in target["movements"]}
        for movement in day.get("movements", []):
            key = repr(movement)
            if key not in seen:
                target["movements"].append(movement)
                seen.add(key)
    return list(days.values())


def fetch_timeline_pages(session, first_html, detail_url, download_pattern, base_url, max_pages=MAX_TIMELINE_PAGES):
    timeline = parse_timeline(first_html, download_pattern, base_url)
    marker_present = "novaPagina" in first_html
    contract = parse_timeline_contract(first_html, detail_url)
    page = 1
    diagnostics = []
    visited_urls = set()
    repeated_contract = False
    form = BeautifulSoup(first_html, "html.parser").find("form", id="divTimeLine")
    latest_state = None
    if marker_present and not contract:
        diagnostics.append({"code": "pagination_contract_missing"})
    while contract and page < max_pages:
        ajax_page = isinstance(contract, dict)
        if isinstance(contract, str) and contract in visited_urls:
            diagnostics.append({"code": "pagination_repeated_contract", "page": page + 1})
            repeated_contract = True
            contract = None
            break
        if isinstance(contract, str):
            visited_urls.add(contract)
            response = jsf.get(session, contract, label=f"PJe timeline page {page + 1}", referer=detail_url)
        else:
            if not form:
                diagnostics.append({"code": "pagination_form_missing"})
                break
            action = contract["ajax_action"]
            fields = {
                field.get("name"): field.get("value", "")
                for field in form.find_all("input")
                if field.get("name") and field.get("type") not in {"submit", "checkbox", "radio"}
            }
            if latest_state:
                fields["javax.faces.ViewState"] = latest_state
            fields.update(
                {
                    "AJAXREQUEST": "_viewRoot",
                    "divTimeLine": "divTimeLine",
                    action: action,
                    "ajaxSingle": action,
                    "AJAX:EVENTS_COUNT": "1",
                }
            )
            response = jsf.post(session, detail_url, fields, label=f"PJe timeline page {page + 1}")
        page += 1
        incoming_html = response_html(response)
        state = BeautifulSoup(incoming_html, "html.parser").find("input", attrs={"name": "javax.faces.ViewState"})
        latest_state = state.get("value") if state else None
        before = sum(len(day["movements"]) for day in timeline)
        timeline = _merge_timelines(timeline, parse_timeline(incoming_html, download_pattern, base_url))
        after = sum(len(day["movements"]) for day in timeline)
        marker_present = "novaPagina" in incoming_html
        next_contract = parse_timeline_contract(incoming_html, detail_url)
        contract = next_contract
        if ajax_page and before == after:
            diagnostics.append({"code": "pagination_no_new_events", "page": page})
            contract = None
            repeated_contract = True
            break
    capped = bool(contract and page >= max_pages)
    if capped:
        diagnostics.append({"code": "pagination_limit", "maxPages": max_pages})
    parsed = parse_pagination(first_html, timeline)
    more_pages = bool(contract) and not repeated_contract
    complete = not marker_present and not more_pages and not capped and not repeated_contract
    parsed.update(
        {
            "page": page,
            "loadedPages": page,
            "loaded_pages": page,
            "complete": complete,
            "morePagesPossible": more_pages,
            "more_pages_possible": more_pages,
            "limit": max_pages if capped else None,
            "diagnostics": diagnostics,
        }
    )
    return timeline, parsed
