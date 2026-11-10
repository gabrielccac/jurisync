#!/usr/bin/env python3
"""Certificate + TOTP login on the national PJe SSO, with a native signer (A1 file or A3 token).

The SSO login page carries a PJeOffice challenge; the signer signs it, the result goes to the SSO's
``pjeoffice-rest`` callback, and the login form is submitted with the callback's code. Every PJe portal
registered in ``jurisync.portals`` shares this SSO, so the flow is the same for all of them.
"""

import re
import time
import urllib.parse
import uuid
from pathlib import Path

from bs4 import BeautifulSoup
from curl_cffi import requests

from jurisync import portals
from jurisync.connectors.pje.auth import signer as native_signer
from jurisync.connectors.pje.auth.sso import require_allowed_url
from jurisync.core import credentials
from jurisync.core.http import IMPERSONATE, follow_redirects, log, log_request
from jurisync.core.redaction import url_shape
from jurisync.core.totp import accounts_from_image, parse_accounts, parse_index, select_account, totp


def _require_available(page, config=None):
    """A 5xx login page means the portal is down, not that the login flow changed."""
    if page.status_code >= 500:
        name = f"{config.tribunal} PJe" if config else "PJe"
        raise RuntimeError(f"{name} is unavailable (HTTP {page.status_code}); try again later")


def _challenge(html, config):
    match = re.search(r"autenticar\('([^']+)',\s*'([^']+)'\)", html)
    if not match:
        raise RuntimeError(f"{config.tribunal} login page has no certificate challenge")
    return match.group(1), match.group(2)


def _legacy_challenge(html):
    """PJe 1.x-style pages (TJRJ 2G): ASSINATURA_MENSAGEM signed like PJeOffice.

    Returns (mensagem, enviarPara): the challenge and the PJeOffice callback path
    (``/logarPJeOffice.seam?cid=...``), per the PJeOffice protocol (tarefa
    ``cnj.autenticador`` posts ``{uuid, mensagem, assinatura, certChain}``).
    """
    match = re.search(r'ASSINATURA_MENSAGEM\s*=\s*"([^"]+)"', html)
    cid = re.search(r'CONVERSATION_ID\s*=\s*"([^"]+)"', html)
    if not match or not cid:
        return None
    return match.group(1), "/logarPJeOffice.seam?cid=" + cid.group(1)


def _legacy_login(session, config, page, signer):
    """Sign the legacy challenge like PJeOffice and post to its callback."""
    import base64
    import json

    found = _legacy_challenge(page.text)
    if not found:
        return None
    mensagem, enviar_para = found
    # PJeOffice concatenates servidor (the app root) + enviarPara as plain strings.
    action = config.base_url.rstrip("/") + "/" + enviar_para.lstrip("/")
    require_allowed_url(action)
    token = str(uuid.uuid4())
    body = json.dumps(
        {
            "uuid": token,
            "mensagem": mensagem,
            "assinatura": base64.b64encode(signer.sign(mensagem)).decode(),
            "certChain": signer.chain64(),
        }
    )
    response = session.post(
        action,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "PJeOffice/2.5.16",
            "versao": "2.5.16",
        },
        impersonate=IMPERSONATE,
        allow_redirects=False,
        timeout=60,
    )
    log_request("POST", action, response.status_code)
    return _follow(session, response)


def _submit_code(sso_session, action, token):
    response = sso_session.post(
        action,
        data={"username": "", "password": "", "pjeoffice-code": token, "phrase": ""},
        impersonate=IMPERSONATE,
        allow_redirects=False,
        timeout=60,
    )
    log_request("POST", action, response.status_code)
    return response


def _upgrade_pje_http(url):
    parts = urllib.parse.urlsplit(url)
    if parts.scheme.lower() == "http" and (parts.hostname or "").lower() in portals.hosts("pje"):
        # JT SSO bounces back over plain http (pjekz); the same path serves https.
        return "https://" + url.split("://", 1)[1]
    return url


def _follow(sso_session, response, hops=6):
    return follow_redirects(
        sso_session, response, check=require_allowed_url, hops=hops, rewrite=_upgrade_pje_http, timeout=30
    )


def _authenticated(response, host, root="/pje/"):
    soup = BeautifulSoup(response.text, "html.parser")
    shell = "Painel do advogado" in response.text
    return (
        response.status_code == 200
        and urllib.parse.urlsplit(response.url).hostname == host
        and (
            (
                bool(soup.find("a", href=re.compile(re.escape(root) + r"logout\.seam")))
                and bool(soup.find("input", attrs={"name": "javax.faces.ViewState"}))
            )
            # JT portals land on a shell page (via /pjekz/ + error.seam forward) that carries
            # the user profile and menus instead of the logout/ViewState markers.
            or shell
        )
    )


def _twofa(portal, twofa_data=None):
    """The TOTP account this portal's SSO asks for after the certificate, when it does.

    <PREFIX>_2FA_DATA (or _2FA_QR), with the optional <PREFIX>_OTP_PROFILE_MATCH / _OTP_PROFILE_INDEX selectors.
    """
    config = portals.pje(portal)
    data = twofa_data or config.env("2FA_DATA")
    qr_path = config.env("2FA_QR")
    if data:
        accounts, label = parse_accounts(data), f"{config.env_prefix}_2FA_DATA"
    elif qr_path:
        accounts, label = accounts_from_image(Path(qr_path)), f"{config.env_prefix}_2FA_QR"
    else:
        return None
    return select_account(
        accounts,
        match=config.env("OTP_PROFILE_MATCH"),
        index=parse_index(config.env("OTP_PROFILE_INDEX"), f"{config.env_prefix}_OTP_PROFILE_INDEX"),
        label=label,
    )


# A login that stops short of the authenticated page is often a TOTP code the SSO already took or one that expired
# mid-flight. The same code is not valid twice, so the retry waits for the next 30-second window.
RETRYABLE_LOGIN = ("did not reach an authenticated PJe page", "SSO did not advance")
TOTP_WINDOW = 30


def _seconds_to_next_window(now):
    return TOTP_WINDOW - (now % TOTP_WINDOW) + 1


def cert_login(portal, twofa_data=None, session=None, *, pin=None, p12_path=None, sleep=time.sleep, clock=time.time):
    """Return an authenticated PJe session via A1 certificate (+ TOTP when the SSO asks).

    One failed attempt is repeated once, on a new session, after the TOTP window changes.
    """
    config = portals.pje(portal)
    account = _twofa(portal, twofa_data)
    pin = pin or credentials.env("PJE_A1_PIN")
    if not pin:
        raise RuntimeError("Set PJE_A1_PIN to the A1 certificate password")
    signer = native_signer.A1Signer(*native_signer.load_a1(p12_path, pin))
    try:
        return _login(session or requests.Session(), config, account, signer)
    except RuntimeError as error:
        if account is None or not any(marker in str(error) for marker in RETRYABLE_LOGIN):
            raise
        log.info("%s login stopped short (%s); retrying in the next TOTP window", config.key, error)
        sleep(_seconds_to_next_window(clock()))
        return _login(requests.Session(), config, account, signer)


def a3_cert_login(portal, session=None, *, signer=None):
    """Return an authenticated session for any PJe portal via the A3 token + the portal's TOTP.

    The token is held under a cross-process lock for the whole login, so parallel calls (or another server
    using the same A3) never interleave PIN logins. The TOTP account is read only if the SSO asks for it.
    """
    from jurisync.connectors.pje.auth import a3

    config = portals.pje(portal)
    session = session or requests.Session()

    def account():
        found = _twofa(portal)
        if found is None:
            raise RuntimeError(f"SSO asks for a TOTP code: set {config.env_prefix}_2FA_DATA")
        return found

    if signer is not None:
        return _login(session, config, account, signer)
    with a3.token_lock(), a3.A3Signer() as token:
        return _login(session, config, account, token)


def _login(session, config, account, signer):
    """``account`` is the TOTP account (None when not configured), or a callable read only if the SSO asks."""
    page = _follow(session, session.get(config.login_url, impersonate=IMPERSONATE, allow_redirects=False, timeout=30))
    require_allowed_url(page.url)
    log_request("GET", config.login_url, page.status_code)
    _require_available(page, config)
    try:
        _, mensagem = _challenge(page.text, config)
    except RuntimeError:
        # PJe 1.x-style pages (TJRJ 2G): sign ASSINATURA_MENSAGEM like PJeOffice.
        legacy = _legacy_login(session, config, page, signer)
        if legacy is not None and _authenticated(legacy, config.host, config.root):
            log.info("%s legacy certificate login complete -> %s", config.key, url_shape(legacy.url))
            return session
        # Portals like TRT7 serve no inline challenge: their login page only offers
        # PDPJ SSO, entered via authenticateSSO.seam, which redirects to the shared SSO.
        entry = urllib.parse.urljoin(config.login_url, "authenticateSSO.seam")
        require_allowed_url(entry)
        page = _follow(session, session.get(entry, impersonate=IMPERSONATE, allow_redirects=False, timeout=30))
        require_allowed_url(page.url)
        log_request("GET", entry, page.status_code)
        _require_available(page, config)
        _, mensagem = _challenge(page.text, config)
    token = str(uuid.uuid4())
    payload = native_signer.build_payload(token, mensagem, signer.sign(mensagem), signer.chain64())
    native_signer.post_result(session, payload)

    soup = BeautifulSoup(page.text, "html.parser")
    # TRF5-style pages post the signed challenge to loginForm; JT SSO pages
    # (via authenticateSSO.seam) use kc-form-login with the same pjeoffice fields.
    form = soup.find("form", id="loginForm") or soup.find("form", id="kc-form-login")
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
        raise RuntimeError("SSO did not advance after the certificate callback")
    if account is None:
        raise RuntimeError(f"SSO asks for a TOTP code: set {config.env_prefix}_2FA_DATA")
    otp_action = urllib.parse.urljoin(response.url, candidate["action"])
    require_allowed_url(otp_action)

    otp = totp(account() if callable(account) else account).now()
    response = session.post(
        otp_action,
        data={"otp": otp, "login": "Validar"},
        impersonate=IMPERSONATE,
        allow_redirects=False,
        timeout=60,
    )
    log_request("POST", otp_action, response.status_code)
    final = _follow(session, response)
    if not _authenticated(final, config.host, config.root):
        # JT portals park the flow on a /pjekz/ loader: probe an app page, whose
        # error.seam forward renders the authenticated shell when the session holds.
        probe = session.get(config.search_url, impersonate=IMPERSONATE, allow_redirects=False, timeout=30)
        log_request("GET", config.search_url, probe.status_code)
        final = _follow(session, probe)
    if not _authenticated(final, config.host, config.root):
        raise RuntimeError(f"{config.key} login did not reach an authenticated PJe page")
    log.info("%s certificate login complete -> %s", config.key, url_shape(final.url))
    return session
