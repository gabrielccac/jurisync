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


# Query parameters that narrow a panel link to part of its list: the deadlines expiring today, the urgent ones, the
# Relação de Processos by latest movement, the next hearing. A link with one of them is kept apart from the full list.
LINK_VARIANTS = ("vence_hoje", "urgente", "ord_ultimas_movimentacoes", "proxima")


def parse_panel(html, base_url):
    """Discover only routes visibly advertised by the authenticated panel."""
    soup = BeautifulSoup(html, "html.parser")
    queues = {}
    actions = {}
    variants = {}
    for element in soup.select("a[href], form[action]"):
        absolute = _href(element.get("href") or element.get("action"), base_url)
        query = _query(absolute)
        action = query.get("acao", [""])[0]
        if not action:
            continue
        variant = next((name for name in LINK_VARIANTS if name in query), None)
        if variant:
            variants.setdefault(action, {}).setdefault(variant, absolute)
            continue
        actions.setdefault(action, absolute)
        if action in {"citacao_intimacao_prazo_aberto_listar", "citacao_intimacao_pendente_listar"}:
            queues.setdefault(action, absolute)
    for action, found in variants.items():
        # A panel that shows only the narrowed link (TJMG: "vencendo hoje") still advertises the route.
        actions.setdefault(action, next(iter(found.values())))

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
    instances = {}
    for element in soup.select("[data-urlassinadacarregamento]"):
        candidate = _href(element.get("data-urlassinadacarregamento"), base_url)
        query = _query(candidate)
        if query.get("acao_ajax", [""])[0] != "retornar_citacao_intimacao_estado":
            continue
        state = state or candidate
        name = query.get("estado", [""])[0]
        entry = instances.setdefault(name, {"instance": name, "label": "", "current": False, "url": candidate})
        entry["current"] = entry["current"] or query.get("usuarioAtual", [""])[0] == "true"
        entry["label"] = entry["label"] or text(element)
    counters = re.search(r"[\"']([^\"'\s]*controlador_ajax\.php\?acao_ajax=listar_painel_advogado[^\"'\s]*)[\"']", html)
    due_today = next(
        (
            text(anchor)
            for anchor in soup.select('a[href*="vence_hoje=S"]')
            if "citacao_intimacao_prazo_aberto_listar" in anchor.get("href", "")
        ),
        "",
    )
    return {
        "queues": {
            "open_deadlines": queues.get("citacao_intimacao_prazo_aberto_listar"),
            "pending": queues.get("citacao_intimacao_pendente_listar"),
        },
        "queueActions": queues,
        "actions": actions,
        "variants": variants,
        "quickSearch": quick,
        "stateEndpoint": state,
        # The counters of each instance's citações/intimações tab (TJMG: MG and TJMG; JFRS: RS, TRF4, SC, PR).
        "instances": list(instances.values()),
        # The ajax call that fills the panel's counters, and the fieldsets the panel asks it for.
        "countersEndpoint": _href(html_lib.unescape(counters.group(1)), base_url) if counters else None,
        "counterFieldsets": list(dict.fromkeys(re.findall(r"buscarDadosPainel\(\s*'(\w+)'\s*\)", html))),
        "dueTodayCount": int(due_today) if due_today.isdigit() else None,
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


def _court(cell):
    """The whole juízo after "Juízo:" ("BHE 6 V.C. J" at TJMG, "SCUABI011" at JFSC), up to the next line break."""
    lines = [line.strip() for line in cell.get_text("\n", strip=True).split("\n") if line.strip()]
    for index, line in enumerate(lines):
        found = re.match(r"Ju[íi�]zo\s*:\s*(.*)", line, re.I)
        if found:
            return found.group(1).strip() or (lines[index + 1] if index + 1 < len(lines) else "")
    return ""


_DOCUMENT_LINE = re.compile(r"\(?\s*[\d./-]+\s*\)?")
_CELL_LABELS = {"juízo:", "juizo:", "colegiado:", "partes:"}


def _labelled_parties(cell):
    """Parties drawn under bold role headers (``<b>Autor</b><br>NAME<br>(document)<br>X<b>Réu</b>...``, TJMG and
    JFRS queues, 27/11/2026); the document number is left out."""
    parties, role = [], None
    for string in cell.find_all(string=True):
        value = " ".join(string.split())
        if not value or string.find_parent("a") or string.find_parent("script"):
            continue
        if string.find_parent("b"):
            role = None if value.casefold() in _CELL_LABELS else value
            continue
        known = any(party["name"] == value for party in parties)
        if role and value != "X" and not _DOCUMENT_LINE.fullmatch(value) and not known:
            parties.append({"role": role, "name": value})
    return parties


def _hearing_row(row, cells, base_url):
    """A row of "Intimações de Pauta" (TJSC 2G, 16/11/2026): Processo, Assunto, Evento, Data Sessão, Sequencial,
    Composição prevista, Visualizado. It has no class or deadline columns; the session period takes their place."""
    process_cell = cells[0]
    link = process_cell.select_one('a[href*="acao=processo_selecionar"]')
    if not link:
        return None
    lines = [line.strip() for line in process_cell.get_text("\n", strip=True).split("\n") if line.strip()]
    collegiate = next(
        (lines[i + 1] for i, line in enumerate(lines[:-1]) if line.casefold().rstrip(": ") == "colegiado"), ""
    )
    session = _SESSION_PERIOD.findall(text(cells[3]))
    stamp = _EVENT_STAMP.search(text(cells[2]))
    event_lines = [line.strip() for line in cells[2].get_text("\n", strip=True).split("\n") if line.strip()]
    return {
        "id": None,
        "processNumber": text(link),
        "number": text(link),
        "url": _href(link.get("href"), base_url),
        "court": _court(process_cell),
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
    return {
        "id": (row.select_one("input.infraCheckbox[value]") or {}).get("value"),
        "processNumber": text(link),
        "number": text(link),
        "url": _href(link.get("href"), base_url),
        "court": _court(process_cell),
        "parties": parties or _labelled_parties(process_cell),
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
                "processNumber": text(link),
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
            "prazo": text(soup.select_one('select[name="prazoField"] option[selected]')) or None,
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


# Relação de Processos filters: the Prazo select's values and the "Listar processos baixados" checkbox (every portal
# leaves it unchecked, so baixados are left out unless asked for; TJMG and JFRS, 27/11/2026).
PRAZO_FILTER = {"aguardando_abertura": "S", "vencido": "V", "aberto": "A", "fechado": "F", "sem_prazo": "SP"}
ARCHIVED_FIELD = "chkMostrarBaixados"
# The intimation date fields beside Prazo: a period (Data Início, Data Fim) and a single day (Data da Intimação), on
# TRF4, TRF6, TJSC and TJRJ (27/11/2026).
DATE_FIELDS = ("dataInicioField", "dataFimField")
INTIMATION_DATE_FIELD = "dataIntimacaoField"


def _date_fields(form, date_from, date_to):
    names = {node["name"] for node in form.select("input[name]")}
    if set(DATE_FIELDS) <= names:
        return dict(zip(DATE_FIELDS, (date_from or "", date_to or ""), strict=True))
    if INTIMATION_DATE_FIELD in names and date_from == date_to:
        return {INTIMATION_DATE_FIELD: date_from}
    if INTIMATION_DATE_FIELD in names:
        raise ValueError("this portal's Relação de Processos filters a single intimation date: pass the same two dates")
    raise ValueError("this portal's Relação de Processos has no intimation date filter")


def report_filter_request(html, base_url, *, include_archived=None, prazo=None, date_from=None, date_to=None):
    """``(url, fields)`` that search Relação de Processos again with these filters, as its Buscar button does.

    ``include_archived`` None keeps the form's choice; ``prazo`` one of ``PRAZO_FILTER``; ``date_from``/``date_to``
    (``DD/MM/AAAA``) go to Data Início/Data Fim, or to Data da Intimação when the form has only that day. Raises
    ``ValueError`` when the form lacks the field a filter needs, and returns None without the form.
    """
    soup = BeautifulSoup(html, "html.parser")
    form = soup.select_one("form#frmProcessoLista[action]")
    if form is None:
        return None
    fields = [(name, value) for name, value in form_fields(form) if name not in ("hdnInfraPaginaAtual", "hdnExcel")]
    if prazo is not None:
        options = {option.get("value") for option in form.select('select[name="prazoField"] option')}
        if PRAZO_FILTER[prazo] not in options:
            raise ValueError("this portal's Relação de Processos has no Prazo filter")
        fields = [(name, value) for name, value in fields if name != "prazoField"] + [
            ("prazoField", PRAZO_FILTER[prazo])
        ]
    if include_archived is not None:
        box = form.select_one(f'input[name="{ARCHIVED_FIELD}"]')
        if box is None:
            raise ValueError("this portal's Relação de Processos has no Listar baixados option")
        fields = [(name, value) for name, value in fields if name != ARCHIVED_FIELD]
        if include_archived:
            fields.append((ARCHIVED_FIELD, box.get("value", "S")))
    if date_from or date_to:
        dates = _date_fields(form, date_from, date_to)
        fields = [(name, value) for name, value in fields if name not in dates] + list(dates.items())
    fields += [("hdnInfraPaginaAtual", "0"), ("hdnExcel", "0")]
    return _href(form["action"], base_url), fields


def report_page_request(html, base_url, page, *, form="form#frmProcessoLista[action]"):
    """``(url, fields)`` that ask Relação de Processos (or the list whose ``form`` is given) for ``page`` (0-based), as
    its pager does; ``None`` without one.

    The pager (``infraAcaoPaginar('=', page, 'Infra')``) sets ``hdnInfraPaginaAtual`` and re-posts the list form with
    the same criteria, so the portal keeps the filters and the ordering of the first page.
    """
    soup = BeautifulSoup(html, "html.parser")
    form = soup.select_one(form)
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
# 2nd-degree court "judge" is the Relator(a). An id not mapped here goes under "extra" with its raw name. For one cycle
# (docs/padroes.md) every raw key that used to sit at the top still does: the renamed LEGACY_COVER_KEYS and any
# unmapped id. In the captures of TJSC 1G, TRF4 2G, TRF6 2G and TJRJ 1G (27/11/2026) these three were the only ids
# outside the original eight.
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
            else:
                extra[key] = value
            if key in LEGACY_COVER_KEYS or key not in COVER_FIELDS:
                cover[key] = value
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


def additional_information_request(html, base_url):
    """``(url, data)`` the process page posts to load its "Informações Adicionais" (its legend's
    ``carregarInformacoesAdicionais('url', {...})``), or None when the page has none."""
    found = re.search(r"carregarInformacoesAdicionais\(\s*'([^']+)'\s*,\s*\{(.*?)\}\s*\)", html or "", re.S)
    if not found:
        return None
    data = dict(re.findall(r"'(\w+)'\s*:\s*'([^']*)'", found.group(2)))
    data.update(re.findall(r"'(\w+)'\s*:\s*(true|false)\b", found.group(2)))
    return _href(found.group(1), base_url), data


def parse_additional_information(html):
    """``{label: value}`` of the "Informações Adicionais" fragment (justiça gratuita, sigilo, prevenção, valor da causa,
    doença grave, Juízo 100% Digital...); labels without a value are left out."""
    soup = BeautifulSoup(html or "", "html.parser")
    result = {}
    for row in soup.select("div.row"):
        spans = row.find_all("span", recursive=False)
        if len(spans) < 2:
            continue
        label, value = text(spans[0]).rstrip(":").strip(), text(spans[1])
        if label and value:
            result.setdefault(label, value)
    return result


def _flat_cells(row):
    """The texts of a row's cells, also when the portal leaves ``<td>`` unclosed (audiência lists of TJMG, 27/11/2026),
    which a lenient parser nests one cell inside the other."""
    parts = re.split(r"<td\b[^>]*>", str(row), flags=re.I)[1:]
    return [text(BeautifulSoup(part, "html.parser")) for part in parts]


def _table_items(html, fields):
    """Rows of the first list whose headers are ``fields`` keys (lowercase), as dicts with the mapped names."""
    soup = BeautifulSoup(html or "", "html.parser")
    for table in soup.select("table.infraTable"):
        headers = [text(th).casefold() for th in table.select("th")]
        if sum(header in fields for header in headers) < 2:
            continue
        items = []
        for row in table.select("tr.infraTrClara, tr.infraTrEscura"):
            cells = _flat_cells(row)
            item = {
                fields.get(header, header): cells[index] for index, header in enumerate(headers) if index < len(cells)
            }
            items.append({key: value for key, value in item.items() if key})
        return items
    return []


HEARING_FIELDS = {
    "sala": "location",
    "data/hora início": "start",
    "data/hora fim": "end",
    "descrição da audiência": "description",
    "magistrado": "judge",
    "realizador": "conductedBy",
    "situação": "status",
}
EXAM_FIELDS = {
    "periciado": "examinee",
    "data-hora designada": "scheduledAt",
    "sala": "location",
    "perito": "expert",
    "especialidade": "specialty",
}


def parse_process_hearings(html):
    """The process' audiências (Audiência button, ``audiencia_listar``): location, start, end, description, judge,
    conductedBy, status."""
    return _table_items(html, HEARING_FIELDS)


def parse_process_exams(html):
    """The process' perícias (Perícia button, ``exame_listar``, JFRS): examinee, scheduledAt, location, expert,
    specialty."""
    return _table_items(html, EXAM_FIELDS)


def process_links(html, base_url):
    """The process page's audiência, perícia and Árvore (document tree) links, when its action bar shows them."""
    soup = BeautifulSoup(html or "", "html.parser")
    links = {}
    for key, action in (
        ("hearings", "audiencia_listar"),
        ("exams", "exame_listar"),
        ("tree", "arvore_documento_listar"),
    ):
        anchor = soup.select_one(f'a[href*="acao={action}&"]')
        if anchor is not None:
            links[key] = _href(anchor.get("href"), base_url)
    return links


def parse_panel_counters(body):
    """``{name: count}`` of one ``listar_painel_advogado`` XML reply (``<fldAudiencia><audienciaFutura>2</...>``), or
    None when the reply is not that XML (TJMG answers fldDepositosJudiciais with a PHP error, 27/11/2026)."""
    found = re.fullmatch(r"\s*(?:<\?xml[^>]*\?>)?\s*<(\w+)>(.*)</\1>\s*", body or "", re.S)
    if not found:
        return None
    values = {}
    for name, value in re.findall(r"<(\w+)>\s*([^<]*?)\s*</\1>", found.group(2)):
        values[name] = int(value) if value.isdigit() else html_lib.unescape(value)
    return values


_HEARING_TIME = r"(\d{2}/\d{2}/\d{4}(?:\s+\d{2}:\d{2}(?::\d{2})?)?)"


def parse_hearing_list(html, base_url):
    """The panel's audiência lists (``audiencia_futura_listar``, ``audiencia_futura_conciliacao_listar``; TJMG and
    JFRS, 27/11/2026): Número Processo (number, juízo, parties), Evento, Local Data/Hora (Sala, Início, Previsão
    Término) and Observação. The portal leaves the last ``<td>`` of a row unclosed."""
    soup = BeautifulSoup(html or "", "html.parser")
    for table in soup.select("table.infraTable"):
        headers = [text(th).casefold() for th in table.select("th")]
        if not any("processo" in header for header in headers) or not any("local" in header for header in headers):
            continue
        items = []
        for row in table.select("tr.infraTrClara, tr.infraTrEscura"):
            cells = [BeautifulSoup(part, "html.parser") for part in re.split(r"<td\b[^>]*>", str(row), flags=re.I)[1:]]
            link = cells[0].select_one('a[href*="acao=processo_selecionar"]') if cells else None
            if link is None or len(cells) < 3:
                continue
            lines = [line.strip() for line in cells[0].get_text("\n", strip=True).split("\n") if line.strip()]
            court = next((line[1:-1] for line in lines[1:2] if line.startswith("(") and line.endswith(")")), "")
            place = text(cells[2])
            location = re.search(r"Sala\s*:\s*(.*?)\s*(?:In[íi]cio|$)", place)
            start = re.search(r"In[íi]cio\s*" + _HEARING_TIME, place)
            end = re.search(r"T[ée]rmino\s*" + _HEARING_TIME, place)
            items.append(
                {
                    "processNumber": text(link),
                    "number": text(link),
                    "url": _href(link.get("href"), base_url),
                    "court": court,
                    "parties": [
                        {"role": lines[index + 1], "name": line.rstrip(" -").strip()}
                        for index, line in enumerate(lines[:-1])
                        if index > 1 and line.endswith("-")
                    ],
                    "event": text(cells[1]),
                    "location": location.group(1) if location else place,
                    "start": start.group(1) if start else "",
                    "end": end.group(1) if end else "",
                    "observation": text(cells[3]) if len(cells) > 3 else "",
                }
            )
        return {"items": items, "pagination": _list_pagination(soup, items)}
    # An empty list draws an empty table area (JFRS, 27/11/2026); a page without one is not this list.
    area = soup.select_one("#divInfraAreaTabela")
    empty = area is not None and not text(area)
    return {"items": [], "pagination": {"totalItems": 0 if empty else None, "itemCount": 0, "complete": empty}}
