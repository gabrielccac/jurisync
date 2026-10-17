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

TABLE_ID = "processoParteExpedienteMenuGridList:tb"
TAB_CONTROL = "navbar:linkAbaExpedientes1"

HEADER = re.compile(r"^(?P<tipo>.+?)\s*\((?P<id>\d+)\)\s*-\s*Prioridade:\s*(?P<prioridade>\S+)", re.S)
DOCUMENT = re.compile(r"ID do documento\s*\((\d+)\)")
ISSUED = re.compile(
    r"(Expedição eletrônica|Diário Eletrônico|Edital|Correio|Mandado|Central de Mandados)[^()]*\((\d{2}/\d{2}/\d{4}(?: \d{2}:\d{2}:\d{2})?)\)"
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


def _clean(node):
    return " ".join(node.get_text(" ", strip=True).replace("\xa0", " ").split())


def _date(value):
    return datetime.strptime(value[:10], "%d/%m/%Y").date().isoformat()


def parse_expedientes(html):
    """Rows of the tab as dicts. Dates are ISO (``YYYY-MM-DD``); ``limite`` is the PJe deadline."""
    table = BeautifulSoup(html, "html.parser").find(id=TABLE_ID)
    if table is None:
        return []
    items = []
    for row in table.find_all("tr", recursive=False):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 2:
            continue
        for script in cells[0].find_all("script"):
            script.decompose()
        text = _clean(cells[0])
        header = HEADER.match(text)
        if not header:
            continue
        document = DOCUMENT.search(text)
        issued = ISSUED.search(text)
        notice = NOTICE.search(text)
        prazo = PRAZO.search(text)
        party = cells[0].find("h6")
        limit_text = _clean(cells[1])
        limit = LIMIT.search(limit_text)
        action_text = _clean(cells[2]) if len(cells) > 2 else ""
        kind = re.search(r"\(para (manifestação|ciência)\)", limit_text)
        ato = cells[0].find("a", href=ATO_HREF)
        response = row.find("a", href=RESPONSE_HREF)
        closed = _clean(cells[-1]) if len(cells) > 3 else ""
        items.append(
            {
                "expedienteId": header.group("id"),
                "tipo": header.group("tipo").strip(),
                "documentId": document.group(1) if document else None,
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
    response = jsf.post(session, detail_url, tab_request(detail_html), label="PJe process expedientes")
    return parse_expedientes(response_html(response))


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


def pending_notice(items, document_ids):
    """Communications whose act is one of ``document_ids`` and whose ciência is not registered yet."""
    wanted = {str(i) for i in document_ids}
    return [item for item in items if item.get("documentId") in wanted and not item.get("ciencia")]


def ciencia_report(items):
    """What a read may have done: the communications whose ciência was pending when it was read."""
    return {
        "pendenteAoLer": [
            {k: item.get(k) for k in ("expedienteId", "tipo", "parte", "prazoDias", "limite")} for item in items
        ],
        "aviso": "Ler este conteúdo pode ter registrado a ciência da parte e iniciado o prazo; informe o usuário.",
    }


def pending_message(items):
    shown = ", ".join(
        f"{item.get('expedienteId')} ({item.get('tipo')}, {item.get('parte')}, "
        f"{item['prazoDias'] if item.get('prazoDias') is not None else 'sem'} dias)"
        for item in items[:5]
    )
    return (
        "ciência not yet registered for: "
        + shown
        + ". Opening the act or its document may register the party's ciência and start the deadline. "
        "Ask the user for explicit permission; if they agree, repeat the call with acknowledgeCiencia=true."
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
    ato = parse_ato(response_html(response), text_limit)
    if not item.get("ciencia"):
        ato["ciencia"] = ciencia_report([item])
    return ato


def base_url(detail_url):
    parts = urlsplit(detail_url)
    return f"{parts.scheme}://{parts.netloc}"
