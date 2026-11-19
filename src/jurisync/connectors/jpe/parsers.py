"""Parsers for the TJMG JPe (Themis) pages: login, SSO answer, menu, counters, grids and the process view.

What each parser is built from (19/11/2026; no authenticated page has been seen yet):

- Seen live (public, GET only): the portal page ``portaljus/intranet/principal.rupe?contexto=jpe`` with the three
  logins (``formLogin`` -> ``/rupe/j_security_check`` with ``j_username``/``j_password``; ``formLoginCertificado``;
  ``formLoginSSO`` -> ``/rupe/loginSsoKeycloakServlet`` with ``origemURL``), a public JSF page (``formAutenticarDocumento``:
  JSF 1.2 with server-side ViewState ``j_idN`` and RichFaces 3.3.3 ``A4J.AJAX.Submit`` actions) and the PDPJ SSO's
  error page (``#kc-error-message``). Fixtures: ``tests/connectors/jpe/fixtures/portal_principal.html`` and
  ``sso_redirect_uri.html``.
- Read from the public manual's screenshots (www8.tjmg.jus.br/Processo_Eletronico_TJMG/Manual-JPe/): the menu labels
  (Início, Cadastro, Comunicações > Manifestadas / Lidas / Pendentes de Leitura / Pendentes de Manifestação, Consulta,
  Peticionamento, Sair), the home counters ("Total de Comunicações": Pendentes de Leitura / Pendentes de Manifestação
  x Total; "Total de Processos": Ativos / Arquivados / Não protocolizados x Polo Ativo / Polo Passivo), the
  comunicações grid headers (Processo CNJ, Processo TJ, Classe, Envio, Órgão Julgador, Destinatário, Urgente, three
  icon buttons, "N resultado(s)") and the process window ("Dados Processo - <n>" with the tabs Dados de Origem, Dados
  Básicos, Partes, Petições e Documentos, Andamentos, Prioridades, Comprovantes, Acesso de Terceiros).

The HTML markup of the authenticated pages is therefore unknown: every parser below matches on visible labels and
header texts (accents and case ignored), never on generated component ids, and returns ``None``/empty when the page
does not have the expected shape, so the caller can report "not recognized" instead of guessing.
"""

import re
import unicodedata
from urllib.parse import parse_qsl, urljoin, urlsplit

from bs4 import BeautifulSoup

from jurisync.connectors.pje.auth.sso import alert_text

OWN_LOGIN_ACTION = "j_security_check"
RESULTS = re.compile(r"(\d+)\s*resultado\(s\)", re.I)
NUMBER = re.compile(r"-?\d+")
CNJ = re.compile(r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}")
TJ_NUMBER = re.compile(r"\d\.\d{4}\.\d{2}\.\d{6}-\d/\d{3}")  # "Processo TJ": 1.0000.14.004715-0/000

# Menu entries the connector may follow (visible text, normalized). Nothing else of the menu is ever opened:
# Cadastro and Peticionamento change things, Sair ends the session.
COMMUNICATION_MENU = {
    "pendentes_leitura": "pendentes de leitura",
    "pendentes_manifestacao": "pendentes de manifestacao",
    "lidas": "lidas",
    "manifestadas": "manifestadas",
}
HOME_MENU = "inicio"
CONSULTA_MENU = ("consulta", "processo", "consulta processual")
MENU_LABELS = frozenset({HOME_MENU, *COMMUNICATION_MENU.values(), *CONSULTA_MENU})

# Grid headers -> result keys. Unknown headers are kept under their own label.
COLUMNS = {
    "processo cnj": "number",
    "processo": "number",
    "numero": "number",
    "numero cnj": "number",
    "processo tj": "tjNumber",
    "numero tj": "tjNumber",
    "classe": "class",
    "envio": "sentAt",
    "data de envio": "sentAt",
    "leitura": "readAt",
    "data de leitura": "readAt",
    "intimacao": "intimatedAt",
    "prazo": "deadline",
    "orgao julgador": "court",
    "relator": "rapporteur",
    "destinatario": "recipient",
    "urgente": "urgent",
    "distribuicao": "distributedAt",
    "autuacao": "filedAt",
    "situacao": "status",
    "comarca": "district",
    "natureza": "nature",
    "polo ativo": "activeParty",
    "polo passivo": "passiveParty",
    "partes": "parties",
    "data": "date",
    "data/hora": "date",
    "descricao": "description",
    "andamento": "description",
    "movimento": "description",
    "complemento": "complement",
    "tipo": "type",
    "documento": "name",
    "peca": "name",
    "nome": "name",
    "arquivo": "name",
    "data de inclusao": "date",
    "inclusao": "date",
    "protocolo": "protocol",
}
TABS = (
    "dados de origem",
    "dados basicos",
    "partes",
    "peticoes e documentos",
    "andamentos",
    "prioridades",
    "comprovantes",
)  # "Acesso de Terceiros" is never opened: it is the log of other people's accesses
DOCUMENT_TAB = "peticoes e documentos"
MOVEMENT_TAB = "andamentos"
PARTIES_TAB = "partes"
# Words of a control the connector never activates, whatever its kind (matched on its normalized label).
FORBIDDEN_WORDS = (
    "peticion",
    "manifestar",
    "responder",
    "cadastr",
    "alterar",
    "excluir",
    "remover",
    "assinar",
    "protocol",
    "enviar",
    "salvar",
    "confirmar",
    "justific",
    "dados da comunicacao",
    "marcar",
    "sair",
    "logout",
    "acesso de terceiros",
)
# Matched on the label only (a menu URL may well say "comunicacao"): any control about a comunicação (its documents,
# its data) registers the reading in JPe, so none is ever activated.
FORBIDDEN_LABEL_WORDS = ("comunicacao",)
DOWNLOAD_WORDS = ("download", "baixar", "visualizar", "abrir", "pdf", "documento", "peca")
VIEW_PROCESS_WORDS = ("visualizar", "consultar", "dados do processo", "detalhar", "detalhes", "pesquisar dados", "lupa")


def norm(value):
    """Lowercase, no accents, single spaces: how labels are compared."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text).strip().casefold()


def text_of(element):
    return re.sub(r"\s+", " ", element.get_text(" ", strip=True)).strip() if element is not None else ""


def soup_of(html):
    return html if isinstance(html, BeautifulSoup) else BeautifulSoup(html or "", "html.parser")


# --- Login and session ------------------------------------------------------------------------------------------------


def login_form(html):
    """The JPe's own login form (``formLogin`` posting to ``j_security_check``), or ``None``."""
    for form in soup_of(html).find_all("form"):
        if (form.get("action") or "").rstrip("/").endswith(OWN_LOGIN_ACTION):
            return form
    return None


def sso_form(html):
    """The form of the PDPJ button (``formLoginSSO`` -> ``loginSsoKeycloakServlet``), or ``None``."""
    for form in soup_of(html).find_all("form"):
        if (form.get("action") or "").rstrip("/").endswith("loginSsoKeycloakServlet"):
            return form
    return None


def is_login_page(html):
    return login_form(html) is not None


def menu_entries(html):
    """``{normalized label: element}`` of the menu entries the connector may follow (first occurrence wins)."""
    entries = {}
    for element in soup_of(html).find_all(["a", "span", "div", "li", "td"]):
        if element.name != "a" and not element.get("onclick"):
            continue
        label = norm(text_of(element))
        if label in MENU_LABELS and label not in entries and not element.find(["a", "li"]):
            entries[label] = element
    return entries


def is_authenticated(html):
    """A JPe page of a logged-in user: no login form and the Comunicações menu present.

    The public pages also carry a "Sair" link, so the menu is what tells the lawyer's area apart.
    """
    soup = soup_of(html)
    if login_form(soup) is not None:
        return False
    labels = {norm(text_of(element)) for element in soup.find_all(["a", "span", "li", "div"])}
    return "comunicacoes" in labels and any(label in labels for label in COMMUNICATION_MENU.values())


def page_message(html):
    """The portal's own message on a page (login error, warning), one line, at most 200 characters, else ""."""
    soup = soup_of(html)
    for selector in ("#messagesPanel", ".rich-messages", ".rich-message", ".mensagemErro", ".erro", ".error", ".alert"):
        for tag in soup.select(selector):
            text = text_of(tag)
            if text:
                return text[:200]
    return ""


def sso_refusal(html):
    """The PDPJ SSO's error-page message (``#kc-error-message``), else "". Identifiers are masked."""
    soup = soup_of(html)
    if soup.select_one("#kc-error-message") is None:
        return ""
    return alert_text(soup)


def asks_justification(html):
    """Whether the page asks why a process is being opened (Res. CNJ 121 "Acesso de Terceiros"): never answered."""
    soup = soup_of(html)
    for element in soup.find_all(["textarea", "input", "select"]):
        if "justific" in norm(" ".join([element.get("name") or "", element.get("id") or ""])):
            return True
    return "justificativa" in norm(text_of(soup)) and soup.find("textarea") is not None


# --- Controls ---------------------------------------------------------------------------------------------------------


def control_label(element):
    """What a person sees of a control: its text, title, alt, value, or the image file it shows."""
    parts = [text_of(element)]
    for attribute in ("title", "alt", "value", "aria-label"):
        parts.append(element.get(attribute) or "")
    for image in element.find_all("img") if element.name != "img" else [element]:
        parts.extend([image.get("title") or "", image.get("alt") or "", (image.get("src") or "").rsplit("/", 1)[-1]])
    return norm(" ".join(part for part in parts if part))


def is_forbidden(element, *, ignore_text=False):
    """Whether a control's label or script names an action that changes something (``FORBIDDEN_WORDS``).

    ``ignore_text`` leaves the visible text out: a document link shows the file name ("Recibo de protocolo.pdf"),
    which says nothing about what the click does; its title, image and script still count.
    """
    label = control_label(element)
    if ignore_text:
        label = norm(label.replace(norm(text_of(element)), "", 1)) if text_of(element) else label
    script = norm(" ".join(element.get(name) or "" for name in ("onclick", "href", "id", "name")))
    if any(word in label for word in FORBIDDEN_LABEL_WORDS):
        return True
    return any(word in label or word.replace(" ", "") in script.replace(" ", "") for word in FORBIDDEN_WORDS)


def actionable(element):
    """Whether a control does something when clicked (a real href, an onclick, or a submit/image input)."""
    href = (element.get("href") or "").strip()
    if href and href != "#" and not href.lower().startswith("javascript:void"):
        return True
    if element.get("onclick"):
        return True
    return element.name == "input" and (element.get("type") or "").lower() in ("submit", "image", "button")


def row_controls(row):
    """Every actionable control of a grid row (links, buttons, clickable images), outermost first."""
    found = []
    for element in row.find_all(["a", "input", "img", "button", "span", "div"]):
        if actionable(element) and not any(element in parent.descendants for parent in found):
            found.append(element)
    return found


def numeric_ids(element):
    """Numbers of 3+ digits in a control's href query or Ajax parameters: candidates for a stable document id."""
    values = []
    href = element.get("href") or ""
    if href and not href.startswith("#"):
        values += [value for _, value in parse_qsl(urlsplit(href).query)]
    script = element.get("onclick") or ""
    values += re.findall(r"'([^']*)'\s*[,}]", script)
    return [value for value in values if re.fullmatch(r"\d{3,20}", value)]


# --- Grids ------------------------------------------------------------------------------------------------------------


def _own_rows(table):
    return [row for row in table.find_all("tr") if row.find_parent("table") is table]


def _cells(row):
    return [cell for cell in row.find_all(["td", "th"], recursive=False)]


def _headers(table):
    """(header row, normalized header texts) of a table: its th row, else None."""
    for row in _own_rows(table):
        cells = _cells(row)
        if cells and all(cell.name == "th" for cell in cells):
            return row, [norm(text_of(cell)) for cell in cells]
    return None, []


def find_grid(html, required):
    """The innermost table whose header row has every ``required`` header (normalized), or ``None``."""
    best = None
    for table in soup_of(html).find_all("table"):
        _, headers = _headers(table)
        if headers and all(name in headers for name in required) and (best is None or best in table.parents):
            best = table  # document order: a table nested in the current best is more specific
    return best


def grid_rows(table):
    """Data rows of a grid as ``(values by header, row element)``; header, footer and empty rows are skipped."""
    header_row, headers = _headers(table)
    rows = []
    for row in _own_rows(table):
        if row is header_row:
            continue
        cells = [cell for cell in _cells(row) if cell.name == "td"]
        if len(cells) < len([header for header in headers if header]) or len(cells) < 2:
            continue  # a footer ("2 resultado(s)") or a "Nenhum resultado" line
        values = {}
        for header, cell in zip(headers, cells, strict=False):
            if not header:
                continue
            checkbox = cell.find("input", attrs={"type": "checkbox"})
            values[header] = (
                checkbox.has_attr("checked") if checkbox is not None and not text_of(cell) else text_of(cell)
            )
        if any(value not in ("", None, False) for value in values.values()):
            rows.append((values, row))
    return rows


def result_total(table):
    """The "N resultado(s)" a RichFaces grid prints under itself, else ``None``."""
    scope = table
    for _ in range(3):
        match = RESULTS.search(text_of(scope))
        if match:
            return int(match.group(1))
        scope = scope.parent
        if scope is None:
            break
    return None


def keyed(values):
    """Header-keyed values renamed to result keys (``COLUMNS``); unknown headers keep their label."""
    result, other = {}, {}
    for header, value in values.items():
        key = COLUMNS.get(header)
        if key and key not in result:
            result[key] = value
        elif value not in ("", None):
            other[header] = value
    if other:
        result["otherColumns"] = other
    return {key: value for key, value in result.items() if value not in ("", None)}


# --- Home counters ----------------------------------------------------------------------------------------------------


def _number(text):
    match = NUMBER.search(text or "")
    return int(match.group(0)) if match else None


def counters(html):
    """The home page totals, or ``None`` when neither panel is rendered.

    ``communications``: pending reading / pending manifestation. ``processes``: active, archived and not filed,
    each split by pole (``None`` where the page shows "-").
    """
    soup = soup_of(html)
    labels = {
        "pendentes de leitura": ("communications", "pendingReading"),
        "pendentes de manifestacao": ("communications", "pendingManifestation"),
        "ativos": ("processes", "active"),
        "arquivados": ("processes", "archived"),
        "nao protocolizados": ("processes", "notFiled"),
    }
    result = {"communications": {}, "processes": {}}
    for row in soup.find_all("tr"):
        cells = _cells(row)
        if len(cells) < 2:
            continue
        label = norm(text_of(cells[0]))
        if label not in labels:
            continue
        group, key = labels[label]
        numbers = [_number(text_of(cell)) for cell in cells[1:]]
        if group == "communications":
            result[group][key] = next((value for value in numbers if value is not None), None)
        elif len(numbers) >= 2:
            result[group][key] = {"activePole": numbers[0], "passivePole": numbers[1]}
        else:
            result[group][key] = {"total": numbers[0] if numbers else None}
    if not result["communications"] and not result["processes"]:
        return None
    return result


def toggle_panels(html):
    """Collapsed home panels (``Total de ...``) that can be expanded, as ``{normalized title: element}``."""
    panels = {}
    for element in soup_of(html).find_all(["div", "td", "span", "a"]):
        label = norm(text_of(element))
        if label.startswith("total de ") and element.get("onclick") and label not in panels:
            panels[label] = element
    return panels


# --- Process view -----------------------------------------------------------------------------------------------------


def labelled_values(fragment):
    """``{label: value}`` of the two-cell rows ("Comarca:" | "BELO HORIZONTE") of a fragment."""
    values = {}
    for row in soup_of(fragment).find_all("tr"):
        cells = _cells(row)
        if len(cells) != 2 or cells[0].find("table") or cells[1].find("table"):
            continue
        label = text_of(cells[0])
        if label.endswith(":") and len(label) <= 60:
            value = text_of(cells[1])
            if value:
                values.setdefault(label.rstrip(":").strip(), value)
    return values


def process_title(html):
    """The number in the process window's title ("Dados Processo - <n>"), else ""."""
    match = re.search(r"Dados (?:do )?Processo\s*-\s*([\d.\-/]+)", text_of(soup_of(html)), re.I)
    return match.group(1) if match else ""


def tabs(html):
    """The process window's tabs as ``{normalized label: (header element, content element or None)}``.

    RichFaces 3.3 renders a tab header as a cell (``rich-tab-header``, id ``<tab id>_lbl``) and, in client switch
    mode, every tab's content under the tab id; in Ajax mode only the active one is rendered.
    """
    soup = soup_of(html)
    found = {}
    for header in soup.find_all(class_=re.compile(r"rich-tab-(header|active|inactive)")):
        label = norm(text_of(header))
        if label not in TABS or label in found:
            continue
        content = None
        identifier = header.get("id") or ""
        if identifier.endswith("_lbl"):
            content = soup.find(id=identifier[: -len("_lbl")])
        found[label] = (header, content)
    return found


def tables(fragment):
    """Every headed grid of a fragment as ``[(normalized headers, rows)]``, innermost tables only."""
    result = []
    for table in soup_of(fragment).find_all("table"):
        _, headers = _headers(table)
        if not headers or any(_headers(inner)[1] for inner in table.find_all("table")):
            continue
        result.append((headers, grid_rows(table)))
    return result


def popup_url(html, base_url):
    """A document URL an Ajax answer opens (``window.open``) or a page frames (iframe/embed/object), else ``None``."""
    soup = soup_of(html)
    match = re.search(r"window\.open\(\s*['\"]([^'\"]+)['\"]", str(soup))
    if match:
        return urljoin(base_url, match.group(1))
    for tag, attribute in (("iframe", "src"), ("embed", "src"), ("object", "data")):
        element = soup.find(tag, attrs={attribute: True})
        if element is not None:
            return urljoin(base_url, element[attribute])
    return None
