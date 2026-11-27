"""Pure, conservative parsers for the captured JFSC eproc HTML surface."""

import html as html_lib
import re
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup

from jurisync.core.redaction import mask_cpf

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


_SESSION_PERIOD = re.compile(r"(\d{2}/\d{2}/\d{4}(?: \d{2}:\d{2}(?::\d{2})?)?)")
_EVENT_STAMP = re.compile(r"\((\d{2}/\d{2}/\d{4} \d{2}:\d{2}(?::\d{2})?)\)")


def _plain_parties(cell):
    """Parties drawn as plain text (name, then "(document) - ROLE"); the document number is left out."""
    lines = [line.strip() for line in cell.get_text("\n", strip=True).split("\n") if line.strip()]
    parties = []
    for index, line in enumerate(lines):
        if index and index + 1 < len(lines) and re.fullmatch(r"\([^)]*\)\s*-", line):
            name, role = lines[index - 1], lines[index + 1]
            if name not in {party["name"] for party in parties}:
                parties.append({"role": role, "name": name})
    return parties


def _hearing_row(row, cells, base_url):
    """A row of "Intimações de Pauta" (TJSC 2G, 16/11/2026): Processo, Assunto, Evento, Data Sessão, Sequencial,
    Composição prevista, Visualizado. It has no class or deadline columns; the session period takes their place."""
    process_cell = cells[0]
    link = process_cell.select_one('a[href*="acao=processo_selecionar"]')
    if not link:
        return None
    court = re.search(r"Ju[íi�]zo\s*:\s*([^\s]+)", text(process_cell), re.I)
    lines = [line.strip() for line in process_cell.get_text("\n", strip=True).split("\n") if line.strip()]
    collegiate = next(
        (lines[i + 1] for i, line in enumerate(lines[:-1]) if line.casefold().rstrip(": ") == "colegiado"), ""
    )
    session = _SESSION_PERIOD.findall(text(cells[3]))
    stamp = _EVENT_STAMP.search(text(cells[2]))
    event_lines = [line.strip() for line in cells[2].get_text("\n", strip=True).split("\n") if line.strip()]
    return {
        "id": None,
        "number": text(link),
        "url": _href(link.get("href"), base_url),
        "court": court.group(1) if court else "",
        "collegiate": collegiate,
        "parties": _plain_parties(process_cell),
        "caseClass": "",
        "subject": text(cells[1]),
        "deadlineEvent": event_lines[0] if event_lines else "",
        "requestedAt": stamp.group(1) if stamp else "",
        "deadlineStart": "",
        "deadlineEnd": "",
        "sessionStart": session[0] if session else "",
        "sessionEnd": session[1] if len(session) > 1 else "",
        "sessionSequence": text(cells[4]),
        "caseClassCode": row.get("data-classe"),
        "competence": row.get("data-competencia"),
    }


def _process_row(row, base_url):
    cells = row.find_all("td", recursive=False)
    headers = row.find_parent("table").find_all("th")
    if len(cells) >= 5 and len(headers) >= 4 and "sess" in text(headers[3]).casefold():
        return _hearing_row(row, cells, base_url)
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
    # Some queues leave the selection counter at zero but show the actual total in the caption. A list longer than one
    # page says "(843 registros - 1 a 500)" while the counter holds the page size (TJSC report, 19/11/2026).
    count = re.search(
        r"\((\d+)\s+registros?(?:\s*-\s*\d+\s+a\s+\d+)?\)", text(soup.select_one("table.infraTable caption")), re.I
    )
    if count:
        total_items = int(count[1])
    if total_items is not None and total_items < len(items):
        total_items = None
    try:
        page = int(values.get("hdnInfraPaginaAtual", "0"))
    except (TypeError, ValueError):
        page = None
    pager = soup.select_one("select#selInfraPaginacaoSuperior, select#selInfraPaginacaoInferior")
    return {
        "page": page,
        "totalPages": len(pager.select("option")) if pager else None,
        "totalItems": total_items,
        "itemCount": len(items),
        "complete": total_items is not None and len(items) >= total_items,
    }


def parse_lawyer_process_report(html, base_url):
    """Parse Relação de Processos, whose columns differ from the notice queues."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("table.infraTable")
    headers = [text(node) for node in table.select("th")] if table else []
    if (
        table is None
        and soup.select_one("form#frmProcessoLista")
        and "nenhum registro encontrado" in text(soup).lower()
    ):
        # TJSP 2G shows the filter form with this message and no table when the lawyer has no process there.
        return {
            "items": [],
            "pagination": {"page": 0, "totalItems": 0, "itemCount": 0, "complete": True},
            "filters": {"includeClosed": None, "status": None},
            "diagnostics": [],
        }
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
                # "Autores Principais" and "Réu(s)" hold the active and passive poles; the procedural role (Autor,
                # Requerente on TRF4, Apelante on TRF6...) is not on this page, only in the process (parties[].role).
                # role (AUTOR/REU) is the old name of pole, kept for one cycle (docs/padroes.md).
                "parties": [
                    {"pole": pole, "role": role, "name": name}
                    for index, pole, role in ((3, "active", "AUTOR"), (4, "passive", "REU"))
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


def form_fields(form):
    """The ``(name, value)`` pairs a browser submits for ``form`` (no submit button pressed)."""
    fields = []
    for node in form.select("input[name], select[name], textarea[name]"):
        if node.has_attr("disabled"):
            continue
        name = node["name"]
        if node.name == "select":
            options = node.select("option")
            chosen = [option for option in options if option.has_attr("selected")]
            if not chosen and not node.has_attr("multiple") and options:
                chosen = options[:1]
            fields.extend((name, option.get("value", text(option))) for option in chosen)
        elif node.name == "textarea":
            fields.append((name, node.get_text()))
        else:
            kind = (node.get("type") or "text").lower()
            if kind in ("submit", "button", "image", "reset", "file"):
                continue
            if kind in ("checkbox", "radio") and not node.has_attr("checked"):
                continue
            fields.append((name, node.get("value", "on" if kind in ("checkbox", "radio") else "")))
    return fields


def report_page_request(html, base_url, page):
    """``(url, fields)`` that ask Relação de Processos for ``page`` (0-based), as its pager does; ``None`` without one.

    The pager (``infraAcaoPaginar('=', page, 'Infra')``) sets ``hdnInfraPaginaAtual`` and re-posts the list form with
    the same criteria, so the portal keeps the filters and the ordering of the first page.
    """
    soup = BeautifulSoup(html, "html.parser")
    form = soup.select_one("form#frmProcessoLista[action]")
    if form is None or not form.select_one('input[name="hdnInfraPaginaAtual"]'):
        return None
    fields = [(name, value) for name, value in form_fields(form) if name not in ("hdnInfraPaginaAtual", "hdnExcel")]
    fields += [("hdnInfraPaginaAtual", str(page)), ("hdnExcel", "0")]
    return _href(form["action"], base_url), fields


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


# The portal's cover-page field ids (txtNumProcesso, txtClasse, ...) mapped to the connector's field names. On a
# 2nd-degree court "judge" is the Relator(a). An id not mapped here goes under "extra" with its raw name, so the
# keys of the cover itself are the same on every court. LEGACY_COVER_KEYS repeats a renamed field under its old raw
# key for one cycle (docs/padroes.md).
COVER_FIELDS = {
    "NumProcesso": "number",
    "Classe": "caseClass",
    "Competencia": "competence",
    "Autuacao": "filedAt",
    "Situacao": "status",
    "OrgaoJulgador": "judgingBody",
    "Magistrado": "judge",
    "Localidade": "location",
    "MagistradoAtuante": "actingJudge",
    "OrgaoColegiado": "collegiateBody",
    "NumRequis": "paymentRequestNumber",
}
LEGACY_COVER_KEYS = ("MagistradoAtuante", "OrgaoColegiado", "NumRequis")


def _cover(soup):
    cover, extra = {}, {}
    for node in soup.select("#fldCapa [id^=txt]"):
        key, value = node.get("id", "txt")[3:], text(node)
        if value:
            if key in COVER_FIELDS:
                cover[COVER_FIELDS[key]] = value
                if key in LEGACY_COVER_KEYS:
                    cover[key] = value
            else:
                extra[key] = value
    fieldset = soup.select_one("#fldCapa")
    if fieldset:
        cover.setdefault("caseClassCode", fieldset.get("data-classe"))
        cover.setdefault("competenceCode", fieldset.get("data-competencia"))
    cover = {key: value for key, value in cover.items() if value not in (None, "")}
    return {**cover, "extra": extra} if extra else cover


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
                        "taxId": mask_cpf(cpf),
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
    # Newer eproc layouts put a screen-reader-only "Menu lateral" h2 before the page h1.
    result["title"] = text(soup.select_one("h1") or soup.select_one("h2:not(.sr-only), .infraLegendObrigatorio"))
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
