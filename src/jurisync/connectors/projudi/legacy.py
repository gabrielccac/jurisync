"""Read machinery shared by the legacy Projudi generations that were prepared without a live session (TJPR, TJRR, TJAM).

These generations are JSP/servlet frontends with a frameset and a per-session ``_tj`` token on every link. Nothing here
was validated against an authenticated session: the menu, search form and result layout are HYPOTHESES (see the
per-court reports in ``docs/connectors/``), exercised only with synthetic pages. Each generation adds its own login.

Rules every subclass inherits:
- a link is followed only on the portal host and never when its text or address cites, intimates, registers ciência,
  files, signs or changes something; the ``_tj`` token is taken from the page, never built by hand;
- a CAPTCHA, WAF challenge or human-verification page stops the read (``HumanVerificationRequired``): never solved;
- documents stay blocked (``DocumentsBlocked``): opening a Projudi document can register ciência and nothing here
  proves otherwise.
"""

import re
import urllib.parse

from bs4 import BeautifulSoup

from jurisync import portals
from jurisync.connectors.projudi import base, parsers
from jurisync.core.http import REDIRECTS

SESSION_EXPIRED_MARKERS = ("a sessao expirou", "a sessão expirou")
INVALID_LINK_MARKER = "conteúdo inacessível"
HUMAN_VERIFICATION = re.compile(
    r"captcha|human verification|verificação humana|confirmar que você é humano|request rejected|what code is in the image",
    re.I,
)
# Never follow a link that cites, intimates, registers ciência, files, signs or changes something.
FORBIDDEN_LINK = re.compile(
    r"ci[eê]ncia|intima|cita[cç]|peticion|protocol|assin|confirm|aceit|excluir|remover|alterar|cadastr|senha|perfil",
    re.I,
)
COMMENT = re.compile(r"<!--.*?-->", re.S)
PROCESS_FIELD = re.compile(r"numero|processo", re.I)
DATE = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")
MAX_FRAME_PAGES = 6


class MissingCredentials(RuntimeError):
    """The customer's Projudi login or password is not configured; nothing was sent to the portal."""


class HumanVerificationRequired(RuntimeError):
    """The portal asks for a CAPTCHA or human verification: a person must do it; the connector never solves it."""


class DocumentsBlocked(RuntimeError):
    """Reading a Projudi document is blocked until it is proven not to register ciência."""


def has_human_verification(html):
    """Whether a page asks for a CAPTCHA/WAF check. HTML comments are ignored: TJAM's login page keeps a commented-out
    reCAPTCHA block that is not a challenge."""
    return bool(HUMAN_VERIFICATION.search(COMMENT.sub("", html)))


def strip_path_params(url):
    """Projudi writes ``;jsessionid=...`` into paths; the session cookie carries it, so it is dropped."""
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit(parts._replace(path=parts.path.split(";", 1)[0]))


def soup(html):
    return BeautifulSoup(html, "html.parser")


def visible_text(page):
    return " ".join(page.stripped_strings)


def hidden_fields(form):
    return {
        field["name"]: field.get("value", "")
        for field in form.find_all("input", attrs={"type": "hidden"})
        if field.get("name")
    }


def page_links(html, page_url):
    """``(text, absolute url)`` of every followable anchor, with path parameters removed and forbidden ones dropped."""
    links = []
    for anchor in soup(html).find_all("a", href=True):
        href = anchor["href"].strip()
        text = " ".join(anchor.get_text(" ").split())
        if not href or href.startswith(("javascript:", "#", "mailto:")) or FORBIDDEN_LINK.search(f"{text} {href}"):
            continue
        links.append((text, strip_path_params(urllib.parse.urljoin(page_url, href))))
    return links


def search_form(html, page_url, number):
    """``(method, action, fields)`` of the form with a process-number text field, or ``None``."""
    for form in soup(html).find_all("form"):
        for field in form.find_all("input"):
            name = field.get("name") or ""
            if field.get("type", "text").lower() == "text" and PROCESS_FIELD.search(name):
                action = strip_path_params(urllib.parse.urljoin(page_url, form.get("action") or page_url))
                return (form.get("method") or "get").lower(), action, {**hidden_fields(form), name: number}
    return None


def parse_process(html, number):
    """The process number and its dated movement rows from a result page, or ``ValueError`` when it is not there.

    HYPOTHESIS: the real layout is unknown; this reads any table row with a date, and the tests use a synthetic page.
    """
    page = soup(html)
    if number not in visible_text(page):
        raise ValueError("Process not found or not accessible to this account")
    movements = []
    for row in page.find_all("tr"):
        cells = [" ".join(cell.get_text(" ").split()) for cell in row.find_all(["td", "th"])]
        dated = [cell for cell in cells if DATE.match(cell)]
        if dated and len(cells) >= 2:
            description = " | ".join(cell for cell in cells if cell and cell != dated[0])
            movements.append({"date": DATE.search(dated[0]).group(0), "description": description})
    return {"number": number, "movements": movements, "layout": "unvalidated"}


class LegacyAdapter(base.Adapter):
    """Shared reads; a subclass supplies ``login``, ``refresh`` and ``menu_labels``."""

    menu_labels = ("Busca Processual Simples",)

    def is_login_redirect(self, response):
        """Any redirect off the portal host (an SSO) or to a login entry means the session is gone."""
        if response.status_code not in REDIRECTS:
            return False
        own_host = (urllib.parse.urlsplit(response.url or "").hostname or "").lower()
        target = urllib.parse.urlsplit(urllib.parse.urljoin(response.url or "", response.headers.get("Location") or ""))
        path = target.path.rstrip("/").lower()
        off_host = bool(own_host) and (target.hostname or "").lower() not in ("", own_host)
        return off_host or path.endswith(("autenticacao.do", "logon.do", "sessionexpired"))

    def request(self, session, portal, method, path, **kwargs):
        response = super().request(session, portal, method, path, **kwargs)
        if response.status_code == 200:
            html = parsers.decode(response.content).casefold()
            if any(marker in html for marker in SESSION_EXPIRED_MARKERS):
                raise base.ProjudiSessionExpired("Projudi session expired")
            if INVALID_LINK_MARKER in html:
                raise base.ProjudiParseError("Projudi rejected a navigation link (_tj) for this session")
        return response

    def _get_page(self, session, portal, url):
        config = portals.projudi(portal)
        base.require_portal_url(config, url)
        response = self.request(session, portal, "GET", url[len(config.base_url) :])
        if response.status_code != 200:
            raise RuntimeError(f"Projudi page failed (HTTP {response.status_code})")
        return response.url or url, parsers.decode(response.content)

    def _frame_pages(self, session, portal):
        """The portal root and the frames it points at, as ``[(url, html)]``."""
        config = portals.projudi(portal)
        pages = [self._get_page(session, portal, config.base_url)]
        for frame in soup(pages[0][1]).find_all(["frame", "iframe"], src=True):
            if len(pages) >= MAX_FRAME_PAGES:
                break
            url = strip_path_params(urllib.parse.urljoin(pages[0][0], frame["src"]))
            if not url.startswith(config.base_url) or FORBIDDEN_LINK.search(url):
                continue
            pages.append(self._get_page(session, portal, url))
        return pages

    def _menu_page(self, session, portal, labels):
        config = portals.projudi(portal)
        for page_url, html in self._frame_pages(session, portal):
            for text, url in page_links(html, page_url):
                if url.startswith(config.base_url) and any(label.casefold() in text.casefold() for label in labels):
                    return self._get_page(session, portal, url)
        raise base.ProjudiParseError(f"Projudi menu has none of the links {', '.join(labels)} for this session")

    def open_process(self, session, portal, process_number):
        config = portals.projudi(portal)
        url, html = self._menu_page(session, portal, self.menu_labels)
        if has_human_verification(html):
            raise HumanVerificationRequired("precisa de uma pessoa: a busca do Projudi pediu verificação humana")
        form = search_form(html, url, process_number)
        if form is None:
            raise base.ProjudiParseError("Projudi search page has no process number field")
        method, action, fields = form
        base.require_portal_url(config, action)
        arguments = {"data": fields} if method == "post" else {"params": fields}
        response = self.request(session, portal, method.upper(), action[len(config.base_url) :], **arguments)
        if response.status_code != 200:
            raise RuntimeError(f"Projudi process search failed (HTTP {response.status_code})")
        result = parsers.decode(response.content)
        if not DATE.search(result):  # a result list: open the one entry that carries this number, once
            for text, link in page_links(result, response.url or url):
                if process_number in text and link.startswith(config.base_url):
                    result = self._get_page(session, portal, link)[1]
                    break
        return parse_process(result, process_number)

    # Documents: blocked until a live session proves that reading one does not register ciência. -----------------------

    def _blocked(self, *_args, **_kwargs):
        raise DocumentsBlocked("Projudi documents are blocked here: reading one may register ciência")

    fetch_index = movement_files = download_url = download = _blocked
