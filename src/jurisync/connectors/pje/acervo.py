"""The lawyer panel's Acervo tab: list the lawyer's processes, and open one when the process search asks for reCAPTCHA.

Some PJe deployments (TJRN 1G) guard ``ConsultaProcesso/listView.seam`` with an invisible reCAPTCHA that only a
browser can answer ("A verificação de captcha não está correta"). The lawyer panel opens the same detail page
without it: the Acervo tab lists the lawyer's processes per jurisdiction, each with an "Autos Digitais" link that
already carries the access key (``listProcessoCompletoAdvogado.seam?id=...&ca=...``). Its own search
(``formAcervo:btPesqAc``) filters the selected jurisdiction by CNJ number, so this opens each jurisdiction in turn
and searches it until the process shows up.

``list_processes`` reads the whole tab: each jurisdiction, each of its boxes (Caixa de entrada and the lawyer's own
boxes, e.g. Arquivados) and every page of each box's listing. A row shows the class and number, subject, parties,
órgão julgador, distribution date and last movement; the "Autos Digitais" and "Peticionar" links are never followed.

Only reads are posted: the tab, a jurisdiction node, a box, the listing's page scroller and its Pesquisar. Every
control is checked against an allowlist; the box-configuration forms (filters, rename, move, new box) are never
posted.
"""

import math
import re
from html import unescape

from bs4 import BeautifulSoup

from jurisync import portals
from jurisync.connectors.pje import jsf, legacy
from jurisync.connectors.pje.parsers import parse_process_number, response_html

PANEL_SUFFIX = "Painel/painel_usuario/advogado.seam"
TAB = "tabAcervo"
NODE = re.compile(r"^formAbaAcervo:trAc:[^:]+(?::[^:]+)*::jNd$")
SEARCH = "formAcervo:btPesqAc"
LISTING = "formAcervo"
CNJ_FIELDS = (
    "numeroSequencial",
    "numeroDigitoVerificador",
    "Ano",
    "ramoJustica",
    "respectivoTribunal",
    "NumeroOrgaoJustica",
)
AUTOS = re.compile(
    r"window\.open\('[^']*/ConsultaProcesso/Detalhe/listProcessoCompletoAdvogado\.seam\?id=(\d+)&(?:amp;)?ca=([0-9a-fA-F]{32,})"
)
CAPTCHA_REFUSED = re.compile(r"verifica[çc][ãa]o\s+de\s+captcha\s+n[ãa]o\s+est[áa]\s+correta", re.I)
TENCENT = re.compile(r"executarTencentCaptcha\s*\(|tencentCaptchaTicket")
MAX_NODES = 60
BOX = re.compile(r"^formAbaAcervo:trAc:([^:]+):([^:]+)::cxItem$")
SCROLLER = re.compile(rf"^{LISTING}:tbProcessos:sc\w+$")
TOTAL = re.compile(r"(\d+)\s+resultados?\s+encontrados?", re.I)
PROCESS = re.compile(r"\b\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}\b")
ROW = re.compile(rf"^{LISTING}:tbProcessos:(\d+):")
DATE = re.compile(r"(\d{2})/(\d{2})/(\d{4})")
LAST_MOVE = re.compile(r"Último movimento:\s*(\d{2}/\d{2}/\d{4})(?:\s+\d{2}:\d{2}(?::\d{2})?)?\s*-?\s*(.*)$", re.S)


def captcha_guarded(html):
    """Whether the search page's Pesquisar runs a CAPTCHA widget whose token cannot be produced without a browser.

    TJBA 1G calls a Tencent captcha before searching and, without its ticket, answers an empty table with no message.
    """
    return bool(TENCENT.search(html or ""))


def captcha_refused(html):
    """Whether a process search answered with PJe's reCAPTCHA refusal instead of results."""
    return bool(CAPTCHA_REFUSED.search(unescape(html or "")))


def _state(html, current):
    match = re.search(r'name="javax\.faces\.ViewState"[^>]*value="([^"]*)"', html) or re.search(
        r'value="([^"]*)"[^>]*name="javax\.faces\.ViewState"', html
    )
    return match.group(1) if match else current


def parse_nodes(html):
    """The jurisdiction nodes of the Acervo tree: control, A4J container, label and process count."""
    soup = BeautifulSoup(html, "html.parser")
    nodes = []
    for anchor in soup.find_all("a", id=NODE):
        form, container, parameters = jsf.a4j_submit(anchor.get("onclick", ""))
        control = anchor["id"]
        if form != "formAbaAcervo" or parameters.get(control) != control or parameters.get("ajaxSingle") != control:
            continue
        text = " ".join(anchor.get_text(" ", strip=True).split())
        count = re.search(r"(\d+)\s*$", text)
        nodes.append(
            {
                "control": control,
                "container": container,
                "label": re.sub(r"\s*\d+$", "", text),
                "count": int(count.group(1)) if count else None,
            }
        )
    return nodes


def listing_fields(html, process_number=None):
    """The listing search form's fields (the CNJ number filled in when given), as the browser posts them."""
    form = BeautifulSoup(html, "html.parser").find("form", id=LISTING)
    if form is None:
        return None
    parts = dict(zip(CNJ_FIELDS, parse_process_number(process_number), strict=True)) if process_number else {}
    fields = []
    for field in form.find_all(["input", "select"]):
        name = field.get("name") or ""
        kind = (field.get("type") or "").lower()
        if not name or name == "javax.faces.ViewState" or kind in {"button", "submit", "image", "reset", "checkbox"}:
            continue
        if name.startswith(f"{LISTING}:tbProcessos:"):  # the rows' own selection form
            continue
        if field.name == "select":
            option = field.find("option", selected=True) or field.find("option")
            value = option.get("value", "") if option else ""
        else:
            value = field.get("value", "")
        if name.startswith(f"{LISTING}:numeroProcesso:"):
            value = parts.get(name.rsplit(":", 1)[1], value)
        fields.append((name, value))
    return fields


def search_container(html):
    button = BeautifulSoup(html, "html.parser").find(id=SEARCH)
    if button is None:
        return None
    form, container, parameters = jsf.a4j_submit(button.get("onclick", ""))
    if form != LISTING or parameters.get(SEARCH) != SEARCH:
        raise RuntimeError("PJe Acervo search button is not the listing's Pesquisar")
    return container


def autos_link(html, process_number):
    """(process id, access key) of the listing row for ``process_number``, from its "Autos Digitais" link."""
    soup = BeautifulSoup(unescape(html), "html.parser")
    table = soup.find(id=f"{LISTING}:tbProcessos")
    for row in table.find_all("tr") if table else []:
        if process_number not in row.get_text(" "):
            continue
        for anchor in row.find_all("a", title="Autos Digitais"):
            match = AUTOS.search(anchor.get("onclick", ""))
            if match:
                return match.group(1), match.group(2)
    return None


class Tab:
    """The Acervo tab of one logged-in panel, driven the way the browser drives it; every post is a read."""

    def __init__(self, session, portal):
        config = portals.pje(portal)
        self.session = session
        self.url = f"https://{config.host}{config.root}{PANEL_SUFFIX}"
        self.state = ""

    def _post(self, body, label):
        html = response_html(jsf.post(self.session, self.url, body, label=label))
        self.state = _state(html, self.state)
        return html

    def open(self):
        """The jurisdiction nodes of the tab."""
        html = response_html(jsf.get(self.session, self.url, label="PJe panel"))
        self.state = jsf.view_state(html, "PJe panel")
        body = [
            ("AJAXREQUEST", "_viewRoot"),
            (TAB, TAB),
            ("selectedTab", TAB),
            ("pend", "false"),
            ("javax.faces.ViewState", self.state),
            ("AJAX:EVENTS_COUNT", "1"),
        ]
        return parse_nodes(self._post(body, "PJe Acervo tab"))

    def node(self, node):
        """Open a jurisdiction: its boxes, or (no boxes) its listing."""
        control = node["control"]
        body = [
            ("AJAXREQUEST", node["container"]),
            ("formAbaAcervo", "formAbaAcervo"),
            ("autoScroll", ""),
            ("javax.faces.ViewState", self.state),
            (control, control),
            ("ajaxSingle", control),
            ("selectedTab", TAB),
            ("AJAX:EVENTS_COUNT", "1"),
        ]
        return self._post(body, "PJe Acervo jurisdiction")

    def box(self, box):
        control = box["control"]
        body = [
            ("AJAXREQUEST", box["container"]),
            ("formAbaAcervo", "formAbaAcervo"),
            ("autoScroll", ""),
            ("javax.faces.ViewState", self.state),
            (control, control),
            ("ajaxSingle", control),
            ("selectedTab", TAB),
            ("AJAX:EVENTS_COUNT", "1"),
        ]
        return self._post(body, "PJe Acervo box")

    def search(self, listing, process_number=None):
        """The listing's Pesquisar with its current filters (the CNJ number filled in when given)."""
        fields = listing_fields(listing, process_number)
        container = search_container(listing)
        if fields is None or container is None:
            return None
        body = [
            ("AJAXREQUEST", container),
            *fields,
            ("javax.faces.ViewState", self.state),
            (SEARCH, SEARCH),
            ("selectedTab", TAB),
            ("AJAX:EVENTS_COUNT", "1"),
        ]
        return self._post(body, "PJe Acervo search")

    def page(self, listing, scroller, number):
        """Page ``number`` of the open listing, through its data scroller."""
        if not SCROLLER.match(scroller["control"]):
            raise RuntimeError("PJe Acervo pager is not the listing's data scroller")
        body = [
            ("AJAXREQUEST", scroller["container"]),
            *(listing_fields(listing) or []),
            ("javax.faces.ViewState", self.state),
            (scroller["control"], str(number)),
            ("ajaxSingle", scroller["control"]),
            ("selectedTab", TAB),
            ("AJAX:EVENTS_COUNT", "1"),
        ]
        return self._post(body, "PJe Acervo page")


def find_process(session, process_number, *, portal, max_nodes=MAX_NODES):
    """(process id, access key ``ca``) for a process of the lawyer's Acervo, or None when it is not there."""
    tab = Tab(session, portal)
    nodes = [node for node in tab.open() if node["count"] != 0]
    for node in nodes[:max_nodes]:
        found = tab.search(tab.node(node), process_number)
        link = autos_link(found, process_number) if found else None
        if link:
            return link
    return None


# -- listing -------------------------------------------------------------------


def _clean(node):
    return " ".join(node.get_text(" ", strip=True).split()) if node is not None else ""


def _date(value):
    match = DATE.search(value or "")
    return f"{match.group(3)}-{match.group(2)}-{match.group(1)}" if match else None


def parse_boxes(html, node):
    """The boxes a jurisdiction opened into: control, A4J container, label and item count."""
    key = node["control"].split(":")[2]
    boxes = []
    for anchor in BeautifulSoup(unescape(html), "html.parser").find_all("a", id=BOX):
        if BOX.match(anchor["id"]).group(1) != key:
            continue
        form, container, parameters = jsf.a4j_submit(anchor.get("onclick", ""))
        control = anchor["id"]
        if form != "formAbaAcervo" or parameters.get(control) != control or parameters.get("ajaxSingle") != control:
            continue
        row = anchor.find_parent("td") or anchor
        counter = row.select_one(".itemContador")
        count = re.search(r"\d+", _clean(counter)) if counter else None
        label = anchor.select_one(".nomeTarefa")
        boxes.append(
            {
                "control": control,
                "container": container,
                "label": _clean(label) if label else _clean(anchor),
                "count": int(count.group(0)) if count else None,
            }
        )
    return boxes


def parse_row(row):
    """One listing row as shown: number, class, subject, parties, órgão, distribution and last movement."""
    cell = row.find("td", class_="informacoesProcesso") or row
    link = cell.find("a", class_="numero-processo-acervo") or cell.find("a", title="Autos Digitais")
    heading = _clean(link)
    number = PROCESS.search(heading) or PROCESS.search(_clean(cell))
    ids = [ROW.match(td.get("id", "")) for td in row.find_all("td")]
    first = cell.find("div", class_=re.compile(r"col-md-4"))
    subject = ""
    if first is not None:
        subject = _clean(first)
        if heading:
            subject = subject.replace(heading, "", 1).strip()
    lines = cell.find("div", class_="informacoes-linha-acervo")
    texts = [_clean(div) for div in lines.find_all("div", recursive=False)] if lines else []
    parties = cell.find("span", class_="nome-parte")
    orgao = next((t for t in texts if t.startswith("/")), "")
    distributed = next((t for t in texts if t.startswith("Distribuído")), "")
    move = next((LAST_MOVE.search(t) for t in texts if t.startswith("Último movimento")), None)
    return {
        "processNumber": number.group(0) if number else None,
        "processId": next((m.group(1) for m in ids if m), None),
        "classe": heading.replace(number.group(0), "").strip() or None if number else None,
        "assunto": subject or None,
        "partes": _clean(parties) or None,
        "orgaoJulgador": orgao.lstrip("/").strip() or None,
        "distribuidoEm": _date(distributed),
        "ultimoMovimento": {"em": _date(move.group(1)), "descricao": move.group(2).strip()} if move else None,
        "prioritario": cell.find(title="Prioritário") is not None,
    }


def parse_listing(html):
    """One page of a box: rows, the total PJe reports, the active page and the data scroller."""
    soup = BeautifulSoup(unescape(html), "html.parser")
    body = soup.find(id=f"{LISTING}:tbProcessos:tb")
    rows = [parse_row(row) for row in body.find_all("tr", recursive=False)] if body else []
    footer = soup.find(id=f"{LISTING}:tbProcessos")
    total = TOTAL.search(_clean(footer)) if footer else None
    scroller = None
    for node in soup.find_all(id=SCROLLER):
        script = node.find("script")
        if script is None:
            continue
        form, container, _ = jsf.a4j_submit(script.get_text())
        if form == LISTING:
            scroller = {"control": node["id"], "container": container}
    active = soup.find("td", class_="rich-datascr-act")
    return {
        "table": body is not None,
        "rows": [row for row in rows if row["processNumber"]],
        "total": int(total.group(1)) if total else len(rows),
        "page": int(_clean(active)) if active and _clean(active).isdigit() else 1,
        "scroller": scroller,
    }


def _read_box(tab, listing, path, max_pages):
    page = parse_listing(listing)
    rows = {row["processId"] or row["processNumber"]: row for row in page["rows"]}
    size = len(page["rows"])
    pages = math.ceil(page["total"] / size) if size else 0
    for number in range(2, min(pages, max_pages) + 1):
        if page["scroller"] is None:
            break
        listing = tab.page(listing, page["scroller"], number)
        following = parse_listing(listing)
        following["scroller"] = following["scroller"] or page["scroller"]
        page = following
        if page["page"] != number:
            raise RuntimeError(f"PJe Acervo returned page {page['page']} instead of {number}")
        rows.update({row["processId"] or row["processNumber"]: row for row in page["rows"]})
    items = [{**row, "jurisdicao": path[0], "caixa": path[1]} for row in rows.values()]
    return {
        "path": path,
        "total": page["total"],
        "pages": pages,
        "returned": len(items),
        "complete": len(items) >= page["total"],
    }, items


def _empty_box(path):
    return {"path": path, "total": 0, "pages": 0, "returned": 0, "complete": True}, []


def list_processes(session, portal, *, regions=None, max_pages=5, max_nodes=MAX_NODES):
    """The lawyer's processes in the Acervo tab, jurisdiction by jurisdiction and box by box, up to ``max_pages``.

    ``regions`` keeps the jurisdictions whose label contains any of the texts. ``complete`` is true only when every
    box read reaches the total PJe shows for it and the boxes add up to their jurisdiction's counter.
    """
    tab = Tab(session, portal)
    nodes = tab.open()
    wanted = [
        node for node in nodes if not regions or any(text.casefold() in node["label"].casefold() for text in regions)
    ]
    to_read = [node for node in wanted if node["count"] != 0]
    jurisdictions, items = [], {}
    for node in to_read[:max_nodes]:
        html = tab.node(node)
        boxes = parse_boxes(html, node)
        read = []
        if boxes:
            for box in boxes:
                if box["count"] == 0:
                    read.append(_empty_box([node["label"], box["label"]]))
                    continue
                read.append(_read_box(tab, tab.box(box), [node["label"], box["label"]], max_pages))
        else:
            # No boxes (TJRN): the jurisdiction opens its listing, or only its search form, whose Pesquisar lists all.
            listing = html if parse_listing(html)["table"] else tab.search(html)
            if listing is not None:
                read.append(_read_box(tab, listing, [node["label"], "Caixa de entrada"], max_pages))
        for _, rows in read:
            for row in rows:
                items.setdefault(row["processId"] or row["processNumber"], row)
        found = sum(box["total"] for box, _ in read)
        jurisdictions.append(
            {
                "label": node["label"],
                "count": node["count"],
                "boxes": [box for box, _ in read],
                "complete": bool(read)
                and all(box["complete"] for box, _ in read)
                and (node["count"] is None or found >= node["count"]),
            }
        )
    skipped = [node["label"] for node in to_read[max_nodes:]]
    result = {
        "success": True,
        "portal": portal,
        "source": "acervo",
        "jurisdictions": jurisdictions,
        "total": sum(node["count"] or 0 for node in wanted),
        "returned": len(items),
        "items": list(items.values()),
        "complete": not skipped and all(j["complete"] for j in jurisdictions),
    }
    if skipped:
        # One call reads MAX_NODES jurisdictions; the rest come with a second call filtered to them.
        result["notRead"] = skipped
        result["next"] = "Call pje_list_processes again with regions set to notRead to read these jurisdictions."
    return result


ARCHIVED = re.compile(r"arquivad", re.I)


def is_archived(item):
    """Whether a listed process is archived: it sits in an Arquivados box (or PJe-JT bucket). The PJe-JT archiving date
    alone does not say so: a process taken out of the archive keeps it."""
    return bool(ARCHIVED.search(item.get("caixa") or ""))


def read_acervo(portal, *, session, regions=None, max_pages=5):
    """``list_processes`` on the classic panel, the Acervo Geral bucket on the PJe-JT (KZ) one, the Acervo sub-tabs'
    grids on PJe 1.x. Every item says whether the process is ``arquivado``."""
    config = portals.pje(portal)
    if config.pje1x:
        result = legacy.list_processes(session, portal, regions=regions, max_pages=max_pages)
    elif config.kz:
        from jurisync.connectors.pje import kz

        result = kz.read_acervo(session, portal, regions=regions, max_pages=max_pages)
    else:
        result = list_processes(session, portal, regions=regions, max_pages=max_pages)
    for item in result.get("items") or []:
        item["arquivado"] = is_archived(item)
    return result
