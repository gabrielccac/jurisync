"""Árvore de documentos (``arvore_documento_listar``): every document of the case, listed without opening any.

The timeline of eproc_get_process is read up to ``client.MAX_EVENT_PAGES`` pages, so the documents of older events may be
missing from its inventory. The "Árvore" button of the process page lists every document of the case. Its page was not
captured (JURI-21, 27/11/2026): the parser reads the most likely shapes, the infra links to acessar_documento with
``doc=`` and ``evento=`` either as anchors or inside the InfraArvore script, and must be confirmed on a live portal.

Reading the tree requests only ``arvore_documento_listar``. A document found there is opened only through
``documents.read_selected_documents``, after the ciência check of ``client.fetch_process``, like any other.
"""

import html as html_lib
import re
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup

from jurisync import portals
from jurisync.connectors.eproc.parsers import process_links, text
from jurisync.core.responses import iso
from jurisync.core.session_pool import SessionExpired

TREE_ACTION = "arvore_documento_listar"
DOCUMENT_ACTION = "acessar_documento"
INVENTORY_MODES = ("auto", "timeline", "tree")
INVENTORY_PAGE = 150  # inventory entries per answer, within core.responses.BUDGET next to the timeline
_DATE = re.compile(r"\d{2}/\d{2}/\d{4}(?: \d{2}:\d{2}(?::\d{2})?)?")
_EVENT_DATE = re.compile(r"evento\s*:?\s*(\d+)\D{0,20}?(\d{2}/\d{2}/\d{4}(?: \d{2}:\d{2}(?::\d{2})?)?)", re.I)
_STRING = re.compile(r"\"((?:[^\"\\]|\\.)*)\"|'((?:[^'\\]|\\.)*)'")
_NOT_LABEL = re.compile(r"controlador|\.(?:gif|png|jpe?g|svg)\b|^_|^ifr|^\d*$", re.I)


def _query(url):
    return parse_qs(urlsplit(url).query, keep_blank_values=True)


def tree_url(detail_html, base_url, process_number):
    """The process page's "Árvore" link (``parsers.process_links``), or None when the page has none for this process."""
    url = process_links(detail_html, base_url).get("tree")
    if url is None:
        return None
    shown = re.sub(r"\D", "", (_query(url).get("txtNumProcesso") or [""])[0])
    return url if shown == re.sub(r"\D", "", process_number) else None


def _event_dates(html):
    """Event number -> date shown next to it in the tree ("Evento 12 - 03/11/2026 10:00:00 - ...")."""
    plain = html_lib.unescape(re.sub(r"<[^>]+>", " ", html))
    dates = {}
    for event, date in _EVENT_DATE.findall(" ".join(plain.split())):
        dates.setdefault(event, date)
    return dates


def _entry(url, title, description, own_text, dates):
    query = _query(url)
    identifier = (query.get("doc") or [None])[0]
    event = (query.get("evento") or [None])[0]
    date = _DATE.search(own_text or "")
    return {
        "id": identifier,
        "eventId": event,
        "title": title,
        "description": description if description and description != title else None,
        "date": iso(date.group(0) if date else dates.get(event)),
        "url": url,
    }


def _anchors(soup, base_url, dates):
    for anchor in soup.find_all(href=True):
        url = urljoin(base_url, html_lib.unescape(anchor["href"]))
        if (_query(url).get("acao") or [None])[0] != DOCUMENT_ACTION:
            continue
        label = text(anchor)
        tooltip = " ".join((anchor.get("title") or "").split())
        container = anchor.find_parent(["li", "tr"])
        own = " ".join(filter(None, (label, tooltip, text(container) if container else "")))
        yield _entry(url, label or tooltip, tooltip, own, dates)


def _script_nodes(soup, base_url, dates):
    """InfraArvore nodes built in a script, one per line: the link, then its tooltip and label."""
    for script in soup.find_all("script"):
        for line in (script.string or "").splitlines():
            if "acao=" + DOCUMENT_ACTION not in line:
                continue
            values = [html_lib.unescape(first or second) for first, second in _STRING.findall(line)]
            link = next((value for value in values if "acao=" + DOCUMENT_ACTION in value), None)
            if link is None:
                continue
            url = urljoin(base_url, link)
            if (_query(url).get("acao") or [None])[0] != DOCUMENT_ACTION:
                continue
            after = values[values.index(link) + 1 :]
            labels = [" ".join(value.split()) for value in after if value.strip() and not _NOT_LABEL.search(value)]
            title = labels[-1] if labels else ""
            yield _entry(url, title, labels[0] if len(labels) > 1 else None, " ".join(labels), dates)


def parse_tree(html, base_url):
    """The documents the tree page lists, first appearance per id; links to other hosts or actions are ignored."""
    soup = BeautifulSoup(html or "", "html.parser")
    dates = _event_dates(html or "")
    host = urlsplit(base_url).hostname
    found = {}
    for entry in [*_anchors(soup, base_url, dates), *_script_nodes(soup, base_url, dates)]:
        if entry["id"] and urlsplit(entry["url"]).hostname == host:
            found.setdefault(entry["id"], entry)
    return list(found.values())


def check(mode, offset):
    """Refuse bad inventory arguments before anything is requested."""
    if mode not in INVENTORY_MODES:
        raise ValueError(f"inventory must be one of {', '.join(INVENTORY_MODES)}")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValueError("inventory_offset must be a non-negative integer")


def wanted(mode, *, timeline_complete, known, document_ids):
    """Whether this read lists the tree: asked for, or the timeline cannot answer for the whole case."""
    if mode != "auto":
        return mode == "tree"
    return not timeline_complete or any(identifier not in known for identifier in document_ids or [])


def read_tree(session, detail_html, detail_url, process_number, *, portal):
    """``(documents, problem)``: the tree of the process page just read, or why it could not be listed."""
    from jurisync.connectors.eproc.client import _response_error, request, require_route  # the client imports this

    url = tree_url(detail_html, detail_url, process_number)
    if url is None:
        return [], "document_tree_unavailable"
    require_route(url, {TREE_ACTION}, portal=portal)
    try:
        response, body = request(session, "GET", url, portal=portal)
    except RuntimeError:
        return [], "document_tree_failed"
    error = _response_error(response, body)
    if error == "session_expired":
        raise SessionExpired("eproc session expired")  # nothing was opened yet: the read can be repeated
    if error:
        return [], "document_tree_failed"
    documents = parse_tree(body, portals.eproc(portal).base_url)
    return documents, None if documents else "document_tree_empty"


def inventory_page(documents, offset, source):
    """The slice of the inventory this answer shows, with the list envelope's counts and the arguments for the rest."""
    shown = documents[offset : offset + INVENTORY_PAGE]
    end = offset + len(shown)
    more = None
    if end < len(documents):
        more = {"inventoryOffset": end, **({"inventory": "tree"} if source == "tree" else {})}
    return shown, {"returned": len(shown), "total": len(documents), "next": more}
