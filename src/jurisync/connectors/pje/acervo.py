"""Open a process from the lawyer panel's Acervo tab when the process search asks for reCAPTCHA.

Some PJe deployments (TJRN 1G) guard ``ConsultaProcesso/listView.seam`` with an invisible reCAPTCHA that only a
browser can answer ("A verificação de captcha não está correta"). The lawyer panel opens the same detail page
without it: the Acervo tab lists the lawyer's processes per jurisdiction, each with an "Autos Digitais" link that
already carries the access key (``listProcessoCompletoAdvogado.seam?id=...&ca=...``). Its own search
(``formAcervo:btPesqAc``) filters the selected jurisdiction by CNJ number, so this opens each jurisdiction in turn
and searches it until the process shows up.

Only reads are posted: the tab, a jurisdiction node and the listing's Pesquisar. Every control is checked against
an allowlist; the box-configuration forms (filters, rename, move, new box) are never posted.
"""

import re
from html import unescape

from bs4 import BeautifulSoup

from jurisync import portals
from jurisync.connectors.pje import jsf
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


def listing_fields(html, process_number):
    """The listing search form's fields with the CNJ number filled in, as the browser posts them."""
    form = BeautifulSoup(html, "html.parser").find("form", id=LISTING)
    if form is None:
        return None
    parts = dict(zip(CNJ_FIELDS, parse_process_number(process_number), strict=True))
    fields = []
    for field in form.find_all(["input", "select"]):
        name = field.get("name") or ""
        kind = (field.get("type") or "").lower()
        if not name or name == "javax.faces.ViewState" or kind in {"button", "submit", "image", "reset", "checkbox"}:
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


def find_process(session, process_number, *, portal, max_nodes=MAX_NODES):
    """(process id, access key ``ca``) for a process of the lawyer's Acervo, or None when it is not there."""
    config = portals.pje(portal)
    url = f"https://{config.host}{config.root}{PANEL_SUFFIX}"
    html = response_html(jsf.get(session, url, label="PJe panel"))
    state = jsf.view_state(html, "PJe panel")
    body = [
        ("AJAXREQUEST", "_viewRoot"),
        (TAB, TAB),
        ("selectedTab", TAB),
        ("pend", "false"),
        ("javax.faces.ViewState", state),
        ("AJAX:EVENTS_COUNT", "1"),
    ]
    html = response_html(jsf.post(session, url, body, label="PJe Acervo tab"))
    state = _state(html, state)
    nodes = [node for node in parse_nodes(html) if node["count"] != 0]
    for node in nodes[:max_nodes]:
        control = node["control"]
        body = [
            ("AJAXREQUEST", node["container"]),
            ("formAbaAcervo", "formAbaAcervo"),
            ("autoScroll", ""),
            ("javax.faces.ViewState", state),
            (control, control),
            ("ajaxSingle", control),
            ("selectedTab", TAB),
            ("AJAX:EVENTS_COUNT", "1"),
        ]
        listing = response_html(jsf.post(session, url, body, label="PJe Acervo jurisdiction"))
        state = _state(listing, state)
        fields = listing_fields(listing, process_number)
        container = search_container(listing)
        if fields is None or container is None:
            continue
        body = [
            ("AJAXREQUEST", container),
            *fields,
            ("javax.faces.ViewState", state),
            (SEARCH, SEARCH),
            ("selectedTab", TAB),
            ("AJAX:EVENTS_COUNT", "1"),
        ]
        found = response_html(jsf.post(session, url, body, label="PJe Acervo search"))
        state = _state(found, state)
        link = autos_link(found, process_number)
        if link:
            return link
    return None
