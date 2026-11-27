"""Read-only Eproc lists beyond the citação/intimação queues (JURI-21, item 7).

Each reader takes the link the authenticated panel advertises in its menu, reads the list with the same GET (or the
list's own filter form, posted the way the page does) and parses the table. No row link, button or form that acts is
ever requested: opening a message (``caixa_mensagem_vizualizar``) may mark it read, and Distribuir, Peticionar or a
guia's generation write to the portal. Only the actions below enter the allowlist (``client.READ_ACTIONS``).
"""

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from jurisync import portals
from jurisync.connectors.eproc.client import _response_error, request, require_route
from jurisync.connectors.eproc.parsers import _PROCESS_NUMBER, form_fields, parse_panel, text
from jurisync.core.errors import NotSupported, PortalError
from jurisync.core.redaction import mask_cpf
from jurisync.core.responses import BUDGET, compact, dated, listing
from jurisync.core.session_pool import SessionExpired

MESSAGES_ACTION = "caixa_mensagem_listar"
# The tool's status argument -> the value of the page's rdoFiltro radio.
MESSAGE_FILTERS = {"unread": "naoLidas", "read": "lidas"}
_COUNT = re.compile(r"\((\d+)\s+registros?\b", re.I)


def _headers(table):
    return [text(cell) for cell in table.find_all("th") if cell.find_parent("table") is table]


def _rows(table):
    return [row for row in table.select("tr.infraTrClara, tr.infraTrEscura") if row.find_parent("table") is table]


def _caption_total(table):
    found = _COUNT.search(text(table.find("caption")))
    return int(found[1]) if found else None


def _link(action, *, panel_html, panel_url, portal, label):
    """The menu link to ``action``; a portal without it does not offer the list (not a failure of the read)."""
    base_url = portals.eproc(portal).base_url
    link = parse_panel(panel_html, base_url)["actions"].get(action)
    if not link:
        raise NotSupported(
            f"not available: this portal does not offer {label} (its menu has no {action})",
            code="not_available",
            next_step="não repita neste portal: o menu dele não oferece esta lista. Avise o usuário se ele esperava vê-la.",
        )
    return urljoin(panel_url or base_url, link)


def _read(session, method, url, action, *, portal, data=None):
    """The page of ``action`` (and only of it); a login page asks the pool for a new session, any other refusal fails."""
    require_route(url, {action}, portal=portal)
    response, body = request(session, method, url, data=data, portal=portal)
    error = _response_error(response, body)
    if error == "session_expired":
        raise SessionExpired("eproc session expired")  # nothing was changed: the read can be repeated
    if error:
        raise PortalError(f"the portal answered {error}")
    return body


def _fit(items, offset):
    """``items[offset:]`` that fit the answer budget (at least one), and the offset of the rest or None."""
    shown, size = [], 0
    for item in items[offset:]:
        size += len(compact(item)) + 1
        if shown and size > BUDGET - 5_000:
            break
        shown.append(item)
    end = offset + len(shown)
    return shown, end if end < len(items) else None


def parse_messages(html):
    """Caixa de Mensagens: ``{"filter", "items", "total"}``, or None when the page is not the message list.

    The page lists the unread messages (rdoFiltro=naoLidas) unless its form was posted with "lidas". Without messages
    it shows the filter and no table. Only the list is read: the title links to caixa_mensagem_vizualizar, which is
    never followed.
    """
    soup = BeautifulSoup(html, "html.parser")
    form = soup.select_one("form#frmMensagemLista")
    if form is None:
        return None
    checked = form.select_one('input[name="rdoFiltro"][checked]')
    table = soup.select_one("table.infraTable")
    if table is None:
        return {"filter": checked.get("value") if checked else None, "items": [], "total": 0}
    if _headers(table) != ["Data/Hora", "Título", "Mensagem"]:
        return None
    items = []
    for row in _rows(table):
        cells = row.find_all("td", recursive=False)
        if len(cells) != 3:
            return None
        items.append(dated({"sentAt": text(cells[0]), "title": text(cells[1]), "text": text(cells[2])}, "sentAt"))
    total = _caption_total(table)
    return {
        "filter": checked.get("value") if checked else None,
        "items": items,
        "total": total if total is not None and total >= len(items) else len(items),
    }


def list_messages(status="unread", offset=0, *, session, panel_html, panel_url, portal):
    """The lawyer's Caixa de Mensagens, unread (the page's default) or read, without opening any message."""
    portal = portals.eproc(portal).key
    if status not in MESSAGE_FILTERS:
        raise ValueError(f"status must be one of {', '.join(MESSAGE_FILTERS)}")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValueError("offset must be a non-negative integer")
    url = _link(MESSAGES_ACTION, panel_html=panel_html, panel_url=panel_url, portal=portal, label="Caixa de Mensagens")
    body = _read(session, "GET", url, MESSAGES_ACTION, portal=portal)
    if status == "read":
        # The radio's own onclick posts the list form with rdoFiltro=lidas; the same fields go here.
        form = BeautifulSoup(body, "html.parser").select_one("form#frmMensagemLista[action]")
        if form is None:
            raise PortalError("unsupported message list layout")
        fields = [(name, value) for name, value in form_fields(form) if name != "rdoFiltro"]
        fields.append(("rdoFiltro", MESSAGE_FILTERS["read"]))
        body = _read(session, "POST", urljoin(url, form["action"]), MESSAGES_ACTION, portal=portal, data=fields)
    parsed = parse_messages(body)
    if parsed is None:
        raise PortalError("unsupported message list layout")
    if parsed["filter"] != MESSAGE_FILTERS[status]:
        raise PortalError(f"the portal did not show the {status} messages")
    shown, rest = _fit(parsed["items"], offset)
    return {
        "success": True,
        "source": "eproc",
        "portal": portal,
        "status": status,
        **listing(
            shown,
            key="mensagens",
            total=parsed["total"],
            more=None if rest is None else {"status": status, "offset": rest},
        ),
    }


PARADAS_ACTION = "eprocsysadm_listar"
_PARADA_COLUMNS = ("descrição motivo evento", "houve prorrogação de prazo", "data inicio", "data fim")
_SUSPENSAO_COLUMNS = ("data suspensão", "tipo suspensão")
_YES_NO = {"sim": True, "não": False, "nao": False}


def _column_index(table, wanted):
    """The positions of the ``wanted`` headers (lowercase, without the trailing "?" or "."), or None if one is missing."""
    headers = [header.casefold().rstrip("?.: ") for header in _headers(table)]
    return [headers.index(name) for name in wanted] if all(name in headers for name in wanted) else None


def parse_paradas(html):
    """Relatório de Paradas: ``{"paradas", "suspensoes", "total"}``, or None when the page is not that report.

    The first table lists the system outages (reason, whether deadlines were extended, start and end); the second,
    under "Lista de suspensões cadastradas", the dates deadlines were suspended. A portal without records shows the
    form and no table (TJRJ).
    """
    soup = BeautifulSoup(html, "html.parser")
    form = soup.select_one("form#frmEprocsysadmLista")
    if form is None:
        return None
    paradas, suspensoes = [], []
    for table in form.select("table.infraTable"):
        outage = _column_index(table, _PARADA_COLUMNS)
        suspension = _column_index(table, _SUSPENSAO_COLUMNS)
        if outage is None and suspension is None:
            return None
        for row in _rows(table):
            cells = [text(cell) for cell in row.find_all("td", recursive=False)]
            if len(cells) <= max(outage or suspension):
                return None
            if outage is not None:
                reason, extended, start, end = (cells[index] for index in outage)
                paradas.append(
                    dated(
                        {
                            "description": reason,
                            "prorrogacao": _YES_NO.get(extended.casefold()),
                            "start": start,
                            "end": end,
                        },
                        "start",
                        "end",
                    )
                )
            else:
                date, kind = (cells[index] for index in suspension)
                suspensoes.append(dated({"date": date, "type": kind}, "date"))
    counter = form.select_one('input[name="hdnInfraNroItens"]')
    total = int(counter["value"]) if counter and str(counter.get("value", "")).isdigit() else None
    return {
        "paradas": paradas,
        "suspensoes": suspensoes,
        "total": total if total and total >= len(paradas) else len(paradas),
    }


def list_paradas(*, session, panel_html, panel_url, portal):
    """The portal's Relatório de Paradas: outages (with or without prorrogação de prazo) and suspensões de prazo."""
    portal = portals.eproc(portal).key
    url = _link(PARADAS_ACTION, panel_html=panel_html, panel_url=panel_url, portal=portal, label="Paradas do Sistema")
    parsed = parse_paradas(_read(session, "GET", url, PARADAS_ACTION, portal=portal))
    if parsed is None:
        raise PortalError("unsupported Relatório de Paradas layout")
    return {
        "success": True,
        "source": "eproc",
        "portal": portal,
        **listing(parsed["paradas"], key="paradas", total=parsed["total"], suspensoes=parsed["suspensoes"]),
    }


# The tool's kind argument -> (list action, its form, the page's title).
PREPARED = {
    "iniciais": (
        "peticao_inicial_preparada_listar",
        "frmPeticaoInicial",
        "Petições Iniciais pendentes de distribuição",
    ),
    "movimentacoes": ("movimentacao_preparada_listar", "frmMovimentacaoFutura", "Movimentações pendentes"),
}
_ACTION_COLUMNS = {"", "ações", "ação"}


def parse_header_table(html, form):
    """``{"items", "total"}`` of the page's list table read by its headers, or None without the list's ``form`` (CSS).

    For lists whose rows were not seen in any capture: each row keeps its cells under their header text (dates as
    ISO, CPFs masked), without the checkbox and action columns, plus the first CNJ number found as ``processNumber``.
    A page with the form and no table is an empty list.
    """
    soup = BeautifulSoup(html, "html.parser")
    if soup.select_one(form) is None:
        return None
    table = soup.select_one("table.infraTable")
    if table is None:
        return {"items": [], "total": 0}
    headers = _headers(table)
    items = []
    for row in _rows(table):
        values = [text(cell) for cell in row.find_all("td", recursive=False)]
        found = next((match for value in values if (match := _PROCESS_NUMBER.search(value))), None)
        columns = {
            header: mask_cpf(value)
            for header, value in zip(headers, values, strict=False)
            if header.casefold() not in _ACTION_COLUMNS
        }
        items.append({"processNumber": found[0] if found else None, "columns": dated(columns, *columns)})
    total = _caption_total(table)
    return {"items": items, "total": total if total is not None and total >= len(items) else len(items)}


def list_prepared(kind="iniciais", *, session, panel_html, panel_url, portal):
    """Petições iniciais or movimentações the lawyer prepared and has not filed yet; never Distribuir/Peticionar."""
    portal = portals.eproc(portal).key
    if kind not in PREPARED:
        raise ValueError(f"kind must be one of {', '.join(PREPARED)}")
    action, form_id, label = PREPARED[kind]
    url = _link(action, panel_html=panel_html, panel_url=panel_url, portal=portal, label=label)
    parsed = parse_header_table(_read(session, "GET", url, action, portal=portal), f"form#{form_id}")
    if parsed is None:
        raise PortalError(f"unsupported {label} layout")
    return {
        "success": True,
        "source": "eproc",
        "portal": portal,
        "kind": kind,
        **listing(parsed["items"], key="peticoes", total=parsed["total"]),
    }


# The tool's kind argument -> (list action, what the menu calls it). TRF4 and TRF6 offer both (menus "Custas" and
# "Depósitos Judiciais", item "Guias geradas pelo usuário"); "Consultar/gerar guias" and the per-process pages are not
# read, since they also generate guias.
CUSTAS = {
    "custas": ("guia_pagamento_custas_gerados_listar", "guias de custas geradas pelo usuário"),
    "depositos": (
        "guia_pagamento_depositos_judiciais_gerados_listar",
        "guias de depósito judicial geradas pelo usuário",
    ),
}


def list_custas(kind="custas", *, session, panel_html, panel_url, portal):
    """Guias de custas or de depósito judicial the user already generated; nothing is generated."""
    portal = portals.eproc(portal).key
    if kind not in CUSTAS:
        raise ValueError(f"kind must be one of {', '.join(CUSTAS)}")
    action, label = CUSTAS[kind]
    url = _link(action, panel_html=panel_html, panel_url=panel_url, portal=portal, label=label)
    # No capture shows this page: the infra list form posts to its own action.
    parsed = parse_header_table(_read(session, "GET", url, action, portal=portal), f'form[action*="acao={action}"]')
    if parsed is None:
        raise PortalError(f"unsupported layout of the {label}")
    return {
        "success": True,
        "source": "eproc",
        "portal": portal,
        "kind": kind,
        **listing(parsed["items"], key="guias", total=parsed["total"]),
    }
