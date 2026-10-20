#!/usr/bin/env python3
"""PJe certificate + TOTP login with a native A1 signer (any PJe portal)."""

import os
import re
import urllib.parse
import uuid
from pathlib import Path

from bs4 import BeautifulSoup
from curl_cffi import requests

from jurisync import portals
from jurisync.connectors.pje.auth import signer as native_signer
from jurisync.connectors.pje.auth.sso import require_allowed_url
from jurisync.core.http import log, log_request
from jurisync.core.redaction import url_shape
from jurisync.core.totp import accounts_from_image, parse_accounts, select_account, totp


def _require_available(page):
    """A 5xx login page means the portal is down, not that the login flow changed."""
    if page.status_code >= 500:
        raise RuntimeError(f"PJe is unavailable (HTTP {page.status_code}); try again later")


def _challenge(html):
    match = re.search(r"autenticar\('([^']+)',\s*'([^']+)'\)", html)
    if not match:
        raise RuntimeError("TRF5 login page has no certificate challenge")
    return match.group(1), match.group(2)


def _submit_code(sso_session, action, token):
    response = sso_session.post(
        action,
        data={"username": "", "password": "", "pjeoffice-code": token, "phrase": ""},
        impersonate="chrome",
        allow_redirects=False,
        timeout=60,
    )
    log_request("POST", action, response.status_code)
    return response


def _follow(sso_session, response, hops=6):
    for _ in range(hops):
        location = response.headers.get("Location")
        if response.status_code not in {301, 302, 303, 307, 308} or not location:
            break
        next_url = urllib.parse.urljoin(response.url, location)
        require_allowed_url(next_url)
        response = sso_session.get(
            next_url,
            impersonate="chrome",
            allow_redirects=False,
            timeout=30,
        )
    return response


def _authenticated(response, host):
    soup = BeautifulSoup(response.text, "html.parser")
    return (
        response.status_code == 200
        and urllib.parse.urlsplit(response.url).hostname == host
        and bool(soup.find("a", href=re.compile(r"/logout\.seam")))
        and bool(soup.find("input", attrs={"name": "javax.faces.ViewState"}))
    )


def _twofa(portal, twofa_data=None):
    """The TOTP account this portal's SSO asks for after the certificate, when it does."""
    config = portals.pje(portal)
    data = twofa_data or config.env("2FA_DATA")
    qr_path = config.env("2FA_QR")
    if data:
        return select_account(parse_accounts(data), label=f"{config.env_prefix}_2FA_DATA")
    if qr_path:
        return select_account(accounts_from_image(Path(qr_path)), label=f"{config.env_prefix}_2FA_QR")
    return None


def cert_login(portal, twofa_data=None, session=None, *, pin=None, p12_path=None):
    """Return an authenticated PJe session via A1 certificate (+ TOTP when the SSO asks)."""
    config = portals.pje(portal)
    account = _twofa(portal, twofa_data)
    pin = pin or os.environ.get("PJE_A1_PIN")
    if not pin:
        raise RuntimeError("Set PJE_A1_PIN to the A1 certificate password")
    key, cert, chain = native_signer.load_a1(p12_path or native_signer.a1_path(), pin)
    return _login(session or requests.Session(), config, account, key, cert, chain)


def _login(session, config, account, key, cert, chain):
    page = _follow(session, session.get(config.login_url, impersonate="chrome", allow_redirects=False, timeout=30))
    require_allowed_url(page.url)
    log_request("GET", config.login_url, page.status_code)
    _require_available(page)
    _, mensagem = _challenge(page.text)
    token = str(uuid.uuid4())
    payload = native_signer.build_payload(
        token,
        mensagem,
        native_signer.sign_mensagem(mensagem, key),
        native_signer.certificate_chain64(cert, chain),
    )
    native_signer.post_result(session, payload)

    soup = BeautifulSoup(page.text, "html.parser")
    form = soup.find("form", id="loginForm")
    if not form or not form.get("action"):
        raise RuntimeError("TRF5 login form was not returned")
    action = urllib.parse.urljoin(page.url, form["action"])
    require_allowed_url(action)

    response = _follow(session, _submit_code(session, action, token))
    if _authenticated(response, config.host):
        log.info("%s certificate login complete -> %s", config.key, url_shape(response.url))
        return session
    candidate = BeautifulSoup(response.text, "html.parser").find("form", id="kc-otp-login-form")
    if not candidate or not candidate.get("action"):
        raise RuntimeError("SSO did not advance after the certificate callback")
    if account is None:
        raise RuntimeError(f"SSO asks for a TOTP code: set {config.env_prefix}_2FA_DATA")
    otp_action = urllib.parse.urljoin(response.url, candidate["action"])
    require_allowed_url(otp_action)

    otp = totp(account).now()
    response = session.post(
        otp_action,
        data={"otp": otp, "login": "Validar"},
        impersonate="chrome",
        allow_redirects=False,
        timeout=60,
    )
    log_request("POST", otp_action, response.status_code)
    final = _follow(session, response)
    if not _authenticated(final, config.host):
        raise RuntimeError(f"{config.key} login did not reach an authenticated PJe page")
    log.info("%s certificate login complete -> %s", config.key, url_shape(final.url))
    return session
