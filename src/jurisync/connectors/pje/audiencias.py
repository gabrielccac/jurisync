"""Hearings in PJe: the process' Audiência tab and the lawyer's "Pauta de audiência".

Observed on trf5-1g and tjmg-pje-1g (27/11/2026, PJe 2.x):

- The process tab ``navbar:linkAbaAudiencia`` opens over the same AJAX form as the Expedientes and Perícia tabs and
  answers the table ``processoConsultaAudienciaGridList`` (Unidade, Sala, Tipo de Audiência, Data, Status da
  Audiência, Link da sala virtual) and "N resultados encontrados". The date has a two-digit year (``22/01/26 15:00``);
  the status cell opens a modal with who held the hearing (left out) and whether there was an agreement.
- The menu entry "Audiências e sessões > Pauta de audiência" is ``ProcessoAudiencia/PautaAudiencia/listView.seam``:
  a GET renders the search form ``processoAudienciaSearchForm`` (start date "de"/"até", today by default) and the
  table ``idProcessoAudiencia`` (Data/Hora, Processo, Órgão Julgador, Partes, Classe Judicial, Tipo, Sala, Situação),
  15 rows per page; its Pesquisar is one A4J POST, and further pages come from a slider form posting the page number.

Nothing here registers anything. The pauta page also carries "Gerar PDF" and "Download dos documentos dos processos";
they are links, never followed, and every A4J parameter posted is checked against ``FORBIDDEN``.
"""

import re
from datetime import date

from bs4 import BeautifulSoup

from jurisync import portals
from jurisync.connectors.pje import jsf
from jurisync.connectors.pje.expedientes import SCROLLER_OTHER_PAGE
from jurisync.connectors.pje.parsers import response_html

TAB_CONTROL = "navbar:linkAbaAudiencia"
TAB_TABLE = "processoConsultaAudienciaGridList"
PAUTA_PATH = "ProcessoAudiencia/PautaAudiencia/listView.seam"
PAUTA_FORM = "processoAudienciaSearchForm"
PAUTA_BUTTON = f"{PAUTA_FORM}:searchButton"
PAUTA_TABLE = "idProcessoAudiencia"
DATE_FROM = re.compile(r":dtInicioFromFormInputDate$")
DATE_TO = re.compile(r":dtInicioToFormInputDate$")
TOTAL = re.compile(r"(\d+)\s+resultados?\s+encontrados?", re.I)
WHEN = re.compile(r"(\d{2})/(\d{2})/(\d{4}|\d{2})(?:\s+(\d{2}:\d{2}))?")
PROCESS = re.compile(r"\b\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}\b")
PROCESS_ID = re.compile(r"[?&]id=(\d+)")
AGREEMENT = re.compile(r"Houve acordo:\s*(Sim|Não)", re.I)
URL = re.compile(r"https://\S+")
SLIDER_MAX = re.compile(r"'maxValue':'(\d+)'")
# What a posted A4J parameter may never name: the pauta's PDF and document download, and anything that is not a read.
FORBIDDEN = re.compile(r"baixar|download|report|pdf|ciencia|responder|assinar|excluir|remover|designar", re.I)
MAX_PAGES = 20
TAB_COLUMNS = {
    "unidade": "orgaoJulgador",
    "sala": "sala",
    "tipo de audiência": "tipo",
    "data": "data",
    "status da audiência": "situacao",
    "link da sala virtual": "linkSalaVirtual",
}
PAUTA_COLUMNS = {
    "data/hora": "data",
    "processo": "processNumber",
    "órgão julgador": "orgaoJulgador",
    "partes": "partes",
    "classe judicial": "classe",
    "tipo": "tipo",
    "sala": "sala",
    "situação": "situacao",
}


def _clean(node):
    return " ".join(node.get_text(" ", strip=True).replace("\xa0", " ").split())


def _when(text):
    """(ISO date, HH:MM) of a ``dd/mm/yy[yy] hh:mm`` text; a two-digit year is 20yy."""
    found = WHEN.search(text or "")
    if not found:
        return None, None
    year = found[3] if len(found[3]) == 4 else f"20{found[3]}"
    return f"{year}-{found[2]}-{found[1]}", found[4]


def _headers(table, columns):
    """Column keys by header text; any header starting with "data" is the date ("Data", TJMG's "Data prevista")."""
    names = [_clean(th).lower() for th in table.select("thead th")]
    return [columns.get(name) or ("data" if name.startswith("data") else None) for name in names]


def _rows(table, headers):
    """Each body row as (cells by column key), skipping the empty spacer rows RichFaces prints."""
    for row in table.select("tbody > tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) == len(headers):
            yield row, {key: cell for key, cell in zip(headers, cells, strict=True) if key}


def _status(cell):
    """The status as the row prints it ("realizada"), without the modal that names who held the hearing."""
    link = cell.find("a")
    if link is not None:
        return " ".join(link.find_all(string=True, recursive=False)[0].split()) if link.find(string=True) else None
    return _clean(cell) or None


def _agreement(cell):
    found = AGREEMENT.search(_clean(cell))
    return None if not found else found[1].lower() == "sim"


def _virtual_room(cell):
    if cell is None:
        return None
    for link in cell.find_all("a", href=True):
        if link["href"].startswith("https://"):
            return link["href"]
    found = URL.search(_clean(cell))
    return found.group(0) if found else None


def _complete(soup, table, count):
    total = TOTAL.search(_clean(table.parent)) if table.parent else None
    return (total is None or int(total[1]) == count) and not soup.find(class_=SCROLLER_OTHER_PAGE)


# -- process tab ---------------------------------------------------------------


def has_tab(detail_html):
    return TAB_CONTROL in (detail_html or "")


def tab_request(detail_html):
    """The AJAX form body that opens the Audiência tab, as the browser sends it."""
    fields = jsf.form_fields(detail_html, "navbar")
    state, _ = jsf.page_state(detail_html, "PJe process page")
    return {
        "AJAXREQUEST": "_viewRoot",
        **fields,
        "javax.faces.ViewState": state,
        TAB_CONTROL: TAB_CONTROL,
        "AJAX:EVENTS_COUNT": "1",
    }


def parse_tab(html):
    """``(items, complete)`` of the process' Audiência tab: date, time, órgão, sala, type, status, agreement and the
    virtual room link when shown. ``complete`` as in ``pericias.parse_pericias``."""
    soup = BeautifulSoup(html or "", "html.parser")
    table = soup.find(id=TAB_TABLE)
    if table is None:
        empty = re.search(r"\b0\s+resultados?\s+encontrados?|nenhum(?:a)?\s+(?:registro|audi[eê]ncia)", _clean(soup), re.I)
        return [], bool(empty)
    items = []
    for _, cells in _rows(table, _headers(table, TAB_COLUMNS)):
        day, hour = _when(_clean(cells["data"])) if "data" in cells else (None, None)
        status = cells.get("situacao")
        items.append(
            {
                "data": day,
                "hora": hour,
                "tipo": _clean(cells["tipo"]) or None if "tipo" in cells else None,
                "orgaoJulgador": _clean(cells["orgaoJulgador"]) or None if "orgaoJulgador" in cells else None,
                "sala": _clean(cells["sala"]) or None if "sala" in cells else None,
                "situacao": _status(status) if status is not None else None,
                "acordo": _agreement(status) if status is not None else None,
                "linkSalaVirtual": _virtual_room(cells.get("linkSalaVirtual")),
            }
        )
    return items, _complete(soup, table, len(items))


def fetch_tab(session, detail_html, detail_url):
    """``(items, complete)`` of the Audiência tab; see ``parse_tab``."""
    response = jsf.post(session, detail_url, tab_request(detail_html), label="PJe process audiências")
    return parse_tab(response_html(response))


# -- pauta ---------------------------------------------------------------------


def parse_pauta(html):
    """``(items, total)`` of one page of the pauta; ``total`` is the count PJe prints (None when absent)."""
    soup = BeautifulSoup(html or "", "html.parser")
    table = soup.find(id=PAUTA_TABLE)
    if table is None:
        return [], None
    items = []
    for row, cells in _rows(table, _headers(table, PAUTA_COLUMNS)):
        day, hour = _when(_clean(cells["data"])) if "data" in cells else (None, None)
        number = PROCESS.search(_clean(cells["processNumber"])) if "processNumber" in cells else None
        link = row.find("a", href=re.compile(r"ConsultaProcesso/Detalhe/"))
        process_id = PROCESS_ID.search(link["href"]) if link else None
        parties = cells.get("partes")
        items.append(
            {
                "data": day,
                "hora": hour,
                "processNumber": number.group(0) if number else None,
                "processId": process_id.group(1) if process_id else None,
                "classe": _clean(cells["classe"]) or None if "classe" in cells else None,
                "partes": " ".join(parties.get_text(" ", strip=True).split()) or None if parties is not None else None,
                "orgaoJulgador": _clean(cells["orgaoJulgador"]) or None if "orgaoJulgador" in cells else None,
                "tipo": _clean(cells["tipo"]) or None if "tipo" in cells else None,
                "sala": _clean(cells["sala"]) or None if "sala" in cells else None,
                "situacao": _clean(cells["situacao"]) or None if "situacao" in cells else None,
            }
        )
    total = TOTAL.search(_clean(table.parent)) if table.parent else None
    return items, int(total[1]) if total else None


def _checked(parameters):
    for key, value in parameters.items():
        if FORBIDDEN.search(key) or FORBIDDEN.search(str(value)):
            raise RuntimeError("PJe pauta request refused: a parameter names an action that is not a read")
    return parameters


def search_request(html, date_from, date_to):
    """(container, body) of the pauta's Pesquisar with the start-date interval, as the browser sends it."""
    soup = BeautifulSoup(html, "html.parser")
    button = soup.find(id=PAUTA_BUTTON)
    if button is None or "A4J.AJAX.Submit" not in button.get("onclick", ""):
        raise RuntimeError("PJe pauta de audiência has no search form")
    form, container, parameters = jsf.a4j_submit(button["onclick"], label="PJe pauta")
    if form != PAUTA_FORM or parameters.get(PAUTA_BUTTON) != PAUTA_BUTTON:
        raise RuntimeError("PJe pauta search button is not the expected one")
    fields = jsf.form_fields(html, PAUTA_FORM)
    names = {pattern: [name for name in fields if pattern.search(name)] for pattern in (DATE_FROM, DATE_TO)}
    if any(len(found) != 1 for found in names.values()):
        raise RuntimeError("PJe pauta search form has no start-date interval")
    fields[names[DATE_FROM][0]] = _br(date_from)
    fields[names[DATE_TO][0]] = _br(date_to)
    body = {
        "AJAXREQUEST": container,
        **fields,
        "javax.faces.ViewState": jsf.view_state(html, "PJe pauta"),
        **_checked(parameters),
        "AJAX:EVENTS_COUNT": "1",
    }
    return body


def page_request(html, page):
    """The slider form body that turns the pauta to ``page``, or None when the result has a single page."""
    soup = BeautifulSoup(html, "html.parser")
    slider = soup.find("input", class_="rich-inslider-field")
    script = slider.find_parent("form") if slider else None
    if slider is None or script is None:
        return None
    setup = script.find(string=re.compile(r"Richfaces\.Slider"))
    if setup is None:
        return None
    pages = SLIDER_MAX.search(setup)
    onchange = re.search(r"'onchange':'(.*?)','showArrows'", setup, re.S)
    if not pages or int(pages[1]) < page or not onchange:
        return None
    form, container, parameters = jsf.a4j_submit(onchange.group(1).replace("\\'", "'"), label="PJe pauta")
    if form != script.get("id"):
        raise RuntimeError("PJe pauta pager is not the expected form")
    fields = jsf.form_fields(str(script), form)
    fields[slider["name"]] = str(page)
    state = soup.find("input", attrs={"name": "javax.faces.ViewState"})
    return {
        "AJAXREQUEST": container,
        **fields,
        "javax.faces.ViewState": state.get("value") if state else "",
        **_checked(parameters),
        "AJAX:EVENTS_COUNT": "1",
    }


def page_count(html):
    setup = BeautifulSoup(html, "html.parser").find(string=re.compile(r"Richfaces\.Slider"))
    found = SLIDER_MAX.search(setup) if setup else None
    return int(found[1]) if found else 1


def _br(value):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value or ""):
        raise ValueError(f"date {value!r} must be YYYY-MM-DD")
    year, month, day = value.split("-")
    return f"{day}/{month}/{year}"


def read_pauta(session, portal, date_from, date_to, max_pages=MAX_PAGES):
    """The lawyer's pauta de audiência between two start dates (ISO), every page up to ``max_pages``."""
    if date.fromisoformat(date_from) > date.fromisoformat(date_to):
        raise ValueError("dateFrom must not be after dateTo")
    config = portals.pje(portal)
    if config.kz or config.pje1x:
        raise ValueError(f"{portal}: this PJe panel has no Pauta de audiência page")
    url = config.url(PAUTA_PATH)
    html = response_html(jsf.get(session, url, label="PJe pauta de audiência"))
    html = response_html(jsf.post(session, url, search_request(html, date_from, date_to), label="PJe pauta"))
    items, total = parse_pauta(html)
    pages = page_count(html)
    for page in range(2, min(pages, max_pages) + 1):
        body = page_request(html, page)
        if body is None:
            break
        html = response_html(jsf.post(session, url, body, label=f"PJe pauta page {page}"))
        more, total = parse_pauta(html)
        items.extend(more)
    items.sort(key=lambda item: (item["data"] or "", item["hora"] or ""))
    return {
        "success": True,
        "portal": portal,
        "dateFrom": date_from,
        "dateTo": date_to,
        "total": total if total is not None else len(items),
        "returned": len(items),
        "pages": pages,
        "complete": total is not None and len(items) >= total,
        "items": items,
    }
