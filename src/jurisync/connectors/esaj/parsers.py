"""Parse e-SAJ responses without exposing navigation URLs or session material."""

import re
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup

from jurisync.core.mcp import sanitize_diagnostics

CNJ = re.compile(r"\d{7}-\d{2}\.\d{4}\.8\.\d{2}\.\d{4}")
PRIVATE = re.compile(r"url|href|cookie|token|hash|signature|certificado|session|ticket|senha|password|csrf", re.I)


def redact_text(value):
    text = re.sub(r"https?://\S+", "[url]", str(value), flags=re.I)
    return re.sub(
        r"(?:token|ticket|signature|hash|cookie|csrf|CASTGC|JSESSIONID)\s*[=:]\s*\S+", "[redacted]", text, flags=re.I
    )


def public_json(value):
    if isinstance(value, dict):
        return {k: public_json(v) for k, v in value.items() if not PRIVATE.search(k)}
    if isinstance(value, list):
        return [public_json(v) for v in value]
    return sanitize_diagnostics(redact_text(value)) if isinstance(value, str) else value


def soup(html):
    return BeautifulSoup(html, "html.parser")


def captcha(html):
    """A CAPTCHA that stands between the connector and the page.

    Every TJMS consultation page carries the hidden ``recaptcha_response_token`` input, including the pages that answer
    the search, so that input is not evidence. Only a widget the portal shows, or the word in the visible text, is.
    """
    page = soup(html)
    if page.select('.g-recaptcha, [data-sitekey], iframe[src*="recaptcha"]'):
        return True
    for tag in page(["script", "style", "noscript"]):
        tag.decompose()
    return "captcha" in page.get_text(" ", strip=True).lower()


def messages(html):
    page = soup(html)
    result = []
    for row in page.select("tr"):
        check = row.find("input", attrs={"name": "nuSeqMensagem"})
        cells = row.find_all("td", recursive=False)
        if check and len(cells) >= 3:
            message_id = check.get("value", "")
            if not re.fullmatch(r"\d+", message_id):
                continue
            result.append(
                {
                    "messageId": message_id,
                    "sentAt": cells[1].get_text(" ", strip=True),
                    "subject": sanitize_diagnostics(redact_text(cells[2].get_text(" ", strip=True))),
                }
            )
    if not page.find("input", attrs={"name": "pag"}) or "Assunto" not in page.get_text():
        from jurisync.connectors.esaj.base import EsajParseError

        raise EsajParseError("Unrecognized e-SAJ mailbox")
    return result


def process_links(html, prefix):
    result = []
    for link in soup(html).select("a[href]"):
        parts = urlsplit(link["href"])
        query = parse_qs(parts.query)
        if parts.path.endswith(f"/{prefix}/show.do") and query.get("processo.codigo"):
            result.append(link["href"])
    return list(dict.fromkeys(result))


def not_found(html):
    """The consultation's own answer that no process matches ("Não existem informações disponíveis ...")."""
    text = " ".join(soup(html).get_text(" ", strip=True).split())
    return "Não existem informações disponíveis para os parâmetros informados" in text


def process(html, number):
    page = soup(html)
    displayed = page.select_one("#numeroProcesso, #nuProcessoUnificadoFormatado")
    numbers = CNJ.findall(displayed.get_text() if displayed else page.get_text(" ", strip=True))
    if number not in numbers:
        from jurisync.connectors.esaj.base import EsajParseError

        raise EsajParseError("e-SAJ did not return the requested process detail")
    fields = {}
    for name in ("classeProcesso", "assuntoProcesso", "foroProcesso", "varaProcesso", "juizProcesso"):
        node = page.find(id=name)
        if node:
            fields[name] = sanitize_diagnostics(node.get_text(" ", strip=True))
    parties = []
    for row in page.select("#tablePartesPrincipais tr, #tablePartesSecundarias tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) >= 2:
            parties.append(
                {
                    "role": cells[0].get_text(" ", strip=True),
                    "name": sanitize_diagnostics(cells[1].get_text(" ", strip=True)),
                }
            )
    return {"processNumber": number, "fields": fields, "parties": parties, "movements": movements(html)}


def movements(html):
    """Movements of the full table; the "last movements" table repeats its first rows, so it is read only alone."""
    page = soup(html)
    table = page.find(id="tabelaTodasMovimentacoes") or page.find(id="tabelaUltimasMovimentacoes")
    rows = table.select("tr") if table is not None else page.select("tr.containerMovimentacao")
    result = []
    for row in rows:
        cells = row.find_all("td", recursive=False)
        if len(cells) < 2:
            continue
        result.append(
            {
                "date": cells[0].get_text(" ", strip=True),
                "text": sanitize_diagnostics(cells[-1].get_text(" ", strip=True)),
                "documents": movement_documents(row),
            }
        )
    return result


# The movement's document link: ``validarAberturaIntimacaoNaoRecebida(event, this, '<process code>', <movement
# sequence>, '<document URL>' | '#liberarAutoPorSenha')``. The number is the movement's sequence, not a document.
RECEIPT_CHECK = re.compile(
    r"validarAberturaIntimacaoNaoRecebida\([^,]+,[^,]+,\s*['\"]?\w+['\"]?,\s*['\"]?(\d+)['\"]?\s*(?:,\s*['\"]([^'\"]*))?"
)


def link_document_id(target):
    """``cdDocumento`` of a movement's document URL (``abrirDocumentoVinculadoMovimentacao.do``), or None."""
    values = parse_qs(urlsplit(target or "").query).get("cdDocumento", [])
    return values[0] if len(values) == 1 and re.fullmatch(r"\d+", values[0]) else None


def movement_documents(row):
    """The documents a movement links, once each: ``documentId`` is the document code of the link's URL (what the
    document viewer lists), ``sequence`` the movement's sequence from the receipt check, when the row carries it.

    The portal draws each document twice (icon and title) with the same URL. A link that opens only the
    process-password dialog names no document: it is kept with ``documentId`` None so its presence is not lost.
    """
    found = {}
    for link in row.select("a[href], a[onclick]"):
        check = RECEIPT_CHECK.search(link.get("onclick", ""))
        document_id = link_document_id(link.get("href")) or link_document_id(check[2] if check else None)
        if not document_id and not check:
            continue
        key = document_id or f"sequence:{check[1]}"
        entry = found.setdefault(
            key,
            {
                "documentId": document_id,
                "sequence": check[1] if check else None,
                "title": "",
                "accessStatus": "flow_required",
            },
        )
        title = sanitize_diagnostics(link.get_text(" ", strip=True))
        if len(title) > len(entry["title"]):
            entry["title"] = title
        if entry["sequence"] is None and check:
            entry["sequence"] = check[1]
    return list(found.values())


def movements_on_demand(html):
    """The detail page loads its movements on demand ("Exibir movimentações"), instead of listing them."""
    return soup(html).find(id="btnExibirMovimentacoes") is not None


def movement_page(html):
    """``(movements, next_cursor)`` of one ``carregarMovimentacoesAjax.do`` answer.

    The first page carries the cursor of the second in ``#cursorMovimentacoesPaginado``; every later page carries the
    cursor of the next one in the hidden row ``#trCursorMovimentacoes``. No cursor: this was the last page.
    """
    page = soup(html)
    if page.select_one(".mensagemErroMovimentacoes"):
        from jurisync.connectors.esaj.base import EsajParseError

        raise EsajParseError("e-SAJ could not load this page of movements")
    cursor = page.find(id="cursorMovimentacoesPaginado")
    cursor = cursor.get("value") if cursor is not None else None
    if not cursor:
        row = page.find(id="trCursorMovimentacoes")
        cursor = row.get("data-cursor") if row is not None else None
    if row := page.find(id="trCursorMovimentacoes"):
        row.decompose()
    return movements(str(page)), cursor or None


def records_need_password(html):
    """The detail page's "Visualizar autos" opens only the process-password dialog: the account is not linked."""
    links = soup(html).select("a.linkPasta[href]")
    return bool(links) and all(link["href"].startswith("#liberarAutoPorSenha") for link in links)


def records_link(html, prefix):
    """The detail page's "Visualizar autos" target (``abrirPastaDigital.do?processo.codigo=``), not its accessible twin."""
    for link in soup(html).select("a.linkPasta[href]"):
        parts = urlsplit(link["href"])
        query = parse_qs(parts.query)
        if (
            parts.path.endswith(f"/{prefix}/abrirPastaDigital.do")
            and query.get("processo.codigo")
            and query.get("acessibilidade", ["false"]) != ["true"]
        ):
            return link["href"]
    return None


def second_instance_link(html, prefix, number):
    """The first-instance page's "Consultar em 2ª instância" (``abrirConsultaProcessoSG.do``) for this process number."""
    digits = re.sub(r"\D", "", number)
    for link in soup(html).select("a[href]"):
        parts = urlsplit(link["href"])
        query = parse_qs(parts.query)
        if parts.path.endswith(f"/{prefix}/abrirConsultaProcessoSG.do") and [
            re.sub(r"\D", "", value) for value in query.get("nuProcesso", [])
        ] == [digits]:
            return link["href"]
    return None


def hidden_process_code(html):
    """The process code the second-instance page keeps for its own scripts (``input[name=cdProcesso]``)."""
    node = soup(html).select_one('input[type="hidden"][name="cdProcesso"]')
    return node.get("value", "") if node is not None else ""


def simultaneous_queries(html):
    """TJSP's throttle answer: "Foram identificadas multiplas consultas simultâneas" instead of the page asked for."""
    import unicodedata

    text = " ".join(soup(html).get_text(" ", strip=True).split()).lower()
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return "multiplas consultas simultaneas" in text


def viewer_documents(html, number):
    """Process-bound PDF parameters from the viewer's JSON, never from a caller URL."""
    import json

    from jurisync.connectors.esaj.base import EsajBlocked, EsajParseError

    match = re.search(r"var requestScope\s*=\s*(\[.*?\]);", html, re.S)
    if not match:
        raise EsajBlocked("The document viewer requires a password or another access flow")
    try:
        nodes = json.loads(match[1])
    except ValueError:
        raise EsajParseError("Invalid document viewer inventory") from None
    if not isinstance(nodes, list):
        raise EsajParseError("Unexpected document viewer inventory")
    result = []
    digits = re.sub(r"\D", "", number)
    for node in nodes:
        data = node.get("data", {})
        children = node.get("children", [])
        doc_id = str(data.get("cdDocumento", ""))
        if not re.fullmatch(r"\d+", doc_id):
            continue
        entry = {
            "documentId": doc_id,
            "title": sanitize_diagnostics(data.get("title", "")),
            "accessStatus": "flow_required",
        }
        if children and not data.get("sigiloAbsoluto"):
            page = children[0].get("data", {})
            params = page.get("parametros", "")
            values = parse_qs(params)
            if [re.sub(r"\D", "", value) for value in values.get("nuProcesso", [])] != [digits] or values.get(
                "cdDocumento"
            ) != [doc_id]:
                raise EsajParseError("Document inventory does not match this process")
            if not page.get("urlMidiaDigital") and not page.get("documentoSigiloso"):
                entry.update(
                    parameters=params, pageCount=page.get("nuPaginas"), accessStatus="permission_check_required"
                )
        result.append(entry)
    if nodes and not result:
        # Folders or nodes in a shape this parser does not know: the folder was not listed, it is not empty.
        raise EsajParseError("The document viewer listed no document this connector recognizes")
    return result


FOUND = re.compile(r"(\d+)\s+Processos?\s+encontrados?", re.I)
DATE = re.compile(r"\d{2}/\d{2}/\d{4}")


def listing_row(row, prefix):
    """One result row of a consultation list (first or second instance), or None when it links another application."""
    link = row.select_one("a.linkProcesso[href]")
    parts = urlsplit(link["href"]) if link else None
    if parts is None or not parts.path.endswith(f"/{prefix}/show.do"):
        return None
    query = parse_qs(parts.query)
    number = CNJ.search(link.get_text(" ", strip=True))

    def text(selector):
        found = row.select_one(selector)
        return sanitize_diagnostics(" ".join(found.get_text(" ", strip=True).split())) if found else None

    # The second instance names the same blocks differently (nomeParticipante, assuntoProcesso, dataLocalDistribuicao).
    received = text(".dataLocalDistribuicaoProcesso, .dataLocalDistribuicao") or ""
    date = DATE.search(received)
    place = received[date.end() :] if date else received
    return {
        "processNumber": number.group(0) if number else None,
        "codigo": query.get("processo.codigo", [None])[0],
        "foro": query.get("processo.foro", [None])[0],
        "parte": {
            "papel": (text(".tipoDeParticipacao") or "").rstrip(":").strip() or None,
            "nome": text(".nomeParte, .nomeParticipante"),
        },
        "classe": text(".classeProcesso"),
        "assunto": text(".assuntoPrincipalProcesso, .assuntoProcesso"),
        "recebidoEm": date.group(0) if date else None,
        "local": place.strip(" -") or None,
    }


def process_listing(html, prefix):
    """``(items, total)`` of a consultation result list (``#listagemDeProcessos``, one ``divProcesso...`` per case).

    Each item carries what the row shows: number, code and foro of its detail link, the party the search matched with
    its role, class, main subject, the date it was received and where. ``total`` is the portal's own count
    ("N Processos encontrados"; ``#contadorDeProcessos`` in the first instance, an unnamed ``.resultadoPaginacao`` in
    the second), None when the page shows none. A page that is neither the list nor the consultation's
    "no information" answer is an error, so an unexpected page is never read as an empty list.
    """
    page = soup(html)
    listing = page.find(id="listagemDeProcessos")
    if listing is None:
        if not_found(html):
            return [], 0
        from jurisync.connectors.esaj.base import EsajParseError

        raise EsajParseError("e-SAJ did not return a process list")
    counter = page.find(id="contadorDeProcessos") or page.select_one(".resultadoPaginacao")
    found = FOUND.search(counter.get_text(" ", strip=True)) if counter else None
    rows = (listing_row(row, prefix) for row in listing.find_all("div", id=re.compile(r"^divProcesso")))
    return [item for item in rows if item], int(found.group(1)) if found else None


def incident_parents(html):
    """``[(cdProcesso, cdForoProcesso)]`` of the rows with an "Incidentes e recursos" link: the children the list
    loads on demand (``obterProcessosFilhos.do``)."""
    result = []
    for link in soup(html).select("a[id^=incidentesRecursos]"):
        code, foro = link.get("cdprocesso", ""), link.get("cdforoprocesso", "")
        if re.fullmatch(r"[A-Za-z0-9]{1,40}", code) and re.fullmatch(r"\d{1,6}", foro):
            result.append((code, foro))
    return list(dict.fromkeys(result))


def incident_children(html, prefix):
    """The rows ``obterProcessosFilhos.do`` answers: the same row shape, without the list around it."""
    rows = (listing_row(row, prefix) for row in soup(html).select(".home__lista-de-processos"))
    return [item for item in rows if item]


def csrf_token(html):
    """The page's ``_csrf`` value, which its own scripts send as ``X-CSRF-Token``; "" when absent."""
    node = soup(html).select_one('input[name="_csrf"]')
    value = node.get("value", "") if node is not None else ""
    return value if re.fullmatch(r"[A-Za-z0-9-]{1,80}", value) else ""
