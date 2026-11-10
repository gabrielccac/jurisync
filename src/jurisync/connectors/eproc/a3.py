"""eproc login with the A3 token, over HTTP (no browser), then the portal's TOTP.

Supported: Keycloak X.509 (TJRS, TJSC). The "certificate" button reloads the same Keycloak URL on a sibling mTLS
host (``keycloak-httpd-mtls.tjrs.jus.br``, ``cert-sso.tjsc.jus.br``), which asks for the certificate in the handshake
and shows the X.509 confirmation form; the OTP form follows. The TLS exchange goes through ``jurisync.core.mtls``
with the key kept inside the token.

Not supported yet: portals whose certificate login goes only through the Certisign authenticator
(``autenticador.certisign.com.br``).
"""

import html
import re
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup
from curl_cffi import requests

from jurisync import portals
from jurisync.connectors.eproc.auth import PANEL_MARKER, portal_totp_account
from jurisync.core import mtls
from jurisync.core.totp import next_code, totp

MTLS_HOST = re.compile(r"document\.location\.host\s*=\s*'([a-z0-9.-]+)'", re.I)


def _parent_domain(host):
    return host.split(".", 1)[1] if "." in host else host


def _cookies_for(session, host):
    """The session cookies a browser would send to ``host``."""
    result = {}
    for cookie in session.cookies.jar:
        domain = (cookie.domain or "").lstrip(".")
        if host == domain or host.endswith("." + domain):
            result[cookie.name] = cookie.value
    return result


def _store_cookies(session, response, host):
    for name, value in response.cookies().items():
        session.cookies.set(name, value, domain=host, path="/")


def _form(text, form_id=None):
    soup = BeautifulSoup(text, "html.parser")
    return soup.find("form", id=form_id) if form_id else soup.find("form")


def eproc_message(url, text):
    """The reason eproc gives for refusing a login (``msg`` query or the page's error box)."""
    query = urlsplit(url).query
    if query:
        for encoding in ("utf-8", "iso-8859-1"):
            try:
                values = parse_qs(query, encoding=encoding, errors="strict").get("msg")
            except UnicodeDecodeError:
                continue
            if values:
                return values[0]
    soup = BeautifulSoup(text, "html.parser")
    # Keycloak's own error slots only: eproc login pages also carry maintenance notices in generic alert boxes.
    box = soup.find(id=re.compile(r"^(input-error.*|kc-error-message)$")) or soup.find(
        class_=re.compile(r"(alert-error|kc-feedback-text)")
    )
    return " ".join(box.stripped_strings)[:200] if box else ""


def _keycloak_x509(session, page, mtls_host, signer):
    auth = urlsplit(page.url)
    if _parent_domain(mtls_host) != _parent_domain(auth.hostname) or not mtls_host.endswith(".jus.br"):
        raise RuntimeError(f"Unexpected certificate host {mtls_host!r} for {auth.hostname}")
    url = urlunsplit(auth._replace(netloc=mtls_host))
    response = mtls.fetch(url, signer=signer, cookies=_cookies_for(session, auth.hostname))
    if not response.certificate_sent:
        raise RuntimeError(f"{mtls_host} did not ask for the certificate")
    _store_cookies(session, response, auth.hostname)
    form = _form(response.text, "kc-x509-login-info")
    if response.status in {301, 302, 303} and response.location:
        return session.get(response.location, impersonate="chrome", timeout=60)
    if form is None or not form.get("action"):
        reason = eproc_message(response.url, response.text)
        if not reason and _form(response.text, "kc-form-login") is not None:
            reason = "the certificate was read, but no user of this portal is linked to it (register the CPF first)"
        raise RuntimeError("Keycloak did not accept the certificate" + (f": {reason}" if reason else ""))
    action = urljoin(url, html.unescape(form["action"]))
    if urlsplit(action).hostname != auth.hostname:
        raise RuntimeError("Keycloak certificate confirmation points to another host")
    return session.post(action, data={"login": "Entrar"}, impersonate="chrome", timeout=60)


def _submit_otp(session, response, portal):
    """Answer Keycloak's OTP form with the portal TOTP; one retry with the next code, as the browser login does."""
    account = None
    for attempt in range(2):
        form = _form(response.text, "kc-otp-login-form")
        if form is None:
            return response
        account = account or portal_totp_account(portal)
        code = totp(account).now() if attempt == 0 else next_code(account)
        action = urljoin(response.url, html.unescape(form["action"]))
        response = session.post(action, data={"otp": code, "login": "Entrar"}, impersonate="chrome", timeout=60)
    if _form(response.text, "kc-otp-login-form") is not None:
        raise RuntimeError("eproc rejected the OTP")
    return response


def _logged_in(response, config):
    return urlsplit(response.url).hostname == config.host and (
        PANEL_MARKER in response.url or PANEL_MARKER in response.text
    )


def login(portal, *, signer=None):
    """Return ``(session, panel_html, panel_url)`` for an eproc portal via the A3 token + its TOTP."""
    from jurisync.connectors.pje.auth import a3

    config = portals.eproc(portal)
    session = requests.Session()
    try:
        page = session.get(config.login_url, impersonate="chrome", timeout=60)
        if page.status_code >= 500:
            raise RuntimeError(f"{config.tribunal} eproc is unavailable (HTTP {page.status_code})")
        host = MTLS_HOST.search(page.text)
        if not host:
            raise RuntimeError(f"{config.key} login page offers no Keycloak X.509 certificate login")

        def run(token):
            return _keycloak_x509(session, page, host.group(1), token)

        if signer is not None:
            response = run(signer)
        else:
            with a3.token_lock(), a3.A3Signer() as token:
                response = run(token)
        response = _submit_otp(session, response, portal)
        if not _logged_in(response, config):
            reason = eproc_message(response.url, response.text)
            raise RuntimeError(
                f"{config.key} certificate login did not reach the panel" + (f": {reason}" if reason else "")
            )
        if not any(cookie.name == "PHPSESSID" for cookie in session.cookies.jar):
            raise RuntimeError("Authenticated panel loaded without a PHPSESSID cookie")
        return session, response.text, response.url
    except Exception:
        session.close()
        raise
