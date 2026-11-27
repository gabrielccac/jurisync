"""Projudi TJPR: the Keycloak login (``keycloak.py``) in front of the same lawyer desk as TJAM (``tjam.py``).

Observed live on 2026-11-24 (one login, a lawyer account, CPF + password + TOTP):

- Entry: the home page's "Advogados, Partes" option is ``autenticacao.do?_tj=<token>``. Without that token the SSO
  accepts the login and Projudi answers "Erro geral" on the way back, so the link is always taken from ``home.do``.
- After the SSO: a page whose menu is nested ``ul#main-menu`` anchors (TJAM writes ``add_menu`` calls). Sections,
  labels, list pages, desk, search and process pages are TJAM's. Pages mix UTF-8 and ISO-8859-1 text.
- The frameset (``usuario/postLogon.do``) redirects to another SSO when requested on its own: the menu only comes with
  the login, so a session without it logs in again.
- The desk's agenda shows 20 of the intimações awaiting reading: the "Novas" lists of both instances, read to the end,
  say which processes have one.

The "Mesa do advogado" (observed live on 2026-11-27, read-only): the tabs post ``usuario/mesaAdvogado.do`` with
``selectedIcon``. Intimações (``actionType=listaInicio``) filter by situation (``lidas=false`` "Aguardando Ciência",
``true`` "Aguardando Cumprimento") and destination (``destinoIntimacao`` 1 Advogado, 2 Sociedade de advogados) and
list both instances together: a 2º grau row links its process to ``processo/recursal/recurso.do``. Columns: process,
party and court; event and deadline as a duration; sent date and last day (to read); start and end of the deadline
(real dates, empty while the ciência is pending). Citações (``actionType=listaCitacoes``, ``lidasCitacoes``): type and
sent date, automatic-reading date, start and end of the deadline. ``pageSize`` takes up to 100. A list posted without
its filters answers "Erro geral", so every field the page's own form sends is sent. Each row's "Ver" link
(``intimacao.do``) registers the reading: it is never followed nor returned.
"""

import re

from jurisync.connectors.projudi import base, legacy, parsers, tjam, tjgo_desk
from jurisync.connectors.projudi.keycloak import KeycloakAdapter
from jurisync.core import intimacao
from jurisync.core.http import TIMEOUT, impersonate_for
from jurisync.core.responses import listing

ENTRY = re.compile(r"""class="externo"[^>]*location\.href='(/projudi/autenticacao\.do\?_tj=[0-9a-fA-F]+)'""")
NOVAS_PAGES = 50
DESK_INTIMACOES = "usuario/mesaAdvogado.do?actionType=listaInicio"
DESK_CITACOES = "usuario/mesaAdvogado.do"
DESK_PAGE_SIZE = 100
SORT = {"sortColumn": "nomeParte", "sortOrder": "asc", "sortComparator": ""}
# key: (kind, destination, awaiting ciência, the radios the page must show checked)
DESK_LISTS = {
    "intimacoesAguardandoCiencia": ("intimação", "advogado", True, {"lidas": "false", "destinoIntimacao": "1"}),
    "intimacoesAguardandoCumprimento": ("intimação", "advogado", False, {"lidas": "true", "destinoIntimacao": "1"}),
    "sociedadeAguardandoCiencia": ("intimação", "sociedade", True, {"lidas": "false", "destinoIntimacao": "2"}),
    "sociedadeAguardandoCumprimento": ("intimação", "sociedade", False, {"lidas": "true", "destinoIntimacao": "2"}),
    "citacoesAguardandoCiencia": ("citação", "advogado", True, {"lidasCitacoes": "false"}),
    "citacoesAguardandoCumprimento": ("citação", "advogado", False, {"lidasCitacoes": "true"}),
}
DESK_AWAITING = tuple(key for key, (_kind, _to, awaiting, _radios) in DESK_LISTS.items() if awaiting)
PARENTHESIS = re.compile(r"\(([^()]*)\)\s*$")
DJEN = re.compile(r"Veiculado no DJEN em (\d{2}/\d{2}/\d{4})", re.I)


def menu_entries(html, root):
    """``[(section, label, path)]`` of the ``ul#main-menu`` anchors, with paths relative to the application root."""
    menu = legacy.soup(html).find(id="main-menu")
    entries = []

    def walk(items, sections):
        for item in items.find_all("li", recursive=False):
            anchor, inner = item.find("a", recursive=False), item.find("ul", recursive=False)
            label = tjam.clean(anchor) if anchor else ""
            if inner is not None:
                walk(inner, [*sections, label])
            elif anchor is not None and (anchor.get("href") or "").startswith(root):
                entries.append((" / ".join(sections), label, "/" + anchor["href"].removeprefix(root)))

    if menu is not None:
        walk(menu, [])
    return entries


def iso(value):
    """``yyyy-mm-dd`` of the first ``dd/mm/yyyy`` in ``value``, or None."""
    day = tjgo_desk.parse_date(value)
    return day.isoformat() if day else None


def _dates(cell):
    return tjam.DATE.findall(tjam.clean(cell))


def _process_cell(cell):
    """``{processNumber, instance, parte, orgao}``: a ``recurso.do`` link is a 2º grau process."""
    link = cell.find("a", href=True)
    number = tjam.CNJ.search(tjam.clean(link) if link else tjam.clean(cell))
    strong = cell.find("strong")
    texts = list(cell.stripped_strings)
    return {
        "processNumber": number.group(0) if number else "",
        "instance": "2" if link is not None and "/recursal/recurso.do" in link["href"] else "1",
        "parte": tjam.clean(strong) if strong else "",
        "orgao": texts[-1] if len(texts) > 2 else "",
    }


def _intimation_row(cells):
    event = tjam.clean(cells[1])
    deadline = PARENTHESIS.search(event)
    sent, last = (_dates(cells[2]) + ["", ""])[:2]
    start, end = (_dates(cells[3]) + ["", ""])[:2]
    djen = DJEN.search(event)
    return {
        "evento": event[: deadline.start()].strip() if deadline else event,
        "prazo": deadline.group(1).strip() if deadline else "",
        "dataEnvio": sent,
        "ultimoDia": last,
        "inicioPrazo": start,
        "finalPrazo": end,
        "djen": djen.group(1) if djen else "",
    }


def _citation_row(cells):
    sent = _dates(cells[1])
    start, end = (_dates(cells[3]) + ["", ""])[:2]
    return {
        "tipo": tjam.DATE.sub("", tjam.clean(cells[1])).strip(),
        "dataEnvio": sent[0] if sent else "",
        "leituraAutomatica": (_dates(cells[2]) or [""])[0],
        "inicioPrazo": start,
        "finalPrazo": end,
    }


def parse_desk_page(html, kind):
    """One page of a desk tab: ``{"total", "rows", "checked"}``. ``checked`` is the filter radios the page shows
    selected; ``rows`` carry ``_numbers`` (every CNJ number of the row, recurso and origin) and never a link."""
    page = legacy.soup(html)
    form = page.find("form", attrs={"name": "mesaAdvogadoForm"})
    table = next(
        (t for t in page.find_all("table", class_="resultTable") if "Processo/Recurso" in tjam.clean(t.find("tr"))),
        None,
    )
    if form is None or table is None:
        raise base.ProjudiParseError("Projudi desk tab has no list form or result table")
    checked = {radio.get("name"): radio.get("value") for radio in form.find_all("input", type="radio", checked=True)}
    counted = tjam.SHOWING.search(tjam.clean(page))
    if counted is not None:
        total = int(counted.group(1))
    elif "nenhum registro encontrado" in tjam.clean(table).casefold():
        total = 0
    else:
        raise base.ProjudiParseError("Projudi desk tab has no record count")
    rows = []
    for row in (table.find("tbody") or table).find_all("tr", recursive=False):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 4 or not tjam.CNJ.search(tjam.clean(cells[0])):
            continue
        item = _process_cell(cells[0]) | (_intimation_row(cells) if kind == "intimação" else _citation_row(cells))
        item["_numbers"] = set(tjam.CNJ.findall(tjam.clean(row)))
        rows.append(item)
    return {"total": total, "rows": rows, "checked": checked}


def desk_situation(row, awaiting, today=None):
    """``core.intimacao`` fields of a desk row: awaiting ciência, or the deadline the desk prints (Final Prazo)."""
    if awaiting:
        return intimacao.fields(intimacao.AWAITING_CIENCIA)
    end = iso(row.get("finalPrazo"))
    if end:
        expired = end < (today or tjgo_desk.today()).isoformat()
        return intimacao.fields(intimacao.EXPIRED if expired else intimacao.RUNNING, end, "portal")
    if "sem prazo" in (row.get("prazo") or row.get("tipo") or "").casefold():
        return intimacao.fields(intimacao.NO_DEADLINE)
    return intimacao.fields(intimacao.RUNNING)


def desk_fields(key, page_number):
    """The fields the tab's own form posts (``(path, fields)``), with this list's filter and page."""
    kind, _to, _awaiting, radios = DESK_LISTS[key]
    paging = {"pageSize": str(DESK_PAGE_SIZE), "pageNumber": str(page_number), **SORT}
    if kind == "intimação":
        return DESK_INTIMACOES, {"selectedIcon": "tabIntimacoes", "page": "1", **radios, **paging}
    return DESK_CITACOES, {
        "selectedIcon": "tabCitacoes",
        "page": "1",
        "actionType": "listaCitacoes",
        **radios,
        **paging,
    }


class TjprAdapter(KeycloakAdapter, tjam.TjamAdapter):
    def _entry_url(self, session, config):
        for path in ("", "home.do"):  # the frameset, then the page it shows
            page = session.get(
                config.url(path), impersonate=impersonate_for(config), allow_redirects=False, timeout=TIMEOUT
            )
        link = ENTRY.search(parsers.decode(page.content))
        if page.status_code != 200 or link is None:
            raise base.ProjudiParseError("Projudi home page has no lawyer login link")
        return config.url(link.group(1).removeprefix(config.root))

    def _logged_in(self, session, config, page):
        entries = menu_entries(parsers.decode(page.content), config.root)
        if not entries:
            raise RuntimeError("Projudi login did not open the lawyer menu")
        state = self._state(session)
        state["entries"] = entries
        state["menu"] = {label: path for _section, label, path in reversed(entries)}

    def refresh(self, portal, session):
        return tjam.TjamAdapter.refresh(self, portal, session)

    def _menu(self, session, portal):
        self._entries(session, portal)
        return self._state(session)["menu"]

    def _entries(self, session, portal):
        entries = self._state(session).get("entries")
        if not entries:
            raise base.ProjudiSessionExpired("Projudi session has no menu")
        return entries

    def _read_desk(self, session, portal, key, *, max_pages=10):
        """Every page of one desk list, up to ``max_pages`` of 100 rows: ``{list, kind, destino, total, returned,
        complete, rows}``. A page whose radios differ from the filter asked is an error, not a list."""
        kind, destination, _awaiting, radios = DESK_LISTS[key]
        rows, total, number = [], None, 1
        while True:
            path, fields = desk_fields(key, number)
            response = self.request(session, portal, "POST", path, data=fields)
            if response.status_code != 200:
                raise RuntimeError(f"Projudi desk list failed (HTTP {response.status_code})")
            page = parse_desk_page(parsers.decode(response.content), kind)
            if any(page["checked"].get(name) != value for name, value in radios.items()):
                raise base.ProjudiParseError("Projudi desk answered another filter than the one asked")
            total = page["total"]
            rows.extend(page["rows"])
            if not page["rows"] or len(rows) >= total or number >= max_pages:
                break
            number += 1
        rows = rows[:total]
        return {
            "list": key,
            "kind": kind,
            "destino": destination,
            "total": total,
            "returned": len(rows),
            "complete": len(rows) == total,
            "rows": rows,
        }

    def _awaiting(self, session, portal):
        """Processes with an intimação or citação awaiting ciência: the "Novas" lists of both instances and the desk's
        "Aguardando Ciência" intimações (to the lawyer and to the Sociedade de advogados) and online citações. Every
        process number a row names (the recurso and its origin) counts."""
        numbers, complete = set(), True
        for instance in tjam.INSTANCES:
            section, label = tjam.INTIMATION_LISTS["novas"]
            read = self._read_list(session, portal, section, label, instance=instance, max_pages=NOVAS_PAGES)
            numbers.update(row["processNumber"] for row in read["rows"])
            complete = complete and read["complete"]
        for key in DESK_AWAITING:
            read = self._read_desk(session, portal, key, max_pages=NOVAS_PAGES)
            numbers.update(number for row in read["rows"] for number in row["_numbers"])
            complete = complete and read["complete"]
        return numbers, complete

    def list_intimations(self, session, portal, *, lists=None, instances=tuple(tjam.INSTANCES), max_pages=10):
        """Without ``lists``: the desk's intimações and citações that need attention (awaiting ciência, deadline
        running; both instances; to the lawyer and to the Sociedade de advogados), with the deadline's end date as the
        desk prints it. With ``lists``: those menu lists (``tjam.INTIMATION_LISTS``), both instances by default."""
        if lists:
            return super().list_intimations(session, portal, lists=lists, instances=instances, max_pages=max_pages)
        today = tjgo_desk.today()
        summaries, items, awaiting, omitted = [], [], set(), {}
        for key, (_kind, _to, pending, _radios) in DESK_LISTS.items():
            read = self._read_desk(session, portal, key, max_pages=max_pages)
            for row in read.pop("rows"):
                if pending:
                    awaiting.update(row["_numbers"])
                item = {"kind": read["kind"], "destino": read["destino"], **tjgo_desk.public(row)}
                item.update(cienciaPendente=pending, **desk_situation(row, pending, today))
                if item["situacao"] in intimacao.ATTENTION:
                    items.append(item)
                else:
                    omitted[item["situacao"]] = omitted.get(item["situacao"], 0) + 1
            summaries.append(read)
        complete = all(summary["complete"] for summary in summaries)
        return listing(
            items,
            total=len(items) if complete else None,
            omitted=omitted,
            desk=summaries,
            awaitingCiencia={"processNumbers": sorted(awaiting), "complete": complete},
            complete=complete,
        )


ADAPTER = TjprAdapter()
