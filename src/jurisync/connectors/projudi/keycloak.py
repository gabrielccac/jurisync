"""Projudi "legacy-keycloak" generation, prepared for TJPR 1º grau (and its Turmas Recursais, same Projudi).

STATUS: built against public pages and simulated responses only. No authenticated session was ever opened, so the
portal stays ``wired=False`` in ``portals.py`` and this adapter is not registered in ``server.ADAPTERS``. What is
real (observed on public pages) and what is a hypothesis is listed in ``docs/connectors/projudi-tjpr-prep.md``.

Login: the Projudi page ``autenticacao.do`` redirects to a Keycloak (realm ``projudi`` at ``sso.projudi.tjpr.jus.br``)
that asks CPF/CNPJ + password (``form#kc-form-login``) and, when enforced, a TOTP (``form#kc-otp-login-form``). The
public page offers a certificate login only for A3 (hardware token), so there is no A1 path here.

Safety rules of this adapter:
- one credential POST and at most one TOTP POST per ``login``; no retry (a few wrong passwords lock the account) and
  ``SessionPool`` blocks further logins after ``LoginRejected``;
- only what a browser sends is posted: hidden fields, user, password, code. Never "remember me" / "trust device";
- a CAPTCHA or human-verification page stops the login (``HumanVerificationRequired``), never solved or bypassed;
- a Keycloak required action (set up the authenticator, update the password, accept terms) stops the login
  (``AccountActionRequired``); this adapter never registers a TOTP or changes the account;
- links are followed only on the portal host and its SSO host, and never when their text or address points at a
  citation, intimation, ciência, filing, signature or change;
- documents stay blocked (``DocumentsBlocked``): opening a Projudi document can register ciência and nothing here
  guarantees otherwise.
"""

import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from bs4 import BeautifulSoup
from curl_cffi import requests
from curl_cffi.requests.exceptions import RequestException

from jurisync import portals
from jurisync.connectors.projudi import base, parsers
from jurisync.core.http import REDIRECTS, TIMEOUT, follow_redirects, impersonate_for, log_request, require_url
from jurisync.core.session_pool import LoginRejected
from jurisync.core.totp import accounts_from_image, parse_accounts, portal_account, totp

USERNAME_PATTERN = re.compile(r"(\d{11}|[A-Z0-9]{12}\d{2})")
SESSION_EXPIRED_MARKERS = ("a sessao expirou", "a sessão expirou")
INVALID_LINK_MARKER = "conteúdo inacessível"
PUBLIC_HOME_MARKER = "acesso ao sistema"
HUMAN_VERIFICATION = re.compile(r"captcha|human verification|verificação humana|confirmar que você é humano", re.I)
ACCOUNT_ACTION_FORMS = ("kc-totp-settings-form", "kc-passwd-update-form", "kc-terms-form", "kc-update-profile-form")
ACCOUNT_ACTION_TEXT = re.compile(
    r"configura[cç][aã]o do autenticador|mobile authenticator setup|atualizar senha|update password|termos de uso",
    re.I,
)
# Never follow a link that cites, intimates, registers ciência, files, signs or changes something.
FORBIDDEN_LINK = re.compile(
    r"ci[eê]ncia|intima|cita[cç]|peticion|protocol|assin|confirm|aceit|excluir|remover|alterar|cadastr|senha|perfil",
    re.I,
)
PROCESS_FIELD = re.compile(r"numero|processo", re.I)
DATE = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")
MAX_FRAME_PAGES = 6
MENU_SEARCH = "Busca Processual Simples"


class MissingCredentials(RuntimeError):
    """The customer's Projudi login or password is not configured; nothing was sent to the portal."""


class HumanVerificationRequired(RuntimeError):
    """The portal asks for a CAPTCHA or human verification: a person must do it; the connector never solves it."""


class AccountActionRequired(RuntimeError):
    """Keycloak asks for an account action (authenticator setup, new password, terms) that this adapter never takes."""


class DocumentsBlocked(RuntimeError):
    """Reading a TJPR Projudi document is blocked until it is proven not to register ciência."""


@dataclass(frozen=True)
class Sso:
    host: str
    realm: str
    entry: str = "autenticacao.do"

    @property
    def path_prefix(self):
        return f"/auth/realms/{self.realm}/"


# One entry per portal: this adapter is configured, not copied. TJRR (realm projudi at keycloak.tjrr.jus.br) is not
# covered here: it needs its own validation first.
SSO = {"tjpr-projudi-1g": Sso("sso.projudi.tjpr.jus.br", "projudi")}


def sso_for(config):
    sso = SSO.get(config.key)
    if sso is None:
        raise NotImplementedError(f"Projudi {config.tribunal} ({config.key}) has no Keycloak configuration yet")
    return sso


def strip_path_params(url):
    """Projudi writes ``;jsessionid=...`` into paths; the session cookie carries it, so it is dropped."""
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit(parts._replace(path=parts.path.split(";", 1)[0]))


def login_credentials(config):
    """``(username, password)`` from the customer's configuration; raises before any request when one is missing."""
    username = re.sub(r"[.\-/\s]", "", config.env("USERNAME")).upper()
    password = config.env("PASSWORD")
    missing = [config.env_name(name) for name, value in (("USERNAME", username), ("PASSWORD", password)) if not value]
    if missing:
        raise MissingCredentials(
            f"precisa de login e senha da cliente: set {' and '.join(missing)} (Projudi {config.tribunal})"
        )
    if not USERNAME_PATTERN.fullmatch(username):
        raise ValueError(f"{config.env_name('USERNAME')} must be a CPF (11 digits) or a CNPJ")
    return username, password


def otp_account(config):
    data, qr_path = config.env("2FA_DATA"), config.env("2FA_QR")
    if not data and not qr_path:
        raise MissingCredentials(
            f"Projudi asks for a TOTP code: set {config.env_name('2FA_DATA')} or {config.env_name('2FA_QR')} "
            f"(and {config.env_name('OTP_PROFILE_MATCH')} when the export holds several profiles)"
        )
    return portal_account(config, parse_accounts(data) if data else accounts_from_image(Path(qr_path)))


# --- Pages ------------------------------------------------------------------------------------------------------------


def _soup(html):
    return BeautifulSoup(html, "html.parser")


def _text(soup):
    return " ".join(soup.stripped_strings)


def _hidden_fields(form):
    return {
        field["name"]: field.get("value", "")
        for field in form.find_all("input", attrs={"type": "hidden"})
        if field.get("name")
    }


def classify_sso_page(html):
    """What a Keycloak page is: ``login``, ``otp``, ``human``, ``action`` or ``other``."""
    soup = _soup(html)
    text = _text(soup)
    if HUMAN_VERIFICATION.search(html):
        return "human"
    if any(soup.find("form", id=form_id) for form_id in ACCOUNT_ACTION_FORMS) or ACCOUNT_ACTION_TEXT.search(text):
        return "action"
    if soup.find("form", id="kc-otp-login-form"):
        return "otp"
    if soup.find("form", id="kc-form-login"):
        return "login"
    return "other"


def is_logged_in_page(html):
    """An authenticated Projudi page: not the public "Acesso ao Sistema" home and carrying ``_tj`` navigation links.

    HYPOTHESIS from public pages (every internal link carries ``_tj``); confirm with a real session.
    """
    lowered = html.casefold()
    return PUBLIC_HOME_MARKER not in lowered and "_tj=" in html and "kc-form-login" not in lowered


def page_links(html, page_url):
    """``(text, absolute url)`` of every followable anchor, with path parameters removed and forbidden ones dropped."""
    links = []
    for anchor in _soup(html).find_all("a", href=True):
        href = anchor["href"].strip()
        text = " ".join(anchor.get_text(" ").split())
        if not href or href.startswith(("javascript:", "#", "mailto:")) or FORBIDDEN_LINK.search(f"{text} {href}"):
            continue
        links.append((text, strip_path_params(urllib.parse.urljoin(page_url, href))))
    return links


def search_form(html, page_url, number):
    """``(method, action, fields)`` of the form with a process-number text field, or ``None``."""
    for form in _soup(html).find_all("form"):
        for field in form.find_all("input"):
            name = field.get("name") or ""
            if field.get("type", "text").lower() == "text" and PROCESS_FIELD.search(name):
                action = strip_path_params(urllib.parse.urljoin(page_url, form.get("action") or page_url))
                return (form.get("method") or "get").lower(), action, {**_hidden_fields(form), name: number}
    return None


def parse_process(html, number):
    """The process number and its dated movement rows from a result page, or ``ValueError`` when it is not there.

    HYPOTHESIS: the real layout is unknown; this reads any table row with a date, and the tests use a synthetic page.
    """
    soup = _soup(html)
    if number not in _text(soup):
        raise ValueError("Process not found or not accessible to this account")
    movements = []
    for row in soup.find_all("tr"):
        cells = [" ".join(cell.get_text(" ").split()) for cell in row.find_all(["td", "th"])]
        dated = [cell for cell in cells if DATE.fullmatch(cell) or DATE.match(cell)]
        if dated and len(cells) >= 2:
            description = " | ".join(cell for cell in cells if cell and cell != dated[0])
            movements.append({"date": DATE.search(dated[0]).group(0), "description": description})
    return {"number": number, "movements": movements, "layout": "unvalidated"}


# --- Adapter ------------------------------------------------------------------------------------------------------------


class KeycloakAdapter(base.Adapter):
    flavor = "legacy-keycloak"

    def _hosts(self, config):
        return {config.host, sso_for(config).host}

    def _follow(self, session, config, response, *, cert=None):
        hosts = self._hosts(config)
        return follow_redirects(
            session,
            response,
            check=lambda url: require_url(url, hosts=hosts, label="Projudi login redirect"),
            rewrite=strip_path_params,
            cert=cert,
            impersonate=impersonate_for(config),
        )

    def _post(self, session, config, url, data):
        sso = sso_for(config)
        require_url(url, hosts={sso.host}, path_prefix=sso.path_prefix, label="Projudi SSO form")
        response = session.post(
            url, data=data, impersonate=impersonate_for(config), allow_redirects=False, timeout=TIMEOUT
        )
        log_request("POST", url, response.status_code)
        return self._follow(session, config, response)

    def _classify(self, config, response):
        """The state after an SSO step: ``app`` (back on Projudi) or the kind of SSO page; the unsafe ones raise."""
        host = (urllib.parse.urlsplit(response.url or "").hostname or "").lower()
        if host == config.host:
            return "app"
        if host != sso_for(config).host or response.status_code != 200:
            raise RuntimeError(f"Projudi login ended on an unexpected page (HTTP {response.status_code})")
        kind = classify_sso_page(response.text)
        if kind == "human":
            raise HumanVerificationRequired("precisa de uma pessoa: Projudi pediu verificação humana (CAPTCHA)")
        if kind == "action":
            raise AccountActionRequired(
                "Projudi pediu uma ação na conta (autenticador, senha ou termos); o conector não executa isso"
            )
        if kind == "other":
            raise RuntimeError("Projudi SSO returned a page the connector does not recognize")
        return kind

    def login(self, portal, *, session=None):
        config = portals.projudi(portal)
        sso = sso_for(config)
        username, password = login_credentials(config)  # nothing is requested before the credentials exist
        session = session or requests.Session()
        first = session.get(
            config.url(sso.entry), impersonate=impersonate_for(config), allow_redirects=False, timeout=TIMEOUT
        )
        page = self._follow(session, config, first)
        if self._classify(config, page) != "login":
            raise RuntimeError("Projudi did not open the SSO login form")
        form = _soup(page.text).find("form", id="kc-form-login")
        action = urllib.parse.urljoin(page.url, form.get("action") or "")
        # Browser payload only: hidden fields + user + password (no "remember me", no submit button).
        response = self._post(
            session, config, action, {**_hidden_fields(form), "username": username, "password": password}
        )
        state = self._classify(config, response)
        if state == "login":
            raise LoginRejected("Projudi did not accept the login and password")
        if state == "otp":
            account = otp_account(config)
            form = _soup(response.text).find("form", id="kc-otp-login-form")
            action = urllib.parse.urljoin(response.url, form.get("action") or "")
            response = self._post(session, config, action, {**_hidden_fields(form), "otp": totp(account).now()})
            state = self._classify(config, response)
            if state in ("login", "otp"):
                raise LoginRejected("Projudi did not accept the authenticator code")
        if state != "app":
            raise RuntimeError("Projudi login did not return to the portal")
        if self.refresh(portal, session) is None:
            raise RuntimeError("Projudi login did not reach an authenticated page")
        return session

    def refresh(self, portal, session):
        config = portals.projudi(portal)
        try:
            response = session.get(
                config.url("home.do"), impersonate=impersonate_for(config), allow_redirects=False, timeout=TIMEOUT
            )
        except RequestException:
            return None
        if response.status_code != 200:
            return None
        return session if is_logged_in_page(parsers.decode(response.content)) else None

    def is_login_redirect(self, response):
        """Any redirect off the portal host (the SSO) or to the login entry means the session is gone."""
        if response.status_code not in REDIRECTS:
            return False
        own_host = (urllib.parse.urlsplit(response.url or "").hostname or "").lower()
        target = urllib.parse.urlsplit(urllib.parse.urljoin(response.url or "", response.headers.get("Location") or ""))
        path = target.path.rstrip("/").lower()
        off_host = bool(own_host) and (target.hostname or "").lower() not in ("", own_host)
        return off_host or path.endswith(("autenticacao.do", "logon.do"))

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
        for frame in _soup(pages[0][1]).find_all(["frame", "iframe"], src=True):
            if len(pages) >= MAX_FRAME_PAGES:
                break
            url = strip_path_params(urllib.parse.urljoin(pages[0][0], frame["src"]))
            if not url.startswith(config.base_url) or FORBIDDEN_LINK.search(url):
                continue
            pages.append(self._get_page(session, portal, url))
        return pages

    def _menu_page(self, session, portal, label):
        config = portals.projudi(portal)
        for page_url, html in self._frame_pages(session, portal):
            for text, url in page_links(html, page_url):
                if label.casefold() in text.casefold() and url.startswith(config.base_url):
                    return self._get_page(session, portal, url)
        raise base.ProjudiParseError(f"Projudi menu has no '{label}' link for this session")

    def open_process(self, session, portal, process_number):
        config = portals.projudi(portal)
        url, html = self._menu_page(session, portal, MENU_SEARCH)
        form = search_form(html, url, process_number)
        if form is None:
            raise base.ProjudiParseError("Projudi search page has no process number field")
        if HUMAN_VERIFICATION.search(html):
            raise HumanVerificationRequired("precisa de uma pessoa: a busca do Projudi pediu verificação humana")
        method, action, fields = form
        base.require_portal_url(config, action)
        path = action[len(config.base_url) :]
        arguments = {"data": fields} if method == "post" else {"params": fields}
        response = self.request(session, portal, method.upper(), path, **arguments)
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
        raise DocumentsBlocked("TJPR Projudi documents are blocked: reading one may register ciência")

    fetch_index = movement_files = download_url = download = _blocked


ADAPTER = KeycloakAdapter()
