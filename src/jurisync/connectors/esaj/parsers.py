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
    result = []
    for row in soup(html).select(
        "#tabelaTodasMovimentacoes tr, #tabelaUltimasMovimentacoes tr, tr.containerMovimentacao"
    ):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 2:
            continue
        docs = []
        for a in row.select("a[onclick]"):
            match = re.search(
                r"validarAberturaIntimacaoNaoRecebida\([^,]+,[^,]+,\s*['\"]?\w+['\"]?,\s*['\"]?(\d+)", a["onclick"]
            )
            if match:
                docs.append(
                    {
                        "documentId": match[1],
                        "title": sanitize_diagnostics(a.get_text(" ", strip=True)),
                        "accessStatus": "flow_required",
                    }
                )
        result.append(
            {
                "date": cells[0].get_text(" ", strip=True),
                "text": sanitize_diagnostics(cells[-1].get_text(" ", strip=True)),
                "documents": docs,
            }
        )
    return result


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
    return result
