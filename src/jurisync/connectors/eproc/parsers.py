"""Pure, conservative parsers for the captured JFSC eproc HTML surface."""

import html as html_lib
import re
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup

_PROCESS_NUMBER = re.compile(r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}")


def text(node):
    return " ".join(node.get_text(" ", strip=True).split()) if node else ""


def _href(value, base_url):
    return urljoin(base_url, html_lib.unescape(value or ""))


def _query(href):
    return parse_qs(urlsplit(href).query, keep_blank_values=True)


def parse_panel(html, base_url):
    """Discover only routes visibly advertised by the authenticated panel."""
    soup = BeautifulSoup(html, "html.parser")
    queues = {}
    actions = {}
    for element in soup.select("a[href], form[action]"):
        absolute = _href(element.get("href") or element.get("action"), base_url)
        action = _query(absolute).get("acao", [""])[0]
        if not action:
            continue
        actions.setdefault(action, absolute)
        if action in {"citacao_intimacao_prazo_aberto_listar", "citacao_intimacao_pendente_listar"}:
            queues.setdefault(action, absolute)

    form = soup.select_one("form#formPesquisaRapida")
    quick = {}
    if form:
        number_field = form.select_one("[name=txtNumProcessoPesquisaRapida]")
        submit_field = form.select_one("[name=btnPesquisaRapidaSubmit], #btnPesquisaRapidaSubmit")
        return_field = form.select_one("[name=acao_retorno_pesquisa_rapida]")
        quick = {
            "action": _href(form.get("action"), base_url),
            "method": (form.get("method") or "get").lower(),
            "numberField": number_field.get("name") if number_field else None,
            "submitField": submit_field.get("name") if submit_field else None,
            "submitValue": submit_field.get("value") if submit_field else None,
            "returnField": return_field.get("name") if return_field else None,
            "returnValue": return_field.get("value") if return_field else None,
        }

    state = None
    for element in soup.select("[data-urlassinadacarregamento]"):
        candidate = _href(element.get("data-urlassinadacarregamento"), base_url)
        if _query(candidate).get("acao_ajax", [""])[0] == "retornar_citacao_intimacao_estado":
            state = candidate
            break
    return {
        "queues": {
            "open_deadlines": queues.get("citacao_intimacao_prazo_aberto_listar"),
            "pending": queues.get("citacao_intimacao_pendente_listar"),
        },
        "queueActions": queues,
        "actions": actions,
        "quickSearch": quick,
        "stateEndpoint": state,
    }


def _process_row(row, base_url):
    cells = row.find_all("td", recursive=False)
    headers = row.find_parent("table").find_all("th")
    expired = len(cells) == 7 and bool(headers) and "conferido" in text(headers[-1]).casefold()
    if len(cells) < 8 and not expired:
        return None
    # Expired-deadline rows omit the checkbox and deadline-start columns.
    offset = 0 if expired else 1
    process_cell = cells[offset]
    link = process_cell.select_one('a[href*="acao=processo_selecionar"]')
    if not link:
        return None
    parties = []
    for part in process_cell.select("[data-parte]"):
        name = text(part)
        if name and name not in {p["name"] for p in parties}:
            parties.append({"role": part.get("data-parte", "").strip(), "name": name})
    court = re.search(r"Ju[íi�]zo\s*:\s*([^\s]+)", text(process_cell), re.I)
    return {
        "id": (row.select_one("input.infraCheckbox[value]") or {}).get("value"),
        "number": text(link),
        "url": _href(link.get("href"), base_url),
        "court": court.group(1) if court else "",
        "parties": parties,
        "caseClass": text(cells[offset + 1]),
        "subject": text(cells[offset + 2]),
        "deadlineEvent": text(cells[offset + 3]),
        "requestedAt": text(cells[offset + 4]),
        "deadlineStart": "" if expired else text(cells[6]),
        "deadlineEnd": text(cells[5] if expired else cells[7]),
        "caseClassCode": row.get("data-classe"),
        "competence": row.get("data-competencia"),
    }


def parse_process_list(html, base_url):
    soup = BeautifulSoup(html, "html.parser")
    items = [
        item
        for row in soup.select("table.infraTable tr.infraTrClara, table.infraTable tr.infraTrEscura")
        if (item := _process_row(row, base_url))
    ]
    # Keep the historical list return for callers that already consume it.
    return items


def parse_process_list_page(html, base_url):
    """Return list items with pagination evidence from the returned form."""
    items = parse_process_list(html, base_url)
    soup = BeautifulSoup(html, "html.parser")
    return {"items": items, "pagination": _list_pagination(soup, items), "diagnostics": []}


def _list_pagination(soup, items):
    values = {node.get("name"): node.get("value", "") for node in soup.select("input[name]")}
    try:
        total_items = int(values["hdnInfraNroItens"])
    except (KeyError, TypeError, ValueError):
        total_items = None
    # Some queues leave the selection counter at zero but show the actual total in the caption.
    count = re.search(r"\((\d+)\s+registros?\)", text(soup.select_one("table.infraTable caption")), re.I)
    if count:
        total_items = int(count[1])
    if total_items is not None and total_items < len(items):
        total_items = None
    try:
        page = int(values.get("hdnInfraPaginaAtual", "0"))
    except (TypeError, ValueError):
        page = None
    return {
        "page": page,
        "totalItems": total_items,
        "itemCount": len(items),
        "complete": total_items is not None and len(items) >= total_items,
    }


def parse_lawyer_process_report(html, base_url):
    """Parse Relação de Processos, whose columns differ from the notice queues."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("table.infraTable")
    headers = [text(node) for node in table.select("th")] if table else []
    expected = [
        "Número Processo",
        "Classe",
        "Autores Principais",
        "Réu(s)",
        "Localidade Judicial",
        "Assunto",
        "Último Evento",
        "Data/Hora Ult. Evento",
        "Data/Hora Autuação",
        "Valor da Causa",
    ]
    if headers != ["", *expected]:
        return {"items": [], "pagination": {}, "diagnostics": ["unsupported_process_report_layout"]}
    items = []
    for row in table.select("tr.infraTrClara, tr.infraTrEscura"):
        cells = row.find_all("td", recursive=False)
        link = cells[1].select_one('a[href*="acao=processo_selecionar"]') if len(cells) == 11 else None
        if not link or not _PROCESS_NUMBER.fullmatch(text(link)):
            return {"items": [], "pagination": {}, "diagnostics": ["invalid_process_report_row"]}
        items.append(
            {
                "number": text(link),
                "url": _href(link.get("href"), base_url),
                "court": text(cells[1]).removeprefix(text(link)).strip(),
                "caseClass": text(cells[2]),
                "parties": [
                    {"role": role, "name": name}
                    for index, role in ((3, "AUTOR"), (4, "REU"))
                    for name in cells[index].stripped_strings
                ],
                "location": text(cells[5]),
                "subject": text(cells[6]),
                "lastEvent": text(cells[7]),
                "lastEventAt": text(cells[8]),
                "filedAt": text(cells[9]),
                "claimValue": text(cells[10]),
            }
        )
    closed = soup.select_one('input[name="chkMostrarBaixados"]')
    return {
        "items": items,
        "pagination": _list_pagination(soup, items),
        "filters": {
            "includeClosed": closed.has_attr("checked") if closed else None,
            "status": text(soup.select_one('select[name="selStatusProcesso"] option[selected]')) or None,
        },
        "diagnostics": [],
    }


def _document(anchor, base_url):
    href = _href(anchor.get("href"), base_url)
    query = _query(href)
    label = " ".join((anchor.get("aria-label") or "").split())
    title = " ".join((anchor.get("title") or "").split())
    kind = anchor.get("data-mimetype")
    if not kind:
        match = re.search(r"tipo\s+([\w/+.-]+)", label, re.I)
        kind = match.group(1).lower() if match else ""
    return {
        "id": anchor.get("data-doc") or (query.get("doc") or [None])[0],
        "name": anchor.get("data-nome") or title,
        "type": kind,
        "url": href,
        "ariaLabel": label,
        "title": title or label,
        "event": (query.get("evento") or [None])[0],
        "key": (query.get("key") or [None])[0],
        "sameInstance": (query.get("mesmoGrau") or [None])[0],
        "hash": (query.get("hash") or [None])[0],
    }


# The portal's cover-page field ids (txtNumProcesso, txtClasse, ...) mapped to the connector's field names.
COVER_FIELDS = {
    "NumProcesso": "number",
    "Classe": "caseClass",
    "Competencia": "competence",
    "Autuacao": "filedAt",
    "Situacao": "status",
    "OrgaoJulgador": "judgingBody",
    "Magistrado": "judge",
    "Localidade": "location",
}


def _cover(soup):
    cover = {}
    for node in soup.select("#fldCapa [id^=txt]"):
        key = node.get("id", "txt")[3:]
        cover[COVER_FIELDS.get(key, key)] = text(node)
    fieldset = soup.select_one("#fldCapa")
    if fieldset:
        cover.setdefault("caseClassCode", fieldset.get("data-classe"))
        cover.setdefault("competenceCode", fieldset.get("data-competencia"))
    return {key: value for key, value in cover.items() if value not in (None, "")}


def _subjects(soup):
    table = soup.select_one("#fldAssuntos table")
    if not table:
        return []
    result = []
    for row in table.select("tr"):
        cells = [text(cell) for cell in row.find_all(["td", "th"], recursive=False)]
        if len(cells) >= 2 and "Código" not in cells:
            result.append({"code": cells[0], "description": cells[1], "primary": cells[2] if len(cells) > 2 else ""})
    return result


def _parties(soup):
    table = soup.select_one("#tblPartesERepresentantes")
    if not table:
        return []
    result, role, headers = [], "", []
    for row in table.select("tr"):
        ths = row.find_all("th", recursive=False)
        if ths:
            labels = [text(th) for th in ths]
            if len(labels) > 1:
                headers = labels
            elif labels[0]:
                role = labels[0]
            continue
        for index, cell in enumerate(row.find_all("td", recursive=False)):
            cpf = text(cell.select_one("[id^='spnCpfParte']"))
            representatives = []
            for sr in cell.select(".sr-only"):
                value = text(sr)
                if "Tipo de Usu" in value:
                    representatives.append(value.split(":", 1)[-1].strip())
            seen = set()
            for part in cell.select("[data-parte]"):
                name = text(part)
                party_role = part.get("data-parte", role).strip()
                key = (party_role, headers[index] if index < len(headers) else role, name, cpf)
                if not name or key in seen:
                    continue
                seen.add(key)
                result.append(
                    {
                        "role": party_role,
                        "group": headers[index] if index < len(headers) else role,
                        "name": name,
                        "taxId": cpf,
                        "representatives": representatives,
                    }
                )
    return result


def _event_rows(soup, base_url):
    result = []
    for row in soup.select("#tblEventos tr[id^='trEvento'], #tblEventosNovos tr[id^='trEvento']"):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 5:
            continue
        docs = [_document(a, base_url) for a in cells[4].select("a.infraLinkDocumento, a[data-doc]")]
        user = cells[3].select_one("label.infraEventoUsuario")
        label = cells[2].select_one("label.infraEventoDescricao")
        result.append(
            {
                "id": re.sub(r"\D", "", row.get("id", "")),
                "dateTime": text(cells[1]),
                "label": text(label) or text(cells[2]),
                "description": text(cells[2]),
                "user": text(user),
                "userDetails": (user.get("aria-label", "") if user else "").replace("<br>", " ").strip(),
                "role": row.get("data-parte"),
                "classes": row.get("class", []),
                "kind": "task" if any("Prazo" in c for c in row.get("class", [])) else "event",
                "documents": docs,
            }
        )
    return result


def _event_result(soup, *, complete=True, pagination=None, base_url):
    events = _event_rows(soup, base_url)
    documents = [doc for event in events for doc in event["documents"]]
    documents = list({doc["id"]: doc for doc in documents if doc.get("id")}.values())
    return {
        "events": events,
        "documents": documents,
        "eventCount": len(events),
        "documentCount": len(documents),
        "pagination": pagination or {"page": 0, "totalPages": None, "complete": complete},
    }


def parse_event_response(body, base_url):
    """Parse one fragment or the portal's five-part ``±`` response."""
    parts = body.split("±") if isinstance(body, str) else []
    if len(parts) <= 1:
        return _event_result(BeautifulSoup(body or "", "html.parser"), base_url=base_url)
    total = None
    try:
        total = int(parts[4].strip())
    except (IndexError, TypeError, ValueError):
        pass
    pagination = {"page": 0, "totalPages": total, "complete": False, "hasNext": bool(total and total > 1)}
    if len(parts) > 3 and parts[3].strip():
        pagination["url"] = parts[3].strip()
    return _event_result(
        BeautifulSoup(parts[1], "html.parser"), complete=False, pagination=pagination, base_url=base_url
    )


def parse_process_detail(html, base_url):
    soup = BeautifulSoup(html, "html.parser")
    result = {
        "parties": _parties(soup),
        "subjects": _subjects(soup),
        "events": [],
        "documents": [],
        "cover": _cover(soup),
        "additionalInformation": text(soup.select_one("#fldInformacoesAdicionais_content")),
        "pagination": {"page": 0, "totalPages": None, "complete": True},
    }
    result["title"] = text(soup.select_one("h1, h2, .infraLegendObrigatorio"))
    result["number"] = text(soup.select_one("#txtNumProcesso")) or next(
        iter(_PROCESS_NUMBER.findall(soup.get_text(" ", strip=True))), None
    )
    result["events"] = _event_rows(soup, base_url)
    documents = [doc for event in result["events"] for doc in event["documents"]]
    result["documents"] = list({doc["id"]: doc for doc in documents if doc.get("id")}.values())
    result["eventCount"] = len(result["events"])
    result["documentCount"] = len(result["documents"])
    event_url = re.search(
        r"[\"']([^\"']*controlador_ajax\.php\?acao_ajax=montar_tabela_eventos_filtrados[^\"']*)[\"']", html
    )
    if event_url:
        checkboxes = soup.select("#tblOpcoesEventos input[type=checkbox][value]")
        result["eventRequest"] = {
            "url": _href(event_url.group(1), base_url),
            "data": {checkbox["value"]: "true" if checkbox.has_attr("checked") else "false" for checkbox in checkboxes},
        }
    else:
        result["eventRequest"] = None
    return result
