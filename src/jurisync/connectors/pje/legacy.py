"""PJe 1.x (TRF5's pje.trf5.jus.br): the lawyer panel (Acervo and Intimações tabs) and the process search.

This PJe is an older Seam/RichFaces app than the other PJe portals: its panel has no Expedientes buckets but tabs
(Acervo, Intimações, Intimações de RPV/PRC, Intimações de Pauta de Julgamento) whose buckets are toggle panels
headed "Label - N", and its search form is ``consultarProcessoForm``. Everything here only reads: switching a tab or
expanding a bucket is a view change on the server-side session, it never takes ciência (that is the "Confirmar"
button of a row, which is not touched).
"""

import re

from bs4 import BeautifulSoup

from jurisync import portals
from jurisync.connectors.pje import jsf
from jurisync.connectors.pje.parsers import parse_process_number, response_html

PANEL_PATH = "Painel/painel_usuario/advogado.seam"
SEARCH_FORM = "consultarProcessoForm"
LEGACY_TIMEOUT = 100  # the Pauta tab alone took more than 30 s once
FOUND = re.compile(r"Foram encontrados:\s*(\d+)\s+resultados?", re.I)
HEADER = re.compile(r"^[«»\s]*(?P<label>.+?)\s+-\s+(?P<count>\d+)$")
DETAIL_LINK = re.compile(r"listProcessoCompletoAdvogado\.seam\?id=(\d+)&(?:amp;)?ca=([0-9a-fA-F]{32,})")
PROCESS_RE = re.compile(r"\b\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}\b")
ACERVO = "Acervo"
DEFAULT_BUCKET = "Intimações / Pendentes"


def _soup(html):
    return BeautifulSoup(html, "html.parser")


def parse_found_counts(html):
    """The "Foram encontrados: N resultados" totals of a page, in page order."""
    return [int(n) for n in FOUND.findall(_soup(html).get_text(" "))]


def parse_tabs(html):
    """The panel's top tabs as (tab id, label, form id), Acervo included."""
    tabs = []
    for cell in _soup(html).select("td.rich-tab-header"):
        ident = cell.get("id", "")
        form = cell.find_parent("form")
        if ident.startswith("tabProcAdv") and ident.endswith("_lbl") and form is not None:
            tabs.append((ident[: -len("_lbl")], " ".join(cell.get_text().split()), form.get("id")))
    return tabs


def parse_buckets(html):
    """The toggle-panel buckets of an Intimações tab: (toggle id, label, count)."""
    buckets = []
    for header in _soup(html).select("div.rich-stglpanel-header"):
        ident = header.get("id", "")
        match = HEADER.match(" ".join(header.get_text(" ").split()))
        if ident.endswith("_header") and match:
            buckets.append((ident[: -len("_header")], match.group("label"), int(match.group("count"))))
    return buckets


def parse_grid(html):
    """Rows of the grid whose header has a "Processo" column, keyed by header name; plus the grid's own total."""
    soup = _soup(html)
    for table in soup.find_all("table", class_="rich-table"):
        heads = [" ".join(th.get_text(" ").split()) for th in table.find_all("th")]
        if "Processo" not in heads:
            continue
        rows = []
        for row in table.select("tbody > tr"):
            cells = row.find_all("td", recursive=False)
            values = [" ".join(cell.get_text(" ").split()) for cell in cells]
            number = PROCESS_RE.search(" ".join(values))
            if not number:
                continue
            columns = {head: values[i] for i, head in enumerate(heads) if head and i < len(values) and values[i]}
            rows.append({"processNumber": number.group(0), "columns": columns})
        counts = parse_found_counts(html)
        return {"total": counts[0] if counts else None, "rows": rows}
    return {"total": (parse_found_counts(html) or [None])[0], "rows": []}


def _panel(session, config):
    response = jsf.get(session, config.url(PANEL_PATH), label=f"{config.key} panel", referer=config.base_url)
    return config.url(PANEL_PATH), response_html(response)


def _post(session, url, html, form_id, extra, label):
    """Submit a panel form as the browser does and return the response HTML."""
    fields = jsf.form_fields(html, form_id)
    try:
        fields["javax.faces.ViewState"] = jsf.view_state(html, label)
    except RuntimeError:
        pass
    fields.update(extra)
    return response_html(jsf.post(session, url, fields, label=label))


def _tab_html(session, url, html, tab, label):
    tab_id, _, form_id = tab
    return _post(session, url, html, form_id, {"AJAXREQUEST": "_viewRoot", tab_id: tab_id, "ajaxSingle": tab_id}, label)


def read_panel(session, portal):
    """Walk the panel's tabs and return the buckets with their counts (and where each one lives).

    Acervo: "Pendentes de Manifestação" and "Acervo Geral" (the two sub-tabs). Each Intimações tab: its toggle buckets.
    """
    config = portals.pje(portal)
    previous = jsf.TIMEOUT
    jsf.TIMEOUT = LEGACY_TIMEOUT
    try:
        url, html = _panel(session, config)
        buckets = []

        def add(group, label, count, **where):
            buckets.append({"index": str(len(buckets)), "label": f"{group} / {label}", "count": count, **where})

        counts = parse_found_counts(html)
        add(ACERVO, "Pendentes de Manifestação", counts[0] if counts else 0, kind="acervo")
        general = _soup(html).find("td", id="idCaixa_lbl")
        if general is not None and general.find_parent("form") is not None:
            html = _post(
                session,
                url,
                html,
                general.find_parent("form").get("id"),
                {"idCaixa_server_submit": "idCaixa_server_submit"},
                f"{config.key} Acervo Geral",
            )
            counts = parse_found_counts(html)
            add(ACERVO, "Acervo Geral", counts[0] if counts else 0, kind="acervo")
        for tab in parse_tabs(html):
            if tab[0] == "tabProcAdvPainelPeticaoInicial":
                continue
            html = _tab_html(session, url, html, tab, f"{config.key} {tab[1]}")
            for toggle, label, count in parse_buckets(html):
                add(tab[1], label, count, kind="intimacoes", tab=tab[0], toggle=toggle)
        return url, buckets
    finally:
        jsf.TIMEOUT = previous


def read_counters(session, portal):
    url, buckets = read_panel(session, portal)
    return {
        "success": True,
        "portal": portal,
        "panel_url": url,
        "buckets": [{k: b[k] for k in ("index", "label", "count")} for b in buckets],
        "notifications": [],
    }


def select_bucket(buckets, wanted):
    """The bucket whose index equals ``wanted`` or whose label contains it (default: Intimações / Pendentes)."""
    wanted = DEFAULT_BUCKET if wanted is None else str(wanted)
    exact = [b for b in buckets if b["index"] == wanted or b["label"].casefold() == wanted.casefold()]
    found = exact or [b for b in buckets if wanted.casefold() in b["label"].casefold()]
    if len(found) != 1:
        valid = ", ".join(f"{b['index']}={b['label']}" for b in buckets)
        raise ValueError(f"Bucket {wanted!r} is not (or not only) one of this panel's: {valid}")
    return found[0]


def read_inbox(session, portal, *, bucket=None, filters=None):
    """The rows of one Intimações bucket. Opening it does not notify anyone; no row is confirmed."""
    config = portals.pje(portal)
    if filters:
        raise ValueError(f"{portal} (PJe 1.x) does not support inbox filters; read the bucket and filter the rows")
    url, buckets = read_panel(session, portal)
    chosen = select_bucket(buckets, bucket)
    brief = {k: chosen[k] for k in ("index", "label", "count")}
    if not chosen["count"]:
        return {"success": True, "portal": portal, "bucket": brief, "inboxes": [], "complete": True}
    if chosen["kind"] != "intimacoes":
        raise ValueError(
            f"Listing the rows of {chosen['label']!r} is not supported on the PJe 1.x panel yet; its counter works"
        )
    previous = jsf.TIMEOUT
    jsf.TIMEOUT = LEGACY_TIMEOUT
    try:
        _, html = _panel(session, config)
        tab = next(t for t in parse_tabs(html) if t[0] == chosen["tab"])
        html = _tab_html(session, url, html, tab, f"{config.key} {tab[1]}")
        toggle = chosen["toggle"]
        html = _post(
            session,
            url,
            html,
            tab[2],
            {"AJAXREQUEST": "_viewRoot", toggle: toggle, "ajaxSingle": toggle},
            f"{config.key} {chosen['label']}",
        )
    finally:
        jsf.TIMEOUT = previous
    grid = parse_grid(html)
    pending = chosen["label"].endswith("/ Pendentes")
    rows = [{**row, "pending": pending} for row in grid["rows"]]
    total = grid["total"] if grid["total"] is not None else chosen["count"]
    inbox = {
        "path": chosen["label"],
        "total": total,
        "pages": 1,
        "returned": len(rows),
        "complete": len(rows) >= total,
        "changedWhileReading": len(rows) > total,
        "rows": rows,
    }
    return {"success": True, "portal": portal, "bucket": brief, "inboxes": [inbox], "complete": inbox["complete"]}


def parse_search_rows(html):
    """The processes a consultarProcessoForm search found: number, detail id and access key when the row has them."""
    rows = parse_grid(html)["rows"]
    link = {}
    soup = _soup(html)
    for tr in soup.select("table.rich-table tbody > tr"):
        match = DETAIL_LINK.search(str(tr))
        number = PROCESS_RE.search(tr.get_text(" "))
        if match and number:
            link[number.group(0)] = match.groups()
    return [
        {**row, "processId": link[row["processNumber"]][0], "ca": link[row["processNumber"]][1]}
        if row["processNumber"] in link
        else row
        for row in rows
    ]


def search_process(
    session,
    process_number,
    raw_html_path=None,
    *,
    portal,
    ato_expediente=None,
    ato_text_limit=20000,
    ato_allow_pending=False,
):
    """Search a process number in the consultarProcessoForm and, when it is listed, read its detail page."""
    from jurisync.connectors.pje import search

    config = portals.pje(portal)
    parts = parse_process_number(process_number)
    previous = jsf.TIMEOUT
    jsf.TIMEOUT = LEGACY_TIMEOUT
    try:
        response = jsf.get(session, config.search_url, label=f"{config.key} search page", referer=config.base_url)
        html = response_html(response)
        result = _post(
            session,
            config.search_url,
            html,
            SEARCH_FORM,
            {
                "AJAXREQUEST": "_viewRoot",
                f"{SEARCH_FORM}:j_id330": "1",
                f"{SEARCH_FORM}:searchButton": f"{SEARCH_FORM}:searchButton",
                f"{SEARCH_FORM}:numeroProcessoDecoration:numeroProcesso": process_number,
            },
            f"{config.key} search",
        )
    finally:
        jsf.TIMEOUT = previous
    found = [row for row in parse_search_rows(result) if row["processNumber"] == process_number]
    summary = {"table_found": "consultaProcessoList" in result, "count": parse_grid(result)["total"]}
    if not found:
        return {
            "success": False,
            "error": "Process not found in the PJe 1.x search (the account may not see it)",
            "search_results": summary,
            "timeline": [],
        }
    row = found[0]
    if "processId" not in row:
        return {
            "success": False,
            "error": "The process is listed but its detail link was not recognised (PJe 1.x detail page not validated)",
            "search_results": summary,
            "timeline": [],
        }
    return search._read_detail(
        session,
        config,
        row["processId"],
        row["ca"],
        parts,
        summary,
        raw_html_path,
        referer=config.search_url,
        ato_expediente=ato_expediente,
        ato_text_limit=ato_text_limit,
        ato_allow_pending=ato_allow_pending,
    )
