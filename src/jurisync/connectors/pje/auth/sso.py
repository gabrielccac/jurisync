#!/usr/bin/env python3
"""Shared PJe SSO (Keycloak) and TOTP authentication."""

import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from curl_cffi import requests

from jurisync import portals
from jurisync.core.http import IMPERSONATE, TIMEOUT, log_request, require_url
from jurisync.core.redaction import url_shape
from jurisync.core.session_pool import LoginRejected
from jurisync.core.totp import totp

SSO_HOST = "sso.cloud.pje.jus.br"
ALLOWED_HOSTS = portals.hosts("pje") | {SSO_HOST}


def require_allowed_url(value):
    """PJe and its SSO only: a login flow must never be redirected elsewhere."""
    return require_url(value, hosts=ALLOWED_HOSTS, label="PJe login redirect")


def form_action(html, page_url, form_id):
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form", id=form_id)
    if not form or not form.get("action"):
        raise RuntimeError(f"Form {form_id!r} was not returned by {url_shape(page_url)}")
    action = urljoin(page_url, form["action"])
    require_allowed_url(action)
    return action


def credential_failure(html):
    """Explain why the credential POST did not reach the OTP step without dumping HTML."""
    soup = BeautifulSoup(html, "html.parser")
    otp_fields = soup.find_all("input", attrs={"name": "otp"})
    if otp_fields:
        parent = otp_fields[0].find_parent("form")
        form_id = parent.get("id") if parent else None
        return f"OTP field was returned under unexpected form {form_id!r}"
    text = " ".join(soup.stripped_strings).lower()
    if "senha expirada" in text:
        return "The portal reports an expired password; reset it through the portal and update the configured password"
    if any(marker in text for marker in ("captcha", "aws waf", "access denied", "challenge")):
        return "The credential response looks like a WAF/challenge page"
    if any(marker in text for marker in ("invalid", "incorreto", "inválido", "usuário ou senha", "senha inválida")):
        return "The portal rejected the username/password; use the CPF/username from the browser request"
    return "The portal returned no OTP form; credentials may be rejected or the login page changed"


def _https_pje(url):
    """PJe-JT SSO bounces back over plain http; the same PJe path serves https."""
    parts = urlsplit(url)
    if parts.scheme == "http" and (parts.hostname or "") in portals.hosts("pje"):
        return "https://" + url.split("://", 1)[1]
    return url


def get_with_log(session, url):
    require_allowed_url(url)
    response = session.get(url, allow_redirects=False, impersonate=IMPERSONATE, timeout=TIMEOUT)
    log_request("GET", url, response.status_code)
    return response


REDIRECTS = {301, 302, 303, 307, 308}


def _sso_page(session, app_url):
    """The SSO login page: TRF1-style portals redirect their login page to it; PJe-JT portals (TST) serve a page
    that only offers the PDPJ button, which enters the SSO through ``authenticateSSO.seam``."""
    response = get_with_log(session, app_url)
    if response.status_code in REDIRECTS:
        return get_with_log(session, urljoin(response.url, response.headers["Location"]))
    if response.status_code != 200:
        raise RuntimeError(f"Initial PJe page did not redirect (HTTP {response.status_code})")
    response = get_with_log(session, urljoin(app_url, "authenticateSSO.seam"))
    for _ in range(6):
        if response.status_code not in REDIRECTS:
            break
        response = get_with_log(session, urljoin(response.url, response.headers["Location"]))
    return response


def _login_form(html, page_url):
    soup = BeautifulSoup(html, "html.parser")
    form_id = "loginForm" if soup.find("form", id="loginForm") else "kc-form-login"
    return form_action(html, page_url, form_id)


def login_session(username, password, account, app_url=None, expected_app_host=None, portal=None):
    config = portals.pje(portal or "trf1-2g")
    app_url = app_url or config.login_url
    expected_app_host = expected_app_host or config.host
    session = requests.Session()
    response = _sso_page(session, app_url)
    login_url = _login_form(response.text, response.url)
    response = session.post(
        login_url,
        data={"username": username, "password": password, "pjeoffice-code": "", "phrase": ""},
        allow_redirects=False,
        impersonate=IMPERSONATE,
    )
    log_request("POST", login_url, response.status_code)
    try:
        otp_url = form_action(response.text, response.url, "kc-otp-login-form")
    except RuntimeError as error:
        reason = credential_failure(response.text)
        refused = "rejected the username/password" in reason or "expired password" in reason
        raise (LoginRejected if refused else RuntimeError)(f"{reason} ({error})") from None
    otp = totp(account).now()

    response = session.post(
        otp_url,
        data={"otp": otp, "login": "Validar"},
        allow_redirects=False,
        impersonate=IMPERSONATE,
    )
    log_request("POST", otp_url, response.status_code)
    if response.status_code not in {301, 302, 303, 307, 308}:
        raise LoginRejected("OTP submission did not redirect; the code may be invalid or expired")

    for _ in range(8):
        location = response.headers.get("Location")
        if response.status_code not in {301, 302, 303, 307, 308} or not location:
            break
        response = get_with_log(session, _https_pje(urljoin(response.url, location)))

    soup = BeautifulSoup(response.text, "html.parser")
    authenticated = (
        response.status_code == 200
        and urlsplit(response.url).hostname == expected_app_host
        and bool(soup.find("a", href=re.compile(r"logout\.seam")))
        and bool(soup.find("input", attrs={"name": "javax.faces.ViewState"}))
        and not soup.find("form", id="kc-otp-login-form")
    )
    if not authenticated and config.kz:
        # PJe-JT (TST/TRT) lands on the /pjekz/ loader; the app shell answers an app page when the session holds,
        # exactly as after the certificate login.
        from jurisync.connectors.pje.auth import certificate

        certificate._require_registered(response, config)
        if not certificate._authenticated(response, config.host, config.root):
            response = certificate._follow(
                session, session.get(config.search_url, impersonate=IMPERSONATE, allow_redirects=False, timeout=30)
            )
            log_request("GET", config.search_url, response.status_code)
        certificate._require_registered(response, config)
        authenticated = certificate._authenticated(response, config.host, config.root)
    if not authenticated:
        raise RuntimeError(f"Login did not reach an authenticated PJe page (HTTP {response.status_code})")
    return session, response
