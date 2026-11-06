"""eproc login with the A1 certificate over mutual TLS (TRF4), without a browser.

The Keycloak login page offers a certificate button that moves to a sibling SSO host
(``eproc-sso-certificado``) which asks the client certificate during the TLS handshake. That host
answers with a "Continue" form, then the SSO asks the authenticator code like the password flow.
"""

import re
import time
import urllib.parse

from bs4 import BeautifulSoup
from curl_cffi import requests

from jurisync import portals
from jurisync.connectors.eproc.auth import PANEL_MARKER, portal_totp_account
from jurisync.connectors.pje.auth import signer as native_signer
from jurisync.core import credentials
from jurisync.core.certificate import client_certificate
from jurisync.core.http import IMPERSONATE, TIMEOUT, follow_redirects, require_url
from jurisync.core.totp import next_code, totp

CERT_HOST = re.compile(r"document\.location\.host\s*=\s*['\"]([a-z0-9.-]+)['\"]", re.I)
BROKER_HOST = "sso.cloud.pje.jus.br"


def require_login_url(config, value):
    """A login redirect must stay on the portal, a court ``*sso*`` host or the national broker."""
    hosts = {config.host, BROKER_HOST}
    host = (urllib.parse.urlsplit(value or "").hostname or "").lower()
    # The TRF4 sections (jfsc, jfrs, jfpr) share one SSO under trf4.jus.br, so the domain is not the portal's.
    # State courts name theirs differently (cert-sso.tjsc, certificado-sso.tjsp, keycloak-eks.tjrs);
    # any SSO/Keycloak host under .jus.br (a restricted judiciary TLD) is accepted.
    if host.endswith(".jus.br") and ("sso" in host or "keycloak" in host):
        hosts.add(host)
    return require_url(value, hosts=hosts, label="eproc login redirect")


def _follow(session, config, response, *, cert=None, hops=14):
    return follow_redirects(session, response, check=lambda url: require_login_url(config, url), cert=cert, hops=hops)


def _form(html, page_url, config, form_id):
    form = BeautifulSoup(html, "html.parser").find("form", id=form_id)
    if form is None or not form.get("action"):
        return None, {}
    action = urllib.parse.urljoin(page_url, form["action"])
    require_login_url(config, action)
    fields = {field["name"]: field.get("value", "") for field in form.find_all("input") if field.get("name")}
    return action, fields


def _certificate_url(html, page_url, config):
    """The login page URL moved to the host that asks for the client certificate."""
    match = CERT_HOST.search(html)
    if not match:
        raise RuntimeError("eproc login page has no certificate option")
    parts = urllib.parse.urlsplit(page_url)
    url = urllib.parse.urlunsplit(parts._replace(netloc=match.group(1).lower()))
    require_login_url(config, url)
    return url


def _login_page(session, config):
    """The SSO login page. The portal root sometimes serves its home page instead of redirecting to it."""
    for url in (config.login_url, config.url("externo_controlador.php?acao=SSO/login")):
        page = _follow(
            session, config, session.get(url, impersonate=IMPERSONATE, allow_redirects=False, timeout=TIMEOUT)
        )
        if CERT_HOST.search(page.text):
            break
    return page


def _otp_step(session, config, response, account):
    action, _ = _form(response.text, response.url, config, "kc-otp-login-form")
    if action is None:
        return response
    for code in (totp(account).now(), None):
        code = code or next_code(account)  # a code already used in this window by a recent login
        submitted = session.post(
            action,
            data={"otp": code, "login": "Sign In"},
            impersonate=IMPERSONATE,
            allow_redirects=False,
            timeout=TIMEOUT,
        )
        response = _follow(session, config, submitted)
        action, _ = _form(response.text, response.url, config, "kc-otp-login-form")
        if action is None:
            return response
        time.sleep(1)
    raise RuntimeError("eproc rejected the OTP")


def cert_login(portal, *, pin=None, p12_path=None, session=None):
    """Return ``(session, panel_html, panel_url)`` like the browser login, using the A1 certificate."""
    config = portals.eproc(portal)
    pin = pin or credentials.env("PJE_A1_PIN")
    if not pin:
        raise RuntimeError("Set PJE_A1_PIN to the A1 certificate password")
    account = portal_totp_account(config)
    key, cert, chain = native_signer.load_a1(p12_path, pin)
    session = session or requests.Session()
    with client_certificate(key, cert, chain) as pem:
        page = _login_page(session, config)
        response = _follow(
            session,
            config,
            session.get(
                _certificate_url(page.text, page.url, config),
                impersonate=IMPERSONATE,
                cert=pem,
                allow_redirects=False,
                timeout=TIMEOUT,
            ),
            cert=pem,
        )
    action, fields = _form(response.text, response.url, config, "kc-x509-login-info")
    if action is None:
        raise RuntimeError("eproc did not accept the A1 certificate")
    response = _follow(
        session,
        config,
        session.post(action, data=fields, impersonate=IMPERSONATE, allow_redirects=False, timeout=TIMEOUT),
    )
    response = _otp_step(session, config, response, account)
    if PANEL_MARKER not in response.url or not any(c.name == "PHPSESSID" for c in session.cookies.jar):
        raise RuntimeError("eproc certificate login did not reach the panel")
    return session, response.text, response.url
