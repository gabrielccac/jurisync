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
    for element in soup.select("a[href], form[action]"):
        absolute = _href(element.get("href") or element.get("action"), base_url)
        action = _query(absolute).get("acao", [""])[0]
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
        "quickSearch": quick,
        "stateEndpoint": state,
    }


def _process_row(row, base_url):
    cells = row.find_all("td", recursive=False)
    if len(cells) < 8:
        return None
    link = cells[1].select_one('a[href*="acao=processo_selecionar"]')
    if not link:
        return None
    parties = []
    for part in cells[1].select("[data-parte]"):
        name = text(part)
        if name and name not in {p["nome"] for p in parties}:
            parties.append({"parte": part.get("data-parte", "").strip(), "nome": name})
    court = re.search(r"Ju[íi�]zo\s*:\s*([^\s]+)", text(cells[1]), re.I)
    return {
        "id": (row.select_one("input.infraCheckbox[value]") or {}).get("value"),
        "numero": text(link),
        "url": _href(link.get("href"), base_url),
        "juizo": court.group(1) if court else "",
        "partes": parties,
        "classe": text(cells[2]),
        "assunto": text(cells[3]),
        "eventoPrazo": text(cells[4]),
        "dataEnvioRequisicao": text(cells[5]),
        "inicioPrazo": text(cells[6]),
        "finalPrazo": text(cells[7]),
        "classeCodigo": row.get("data-classe"),
        "competencia": row.get("data-competencia"),
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
    values = {node.get("name"): node.get("value", "") for node in soup.select("input[name]")}
    try:
        total_items = int(values["hdnInfraNroItens"])
    except (KeyError, TypeError, ValueError):
        total_items = None
    try:
        page = int(values.get("hdnInfraPaginaAtual", "0"))
    except (TypeError, ValueError):
        page = None
    return {
        "items": items,
        "pagination": {
            "page": page,
            "totalItems": total_items,
            "itemCount": len(items),
            "complete": total_items is not None and len(items) >= total_items,
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
        "nome": anchor.get("data-nome") or title,
        "tipo": kind,
        "url": href,
        "ariaLabel": label,
        "titulo": title or label,
        "evento": (query.get("evento") or [None])[0],
        "key": (query.get("key") or [None])[0],
        "mesmoGrau": (query.get("mesmoGrau") or [None])[0],
        "hash": (query.get("hash") or [None])[0],
    }


def _cover(soup):
    cover = {}
    for node in soup.select("#fldCapa [id^=txt]"):
        key = node.get("id", "txt")[3:]
        cover[key] = text(node)
    fieldset = soup.select_one("#fldCapa")
    if fieldset:
        cover.setdefault("classeCodigo", fieldset.get("data-classe"))
        cover.setdefault("competenciaCodigo", fieldset.get("data-competencia"))
    return {key: value for key, value in cover.items() if value not in (None, "")}


def _subjects(soup):
    table = soup.select_one("#fldAssuntos table")
    if not table:
        return []
    result = []
    for row in table.select("tr"):
        cells = [text(cell) for cell in row.find_all(["td", "th"], recursive=False)]
        if len(cells) >= 2 and "Código" not in cells:
            result.append({"codigo": cells[0], "descricao": cells[1], "principal": cells[2] if len(cells) > 2 else ""})
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
                papel = part.get("data-parte", role).strip()
                key = (papel, headers[index] if index < len(headers) else role, name, cpf)
                if not name or key in seen:
                    continue
                seen.add(key)
                result.append(
                    {
                        "papel": papel,
                        "grupo": headers[index] if index < len(headers) else role,
                        "nome": name,
                        "documento": cpf,
                        "representantes": representatives,
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
                "dataHora": text(cells[1]),
                "label": text(label) or text(cells[2]),
                "descricao": text(cells[2]),
                "usuario": text(user),
                "usuarioDetalhes": (user.get("aria-label", "") if user else "").replace("<br>", " ").strip(),
                "parte": row.get("data-parte"),
                "classes": row.get("class", []),
                "tipo": "tarefa" if any("Prazo" in c for c in row.get("class", [])) else "evento",
                "documentos": docs,
            }
        )
    return result


def _event_result(soup, *, complete=True, pagination=None, base_url):
    events = _event_rows(soup, base_url)
    documents = [doc for event in events for doc in event["documentos"]]
    documents = list({doc["id"]: doc for doc in documents if doc.get("id")}.values())
    return {
        "eventos": events,
        "documentos": documents,
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
        "partes": _parties(soup),
        "assuntos": _subjects(soup),
        "eventos": [],
        "documentos": [],
        "capa": _cover(soup),
        "informacoesAdicionais": text(soup.select_one("#fldInformacoesAdicionais_content")),
        "pagination": {"page": 0, "totalPages": None, "complete": True},
    }
    result["titulo"] = text(soup.select_one("h1, h2, .infraLegendObrigatorio"))
    result["numero"] = text(soup.select_one("#txtNumProcesso")) or next(
        iter(_PROCESS_NUMBER.findall(soup.get_text(" ", strip=True))), None
    )
    result["eventos"] = _event_rows(soup, base_url)
    documents = [doc for event in result["eventos"] for doc in event["documentos"]]
    result["documentos"] = list({doc["id"]: doc for doc in documents if doc.get("id")}.values())
    result["eventCount"] = len(result["eventos"])
    result["documentCount"] = len(result["documentos"])
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
