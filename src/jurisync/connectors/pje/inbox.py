"""The Expedientes panel of the PJe lawyer dashboard: buckets, regions and paged inbox lists.

The panel (``advogado.seam``) groups every communication of the user in fixed buckets
("Pendentes de ciência ou de resposta", "Sem prazo", ...). A bucket opens a tree whose
branches (jurisdictions, and whatever else the user's profile has) lead to inboxes
("Caixa de entrada"), and an inbox is a paged table with one row per communication. The
buckets are the same for every login; the tree below them depends on the user, so it is
discovered from the markup instead of listed here.

Opening a bucket, expanding a branch, opening an inbox and turning a page do not notify
anyone. The rows also carry controls that do (``Tomar ciência`` starts the deadline,
``Responder`` opens the reply), so every request goes through ``check_control``: only the
four read-only shapes below are ever posted.
"""

import math
import re
from html import unescape

from bs4 import BeautifulSoup

from jurisync import portals
from jurisync.connectors.pje import jsf
from jurisync.connectors.pje.expedientes import NOTICE, PRAZO
from jurisync.connectors.pje.parsers import response_html
from jurisync.connectors.pje.process import login_portal

PANEL_PATH = "/pje/Painel/painel_usuario/advogado.seam"
CONTAINER = "conteudoAbaExpedientes"
BUCKET_ROW = re.compile(r"formAbaExpediente:listaAgrSitExp:(\d+):linhaN1$")
NODE_ID = re.compile(
    r"^(?P<tree>formAbaExpediente:listaAgrSitExp:\d+:\w+):(?P<key>[^:]+(?::[^:]+)*)::(?P<face>j_id\d+)$"
)
TABLE = "formExpedientes:tbExpedientes"
SCROLLER = re.compile(rf"^{TABLE}:(sc\w+)$")
ROW_ID = re.compile(rf"{TABLE}:(\d{{4,}}):")
TOTAL = re.compile(r"(\d+)\s+resultados?\s+encontrados?", re.I)
PROCESS = re.compile(r"\b\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}\b")
LIMIT = re.compile(r"Data limite prevista para (manifestação|ciência):\s*(\d{2}/\d{2}/\d{4})")
LAST_MOVE = re.compile(r"Último movimento:\s*(\d{2}/\d{2}/\d{4})(?:\s+\d{2}:\d{2}(?::\d{2})?)?\s*-?\s*(.*)$", re.S)
PROCESS_ID = re.compile(r"listProcessoCompletoAdvogado\.seam\?id=(\d+)")
DATE = re.compile(r"\d{2}/\d{2}/\d{4}")
MAX_DEPTH = 6

# The only controls this module may post, by what they do. Everything else on the page (tomar
# ciência, responder, select, reminders, history, move) is refused even if a page asks for it.
SAFE_CONTROL = {
    "bucket": re.compile(r"^formAbaExpediente:listaAgrSitExp:\d+:j_id\d+$"),
    "branch": re.compile(r"^formAbaExpediente:listaAgrSitExp:\d+:\w+:[^:]+(?::[^:]+)*::j_id\d+$"),
    "inbox": re.compile(r"^formAbaExpediente:listaAgrSitExp:\d+:\w+:[^:]+(?::[^:]+)*::j_id\d+:cxExItem$"),
    "page": re.compile(rf"^{TABLE}:sc\w+$"),
    "search": re.compile(r"^formExpedientes:btPesq$"),
}
FORBIDDEN = re.compile(r"ciencia|responder|selecion|lembrete|historico|detalhe|mover|baixar|assinar|distribu", re.I)


def _clean(node):
    return " ".join(node.get_text(" ", strip=True).replace("\xa0", " ").split())


def _count(text):
    match = re.search(r"(\d+)\s*$", text.strip())
    return int(match.group(1)) if match else 0


def _date(value):
    day, month, year = value[:10].split("/")
    return f"{year}-{month}-{day}"


def check_control(kind, control):
    """Refuse any control that is not one of the read-only panel controls."""
    if kind not in SAFE_CONTROL or FORBIDDEN.search(control) or not SAFE_CONTROL[kind].match(control):
        raise RuntimeError(f"PJe panel control refused ({kind}): it is not a read-only inbox control")
    return control


# -- A4J submits ---------------------------------------------------------------


def a4j_submit(javascript):
    """(form id, container id, parameters) of the ``A4J.AJAX.Submit`` call in an attribute."""
    javascript = re.sub(r"\\x([0-9A-Fa-f]{2})", lambda m: chr(int(m.group(1), 16)), javascript)
    call = re.search(r"A4J\.AJAX\.Submit\(\s*'([^']+)'", javascript)
    if not call:
        raise RuntimeError("PJe did not render the panel's Ajax action")
    container = re.search(r"'containerId':'([^']+)'", javascript)
    parameters = re.search(r"'parameters':\{([^}]*)\}", javascript)
    values = dict(re.findall(r"'([^']+)':'([^']*)'", parameters.group(1))) if parameters else {}
    return call.group(1), container.group(1) if container else CONTAINER, values


# -- parsing -------------------------------------------------------------------


def parse_buckets(html):
    """The fixed top-level buckets: index, label, count, and the control that opens each."""
    soup = BeautifulSoup(html, "html.parser")
    buckets = []
    for node in soup.find_all(id=BUCKET_ROW):
        label = node.select_one(".nomeTarefa")
        count = node.select_one(".pull-right")
        opener = next((a for a in node.find_all("a") if "A4J.AJAX.Submit" in a.get("onclick", "")), None)
        buckets.append(
            {
                "index": int(BUCKET_ROW.fullmatch(node["id"]).group(1)),
                "label": unescape(_clean(label)) if label else _clean(node),
                "count": _count(_clean(count)) if count else 0,
                "control": a4j_submit(opener["onclick"])[2].get("ajaxSingle") if opener else None,
            }
        )
    return buckets


def _submit(javascript):
    form, _, parameters = a4j_submit(javascript)
    return {"form": form, "params": parameters}


def _text_and_count(anchor):
    label = anchor.select_one(".nomeTarefa")
    count = anchor.select_one(".pull-right")
    if label:
        return _clean(label), _count(_clean(count)) if count else 0
    text = _clean(anchor)
    return re.sub(r"\s*\d+$", "", text), _count(text)


def parse_tree(html, bucket):
    """Nodes of one bucket's tree, parents before children. Branches expand; inboxes list.

    Each node is ``{id, parent, label, count, kind, expanded, expand, inbox}``. ``kind`` is the
    icon title PJe gives the branch (``Jurisdição``, or whatever else the profile has); a node
    whose row holds a ``cxExItem`` link is an inbox and has ``inbox`` set to that control.
    """
    soup = BeautifulSoup(html, "html.parser")
    nodes, seen = [], set()
    for table in soup.find_all("table", class_="rich-tree-node"):
        match = NODE_ID.match(table.get("id", ""))
        if (
            not match
            or table["id"] in seen
            or not table["id"].startswith(f"formAbaExpediente:listaAgrSitExp:{bucket}:")
        ):
            continue
        seen.add(table["id"])
        parent_div = table.find_parent("div", class_="rich-tree-node-children")
        parent = (
            parent_div["id"][: -len(":childs")] if parent_div and parent_div.get("id", "").endswith(":childs") else None
        )
        inbox = table.find("a", id=re.compile(r":cxExItem$"))
        anchor = inbox or table.find("a", id=re.compile(r"::jNp$"))
        icon = table.find("i", title=True)
        expander = table.find(attrs={"rich:onexpand": True})
        flag = table.find("input", id=re.compile(r"NodeExpanded$"))
        label, count = _text_and_count(anchor) if anchor else ("", 0)
        if not count:
            main_row = table.find("tr", id=re.compile(r":mainRow$"))
            count = _count(_clean(main_row)) if main_row else 0
        node = {
            "id": table["id"],
            "parent": parent,
            "label": label,
            "count": count,
            "kind": icon["title"] if icon else None,
            "expanded": bool(flag and flag.get("value") == "true"),
            "expand": _submit(expander["rich:onexpand"]) if expander and not inbox else None,
            "inbox": _submit(inbox["onclick"]) if inbox else None,
        }
        if node["expand"] or node["inbox"]:
            nodes.append(node)
    return nodes


def tree_state(html, bucket):
    """The hidden fields the tree sends with every request: node expansion flags and selection."""
    state = {}
    prefix = f"formAbaExpediente:listaAgrSitExp:{bucket}:"
    for field in BeautifulSoup(html, "html.parser").find_all("input", attrs={"name": True}):
        name = field["name"]
        if name.startswith(prefix) and (name.endswith("NodeExpanded") or name.endswith(":input")):
            state[name] = field.get("value", "")
    return state


def _field_value(field):
    if field.name == "select":
        option = field.find("option", selected=True) or field.find("option")
        return option.get("value", "") if option else ""
    return field.get("value", "")


def list_fields(html):
    """The inbox's filter and header fields, as the browser sends them back with the pager."""
    fields = []
    for field in BeautifulSoup(html, "html.parser").find_all(["input", "select", "textarea"]):
        name = field.get("name") or ""
        kind = (field.get("type") or "").lower()
        if not name.startswith("formExpedientes") or name == "formExpedientes" or ROW_ID.search(name):
            continue
        if kind in {"button", "submit", "image", "reset"} or (
            kind in {"checkbox", "radio"} and not field.has_attr("checked")
        ):
            continue
        if name in {"formExpedientes:j_idcl", "formExpedientes:_link_hidden_"}:
            continue
        fields.append((name, _field_value(field)))
    return fields


def parse_row(row):
    text = _clean(row)
    cells = row.find_all("td", recursive=False)
    ids = ROW_ID.findall(str(row))
    link = row.find("a", class_="numero-processo-expediente")
    link_text = _clean(link) if link else ""
    process = PROCESS.search(link_text) or PROCESS.search(text)
    tipo = row.find("span", title="Tipo de documento")
    kind = re.match(r"(.+?)\s*\((\d+)\)", _clean(tipo)) if tipo else None
    medium = row.find("span", title="Meio de comunicação")
    created = row.find("span", title="Data de criação do expediente")
    prazo = PRAZO.search(_clean(row.find("div", title="Prazo para manifestação") or row))
    notice_node = row.find(string=re.compile("(?:registrou|tomou) ciência"))
    notice = NOTICE.search(_clean(notice_node.parent)) if notice_node else None
    limit = LIMIT.search(text)
    move = LAST_MOVE.search(text)
    party = row.find("span", title="Destinatário")
    pending = row.find("span", id=re.compile(r":btnTomarCiencia$"))
    summary = text[text.find(process.group(0)) + len(process.group(0)) :] if process else ""
    summary = summary.split("Último movimento:")[0].strip()
    return {
        "expedienteId": kind.group(2) if kind else (ids[0] if ids else None),
        "tipo": kind.group(1).strip() if kind else None,
        "parte": _clean(party) if party else None,
        "meio": _clean(medium).split("(")[0].strip() if medium else None,
        "expedidoEm": _date(_clean(created)) if created and DATE.search(_clean(created)) else None,
        "ciencia": {"por": notice.group("quem").split(")")[-1].strip(), "em": _date(notice.group("quando"))}
        if notice
        else None,
        "cienciaPendente": bool(pending and pending.find(True)),
        "prazoDias": int(prazo.group(1)) if prazo and prazo.group(1) else None,
        "semPrazo": bool(prazo and not prazo.group(1)),
        "limite": _date(limit.group(2)) if limit else None,
        "limiteTipo": limit.group(1) if limit else None,
        "processNumber": process.group(0) if process else None,
        "classe": link_text.replace(process.group(0), "").strip() if process else None,
        "processId": (PROCESS_ID.search(link.get("onclick", "")) or [None, None])[1] if link else None,
        "resumo": summary,
        "ultimoMovimento": {"em": _date(move.group(1)), "descricao": move.group(2).strip()} if move else None,
        "respondido": bool(re.search(r"\bResposta\b", _clean(cells[0]))) if cells else False,
    }


def parse_inbox(html):
    """One page of an inbox: rows, the total PJe reports, the current page and the scroller id."""
    soup = BeautifulSoup(html, "html.parser")
    for node in soup(["script", "style"]):
        node.decompose()
    table = soup.find(id=f"{TABLE}:tb")
    rows = [parse_row(row) for row in table.find_all("tr", recursive=False)] if table else []
    total = TOTAL.search(soup.get_text(" "))
    scroller = next((m.group(1) for m in (SCROLLER.match(e["id"]) for e in soup.find_all(id=True)) if m), None)
    active = soup.find("td", class_="rich-datascr-act")
    return {
        "rows": [row for row in rows if row["expedienteId"]],
        "total": int(total.group(1)) if total else len(rows),
        "page": int(_clean(active)) if active and _clean(active).isdigit() else 1,
        "scroller": f"{TABLE}:{scroller}" if scroller else None,
    }


# -- search filters ------------------------------------------------------------

# Filters of the inbox search form. The text boxes are keyed by the stable tail of their
# generated names; the dates ("Criação do expediente", entre ... e ...) and the CNJ number
# parts are filled by position. The two select filters (órgão julgador, prioridade) take PJe
# option ids and are not mapped.
TEXT_FILTERS = {
    "expedienteId": "itNE",
    "classe": "itCL",
    "assunto": "itAS",
    "destinatario": "itDestPend",
    "documento": "itIMF",
    "oab": "itOAB",
}
CNJ = re.compile(r"^(\d{7})-(\d{2})\.(\d{4})\.(\d)\.(\d{2})\.(\d{4})$")
CNJ_FIELDS = (
    "numeroSequencial",
    "numeroDigitoVerificador",
    "Ano",
    "ramoJustica",
    "respectivoTribunal",
    "NumeroOrgaoJustica",
)
INPUT_DATE = re.compile(r"^formExpedientes:j_id\d+InputDate$")


def _br_date(value):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"date {value!r} must be YYYY-MM-DD")
    year, month, day = value.split("-")
    return f"{day}/{month}/{year}"


def apply_filters(fields, filters):
    """The form fields with the search filters filled in. Unknown filters are an error."""
    unknown = set(filters) - {"processNumber", "from", "to", *TEXT_FILTERS}
    if unknown:
        raise ValueError(f"unknown inbox filter(s): {sorted(unknown)}")
    values = {}
    if filters.get("processNumber"):
        match = CNJ.match(filters["processNumber"])
        if not match:
            raise ValueError("processNumber must be a CNJ number (0000000-00.0000.0.00.0000)")
        values.update(
            {f"formExpedientes:numeroProcesso:{n}": v for n, v in zip(CNJ_FIELDS, match.groups(), strict=True)}
        )
    for key, suffix in TEXT_FILTERS.items():
        if filters.get(key):
            values[f"formExpedientes:{suffix}"] = str(filters[key])
    dates = [name for name, _ in fields if INPUT_DATE.match(name)]
    if filters.get("from") or filters.get("to"):
        if len(dates) != 2:
            raise RuntimeError("PJe inbox search has no expedition date interval")
        if filters.get("from"):
            values[dates[0]] = _br_date(filters["from"])
        if filters.get("to"):
            values[dates[1]] = _br_date(filters["to"])
    missing = set(values) - {name for name, _ in fields}
    if missing:
        raise RuntimeError("PJe inbox search form does not have the expected filter fields")
    return [(name, values.get(name, value)) for name, value in fields]


# -- navigation ----------------------------------------------------------------


class Panel:
    """The lawyer panel of one logged-in portal session, driven the way the browser drives it."""

    def __init__(self, session, portal):
        self.session = session
        self.portal = portal
        self.url = f"https://{portals.pje(portal).host}{PANEL_PATH}"
        self.html = ""
        self.state = ""
        self.tree = {}
        self.fields = []
        self.search_submit = None
        self.filtered = False

    def open(self):
        response = jsf.get(self.session, self.url, label="PJe panel")
        self.html = response_html(response)
        self.state = jsf.view_state(self.html, "PJe panel")
        return parse_buckets(self.html)

    def _post(self, kind, control, form, container, parameters, extra=()):
        check_control(kind, control)
        body = [
            ("AJAXREQUEST", container),
            *extra,
            (form, form),
            ("autoScroll", ""),
            ("javax.faces.ViewState", self.state),
            *parameters.items(),
            ("AJAX:EVENTS_COUNT", "1"),
        ]
        response = jsf.post(self.session, self.url, body, label="PJe panel")
        html = response_html(response)
        match = re.search(r'name="javax\.faces\.ViewState"[^>]*value="([^"]*)"', html) or re.search(
            r'value="([^"]*)"[^>]*name="javax\.faces\.ViewState"', html
        )
        self.state = match.group(1) if match else self.state
        return html

    def open_bucket(self, bucket):
        if not bucket["control"]:
            return []
        control = bucket["control"]
        html = self._post("bucket", control, "formAbaExpediente", CONTAINER, {control: control, "ajaxSingle": control})
        self.tree = tree_state(html, bucket["index"])
        return parse_tree(html, bucket["index"])

    def expand(self, bucket, node):
        form, parameters = node["expand"]["form"], node["expand"]["params"]
        control = parameters["ajaxSingle"]
        flag = f"{node['id']}NodeExpanded"
        self.tree[flag] = "true"
        html = self._post("branch", control, form, CONTAINER, parameters, extra=list(self.tree.items()))
        self.tree.update(tree_state(html, bucket["index"]))
        self.tree[flag] = "true"
        return parse_tree(html, bucket["index"])

    def open_inbox(self, node):
        self.filtered = False
        parameters = node["inbox"]["params"]
        control = parameters["ajaxSingle"]
        html = self._post("inbox", control, node["inbox"]["form"], CONTAINER, parameters)
        return self._page(html)

    def turn_page(self, page, scroller):
        extra = [*self.fields, ("javax.faces.ViewState", self.state)]
        html = self._post(
            "page", scroller, "formExpedientes", CONTAINER, {scroller: str(page), "ajaxSingle": scroller}, extra
        )
        return self._page(html)

    def search(self, filters):
        """Run the open inbox's own search ("Pesquisar") with the filters; read its pages as usual."""
        if not self.search_submit:
            raise RuntimeError("PJe inbox has no search form; open an inbox first")
        form, container, parameters = self.search_submit
        control = parameters["formExpedientes:btPesq"]
        self.fields = apply_filters(self.fields, filters)
        self.filtered = True
        extra = [*self.fields, ("javax.faces.ViewState", self.state)]
        return self._page(self._post("search", control, form, container, parameters, extra))

    def _page(self, html):
        result = parse_inbox(html)
        fields = list_fields(html)
        if fields and not self.filtered:
            self.fields = fields
        button = BeautifulSoup(html, "html.parser").find(id="formExpedientes:btPesq")
        if button and button.get("onclick"):
            self.search_submit = a4j_submit(button["onclick"])
        return result


# -- reading -------------------------------------------------------------------


def _select_bucket(buckets, wanted):
    wanted = 0 if wanted is None else wanted
    for bucket in buckets:
        if str(wanted).isdigit() and bucket["index"] == int(wanted):
            return bucket
        if not str(wanted).isdigit() and str(wanted).casefold() in bucket["label"].casefold():
            return bucket
    raise ValueError(f"PJe panel has no bucket {wanted!r}; available: {[b['label'] for b in buckets]}")


def _paths(nodes):
    """Label path of every node, e.g. ``["PE / Serra Talhada", "Caixa de entrada"]``."""
    paths = {}
    for node_id, node in nodes.items():
        parent = nodes.get(node["parent"])
        paths[node_id] = [*(paths.get(parent["id"], []) if parent else []), node["label"]]
    return paths


def _wanted(path, regions):
    return not regions or any(r.casefold() in " / ".join(path).casefold() for r in regions)


def walk(panel, bucket, regions=None):
    """Expand a bucket's tree down to its inboxes (only branches matching ``regions`` if given)."""
    nodes = {node["id"]: node for node in panel.open_bucket(bucket)}
    for _ in range(MAX_DEPTH):
        paths = _paths(nodes)
        pending = [n for n in nodes.values() if n["expand"] and not n["expanded"] and _wanted(paths[n["id"]], regions)]
        if not pending:
            break
        for node in pending:
            for child in panel.expand(bucket, node):
                nodes[child["id"]] = {**nodes.get(child["id"], {}), **child}
            nodes[node["id"]]["expanded"] = True
    return nodes


def read_counters(portal, *, session=None, tree=False):
    """The fixed buckets with their counts; ``tree=True`` also lists each bucket's regions and inboxes."""
    with _panel(portal, session) as panel:
        buckets = panel.open()
        result = {
            "success": True,
            "portal": portal,
            "panel_url": panel.url,
            "buckets": [{k: b[k] for k in ("index", "label", "count")} for b in buckets],
            "notifications": [],
        }
        if tree:
            for bucket, out in zip(buckets, result["buckets"], strict=True):
                if bucket["count"] > 0:
                    nodes = walk(panel, bucket)
                    paths = _paths(nodes)
                    out["regions"] = [
                        {"path": paths[n["id"]], "count": n["count"], "kind": n["kind"], "inbox": bool(n["inbox"])}
                        for n in nodes.values()
                    ]
        return result


def read_inbox(portal, *, bucket=None, regions=None, filters=None, max_pages=25, session=None):
    """Every communication in a bucket (default: pendentes), by region, all pages.

    ``regions`` keeps the inboxes whose path contains any of the given texts. ``filters`` runs
    the inbox search in each of them (processNumber, from/to expedition dates, expedienteId,
    classe, assunto, destinatario, documento, oab). ``complete`` is true for an inbox only when
    the rows read reach the total PJe reports for it; more rows than the total means the inbox
    gained items while its pages were being read (``changedWhileReading``), and they are all kept.
    """
    with _panel(portal, session) as panel:
        selected = _select_bucket(panel.open(), bucket)
        if not selected["count"]:
            return {"success": True, "portal": portal, "bucket": _brief(selected), "inboxes": [], "complete": True}
        nodes = walk(panel, selected, regions)
        paths = _paths(nodes)
        inboxes = []
        for node in nodes.values():
            if node["inbox"] and _wanted(paths[node["id"]], regions):
                inboxes.append(_read_pages(panel, node, paths[node["id"]], max_pages, filters))
        return {
            "success": True,
            "portal": portal,
            "bucket": _brief(selected),
            "inboxes": inboxes,
            "complete": all(box["complete"] for box in inboxes),
        }


def _brief(bucket):
    return {k: bucket[k] for k in ("index", "label", "count")}


def _read_pages(panel, node, path, max_pages, filters=None):
    page = panel.open_inbox(node)
    if filters:
        page = panel.search(filters)
    rows = {row["expedienteId"]: row for row in page["rows"]}
    size = len(page["rows"])
    pages = math.ceil(page["total"] / size) if size else 0
    for number in range(2, min(pages, max_pages) + 1):
        page = panel.turn_page(number, page["scroller"])
        if page["page"] != number:
            raise RuntimeError(f"PJe inbox returned page {page['page']} instead of {number}")
        rows.update({row["expedienteId"]: row for row in page["rows"]})
    return {
        "path": path,
        "total": page["total"],
        "pages": pages,
        "returned": len(rows),
        "complete": len(rows) >= page["total"],
        "changedWhileReading": len(rows) > page["total"],
        "rows": list(rows.values()),
    }


class _panel:
    """A panel on the supplied session, or on a fresh login that is closed afterwards."""

    def __init__(self, portal, session):
        self.portal, self.session, self.owned = portal, session, session is None

    def __enter__(self):
        if self.owned:
            self.session, _ = login_portal(self.portal)
        return Panel(self.session, self.portal)

    def __exit__(self, *exc):
        if self.owned and self.session is not None:
            self.session.close()
