"""TJMG JPe (Themis) client: the two logins, the session, and the only page interactions the connector performs.

JPe ("Portal do Processo Eletrônico", 2nd instance) is a JSF 1.2 + RichFaces 3.3 application under
``https://jpe.tjmg.jus.br/rupe/`` (pages end in ``.rupe``; the session is the ``JSESSIONID`` cookie). Two logins are
wired, picked by configuration, never by trying one after the other (``auth_route``):

- **JPe's own login** (``<PREFIX>_LOGIN`` + ``<PREFIX>_LOGIN_PASSWORD``): the "Usuário" created by self-registration
  with an ICP certificate, which is not the CPF, posted to the container's form login ``/rupe/j_security_check``
  (``j_username``/``j_password``). No second factor.
- **PDPJ SSO** (``<PREFIX>_USERNAME`` = CPF, ``<PREFIX>_PASSWORD``, ``<PREFIX>_2FA_DATA|_2FA_QR``): the "Login por
  credencial do PJE" button posts ``formLoginSSO`` to ``/rupe/loginSsoKeycloakServlet``, which redirects to
  ``sso.cloud.pje.jus.br`` (realm ``pje``, client ``jpe-tjmg-2g``). The JPe servlet, not the connector, writes that
  authorize URL: its ``redirect_uri`` is built from the form's ``origemURL``, which the page's script fills with the
  page address (``window.location.href``). Posted empty, the servlet sends a relative ``redirect_uri``
  (``/rupe/loginSsoKeycloakServlet/callback``); posted as the browser does, the absolute
  ``https://jpe.tjmg.jus.br/rupe/loginSsoKeycloakServlet/callback``. On 19/11 and 23/11/2026 the SSO answered both with
  HTTP 400 "Parâmetro inválido: redirect_uri", in a real browser too, before asking for any credential: the client
  ``jpe-tjmg-2g`` does not accept the JPe's own callback. That is the court's SSO configuration; ``SsoRefused``
  (``portal_unavailable``) reports it and nothing is retried.

``<PREFIX>`` is ``TJMG_JPE_2G``; the older ``TJMG_THEMIS_2G`` names are read too (``portals.py``).

Safety. Every request stays on the JPe host under ``/rupe/`` (the SSO host only during the PDPJ login). A JSF page
is driven by clicking its controls, so the guard is on the control, not on a URL: ``open_menu`` follows only the
menu entries in ``parsers.MENU_LABELS``, and ``click`` takes a ``kind`` and refuses any control whose label or
script names a change (``parsers.FORBIDDEN_WORDS``: peticionar, manifestar, cadastrar, assinar, justificativa...).
Nothing in a comunicação row is ever clicked: in JPe, opening its documents or its "dados da comunicação" registers
the reading (the ciência). A page that asks for the justification of a third-party access stops the call
(``ThirdPartyAccess``) and nothing is submitted.
"""

import re
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode, urljoin, urlsplit

from curl_cffi import requests

from jurisync import portals
from jurisync.connectors.jpe import parsers
from jurisync.core.documents import read_limited
from jurisync.core.http import REDIRECTS, TIMEOUT, follow_redirects, impersonate_for, log_request, require_url
from jurisync.core.jsf import a4j_submit, jsf_data, response_html
from jurisync.core.keycloak import OTP_FORM, PDPJ_SSO_HOST, credential_refusal, credentials_form, login_form_id
from jurisync.core.redaction import url_shape
from jurisync.core.session_pool import LoginRejected, MissingLogin, SessionExpired
from jurisync.core.totp import configured_account, fresh_code

PORTAL = "tjmg-jpe-2g"
ROOT = "/rupe/"
SSO_SERVLET = "loginSsoKeycloakServlet"
SSO_REFUSED = (
    "o JPe recusou o login pelo PDPJ do lado do tribunal: o SSO do PDPJ não aceita o endereço de retorno que o "
    "próprio JPe envia para o client jpe-tjmg-2g (acontece também no navegador, antes de qualquer credencial). Use o "
    "login próprio do JPe (TJMG_JPE_2G_LOGIN) ou peça ao TJMG que corrija o client jpe-tjmg-2g"
)
CPF = re.compile(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}")
ROUTES = {"pdpj": "sso", "sso": "sso", "jpe": "jpe", "proprio": "jpe", "own": "jpe"}


class JpeError(RuntimeError):
    """A JPe page did not have the expected shape, or answered an error."""


class JpeSessionExpired(SessionExpired):
    pass


class SsoRefused(LoginRejected):
    """The PDPJ SSO refused to start the JPe login (client ``jpe-tjmg-2g``); no credential was sent.

    The court's SSO configuration, not the account: the login is unavailable until the TJMG fixes it. A
    ``LoginRejected`` so that the pool does not try again at every call.
    """

    reason = "portal_unavailable"


class MissingCredentials(MissingLogin):
    pass


class UnsafeAction(RuntimeError):
    """A control or request outside what the connector may do; refused before anything is sent."""


class ThirdPartyAccess(UnsafeAction):
    """JPe asked why a process is being opened: it is not one the lawyer represents. Never answered."""


@dataclass
class Page:
    """One answer of the portal. ``base`` is the full page an Ajax fragment belongs to (its forms and ViewState)."""

    url: str
    html: str
    base: "Page | None" = None

    @property
    def root(self):
        return self.base or self


@dataclass
class Session:
    http: object
    portal: object
    route: str
    home: Page
    cache: dict = field(default_factory=dict)

    def close(self):
        self.http.close()


def config():
    return portals.jpe(PORTAL)


# --- Configuration ----------------------------------------------------------------------------------------------------


def auth_route(portal):
    """``"jpe"`` (own login) or ``"sso"`` (PDPJ), from the configuration alone.

    ``<PREFIX>_AUTH`` (``jpe``/``pdpj``) decides when set. Otherwise a ``<PREFIX>_LOGIN`` means the JPe's own login,
    and a CPF in ``<PREFIX>_USERNAME`` means the PDPJ. A USERNAME that is not a CPF is an error: it is probably the
    JPe Login put in the wrong variable, and sending it to the PDPJ would only cost a failed attempt.
    """
    chosen = portal.env("AUTH").strip().lower()
    if chosen:
        if chosen not in ROUTES:
            raise MissingCredentials(f"{portal.env_name('AUTH')} must be 'jpe' (own login) or 'pdpj'")
        return ROUTES[chosen]
    if portal.env("LOGIN"):
        return "jpe"
    username = portal.env("USERNAME").strip()
    if username and CPF.fullmatch(username):
        return "sso"
    if username:
        raise MissingCredentials(
            f"{portal.env_name('USERNAME')} is not a CPF. The PDPJ login takes the CPF; the JPe's own Login goes in "
            f"{portal.env_name('LOGIN')} with {portal.env_name('LOGIN_PASSWORD')}"
        )
    raise MissingCredentials(
        f"Set {portal.env_name('LOGIN')} and {portal.env_name('LOGIN_PASSWORD')} (JPe's own login), or "
        f"{portal.env_name('USERNAME')} (CPF), {portal.env_name('PASSWORD')} and {portal.env_name('2FA_DATA')}/"
        f"{portal.env_name('2FA_QR')} (PDPJ)"
    )


def otp_account(portal):
    return configured_account(portal, MissingCredentials)


# --- Requests ---------------------------------------------------------------------------------------------------------


def without_session_id(url):
    """The URL without a ``;jsessionid=`` path parameter: the container adds it to links and form actions until it
    sees the cookie, and the cookie already carries the session (``require_url`` refuses ``;`` in a path)."""
    return re.sub(r";jsessionid=[^?#]*", "", url or "", flags=re.I)


def require_jpe(portal, url):
    return require_url(without_session_id(url), hosts={portal.host}, path_prefix=ROOT, label="JPe")


def _login_hop(portal):
    def check(url):
        if (urlsplit(url).hostname or "").lower() == PDPJ_SSO_HOST:
            return require_url(url, hosts={PDPJ_SSO_HOST}, path_prefix="/auth/", label="PDPJ SSO")
        return require_jpe(portal, url)

    return check


def _send(http, portal, method, url, *, data=None, referer=None, ajax=False, stream=False, check=None):
    url = (check or (lambda value: require_jpe(portal, value)))(without_session_id(url))
    headers = {"Referer": referer} if referer else {}
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
    if ajax:
        headers["X-Requested-With"] = "XMLHttpRequest"
    response = http.request(
        method,
        url,
        data=urlencode(data) if data is not None else None,
        headers=headers,
        impersonate=impersonate_for(portal),
        timeout=TIMEOUT,
        allow_redirects=False,
        stream=stream,
    )
    log_request(method, url, response.status_code)
    return response


def _follow(http, portal, response, check=None):
    return follow_redirects(
        http,
        response,
        check=check or (lambda value: require_jpe(portal, value)),
        rewrite=without_session_id,
        impersonate=impersonate_for(portal),
    )


def _page(session, response, base=None):
    """A checked answer: a login page or an Ajax "expired" header is an expired session; a justification prompt
    stops the call before anything else happens."""
    headers = {key.lower(): value for key, value in (response.headers or {}).items()}
    if headers.get("ajax-expired"):
        raise JpeSessionExpired("JPe session expired")
    html = response_html(response)
    if parsers.is_login_page(html):
        raise JpeSessionExpired("JPe sent the session back to its login page")
    if parsers.asks_justification(html):
        raise ThirdPartyAccess(
            "JPe asked for the justification of a third-party access: this process is not one the account "
            "represents. Nothing was submitted and the process was not opened"
        )
    if response.status_code != 200:
        raise JpeError(f"JPe answered HTTP {response.status_code} at {url_shape(response.url)}")
    return Page(response.url, html, base)


def get(session, url, *, referer=None):
    response = _send(session.http, session.portal, "GET", url, referer=referer)
    response = _follow(session.http, session.portal, response)
    return _page(session, response)


# --- Controls ---------------------------------------------------------------------------------------------------------


def _form_of(page, form_id):
    """The form a control submits, looked up in the Ajax fragment first, then in its full page."""
    for source in (page, page.root):
        soup = parsers.soup_of(source.html)
        form = soup.find("form", id=form_id) or soup.find("form", attrs={"name": form_id})
        if form is not None:
            return form, source
    raise JpeError(f"JPe form {form_id} is not on the page")


def _form_data(form, source, page):
    """The form's submittable fields (buttons and JS-only helpers left out) with the latest ViewState."""
    state, fields = jsf_data(str(form))
    for button in form.find_all("input", attrs={"type": re.compile(r"^(button|submit|reset|image)$", re.I)}):
        fields.pop(button.get("name"), None)
    for name in list(fields):
        if name.endswith((":j_idcl", ":_link_hidden_")):
            fields[name] = ""
    latest = jsf_data(page.html)[0] or jsf_data(page.root.html)[0] or state
    if not latest:
        raise JpeError("JPe page has no JSF ViewState")
    fields["javax.faces.ViewState"] = latest
    form_id = form.get("id") or form.get("name")
    fields[form_id] = form_id
    return fields


def _action_url(form, source, portal):
    return require_jpe(portal, urljoin(source.url, form.get("action") or source.url))


def _jsfcljs(script):
    """(form id, parameters) of a JSF 1.2 commandLink (``jsfcljs(document.forms['f'], {'id':'id'}, '')``)."""
    match = re.search(r"jsfcljs\(\s*document\.(?:forms\[['\"]([^'\"]+)['\"]\]|getElementById\(['\"]([^'\"]+)['\"]\))"
                      r"\s*,\s*['\"]?\{?([^}]*)\}?", script)  # fmt: skip
    if not match:
        return None
    form_id = match.group(1) or match.group(2)
    pairs = re.findall(r"['\"]([^'\"]+)['\"]\s*[:,]\s*['\"]([^'\"]*)['\"]", match.group(3) or "")
    return form_id, dict(pairs)


def click(session, page, element, *, kind, stream=False, ignore_text=False):
    """Activate one control the way the browser would, after the guard.

    A plain link is a GET; a JSF commandLink posts its form; a RichFaces control posts its form as an Ajax request
    (``AJAXREQUEST``). Returns the answer as a ``Page`` (an Ajax answer keeps ``base`` = the full page), or the raw
    streamed response when ``stream`` is set (downloads).
    """
    if parsers.is_forbidden(element, ignore_text=ignore_text):
        raise UnsafeAction(f"refused: the {kind} control looks like an action that changes something in JPe")
    portal = session.portal
    script = element.get("onclick") or ""
    href = (element.get("href") or "").strip()
    if href and not href.startswith(("#", "javascript:")) and "A4J.AJAX" not in script and "jsfcljs" not in script:
        url = require_jpe(portal, urljoin(page.root.url, href))
        response = _send(session.http, portal, "GET", url, referer=page.root.url, stream=stream)
        if stream:
            return response
        return _page(session, _follow(session.http, portal, response))
    if "A4J.AJAX.Submit" in script:
        form_id, container, parameters = a4j_submit(script, "_viewRoot")
        form, source = _form_of(page, form_id)
        data = _form_data(form, source, page)
        data.update(parameters)
        control = re.search(r"'similarityGroupingId':'([^']+)'", script)
        if control:
            data.setdefault(control.group(1), control.group(1))
        data["AJAXREQUEST"] = container or "_viewRoot"
        url = _action_url(form, source, portal)
        response = _send(session.http, portal, "POST", url, data=data, referer=page.root.url, ajax=True, stream=stream)
        if stream:
            return response
        if response.status_code in REDIRECTS:
            return _page(session, _follow(session.http, portal, response))
        return _page(session, response, base=page.root)
    command = _jsfcljs(script)
    if command is None and element.name == "input" and (element.get("type") or "").lower() in ("submit", "image"):
        parent = element.find_parent("form")
        command = (
            (parent.get("id") or parent.get("name"), {element.get("name"): element.get("value") or ""})
            if parent
            else None
        )
    if command is not None:
        form_id, parameters = command
        form, source = _form_of(page, form_id)
        data = _form_data(form, source, page)
        data.update(parameters)
        url = _action_url(form, source, portal)
        response = _send(session.http, portal, "POST", url, data=data, referer=page.root.url, stream=stream)
        if stream:
            return response
        return _page(session, _follow(session.http, portal, response))
    raise JpeError(f"JPe {kind} control has no action the connector recognizes")


def open_menu(session, label):
    """Follow one menu entry of the logged-in home page (``parsers.MENU_LABELS``) and return its page."""
    if label not in parsers.MENU_LABELS:
        raise UnsafeAction(f"refused: menu entry {label!r} is not one the connector reads")
    entries = parsers.menu_entries(session.home.html)
    element = entries.get(label)
    if element is None:
        raise JpeError(f"JPe menu entry {label!r} was not found on the home page")
    # The labels were checked against the allow-list, so the label guard of ``click`` is not needed here; its
    # script guard still applies.
    return click(session, session.home, element, kind="menu", ignore_text=True)


def reload_home(session):
    """The logged-in home page again (the URL the login landed on), unframed."""
    page = _unframe(session.http, session.portal, get(session, session.cache.get("home_url") or session.home.url))
    if not parsers.is_authenticated(page.html):
        raise JpeSessionExpired("JPe home page is not the lawyer's area")
    session.home = page
    return page


def _unframe(http, portal, page):
    """The page itself, or the JPe page its frame/iframe shows when the menu is not on the outer page.

    The public pages call ``parent.frames[0]``, so the lawyer's area may sit inside a frame of the portal shell.
    """
    if parsers.is_authenticated(page.html):
        return page
    soup = parsers.soup_of(page.html)
    for frame in soup.find_all(["frame", "iframe"], src=True):
        url = urljoin(page.url, frame["src"])
        try:
            require_jpe(portal, url)
        except Exception:  # noqa: BLE001 - a frame elsewhere (a banner, a chat) is simply not the app
            continue
        response = _follow(http, portal, _send(http, portal, "GET", url, referer=page.url))
        inner = Page(response.url, response_html(response))
        if response.status_code == 200 and parsers.is_authenticated(inner.html):
            return inner
    return page


# --- Login ------------------------------------------------------------------------------------------------------------


def login(portal_key=PORTAL, *, http_factory=requests.Session, clock=time.time, sleep=time.sleep):
    """One login by the configured route. A refusal is ``LoginRejected`` (or ``SsoRefused``) and is never retried."""
    portal = portals.jpe(portal_key)
    route = auth_route(portal)
    if route == "jpe":
        username, password = portal.env("LOGIN").strip(), portal.env("LOGIN_PASSWORD")
        if not username or not password:
            raise MissingCredentials(f"Set {portal.env_name('LOGIN')} and {portal.env_name('LOGIN_PASSWORD')}")
        account = None
    else:
        username, password = portal.env("USERNAME").strip(), portal.env("PASSWORD")
        if not username or not password:
            raise MissingCredentials(f"Set {portal.env_name('USERNAME')} and {portal.env_name('PASSWORD')}")
        account = otp_account(portal)  # a configuration error must not cost a login
    http = http_factory()
    try:
        entry = _send(http, portal, "GET", portal.login_url)
        entry = _follow(http, portal, entry)
        if entry.status_code != 200 or not parsers.is_login_page(response_html(entry)):
            raise JpeError(f"JPe login page did not load (HTTP {entry.status_code})")
        html = response_html(entry)
        if route == "jpe":
            landing = _own_login(http, portal, html, entry.url, username, password)
        else:
            landing = _sso_login(http, portal, html, entry.url, username, password, account, clock=clock, sleep=sleep)
        landing = _unframe(http, portal, landing)
        if not parsers.is_authenticated(landing.html):
            raise JpeError(
                f"JPe login did not reach the lawyer's area (at {url_shape(landing.url)}); the page is not recognized"
            )
        return Session(http, portal, route, landing, {"home_url": landing.url})
    except BaseException:
        http.close()
        raise


def _own_login(http, portal, html, page_url, username, password):
    form = parsers.login_form(html)
    action = require_jpe(portal, urljoin(page_url, form.get("action")))
    response = _send(
        http, portal, "POST", action, data={"j_username": username, "j_password": password}, referer=page_url
    )
    if response.status_code in (400, 408):
        # Tomcat/JBoss answer this when the form login has no pending request: a flow error, not a refusal.
        raise JpeError(f"JPe form login answered HTTP {response.status_code}; the login flow may have changed")
    response = _follow(http, portal, response)
    landing = response_html(response)
    if parsers.is_login_page(landing) or response.status_code in (401, 403):
        message = parsers.page_message(landing)
        quoted = f' (JPe: "{message}")' if message else ""
        raise LoginRejected(f"JPe refused the Login/password of its own login{quoted}")
    if response.status_code != 200:
        raise JpeError(f"JPe login landed on HTTP {response.status_code}")
    return Page(response.url, landing)


def _sso_login(http, portal, html, page_url, username, password, account, *, clock, sleep):
    check = _login_hop(portal)
    form = parsers.sso_form(html)
    if form is None:
        raise JpeError("JPe login page has no PDPJ button (formLoginSSO)")
    action = require_jpe(portal, urljoin(page_url, form.get("action")))
    # The page's script fills origemURL with the page address on load (``window.location.href``); the servlet builds
    # the SSO redirect_uri from it, and posted empty it sends a relative one (seen live 23/11/2026).
    fields = {element.get("name"): "" for element in form.find_all("input") if element.get("name")}
    if "origemURL" in fields:
        fields["origemURL"] = page_url
    response = _send(http, portal, "POST", action, data=fields, referer=page_url)
    response = _follow(http, portal, response, check=check)
    sso_html = response_html(response)
    refusal = parsers.sso_refusal(sso_html)
    if refusal:
        if "redirect_uri" in refusal.casefold():
            raise SsoRefused(f'{SSO_REFUSED} (SSO: HTTP {response.status_code} "{refusal}")')
        raise JpeError(f'PDPJ SSO refused to start the JPe login: "{refusal}"')
    if (urlsplit(response.url).hostname or "") != PDPJ_SSO_HOST:
        raise JpeError(f"JPe PDPJ button did not reach the SSO (landed at {url_shape(response.url)})")
    form_id = login_form_id(sso_html)
    login_form = parsers.soup_of(sso_html).find("form", id=form_id)
    if login_form is None or not login_form.get("action"):
        raise JpeError("PDPJ SSO page has no login form")
    login_url = check(urljoin(response.url, login_form["action"]))
    response = _send(
        http, portal, "POST", login_url, check=check, referer=response.url,
        data=credentials_form(username, password, form_id),
    )  # fmt: skip
    otp_form = parsers.soup_of(response_html(response)).find("form", id=OTP_FORM)
    if otp_form is None or not otp_form.get("action"):
        raise credential_refusal(response_html(response), JpeError)
    otp_url = check(urljoin(response.url, otp_form["action"]))
    code = fresh_code(account, clock=clock, sleep=sleep)
    response = _send(http, portal, "POST", otp_url, check=check, data={"otp": code, "login": "Validar"})
    if response.status_code not in REDIRECTS:
        raise LoginRejected("PDPJ SSO did not accept the authenticator code", reason="otp_refused")
    response = _follow(http, portal, response, check=check)
    landing = response_html(response)
    if (urlsplit(response.url).hostname or "") != portal.host:
        message = parsers.sso_refusal(landing)
        raise JpeError(f"PDPJ login did not return to JPe (at {url_shape(response.url)}) {message}".strip())
    if parsers.is_login_page(landing) and not parsers.is_authenticated(landing):
        message = parsers.page_message(landing)
        raise LoginRejected(
            "JPe did not accept the PDPJ login (the account may not be linked to a JPe user)"
            + (f' (JPe: "{message}")' if message else ""),
            reason="not_registered",
        )
    return Page(response.url, landing)


def refresh(portal_key, session):
    """The session when the home page still shows the lawyer's area; ``None`` makes the pool log in again."""
    try:
        reload_home(session)
    except (JpeSessionExpired, JpeError, requests.exceptions.RequestException):
        return None
    return session


# --- Downloads --------------------------------------------------------------------------------------------------------


def fetch_file(session, page, element):
    """``(content, content_type)`` of the file a document control serves, or ``(None, reason)``.

    The control may answer the file itself, or a page/Ajax fragment that frames or pops up the file's URL; that URL
    is fetched (at most twice, on the JPe host only).
    """
    response = click(session, page, element, kind="download", stream=True, ignore_text=True)
    try:
        for _ in range(3):
            if response.status_code in REDIRECTS:
                target = urljoin(response.url, response.headers.get("Location", ""))
            else:
                if response.status_code in (401, 403, 404):
                    return None, "access_denied"
                if response.status_code != 200:
                    return None, "request_failed"
                content_type = response.headers.get("content-type", "")
                content = read_limited(response)
                if content is None:
                    return None, "too_large"
                head = content.lstrip()[:15].lower()
                if "html" not in content_type.lower() and not head.startswith((b"<html", b"<!doc", b"<?xml")):
                    return content, content_type
                html = content.decode("iso-8859-1", "replace")
                if parsers.is_login_page(html):
                    raise JpeSessionExpired("JPe sent the session back to its login page")
                target = parsers.popup_url(html, response.url)
                if target is None:
                    return content, content_type  # an HTML document; the text extractor decides what it is
            following = _send(session.http, session.portal, "GET", require_jpe(session.portal, target), stream=True)
            response.close()
            response = following
        return None, "request_failed"
    finally:
        response.close()
