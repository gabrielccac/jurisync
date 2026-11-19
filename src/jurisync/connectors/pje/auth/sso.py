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


# Where the Keycloak themes of the PDPJ SSO put the message of a refused step: the field error (#input-error), the
# feedback alert (pje-v2: span.kc-feedback-text inside .alert-error) and the error page (pdpj: #kc-error-message).
ALERT_SELECTORS = (
    "#input-error",
    "#input-error-username",
    "#input-error-password",
    ".kc-feedback-text",
    "#kc-error-message",
    ".alert-error",
    ".alert-danger",
    ".alert-warning",
    ".pf-c-alert__title",
)
# A CPF/CNPJ (with or without punctuation) or an e-mail the SSO may echo back never leaves this module.
IDENTIFIERS = re.compile(r"\d[\d.\-/]{9,}\d|[\w.+-]+@[\w-]+\.[\w.-]+")
# The exact wording of each refusal, in Portuguese and English: a bare "invalid" also matches "sessão inválida".
WRONG_CREDENTIALS = (
    "usuário ou senha inválid",
    "usuário e/ou senha inválid",
    "nome de usuário ou senha inválid",
    "senha inválida",
    "senha incorreta",
    "credenciais inválidas",
    "invalid username or password",
    "invalid username, email or password",
    "invalid password",
)
EXPIRED_PASSWORD = ("senha expirada", "password expired", "expired password")
ACCOUNT_BLOCKED = ("conta desativada", "conta bloqueada", "temporariamente", "account is disabled", "account disabled")
FLOW_ERRORS = ("cookie", "expirou", "expired", "tempo esgotado", "timeout", "erro inesperado", "unexpected error")


def alert_text(soup):
    """The SSO's own alert on the page (one line, at most 200 characters, identifiers masked), else ""."""
    for selector in ALERT_SELECTORS:
        for tag in soup.select(selector):
            for link in tag.find_all("a"):
                link.decompose()  # "Voltar para login"
            text = " ".join(" ".join(tag.stripped_strings).split())
            if text:
                return IDENTIFIERS.sub("[…]", text)[:200]
    return ""


def credential_failure(html):
    """Return ``(kind, reason)`` for a credential POST that did not reach the OTP step, without dumping HTML.

    ``kind`` is ``"credentials"`` (the SSO said the username or password is wrong), ``"expired"``, ``"blocked"``
    (disabled or locked account), ``"flow"`` (a session/cookie/timeout error: another attempt may work), ``"waf"``
    or ``"unknown"``. The reason quotes the SSO's own alert so a refusal can be told from any other error.
    """
    soup = BeautifulSoup(html, "html.parser")
    otp_fields = soup.find_all("input", attrs={"name": "otp"})
    if otp_fields:
        parent = otp_fields[0].find_parent("form")
        form_id = parent.get("id") if parent else None
        return "unknown", f"OTP field was returned under unexpected form {form_id!r}"
    alert = alert_text(soup)
    quoted = f' (SSO message: "{alert}")' if alert else " (the page carried no SSO alert)"
    text = (alert or " ".join(soup.stripped_strings)).lower()
    if any(marker in text for marker in EXPIRED_PASSWORD):
        return "expired", (
            "The portal reports an expired password; reset it through the portal and update the configured password"
            + quoted
        )
    if any(marker in text for marker in ACCOUNT_BLOCKED):
        return "blocked", "The SSO refused the account itself (disabled or locked); ask the court's support" + quoted
    if any(marker in text for marker in WRONG_CREDENTIALS):
        return "credentials", "The portal rejected the username/password" + quoted
    if any(marker in text for marker in ("captcha", "aws waf", "access denied")):
        return "waf", "The credential response looks like a WAF/challenge page" + quoted
    if alert and any(marker in text for marker in FLOW_ERRORS):
        return "flow", "The SSO interrupted the login flow, not the credentials" + quoted
    if alert:
        return "unknown", "The SSO answered with an error that is not a password refusal" + quoted
    return "unknown", "The portal returned no OTP form and no SSO alert; the login page may have changed"


def sso_username(username, form_id):
    """The username as the PDPJ theme of the PJe-JT login (``kc-form-login``) expects it: a CPF/CNPJ as digits.

    Its page asks for "CPF/CNPJ" and its own submit script strips ``.``, ``-`` and ``/``; the TRF-style theme
    (``loginForm``) gets the value as configured, which those portals already accept.
    """
    value = username.strip()
    if form_id == "kc-form-login" and re.fullmatch(r"[\d.\-/ ]+", value):
        digits = re.sub(r"\D", "", value)
        if len(digits) in (11, 14):
            return digits
    return value


PORTAL_LOGIN_ERROR = re.compile(r"var\s+mensagemErroLogin\s*=\s*'([^']*)'")


def portal_login_error(html):
    """The message a PJe's ssoCallbackLogin page carries when the court refuses an SSO login of its own accord
    (TJPA: "Senha expirada. Solicite uma nova senha"), else ""."""
    match = PORTAL_LOGIN_ERROR.search(html or "")
    return match.group(1).strip() if match else ""


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


def _login_form_id(html):
    return "loginForm" if BeautifulSoup(html, "html.parser").find("form", id="loginForm") else "kc-form-login"


def _login_form(html, page_url):
    return form_action(html, page_url, _login_form_id(html))


def login_session(username, password, account, app_url=None, expected_app_host=None, portal=None):
    config = portals.pje(portal or "trf1-2g")
    app_url = app_url or config.login_url
    expected_app_host = expected_app_host or config.host
    session = requests.Session()
    response = _sso_page(session, app_url)
    form_id = _login_form_id(response.text)
    login_url = form_action(response.text, response.url, form_id)
    response = session.post(
        login_url,
        data={"username": sso_username(username, form_id), "password": password, "pjeoffice-code": "", "phrase": ""},
        allow_redirects=False,
        impersonate=IMPERSONATE,
    )
    log_request("POST", login_url, response.status_code)
    try:
        otp_url = form_action(response.text, response.url, "kc-otp-login-form")
    except RuntimeError as error:
        kind, reason = credential_failure(response.text)
        refused = kind in {"credentials", "expired", "blocked"}
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
            # The landing page varies by court; the REST API that every PJe-JT read uses is the real test.
            from jurisync.connectors.pje import kz

            authenticated = kz.is_authenticated(session, config.key)
    if not authenticated and (refusal := portal_login_error(response.text)):
        # The SSO accepted the account but this court did not: retrying cannot help until the user acts on it.
        raise LoginRejected(f"{config.key} refused the login: {refusal}")
    if not authenticated:
        raise RuntimeError(
            f"Login did not reach an authenticated PJe page (HTTP {response.status_code} at {url_shape(response.url)})"
        )
    return session, response
