"""Consulta Processual (``processo_consultar``): the court's search by party name, CPF/CNPJ, OAB or originating number.

Unlike Relação de Processos, it reaches any process of the court, the lawyer's or not. The page's form says which
search types the portal offers; Consultar posts the form to the lookup the chosen type advertises (an ajax route of
``client.SEARCH_AJAX_ACTIONS``) and the answer's table is read by its headers. What was searched for is never logged
nor echoed in errors.
"""

import json
import re
import unicodedata
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from jurisync import portals
from jurisync.connectors.eproc import client
from jurisync.connectors.eproc.parsers import _href, _list_pagination, form_fields, parse_panel, text
from jurisync.core.errors import InputError, PortalError
from jurisync.core.redaction import mask_cpf
from jurisync.core.responses import dated, listing
from jurisync.core.session_pool import SessionExpired

MAX_SEARCH_PAGES = 5
FORM = "form#frmProcessoListaAjax"
# argument -> (how the tipoPesquisa label starts, the field it fills, the lookup its option posts to)
CRITERIA = {
    "partyName": ("nome da parte", "strNomeParte", "processos_consulta_por_nome_parte"),
    "taxId": ("cpf/cnpj", "strDocParte", "processos_consulta_por_documento_identificacao"),
    "oab": ("oab", "numNumOab", "processos_consulta_por_oab"),
    "originNumber": ("originario", "numProcessoOriginario", "processos_consulta_por_originario"),
}
CLOSED_FIELD = "chkExibirBaixados"
UFS = {
    "AC",
    "AL",
    "AM",
    "AP",
    "BA",
    "CE",
    "DF",
    "ES",
    "GO",
    "MA",
    "MG",
    "MS",
    "MT",
    "PA",
    "PB",
    "PE",
    "PI",
    "PR",
    "RJ",
    "RN",
    "RO",
    "RR",
    "RS",
    "SC",
    "SE",
    "SP",
    "TO",
}
CNJ = re.compile(r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}")


def fold(value):
    """Lowercase text without accents or repeated spaces, to compare labels across portals."""
    decomposed = unicodedata.normalize("NFKD", value or "")
    return " ".join("".join(char for char in decomposed if not unicodedata.combining(char)).casefold().split())


def _party_name(value):
    words = value.split()
    if len(words) < 2:
        raise InputError("partyName needs at least two words (the portal asks for name and surname)")
    return " ".join(words)


def _tax_id(value):
    if not re.fullmatch(r"[\d.\-/\s]+", value):
        raise InputError("taxId takes only digits and the punctuation . - /")
    digits = re.sub(r"\D", "", value)
    if len(digits) == 11:
        return f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"
    if len(digits) == 14:
        return f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:]}"
    raise InputError("taxId must have 11 digits (CPF) or 14 (CNPJ)")


def _oab(value):
    """The portal's OAB format: UF and 6 digits, zero-padded ("UF + 6 digitos (Ex: RS123456)" in its help)."""
    compact = re.sub(r"[\s.]", "", value.upper()).removeprefix("OAB").lstrip("/:-")
    match = re.fullmatch(r"([A-Z]{2})[/-]?(\d{1,6})", compact)
    reverse = re.fullmatch(r"(\d{1,6})[/-]?([A-Z]{2})", compact)
    uf, number = match.groups() if match else reverse.groups()[::-1] if reverse else (None, None)
    if uf not in UFS:
        raise InputError("oab must be a UF and up to 6 digits, e.g. 'SC123456' or '12345/SC'")
    return f"{uf}{number:0>6}"


def _origin_number(value):
    digits = re.sub(r"[\s./-]", "", value)
    if not re.fullmatch(r"\d{1,25}", digits):
        raise InputError("originNumber takes a process number: digits and the punctuation . - /")
    return digits


NORMALIZE = {"partyName": _party_name, "taxId": _tax_id, "oab": _oab, "originNumber": _origin_number}


def search_term(*, party_name=None, tax_id=None, oab=None, origin_number=None):
    """``(criterion, value)`` in the portal's format; exactly one criterion must be given."""
    given = {
        name: value.strip()
        for name, value in (("partyName", party_name), ("taxId", tax_id), ("oab", oab), ("originNumber", origin_number))
        if isinstance(value, str) and value.strip()
    }
    if len(given) != 1:
        raise InputError("pass exactly one of partyName, taxId, oab or originNumber")
    ((name, value),) = given.items()
    return name, NORMALIZE[name](value)


def search_request(html, base_url, criterion, value, *, include_closed=False):
    """``(url, fields)`` that Consultar posts for this search: the form as served, with the search type read from its
    own tipoPesquisa option and the value in that type's field."""
    form = BeautifulSoup(html, "html.parser").select_one(FORM)
    if form is None:
        raise PortalError("Consulta Processual did not show its search form")
    label, field, _ = CRITERIA[criterion]
    options = form.select('select[name="tipoPesquisa"] option')
    option = next((option for option in options if fold(text(option)).startswith(label)), None)
    if option is None or not option.get("data-acaoconsultaassinada") or not form.select_one(f'[name="{field}"]'):
        raise InputError(f"this portal's Consulta Processual has no search by {criterion}")
    chosen = {"tipoPesquisa": option.get("value", ""), field: value}
    if include_closed:
        box = form.select_one(f'input[name="{CLOSED_FIELD}"]')
        if box is None:
            raise InputError("this portal's Consulta Processual has no option to show baixados")
        chosen[CLOSED_FIELD] = box.get("value", "on")
    fields = [(name, kept) for name, kept in form_fields(form) if name not in chosen and name != CLOSED_FIELD]
    return _href(option["data-acaoconsultaassinada"], base_url), fields + list(chosen.items())


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _fragment(body):
    """The HTML of a lookup answer: the body, or the longest HTML string inside its JSON; None without HTML."""
    try:
        data = json.loads(body)
    except ValueError:
        return body
    pieces = [piece for piece in _strings(data) if "<" in piece]
    return max(pieces, key=len) if pieces else None


def _column(header):
    """The item key a result column fills, or None for one kept in ``extra``."""
    name = fold(header).rstrip(" :")
    if "processo" in name and not any(
        word in name for word in ("originario", "relacionad", "apens", "data", "classe", "situacao")
    ):
        return "processNumber"
    rules = (
        (("classe",), "caseClass"),
        (("autor", "polo ativo", "requerente", "parte autora"), "active"),
        (("reu", "polo passivo", "requerido", "parte re"), "passive"),
        (("parte",), "parties"),
        (("assunto",), "subject"),
        (("situacao",), "status"),
        (("orgao", "juizo", "localidade", "vara", "unidade"), "court"),
    )
    for prefixes, key in rules:
        if name.startswith(prefixes):
            return key
    if "autuacao" in name:
        return "filedAt"
    if "ultimo evento" in name or "ult. evento" in name:
        return "lastEventAt" if "data" in name else "lastEvent"
    return None


def _row(headers, cells):
    item = {"processNumber": None, "parties": [], "extra": {}}
    for header, cell in zip(headers, cells, strict=True):
        key, value = _column(header), mask_cpf(text(cell))
        if key == "processNumber":
            found = CNJ.search(value)
            item["processNumber"] = found.group(0) if found else None
        elif key in ("active", "passive", "parties"):
            pole = None if key == "parties" else key
            item["parties"] += [{"pole": pole, "name": mask_cpf(name)} for name in cell.stripped_strings]
        elif key:
            item[key] = value or None
        elif value and header:
            item["extra"][header] = value
    if item["processNumber"] is None:
        link = next(
            (CNJ.search(text(cell)) for cell in cells if cell.select_one('a[href*="processo_selecionar"]')), None
        )
        item["processNumber"] = link.group(0) if link else None
    return dated(item, "filedAt", "lastEventAt") if item["processNumber"] else None


def parse_search_results(body):
    """``{items, pagination, diagnostics, hidden}`` of a Consulta Processual lookup answer; ``hidden`` are the answer's own
    infra fields, which its pager posts back."""
    fragment = _fragment(body)
    soup = BeautifulSoup(fragment or "", "html.parser")
    for node in soup.select("script, style"):
        node.decompose()
    table = next((table for table in soup.select("table") if table.select("th")), None)
    words = fold(text(soup) if fragment else body)
    if table is None:
        if "captcha" in words:
            return _failed("captcha_required (the portal asked for a CAPTCHA, which this tool does not solve)")
        if fragment is not None and "nenhum" in words:
            empty = {"page": 0, "totalItems": 0, "itemCount": 0, "complete": True}
            return {"items": [], "pagination": empty, "diagnostics": [], "hidden": []}
        return _failed("unsupported_search_response")
    header_row = next(row for row in table.select("tr") if row.find_all("th"))
    headers = [text(node) for node in header_row.find_all(["th", "td"], recursive=False)]
    rows = [row for row in table.select("tr") if row is not header_row and row.find_all("td", recursive=False)]
    items = []
    for row in rows:
        cells = row.find_all("td", recursive=False)
        item = _row(headers, cells) if len(cells) == len(headers) else None
        if item:
            items.append(item)
    if rows and not items:
        return _failed(
            "search_answer_without_processes (the portal listed something else, e.g. people to choose from "
            "for a name search; not supported yet)"
        )
    hidden = [
        (node["name"], node.get("value", ""))
        for node in soup.select('input[type="hidden"][name^="hdnInfra"]')
        if node["name"] != "hdnInfraPaginaAtual"
    ]
    return {"items": items, "pagination": _list_pagination(soup, items), "diagnostics": [], "hidden": hidden}


def _failed(diagnostic):
    return {"items": [], "pagination": {}, "diagnostics": [diagnostic], "hidden": [], "notes": [], "more": None}


def search_processes(term, *, session=None, panel_html=None, panel_url=None, portal, include_closed=False, max_pages=1):
    """Search Consulta Processual for ``term`` (``search_term``), up to ``max_pages`` pages of its answer."""
    portal = portals.eproc(portal).key
    if not isinstance(max_pages, int) or isinstance(max_pages, bool) or not 1 <= max_pages <= MAX_SEARCH_PAGES:
        raise ValueError(f"max_pages must be between 1 and {MAX_SEARCH_PAGES}")
    owned = session is None
    if owned:
        session, panel_html, panel_url = client.authenticate(portal)
    try:
        base_url = portals.eproc(portal).base_url
        page_url = parse_panel(panel_html, base_url)["actions"].get(client.SEARCH_ACTION)
        if not page_url:
            raise RuntimeError("Authenticated panel did not expose Consulta Processual")
        page_url = urljoin(panel_url or base_url, page_url)
        client.require_route(page_url, {client.SEARCH_ACTION}, portal=portal)
        response, body = client.request(session, "GET", page_url, portal=portal)
        error = client._response_error(response, body)
        parsed = _failed(error) if error else _search(session, body, page_url, term, include_closed, max_pages, portal)
        return {
            "success": not parsed["diagnostics"],
            "source": "eproc",
            "portal": portal,
            "scope": "court_search",
            "searchBy": term[0],
            "includeClosed": include_closed,
            **listing(
                parsed["items"], key="processes", total=parsed["pagination"].get("totalItems"), more=parsed["more"]
            ),
            "pagination": parsed["pagination"],
            "notes": parsed["notes"],
            "diagnostics": parsed["diagnostics"],
        }
    finally:
        if owned:
            session.close()


def _search(session, body, page_url, term, include_closed, max_pages, portal):
    """The lookup's answer, then its following pages as the pager asks for them (the answer's own infra fields and
    ``hdnInfraPaginaAtual``), at most ``max_pages`` in all, with truthful pagination."""
    url, fields = search_request(body, portals.eproc(portal).base_url, *term, include_closed=include_closed)
    url = urljoin(page_url, url)
    client.require_route(url, {CRITERIA[term[0]][2]}, ajax=True, portal=portal)
    response, answer = client.request(session, "POST", url, data=fields, ajax=True, portal=portal)
    error = client._response_error(response, answer)
    first = _failed(error) if error else parse_search_results(answer)
    if first["diagnostics"]:
        return first
    items, hidden, notes, page = list(first["items"]), first["hidden"], [], 0
    pagination = dict(first["pagination"])
    total, pages = pagination.get("totalItems"), pagination.get("totalPages")
    while (total is None or len(items) < total) and pages and page + 1 < min(pages, max_pages):
        page += 1
        data = [*fields, *hidden, ("hdnInfraPaginaAtual", str(page))]
        response, answer = client.request(session, "POST", url, data=data, ajax=True, portal=portal)
        error = client._response_error(response, answer)
        if error == "session_expired":
            raise SessionExpired("eproc session expired")  # a search can be repeated
        parsed = _failed(error) if error else parse_search_results(answer)
        if parsed["diagnostics"] or (parsed["pagination"].get("page"), parsed["pagination"].get("totalItems")) != (
            page,
            total,
        ):
            notes.append(f"search_page_{page + 1}_failed")  # another page or list came back: not mixed in
            break
        items.extend(parsed["items"])
        hidden = parsed["hidden"]
    read = page + 1 - (1 if notes else 0)
    complete = total is not None and len(items) >= total
    pagination.update({"pagesRead": read, "itemCount": len(items), "complete": complete})
    more = None
    if not complete and not notes and pages and pages > max_pages:
        notes.append(f"stopped at maxPages={max_pages} of {pages}")
        more = {"maxPages": min(pages, MAX_SEARCH_PAGES)} if max_pages < MAX_SEARCH_PAGES else None
    return {"items": items, "pagination": pagination, "diagnostics": [], "notes": notes, "more": more}
