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
from jurisync.core import awswaf, credentials
from jurisync.core.http import IMPERSONATE, follow_redirects, log, log_request
from jurisync.core.redaction import url_shape
from jurisync.core.session_pool import LoginRejected
from jurisync.core.totp import accounts_from_image, parse_accounts, portal_account, totp


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

    Returns (mensagem, cid): the challenge and the conversation the PJeOffice callback
    (``/logarPJeOffice.seam?cid=...``) and the landing page (``/homePJeOffice.seam?cid=...``) belong to.
    """
    match = re.search(r'ASSINATURA_MENSAGEM\s*=\s*"([^"]+)"', html)
    cid = re.search(r'CONVERSATION_ID\s*=\s*"([^"]+)"', html)
    if not match or not cid:
        return None
    return match.group(1), cid.group(1)


# The PJeOffice version string sent in the ``versao`` header, as PJeOffice Pro does.
PJEOFFICE_VERSION = "2.5.16"


def _legacy_login(session, config, page, signer):
    """Sign the legacy challenge and post it to the page's PJeOffice callback, as PJeOffice's ``cnj.autenticador`` does.

    PJeOffice (``PjeWebClient``) posts a form, not JSON: ``assinatura`` (base64 MD5withRSA over the UTF-8 challenge)
    and ``cadeiaCertificado`` (base64 Java PkiPath, root first), with the browser's cookies and a ``versao`` header.
    The callback answers ``Sucesso`` or ``Erro:<motivo>``; on success the browser goes to ``homePJeOffice.seam``
    of the same conversation. Some portals (TRF5 ``pje.trf5.jus.br``) answer instead with an empty 301/302 whose
    ``Location`` is the landing page (the notice board): that redirect is the success and the browser opens it.
    """
    import base64

    found = _legacy_challenge(page.text)
    if not found:
        return None
    mensagem, cid = found
    root = config.base_url.rstrip("/")
    action = f"{root}/logarPJeOffice.seam?cid={cid}"
    require_allowed_url(action)
    response = session.post(
        action,
        data={
            "assinatura": base64.b64encode(signer.sign(mensagem)).decode("ascii"),
            "cadeiaCertificado": signer.pkipath64(),
        },
        headers={"versao": PJEOFFICE_VERSION},
        impersonate=IMPERSONATE,
        allow_redirects=False,
        timeout=60,
    )
    log_request("POST", action, response.status_code)
    answer = (response.text or "").strip()
    home = _legacy_redirect(response, config)
    if home is None:
        if not answer.startswith("Sucesso"):
            reason = answer[len("Erro:") :] if answer.startswith("Erro:") else f"HTTP {response.status_code}"
            reason = " ".join(BeautifulSoup(reason, "html.parser").get_text(" ").split())[:500]
            if re.search(r"Certificado n[ãa]o encont", reason):
                # The callback read the signature and the chain and found no user for the holder's certificate.
                raise RuntimeError(
                    f"{config.key}: the portal found no user for this certificate (Certificado não encontrado); "
                    f"the lawyer is probably not registered in this PJe. Portal message: {reason}"
                )
            raise RuntimeError(f"{config.key}: the PJeOffice callback refused the certificate login: {reason}")
        home = f"{root}/homePJeOffice.seam?cid={cid}"
    require_allowed_url(home)
    landing = _follow(session, session.get(home, impersonate=IMPERSONATE, allow_redirects=False, timeout=60))
    log_request("GET", home, landing.status_code)
    return landing


def _legacy_redirect(response, config):
    """The landing URL of a 301/302 answer to the PJeOffice callback, or None when it is not a login success.

    The ``Location`` must stay on the portal's host (``require_allowed_url``) and must not send the user back to
    ``login.seam``, which is what a refused login does.
    """
    if response.status_code not in (301, 302):
        return None
    location = (response.headers.get("Location") or "").strip()
    if not location:
        return None
    target = urllib.parse.urljoin(response.url or config.login_url, location)
    require_allowed_url(target)
    parts = urllib.parse.urlsplit(target)
    if parts.hostname != config.host or parts.path.endswith("/login.seam"):
        return None
    return target


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


# Where PJe sends a user it does not know: the pre-registration page itself (PJe-JT), or the SSO registration
# callback that forwards to it by script (TJCE/TJPE/TJPI 2G, TRF3 2G, TJBA 1G).
UNREGISTERED_PATHS = ("/publico/usuario/cadastro", "/publico/usuario/ssoCadastroCallback")


def _require_registered(response, config):
    """PJe sends a user it does not know to the public pre-registration page, after the SSO accepted the login.

    That is not a wrong TOTP code or certificate: another attempt gets the same answer and spends another code,
    so it raises ``LoginRejected`` (never retried, and the pool holds further logins back for a while).
    """
    path = urllib.parse.urlsplit(response.url).path
    if path.endswith("/errorUnexpected.seam"):
        raise RuntimeError(f"{config.key}: the portal returned errorUnexpected.seam; no authenticated session")
    if any(marker in path for marker in UNREGISTERED_PATHS):
        raise LoginRejected(
            f"{config.key}: the SSO accepted the certificate and the TOTP code, but this user is not registered in "
            "this PJe (it redirected to its pre-registration page, cadastro.seam); ask the court for the lawyer's "
            "registration in this PJe. Retrying will not help."
        )


def _authenticated(response, host, root="/pje/"):
    # The logout link is ``<root>logout.seam`` on most portals; TRF5's PJe 1.x (pje.trf5.jus.br) links
    # ``home.seam?actionMethod=...:identity.logout`` instead.
    if urllib.parse.urlsplit(response.url).path.endswith("/errorUnexpected.seam"):
        return False
    soup = BeautifulSoup(response.text, "html.parser")
    shell = "Painel do advogado" in response.text
    return (
        response.status_code == 200
        and urllib.parse.urlsplit(response.url).hostname == host
        and (
            (
                bool(soup.find("a", href=re.compile(re.escape(root) + r"logout\.seam|identity\.logout")))
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
        return portal_account(config, parse_accounts(data))
    if qr_path:
        return portal_account(config, accounts_from_image(Path(qr_path)))
    return None


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
        try:
            return _login(requests.Session(), config, account, signer)
        except RuntimeError as second:
            if not any(marker in str(second) for marker in RETRYABLE_LOGIN):
                raise
            # Two codes in two windows were refused: stop here, or every tool call spends two more attempts.
            raise LoginRejected(f"{second} (twice; check the TOTP secret and the certificate)") from None


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
    if awswaf.is_challenge(page):
        # TJES 1G: an AWS WAF CAPTCHA answers instead of PJe until the session carries its token.
        log.info("%s shows an AWS WAF CAPTCHA; solving it in a browser", config.key)
        awswaf.apply(session, awswaf.solve(config.login_url))
        page = _follow(
            session, session.get(config.login_url, impersonate=IMPERSONATE, allow_redirects=False, timeout=30)
        )
        require_allowed_url(page.url)
        log_request("GET", config.login_url, page.status_code)
        if awswaf.is_challenge(page):
            raise RuntimeError(f"{config.key}: the AWS WAF still asks for a CAPTCHA after it was solved")
    _require_available(page, config)
    try:
        _, mensagem = _challenge(page.text, config)
    except RuntimeError:
        # PJe 1.x-style pages (TJRJ 2G): sign ASSINATURA_MENSAGEM like PJeOffice.
        legacy = _legacy_login(session, config, page, signer)
        if legacy is not None:
            _require_registered(legacy, config)
            if not _authenticated(legacy, config.host, config.root):
                raise RuntimeError(
                    f"{config.key}: the PJeOffice login was accepted but no authenticated page followed"
                ) from None
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
    _require_registered(response, config)
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
    _require_registered(final, config)
    if not _authenticated(final, config.host, config.root):
        # JT portals park the flow on a /pjekz/ loader: probe an app page, whose
        # error.seam forward renders the authenticated shell when the session holds.
        probe = session.get(config.search_url, impersonate=IMPERSONATE, allow_redirects=False, timeout=30)
        log_request("GET", config.search_url, probe.status_code)
        final = _follow(session, probe)
    _require_registered(final, config)
    if not _authenticated(final, config.host, config.root):
        raise RuntimeError(f"{config.key} login did not reach an authenticated PJe page")
    log.info("%s certificate login complete -> %s", config.key, url_shape(final.url))
    return session
