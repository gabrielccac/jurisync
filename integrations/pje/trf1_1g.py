#!/usr/bin/env python3
"""Local equivalent of the existing TRF1 1G n8n search + attachment pipeline."""

import re
from html import unescape
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from bs4 import BeautifulSoup

from .auth import APP_HOST_1G, login_session
from . import trf1_2g
from .trf1_2g import download_documents, jsf_data, parse_process_number, parse_timeline, response_html


APP_URL = f"https://{APP_HOST_1G}/pje/login.seam"
SEARCH_URL = f"https://{APP_HOST_1G}/pje/Processo/ConsultaProcesso/listView.seam"
PROCESS_NUMBER = ""
INIT_BUTTON_ID = "fPP:j_id550"
SEARCH_BUTTON_ID = "fPP:j_id494"



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


def select_control(html, process_id):
    soup = BeautifulSoup(unescape(html), "html.parser")
    for anchor in soup.select('a[onclick*="idProcessoSelecionado"]'):
        if re.search(rf"idProcessoSelecionado['\"]?\s*:\s*{re.escape(process_id)}", anchor.get("onclick", "")):
            return anchor.get("name") or anchor.get("id")
    return f"fPP:processosTable:{process_id}:j_id509"


def search_process_1g(session, process_number, raw_html_path=None):
    parts = parse_process_number(process_number)
    response = session.get(SEARCH_URL, headers={"Referer": f"https://{APP_HOST_1G}/pje/"}, impersonate="chrome")
    print(f"GET  {urlsplit(SEARCH_URL).path} -> {response.status_code}")
    if response.status_code != 200:
        raise RuntimeError(f"Search page returned HTTP {response.status_code}")
    state, fields = jsf_data(response_html(response))
    if not state:
        raise RuntimeError("Search page did not contain javax.faces.ViewState")

    first = {
        "AJAXREQUEST": "_viewRoot",
        **fields,
        "fPP": "fPP",
        "javax.faces.ViewState": state,
        INIT_BUTTON_ID: INIT_BUTTON_ID,
        "ajaxSingle": INIT_BUTTON_ID,
        "AJAX:EVENTS_COUNT": "1",
        "fPP:numeroProcesso:ramoJustica": parts[3],
        "fPP:numeroProcesso:respectivoTribunal": parts[4],
        "autoScroll": "",
    }
    response = post_ajax(session, first)
    state, fields = jsf_data(response_html(response))
    if not state:
        raise RuntimeError("First 1G JSF response did not contain javax.faces.ViewState")

    second = {
        "AJAXREQUEST": "_viewRoot",
        **fields,
        "fPP": "fPP",
        "javax.faces.ViewState": state,
        SEARCH_BUTTON_ID: SEARCH_BUTTON_ID,
        "ajaxSingle": SEARCH_BUTTON_ID,
        "fPP:numeroProcesso:numeroSequencial": parts[0],
        "fPP:numeroProcesso:numeroDigitoVerificador": parts[1],
        "fPP:numeroProcesso:Ano": parts[2],
        "fPP:numeroProcesso:ramoJustica": parts[3],
        "fPP:numeroProcesso:respectivoTribunal": parts[4],
        "fPP:numeroProcesso:NumeroOrgaoJustica": parts[5],
        "AJAX:EVENTS_COUNT": "1",
        "autoScroll": "",
    }
    response = post_ajax(session, second)
    decoded = unescape(response_html(response))
    search_results = trf1_2g.parse_search_results(decoded)
    process_match = re.search(r"['\"]?idProcessoSelecionado['\"]?\s*:\s*(\d+)", decoded)
    if not process_match:
        return {"success": False, "error": "Process not found or 1G component IDs differ", "search_results": search_results, "timeline": []}
    process_id = process_match.group(1)
    state, fields = jsf_data(response_html(response))
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
    response = post_ajax(session, third)
    ca_match = re.search(r"ca=([a-fA-F0-9]{32,})", unescape(response_html(response)))
    if not ca_match:
        return {"success": False, "error": "Process selected but CA token was not returned", "processo_id": process_id, "search_results": search_results, "timeline": []}

    detail_url = f"https://{APP_HOST_1G}/pje/Processo/ConsultaProcesso/Detalhe/listProcessoCompletoAdvogado.seam?id={process_id}&ca={ca_match.group(1)}&aba="
    response = session.get(detail_url, headers={"Referer": SEARCH_URL}, impersonate="chrome")
    print(f"GET  {urlsplit(detail_url).path} -> {response.status_code}")
    detail_html = response_html(response)
    if raw_html_path:
        Path(raw_html_path).parent.mkdir(parents=True, exist_ok=True)
        Path(raw_html_path).write_bytes(response.content)
    pattern_match = re.search(r"window\.open\('([^']*/documento/download/TRF1/[^']*/\d+/)'", detail_html)
    download_pattern = pattern_match.group(1) if pattern_match else None
    timeline = parse_timeline(detail_html, download_pattern, SEARCH_URL)
    documents = [doc for day in timeline for movement in day["movements"] for doc in movement["documents"]]
    return {
        "success": response.status_code == 200,
        "processo_id": process_id,
        "numero_processo": process_number,
        "detail_status": response.status_code,
        "download_pattern_found": bool(download_pattern),
        "days": len(timeline),
        "movements": sum(len(day["movements"]) for day in timeline),
        "documents": len(documents),
        "search_results": search_results,
        "process_details": trf1_2g.parse_process_details(detail_html),
        "parties": trf1_2g.parse_parties(detail_html),
        "timeline": timeline,
        "document_inventory": trf1_2g.parse_document_inventory(detail_html, timeline, download_pattern, SEARCH_URL),
        "pagination": trf1_2g.parse_pagination(detail_html, timeline),
    }
