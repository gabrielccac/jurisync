"""The Perícia tab of a PJe process: each perícia with its date, the person examined, the fee, the expert and its state.

Observed on trf5-1g (26/11/2026): the tab ``navbar:linkAbaPericia`` opens over the same AJAX form as the Expedientes
tab and answers the table ``processoPericiaNovaPericiaList`` (Data, Periciado, Valor da perícia, Perito, Situação)
followed by "N resultados encontrados". The perito cell carries the expert's CPF after the name; it is left out.
Opening the tab registers nothing.
"""

import re

from bs4 import BeautifulSoup

from jurisync.connectors.pje import jsf
from jurisync.connectors.pje.expedientes import SCROLLER_OTHER_PAGE
from jurisync.connectors.pje.parsers import response_html

TABLE_ID = "processoPericiaNovaPericiaList"
TAB_CONTROL = "navbar:linkAbaPericia"
TOTAL = re.compile(r"(\d+)\s+resultados?\s+encontrados?", re.I)
WHEN = re.compile(r"(\d{2})/(\d{2})/(\d{4})(?:\s+(\d{2}:\d{2}))?")
CPF = re.compile(r"\s*-?\s*\d{3}\.?\d{3}\.?\d{3}-?\d{2}\s*$")
COLUMNS = {
    "data": "data",
    "periciado": "periciado",
    "valor da perícia": "valor",
    "perito": "perito",
    "situação": "situacao",
}


def _clean(node):
    return " ".join(node.get_text(" ", strip=True).replace("\xa0", " ").split())


def has_tab(detail_html):
    return TAB_CONTROL in (detail_html or "")


def tab_request(detail_html):
    """The AJAX form body that opens the Perícia tab, as the browser sends it."""
    fields = jsf.form_fields(detail_html, "navbar")
    state, _ = jsf.page_state(detail_html, "PJe process page")
    return {
        "AJAXREQUEST": "_viewRoot",
        **fields,
        "javax.faces.ViewState": state,
        TAB_CONTROL: TAB_CONTROL,
        "AJAX:EVENTS_COUNT": "1",
    }


def parse_pericias(html):
    """``(items, complete)``: the rows of the tab and whether they are provably all of them (the table was found,
    every row was read, the printed total matches and there is no further page)."""
    soup = BeautifulSoup(html or "", "html.parser")
    table = soup.find(id=TABLE_ID)
    if table is None:
        # No table: an empty tab, when it says so; anything else was not read.
        empty = re.search(
            r"\b0\s+resultados?\s+encontrados?|nenhum(?:a)?\s+(?:registro|per[ií]cia)", _clean(soup), re.I
        )
        return [], bool(empty)
    headers = [COLUMNS.get(_clean(th).lower()) for th in table.select("thead th")]
    items = []
    for row in table.select("tbody > tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) != len(headers):
            continue  # the empty spacer row RichFaces prints before the data
        values = dict(zip(headers, (_clean(cell) for cell in cells), strict=True))
        values.pop(None, None)
        when = WHEN.search(values.get("data") or "")
        items.append(
            {
                "data": f"{when[3]}-{when[2]}-{when[1]}" if when else None,
                "hora": when[4] if when else None,
                "periciado": values.get("periciado") or None,
                "valor": values.get("valor") or None,
                "perito": CPF.sub("", values.get("perito") or "") or None,
                "situacao": values.get("situacao") or None,
            }
        )
    total = TOTAL.search(_clean(table.parent))
    complete = (total is None or int(total[1]) == len(items)) and not soup.find(class_=SCROLLER_OTHER_PAGE)
    return items, complete


def fetch_pericias(session, detail_html, detail_url):
    """``(items, complete)`` of the Perícia tab; see ``parse_pericias``."""
    response = jsf.post(session, detail_url, tab_request(detail_html), label="PJe process perícias")
    return parse_pericias(response_html(response))
