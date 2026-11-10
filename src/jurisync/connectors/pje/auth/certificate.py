#!/usr/bin/env python3
"""Certificate + TOTP login on the national PJe SSO, with a native signer (A1 file or A3 token).

The SSO login page carries a PJeOffice challenge; the signer signs it, the result goes to the SSO's
``pjeoffice-rest`` callback, and the login form is submitted with the callback's code. Every PJe portal
registered in ``jurisync.portals`` shares this SSO, so the flow is the same for all of them.
"""

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
from jurisync.core.totp import accounts_from_image, parse_accounts, parse_index, select_account, totp

PORTAL_KEY = "trf5-1g"


def _require_available(page, config=None):
    """A 5xx login page means the portal is down, not that the login flow changed."""
    if page.status_code >= 500:
        name = config.tribunal if config else "TRF5"
        raise RuntimeError(f"{name} PJe is unavailable (HTTP {page.status_code}); try again later")


def _challenge(html, config):
    match = re.search(r"autenticar\('([^']+)',\s*'([^']+)'\)", html)
    if not match:
        raise RuntimeError(f"{config.tribunal} login page has no certificate challenge")
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


def _authenticated(response, host, root="/pje/"):
    soup = BeautifulSoup(response.text, "html.parser")
    return (
        response.status_code == 200
        and urllib.parse.urlsplit(response.url).hostname == host
        and bool(soup.find("a", href=re.compile(re.escape(root) + r"logout\.seam")))
        and bool(soup.find("input", attrs={"name": "javax.faces.ViewState"}))
    )


def otp_account(config):
    """The portal's authenticator: <PREFIX>_2FA_DATA (or _2FA_QR), with the optional profile selectors."""
    data = config.env("2FA_DATA")
    qr_path = config.env("2FA_QR")
    if data:
        accounts, label = parse_accounts(data), config.env_name("2FA_DATA")
    elif qr_path:
        accounts, label = accounts_from_image(Path(qr_path)), config.env_name("2FA_QR")
    else:
        raise RuntimeError(f"Set {config.env_name('2FA_DATA')} or {config.env_name('2FA_QR')}")
    return select_account(
        accounts,
        match=config.env("OTP_PROFILE_MATCH"),
        index=parse_index(config.env("OTP_PROFILE_INDEX"), config.env_name("OTP_PROFILE_INDEX")),
        label=label,
    )


def trf5_cert_login(twofa_data=None, session=None, *, pin=None, p12_path=None):
    """Return an authenticated TRF5 1G session via A1 certificate + TOTP."""
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
    return _login(session or requests.Session(), config, account, native_signer.A1Signer(key, cert, chain))


def a3_cert_login(portal, session=None, *, signer=None):
    """Return an authenticated session for any PJe portal via the A3 token + the portal's TOTP.

    The token is held under a cross-process lock for the whole login, so parallel calls (or another server
    using the same A3) never interleave PIN logins.
    """
    from jurisync.connectors.pje.auth import a3

    config = portals.pje(portal)
    session = session or requests.Session()
    if signer is not None:
        return _login(session, config, lambda: otp_account(config), signer)
    with a3.token_lock(), a3.A3Signer() as token:
        return _login(session, config, lambda: otp_account(config), token)


def _login(session, config, account, signer):
    """``account`` is the TOTP account, or a callable that returns it only if the SSO asks for the OTP."""
    page = _follow(session, session.get(config.login_url, impersonate="chrome", allow_redirects=False, timeout=30))
    require_allowed_url(page.url)
    log_request("GET", config.login_url, page.status_code)
    _require_available(page, config)
    _, mensagem = _challenge(page.text, config)
    token = str(uuid.uuid4())
    payload = native_signer.build_payload(token, mensagem, signer.sign(mensagem), signer.chain64())
    native_signer.post_result(session, payload)

    soup = BeautifulSoup(page.text, "html.parser")
    form = soup.find("form", id="loginForm")
    if not form or not form.get("action"):
        raise RuntimeError(f"{config.tribunal} login form was not returned")
    action = urllib.parse.urljoin(page.url, form["action"])
    require_allowed_url(action)

    response = _follow(session, _submit_code(session, action, token))
    if _authenticated(response, config.host, config.root):
        log.info("%s certificate login complete (no OTP asked) -> %s", config.key, url_shape(response.url))
        return session
    candidate = BeautifulSoup(response.text, "html.parser").find("form", id="kc-otp-login-form")
    if not candidate or not candidate.get("action"):
        raise RuntimeError("SSO did not advance to OTP after the certificate callback")
    otp_action = urllib.parse.urljoin(response.url, candidate["action"])
    require_allowed_url(otp_action)

    otp = totp(account() if callable(account) else account).now()
    response = session.post(
        otp_action,
        data={"otp": otp, "login": "Validar"},
        impersonate="chrome",
        allow_redirects=False,
        timeout=60,
    )
    log_request("POST", otp_action, response.status_code)
    final = _follow(session, response)
    if not _authenticated(final, config.host, config.root):
        raise RuntimeError(f"{config.tribunal} login did not reach an authenticated PJe page")
    log.info("%s certificate login complete -> %s", config.key, url_shape(final.url))
    return session
