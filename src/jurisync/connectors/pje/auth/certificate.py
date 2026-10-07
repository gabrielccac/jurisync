#!/usr/bin/env python3
"""TRF5 certificate + TOTP login with a native A1 signer."""

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

PORTAL_KEY = "trf5-1g"


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
        and bool(soup.find("a", href=re.compile(r"/pje/logout\.seam")))
        and bool(soup.find("input", attrs={"name": "javax.faces.ViewState"}))
    )


def trf5_cert_login(twofa_data=None, session=None, *, pin=None, p12_path=None):
    """Return an authenticated TRF5 1G session via certificate + TOTP."""
    config = portals.pje(PORTAL_KEY)
    twofa_data = twofa_data or os.environ.get("PJE_TRF5_2FA_DATA", "")
    qr_path = os.environ.get("PJE_TRF5_2FA_QR")
    if twofa_data:
        account = select_account(parse_accounts(twofa_data), label="PJE_TRF5_2FA_DATA")
    elif qr_path:
        account = select_account(accounts_from_image(Path(qr_path)), label="PJE_TRF5_2FA_QR")
    else:
        raise RuntimeError("Set PJE_TRF5_2FA_DATA or PJE_TRF5_2FA_QR")
    pin = pin or os.environ.get("PJE_A1_PIN")
    if not pin:
        raise RuntimeError("Set PJE_A1_PIN to the A1 certificate password")
    key, cert, chain = native_signer.load_a1(p12_path or native_signer.a1_path(), pin)
    return _login(session or requests.Session(), config, account, key, cert, chain)


def _login(session, config, account, key, cert, chain):
    page = _follow(session, session.get(config.login_url, impersonate="chrome", allow_redirects=False, timeout=30))
    require_allowed_url(page.url)
    log_request("GET", config.login_url, page.status_code)
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
    candidate = BeautifulSoup(response.text, "html.parser").find("form", id="kc-otp-login-form")
    if not candidate or not candidate.get("action"):
        raise RuntimeError("SSO did not advance to OTP after the certificate callback")
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
        raise RuntimeError("TRF5 login did not reach an authenticated PJe page")
    log.info("TRF5 certificate login complete -> %s", url_shape(final.url))
    return session
