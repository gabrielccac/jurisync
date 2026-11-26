"""Parsers of the TJGO lawyer desk: the home page, the "Intimações/Citações lidas" JSON and the own-process list.

Observed live on 2026-11-17 (a lawyer account, A1 login, read-only):

- Home (``Usuario?PaginaAtual=-10``, the panel's ``userMainFrame``): fieldset "Processos Ativos/Audiências" with one
  link per serventia type (``BuscaProcesso?PaginaAtual=2&Proprio=1&Id_ServentiaSubTipo=<n>&ProcessoStatusCodigo=0``
  and the count); fieldset "Pendências" holding the table "Intimações/Citações Publicadas no Diário Eletrônico"
  (pendência id, short process number ``NNNNNNN.DD``, movement, publication date, possible deadline, "Quantidade: N"
  footer) followed by the portal's own pending communications, ``<em>Não há pendências</em>`` when there are none.
  Its buttons ("Marcar Aguardando Peticionamento", "Retirar da Página Inicial", ``DescartarPendenciaProcesso``) change
  the desk and are never followed.
- "Intimações lidas" / "Citações lidas" load ``PendenciaJson?AJAX=ajax&PaginaAtual=3&fluxo=17`` (intimações) and
  ``fluxo=14`` (citações), 15 rows a page: ``desc2`` short number, ``desc3`` movement, ``desc4`` type, ``desc5`` the
  reading date, ``desc6`` deadline. A Diário intimação is listed there from the moment it is made available, with the
  publication date as its reading date, which may still be in the future. ``dataInicialFim`` filters by that date.
- Own processes per serventia type: the link above plus ``QuantidadeRegistrosPagina=500`` returns every row (short
  number ``NNNNNNN-DD``, parties, distribution) and "Total de: N". A type with a single process answers that
  process' "Dados Processo" page instead of the list.

A session the portal dropped answers 200 "Problema no pedido ... Usuário inválido ou sessão expirada".
"""

import json
import re
from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup

from jurisync.connectors.projudi.base import ProjudiParseError

HOME = "Usuario?PaginaAtual=-10"
HOME_MARKER = re.compile(r"Seja\s+bem[\s-]+vindo", re.I)
EXPIRED = re.compile(r"sess[aã]o expirada|Problema no pedido", re.I)
CATEGORY_LINK = re.compile(
    r"^BuscaProcesso\?PaginaAtual=2&Proprio=1&Id_ServentiaSubTipo=(\d{1,6})&ProcessoStatusCodigo=0$"
)
SHORT_NUMBER = re.compile(r"\b(\d{1,7})[.-](\d{1,2})\b(?!\.\d{4}\.\d)")
CNJ_NUMBER = re.compile(r"\b(\d{7})-(\d{2})\.\d{4}\.\d\.\d{2}\.\d{4}\b")
MOVEMENT_DATE = re.compile(r"^(.*?)\s*\((\d{2}/\d{2}/\d{4}[^)]*)\)\s*$")
DATE = re.compile(r"\b(\d{2})/(\d{2})/(\d{4})\b")
PROCESS_ID = re.compile(r"submete\('(\d{1,40})'\)")
# The Diário row's own link to its process, the one a lawyer not linked to the process still opens it by.
DIARIO_PROCESS_LINK = re.compile(r"^BuscaProcesso\?Id_Processo=(\d{1,40})$")
NUMBER_CELL = re.compile(r"\d{1,7}[.-]\d{1,2}(?:\.\d{4}\.\d\.\d{2}\.\d{4})?")
QUANTITY = re.compile(r"Quantidade:\s*(\d+)")
TOTAL = re.compile(r"Total de:\s*(\d+)")
NONE_PENDING = "não há pendências"
READ_FLOWS = {"17": "intimação", "14": "citação"}
PAGE_SIZE = 15
BRASILIA = timezone(timedelta(hours=-3))


def clean(node):
    text = node.get_text(" ") if hasattr(node, "get_text") else str(node or "")
    return " ".join(text.split())


def today():
    return datetime.now(BRASILIA).date()


def parse_date(value):
    """The date of ``dd/mm/yyyy[ hh:mm:ss]``, or ``None``."""
    match = DATE.search(value or "")
    if not match:
        return None
    day, month, year = (int(part) for part in match.groups())
    try:
        return datetime(year, month, day).date()
    except ValueError:
        return None


def process_key(value):
    """``(sequence, check digits)`` of a CNJ or short TJGO number (``5908387.98``, ``5185787-46``), or ``None``."""
    text = (value or "").strip()
    match = CNJ_NUMBER.search(text) or SHORT_NUMBER.search(text)
    return (int(match.group(1)), int(match.group(2))) if match else None


def is_expired(html):
    return bool(EXPIRED.search(html or "")) and not HOME_MARKER.search(html or "")


def _legend(fieldset):
    legend = fieldset.find("legend", recursive=False)
    return clean(legend) if legend is not None else ""


def _movement(text):
    match = MOVEMENT_DATE.match(text)
    return (match.group(1), match.group(2)) if match else (text, "")


def _dje_items(fieldset, now):
    items = []
    for row in fieldset.select("tbody > tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 5:
            raise ProjudiParseError("Projudi Diário intimation row has an unexpected shape")
        number = clean(cells[1])
        key = process_key(number)
        if not clean(cells[0]).isdigit() or key is None:
            raise ProjudiParseError("Projudi Diário intimation row has no pendência id or process number")
        movement, moved_at = _movement(clean(cells[2]))
        link = cells[1].find("a", href=DIARIO_PROCESS_LINK)
        published = clean(cells[3])
        publication = parse_date(published)
        items.append(
            {
                "id": clean(cells[0]),
                "process": number,
                "movement": movement,
                "movementDate": moved_at,
                "publication": published,
                "deadline": clean(cells[4]),
                "awaitingPublication": publication is None or publication > now,
                "_key": key,
                "_id": DIARIO_PROCESS_LINK.match(link["href"]).group(1) if link else None,
            }
        )
    footer = QUANTITY.search(clean(fieldset.find("tfoot") or ""))
    return items, int(footer.group(1)) if footer else None


def _data_rows(block):
    return [row for row in block.find_all("tr") if row.find("td") is not None and row.find_parent("thead") is None]


def _portal_items(block):
    """Pending communications the portal lists outside the Diário table. Their layout was not observed (the account
    had none), so every data row that names a process is taken as pending; ``parse_home`` treats anything else as
    unreadable."""
    items = []
    for row in _data_rows(block):
        text = clean(row)
        key = process_key(text)
        if key is not None:
            number = (CNJ_NUMBER.search(text) or SHORT_NUMBER.search(text)).group(0)
            items.append({"process": number, "text": text[:300], "_key": key})
    return items


def parse_home(html, now=None):
    """Categories of own processes, the Diário intimations and the portal's pending communications of the desk.

    ``portal["complete"]`` is false when the "Pendências" block has content this parser cannot read in full.
    """
    now = now or today()
    page = BeautifulSoup(html or "", "html.parser")
    if not HOME_MARKER.search(clean(page)):
        raise ProjudiParseError("Projudi did not return the lawyer home page")
    fieldsets = page.find_all("fieldset")
    categories, hearings = [], []
    for fieldset in fieldsets:
        if not _legend(fieldset).startswith("Processos Ativos"):
            continue
        for link in fieldset.find_all("a", href=True):
            href = link["href"].strip()
            match = CATEGORY_LINK.match(href)
            row = link.find_parent("tr")
            count = clean(link)
            if row is None or not count.isdigit():
                raise ProjudiParseError("Projudi own-process category has an unexpected shape")
            label = clean(row.find("td"))
            if match is not None:
                categories.append({"label": label, "subtype": match.group(1), "count": int(count)})
            elif href.startswith("Audiencia"):
                # The same block counts today's hearings (e.g. "Mediação CEJUSC para Hoje" -> Audiencia?...).
                hearings.append({"label": label, "count": int(count)})
            else:
                raise ProjudiParseError("Projudi own-process category has an unexpected link")
    pending = next((f for f in fieldsets if _legend(f) == "Pendências"), None)
    if pending is None:
        raise ProjudiParseError("Projudi home page has no Pendências block")
    dje_items, declared = [], None
    for inner in pending.find_all("fieldset"):
        if "diário" in _legend(inner).casefold():
            items, quantity = _dje_items(inner, now)
            dje_items.extend(items)
            declared = quantity if declared is None else declared + (quantity or 0)
            inner.extract()
    rest = clean(pending).removeprefix("Pendências").strip()
    portal_items = _portal_items(pending)
    # Read in full only when the block says there is nothing, or every data row it has names a process.
    complete = rest.casefold() == NONE_PENDING or (bool(portal_items) and len(_data_rows(pending)) == len(portal_items))
    return {
        "categories": categories,
        "hearings": hearings,
        "dje": {"items": dje_items, "declared": declared},
        "portal": {"items": portal_items, "complete": complete, "text": "" if complete else rest[:300]},
    }


def parse_read_page(content, flow, now=None):
    """``(rows, awaiting)`` of one page of a "lidas" JSON: every row, and those whose reading date is after today."""
    now = now or today()
    text = content.decode("iso-8859-1") if isinstance(content, bytes) else content or ""
    if not text.strip():
        return [], []
    try:
        data = json.loads(text)
    except ValueError:
        raise ProjudiParseError("Projudi read-communications list is not JSON") from None
    if not isinstance(data, list):
        raise ProjudiParseError("Projudi read-communications list has an unexpected shape")
    rows = []
    for item in data:
        if not isinstance(item, dict) or "id" not in item:
            raise ProjudiParseError("Projudi read-communications entry has an unexpected shape")
        if str(item["id"]).startswith("-"):
            continue  # paging metadata
        number = str(item.get("desc2") or "")
        key, read_on = process_key(number), parse_date(str(item.get("desc5") or ""))
        if key is None or read_on is None:
            raise ProjudiParseError("Projudi read-communications entry has no process number or reading date")
        movement, moved_at = _movement(clean(str(item.get("desc3") or "")))
        rows.append(
            {
                "id": str(item["id"]),
                "process": number,
                "kind": clean(str(item.get("desc4") or "")) or READ_FLOWS.get(flow, ""),
                "movement": movement,
                "movementDate": moved_at,
                "publication": clean(str(item.get("desc5") or "")),
                "deadline": clean(str(item.get("desc6") or "")),
                "awaitingPublication": read_on > now,
                "_key": key,
            }
        )
    return rows, [row for row in rows if row["awaitingPublication"]]


def parse_process_list(html):
    """``(rows, total)`` of one own-process list page: ``[{number, parties: [{role, name}], distributedAt}]``."""
    page = BeautifulSoup(html or "", "html.parser")
    total = TOTAL.search(clean(page))
    body = page.find("tbody", id="tabListaProcesso")
    if body is None:
        if "Nenhum Processo foi localizado" in (html or ""):
            return [], 0
        raise ProjudiParseError("Projudi own-process list has no result table")
    rows = []
    for row in body.find_all("tr", recursive=False):
        cells = row.find_all("td", recursive=False)
        # Columns vary by serventia type (the selection checkbox is not always there): the number is the first cell
        # holding only a process number, the parties the cell with role/name blocks, the distribution a date cell.
        parties_cell = next((c for c in cells if c.find("div", class_="coluna100") is not None), None)
        number = next((clean(c) for c in cells if NUMBER_CELL.fullmatch(clean(c))), None)
        if number is None and parties_cell is not None:
            # A number cell with a note next to it: the first number before the parties.
            before = cells[: cells.index(parties_cell)]
            found = next((NUMBER_CELL.search(clean(c)) for c in before if NUMBER_CELL.search(clean(c))), None)
            number = found.group(0) if found else None
        if number is None or parties_cell is None:
            raise ProjudiParseError(f"Projudi own-process row has an unexpected shape ({len(cells)} cells)")
        parties = []
        for block in parties_cell.find_all("div", class_="coluna100"):
            role, name = block.find("div", class_="coluna15"), block.find("div", class_="coluna80")
            if role is not None and name is not None:
                parties.append({"role": clean(role), "name": clean(name)})
        dated = next((clean(c) for c in cells if c is not parties_cell and DATE.search(clean(c))), "")
        opened = PROCESS_ID.search(row.get("onclick") or "")
        rows.append({"number": number, "parties": parties, "distributedAt": dated, "_id": opened and opened.group(1)})
    return rows, int(total.group(1)) if total else None


def public(item):
    return {key: value for key, value in item.items() if not key.startswith("_")}
