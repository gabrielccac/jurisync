"""The Expedientes tab of a PJe process: every communication, its recipient and its deadline.

The tab lists each ato de comunicação (intimação, citação) per recipient with the
channel (Diário Eletrônico for the parte autora, expedição eletrônica for the INSS),
the date the party took notice, the number of days and the deadline PJe itself
computed ("Data limite prevista"). It covers the whole history of the process, so
it answers "is there an open deadline, and until when" without reading certidões.
"""

import re
from datetime import date, datetime
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from jurisync.connectors.pje import jsf
from jurisync.connectors.pje.parsers import response_html
from jurisync.core import ciencia

TABLE_ID = "processoParteExpedienteMenuGridList:tb"
TAB_CONTROL = "navbar:linkAbaExpedientes1"

# "Intimação (123) - Prioridade: Normal - ID do documento (456)"; TJPA prints only "Intimação (123)".
HEADER = re.compile(r"^(?P<tipo>[^()]+?)\s*\((?P<id>\d+)\)(?:\s*-\s*Prioridade:\s*(?P<prioridade>\S+))?")
DOCUMENT = re.compile(r"ID do documento\s*\((\d+)\)")
# The act's own document as the row links it, for rows whose header does not name it (TJPA, a row without header).
ACT_DOCUMENT = re.compile(r"/Processo/Consulta/validaDocumento\.seam\?idProcessoDocumento=(\d+)")
ATO_DOCUMENT = re.compile(r"[?&]paramIdProcessoDocumento=(\d+)")
ISSUED = re.compile(
    r"(Expedição eletrônica|Diário Eletrônico|Edital|Correio|Mandado|Central de Mandados|Sistema)[^()]*"
    r"\((\d{2}/\d{2}/\d{4}(?: \d{2}:\d{2}:\d{2})?)\)"
)
NOTICE = re.compile(
    r"(?P<quem>.+?)\s+(?:registrou|tomou) ciência em\s+(?P<quando>\d{2}/\d{2}/\d{4}(?: \d{2}:\d{2}:\d{2})?)"
)
PRAZO = re.compile(r"Prazo:\s*(?:(\d+)\s*dias?|(\d+)\s*sem prazo|sem prazo)", re.I)
ATO_HREF = re.compile(r"^/[^/]+/Painel/painel_usuario/popup/visualizarExpediente\.seam\?idProcessoParteExpediente=\d+&")
RESPONSE_HREF = re.compile(
    r"^/[^/]+/Painel/painel_usuario/popup/visualizarExpediente\.seam\?.*paramIdProcessoDocumentoBin=\d+"
)
ATTACHMENT = re.compile(r"(\d{6,}) - (.+?) Juntado por (.+?) em (\d{2}/\d{2}/\d{4}) \d{2}:\d{2}:\d{2}")
EXPEDIENTE_BOX = re.compile(
    r"(?P<tipo>[^()]+?) \((?P<id>\d+)\) Destinatário (?P<parte>.+?) Prazo (?P<prazo>\d+ dias?|\d+ sem prazo|sem prazo)"
    r"(?: Data limite para (?:manifestação|ciência) (?P<limite>\d{2}/\d{2}/\d{4})(?: \d{2}:\d{2}:\d{2})?)? Fechado\? (?P<fechado>SIM|NÃO)"
)
LIMIT = re.compile(r"(\d{2}/\d{2}/\d{4})(?: (\d{2}:\d{2}:\d{2}))?")
# A RichFaces datascroller renders the pages that are not the current one with this class.
SCROLLER_OTHER_PAGE = "rich-datascr-inact"


def _clean(node):
    return " ".join(node.get_text(" ", strip=True).replace("\xa0", " ").split())


def _date(value):
    return datetime.strptime(value[:10], "%d/%m/%Y").date().isoformat()


def _header_text(cell):
    """The text before the recipient (``<h6>``): the act's type, its id and its document id, when printed."""
    party = cell.find("h6")
    if party is None:
        return _clean(cell)
    parts = []
    for string in cell.strings:
        if party in string.parents:
            break
        parts.append(string)
    return " ".join(" ".join(parts).replace("\xa0", " ").split())


def _act_document(row, header_text, ato):
    """The act's document id: from the header, else from the row's links to that one document."""
    document = DOCUMENT.search(header_text)
    if document:
        return document.group(1)
    if ato is not None:
        linked = ATO_DOCUMENT.search(ato["href"])
        if linked:
            return linked.group(1)
    linked = {match.group(1) for a in row.find_all("a", href=True) if (match := ACT_DOCUMENT.search(a["href"]))}
    return linked.pop() if len(linked) == 1 else None


def _row_type(cell):
    """A row without header names its type only beside the recipient, as ``<h6>PARTE <span>- Citação</span></h6>``."""
    party = cell.find("h6")
    marker = party.find("span") if party else None
    return (_clean(marker).lstrip("- ").strip() or None) if marker else None


def parse_expedientes(html):
    """Rows of the tab as dicts. Dates are ISO (``YYYY-MM-DD``); ``limite`` is the PJe deadline.

    Most portals head each row with "Tipo (expediente id) - Prioridade: X - ID do documento (id)". TJPA prints only
    "Tipo (expediente id)", and TJPB shows old citações sent to the Domicílio Eletrônico with no header at all. The
    act's document is then the one the row links to (its signature check and, when the user may open it, "Visualizar
    ato"); a row without header has ``expedienteId`` None. A row with neither an expediente id nor a document id is
    left out, so ``tab_complete`` reports the tab as incomplete and reads stay refused.
    """
    table = BeautifulSoup(html, "html.parser").find(id=TABLE_ID)
    if table is None:
        return []
    items = []
    for row in table.find_all("tr", recursive=False):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 2:
            continue
        for script in row.find_all("script"):
            script.decompose()
        text = _clean(cells[0])
        header_text = _header_text(cells[0])
        header = HEADER.match(header_text)
        ato = row.find("a", href=ATO_HREF)
        document_id = _act_document(row, header_text, ato)
        if not header and not document_id:
            continue
        issued = ISSUED.search(text)
        notice = NOTICE.search(text)
        prazo = PRAZO.search(text)
        party = cells[0].find("h6")
        limit_text = _clean(cells[1])
        limit = LIMIT.search(limit_text)
        action_text = _clean(cells[2]) if len(cells) > 2 else ""
        kind = re.search(r"\(para (manifestação|ciência)(?: expressa)?\)", limit_text)
        response = row.find("a", href=RESPONSE_HREF)
        closed = _clean(cells[-1]) if len(cells) > 3 else ""
        items.append(
            {
                "expedienteId": header.group("id") if header else None,
                "tipo": header.group("tipo").strip() if header else _row_type(cells[0]),
                "documentId": document_id,
                "parte": _clean(party) if party else None,
                "meio": issued.group(1) if issued else None,
                "expedidoEm": _date(issued.group(2)) if issued else None,
                "ciencia": {"por": notice.group("quem").split(")")[-1].strip(), "em": _date(notice.group("quando"))}
                if notice
                else None,
                "prazoDias": int(prazo.group(1)) if prazo and prazo.group(1) else None,
                "semPrazo": bool(prazo and not prazo.group(1)),
                "limite": _date(limit.group(1)) if limit else None,
                "limiteTipo": kind.group(1) if kind else None,
                "respondido": "Resposta" in action_text,
                "fechado": {"SIM": True, "NÃO": False}.get(closed.upper()),
                "atoPath": ato["href"] if ato else None,
                "respostaPath": response["href"] if response else None,
            }
        )
    return items


def tab_complete(html, items):
    """Whether ``items`` is provably every communication of the process, as far as this response can tell.

    False when the table is missing, when a row could not be parsed, or when the response carries a RichFaces
    datascroller with further pages (only the first page is ever requested). The ciência check then cannot vouch
    for the process, so document reads are refused instead of assumed safe.
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find(id=TABLE_ID)
    if table is None:
        return False
    rows = [row for row in table.find_all("tr", recursive=False) if len(row.find_all("td", recursive=False)) >= 2]
    if len(rows) != len(items):
        return False
    return not soup.find(class_=SCROLLER_OTHER_PAGE)


def open_deadlines(items, today=None):
    """Communications with a deadline that has not passed and no response filed."""
    today = (today or date.today()).isoformat()
    return [item for item in items if item["limite"] and item["limite"] >= today and not item["respondido"]]


def tab_request(detail_html):
    """The AJAX form body that opens the Expedientes tab, as the browser sends it."""
    fields = jsf.form_fields(detail_html, "navbar")
    state, _ = jsf.page_state(detail_html, "PJe process page")
    return {
        "AJAXREQUEST": "_viewRoot",
        **fields,
        "javax.faces.ViewState": state,
        TAB_CONTROL: TAB_CONTROL,
        "AJAX:EVENTS_COUNT": "1",
    }


def fetch_expedientes(session, detail_html, detail_url):
    """(rows of the tab, whether they are provably all of them); see ``tab_complete``."""
    response = jsf.post(session, detail_url, tab_request(detail_html), label="PJe process expedientes")
    html = response_html(response)
    items = parse_expedientes(html)
    return items, tab_complete(html, items)


def ato_item(items, expediente_id):
    for item in items:
        if item["expedienteId"] == str(expediente_id):
            return item
    raise ValueError(f"expediente {expediente_id} is not in this process")


def parse_ato(html, text_limit=20000):
    """The act behind one communication: its box, the document text and the attached documents."""
    soup = BeautifulSoup(html, "html.parser")
    for node in soup(["script", "style"]):
        node.decompose()
    text = _clean(soup)
    start = text.find("Ícone de expedientes")
    body = text[start:] if start >= 0 else text
    box = EXPEDIENTE_BOX.search(body)
    return {
        "expediente": {
            "expedienteId": box.group("id"),
            "destinatario": box.group("parte"),
            "prazo": box.group("prazo"),
            "limite": _date(box.group("limite")) if box.group("limite") else None,
            "fechado": box.group("fechado") == "SIM",
        }
        if box
        else None,
        "anexos": [
            {"documentId": m.group(1), "titulo": m.group(2), "juntadoPor": m.group(3), "juntadoEm": _date(m.group(4))}
            for m in ATTACHMENT.finditer(body)
        ],
        "texto": body[:text_limit],
        "truncado": len(body) > text_limit,
    }


class PendingNotice(ValueError):
    """Opening this would register the party's ciência, which starts the deadline."""


class NoticeUnchecked(PendingNotice):
    """Whether a ciência is pending could not be determined, so the read is refused."""


def _siblings(timeline):
    """document id -> ids listed in the same timeline entry (an act and the documents attached to it)."""
    groups = {}
    for day in timeline:
        for movement in day.get("movements", []):
            ids = {str(document.get("id")) for document in movement.get("documents", [])}
            for identifier in ids:
                groups.setdefault(identifier, set()).update(ids)
    return groups


def pending_notice(items, document_ids, timeline=None):
    """Communications without ciência that a read of ``document_ids`` could acknowledge.

    That is every pending communication whose act is one of the documents, and also every one whose act cannot be
    told apart from them: a pending communication with no document id (any document may be its act). With the
    ``timeline`` it also covers the documents listed in the same timeline entry as a pending act (its
    attachments), and a requested document the loaded timeline does not show (it cannot be placed, so it may be an
    attachment of any pending act).
    """
    wanted = {str(i) for i in document_ids}
    pending = [item for item in items if not item.get("ciencia")]
    if timeline is None:
        return [item for item in pending if not item.get("documentId") or item["documentId"] in wanted]
    groups = _siblings(timeline)
    if wanted - set(groups):
        return pending
    related = set().union(*(groups[i] for i in wanted))
    return [item for item in pending if not item.get("documentId") or item["documentId"] in related]


UNCHECKED = {
    "unavailable": "the process' Expedientes list could not be read",
    "incomplete": "the process' Expedientes list could not be read in full",
}


def unchecked_message(reason):
    return (
        f"ciência not checked: {UNCHECKED[reason]}, so it is unknown whether this document belongs to a communication "
        "whose ciência is still pending. Nothing was opened. Try again later (pje_get_process shows the expedientes "
        "when they can be read). Opening it anyway may register the party's ciência and start the deadline. "
        f"{ciencia.AUTHORIZATION}."
    )


AVISOS = {
    ciencia.GIVEN: "Ler este conteúdo pode ter registrado a ciência da parte e iniciado o prazo; informe o usuário.",
    ciencia.PENDING: (
        "A ciência desta comunicação está pendente e o ato não foi aberto: abri-lo registra a ciência da parte e "
        "inicia o prazo. Só com autorização do usuário, dada agora ou antes (por exemplo, na instrução de uma rotina "
        "agendada): acknowledgeCiencia=true."
    ),
    ciencia.UNKNOWN: (
        "Não foi possível verificar se havia ciência pendente: ler este conteúdo pode ter registrado a ciência da "
        "parte e iniciado o prazo; informe o usuário."
    ),
}


def ciencia_report(state, items=()):
    """The ``ciencia`` block of a read (``core.ciencia``); ``items`` are the communications whose ciência was pending."""
    return ciencia.report(
        state,
        AVISOS.get(state, ""),
        [{k: item.get(k) for k in ("expedienteId", "tipo", "parte", "prazoDias", "limite")} for item in items],
    )


def ato_ciencia(item, opened):
    """The ``ciencia`` block of an act request: ``item`` is its communication (None when not found)."""
    if item is None or item.get("ciencia"):
        return ciencia_report(ciencia.NONE)
    return ciencia_report(ciencia.GIVEN if opened else ciencia.PENDING, [item])


def pending_message(items):
    shown = ", ".join(
        f"{item.get('expedienteId') or 'sem id, documento ' + str(item.get('documentId'))} "
        f"({item.get('tipo')}, {item.get('parte')}, "
        f"{item['prazoDias'] if item.get('prazoDias') is not None else 'sem'} dias)"
        for item in items[:5]
    )
    unlinked = (
        " At least one of them has no document id, so any document of this process may be its act."
        if any(not item.get("documentId") for item in items)
        else ""
    )
    return (
        "ciência not yet registered for: "
        + shown
        + "."
        + unlinked
        + " Opening the act, its document or an attachment may register the party's ciência and start the deadline. "
        + ciencia.AUTHORIZATION
        + "."
    )


def fetch_ato(session, item, detail_url, text_limit=20000, *, allow_pending=False):
    """Open the act of one communication. Refused while PJe has no notice, unless allowed.

    Opening an act whose notice is still pending can make the party count as notified, which
    starts its deadline. Only communications that already show a notice date are opened unless
    the caller passes ``allow_pending`` after the user agreed to it.
    """
    path = item.get("atoPath")
    if not path:
        raise ValueError("this expediente has no readable act link")
    if not item.get("ciencia") and not allow_pending:
        raise PendingNotice(pending_message([item]))
    response = jsf.get(session, base_url(detail_url) + path, label="PJe expediente act", referer=detail_url)
    return parse_ato(response_html(response), text_limit)


def base_url(detail_url):
    parts = urlsplit(detail_url)
    return f"{parts.scheme}://{parts.netloc}"
