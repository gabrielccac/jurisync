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

from bs4 import BeautifulSoup
from curl_cffi import requests

from jurisync import portals
from jurisync.connectors.pje.auth import signer as native_signer
from jurisync.connectors.pje.auth.sso import alert_text, https_pje, require_allowed_url
from jurisync.core import a3, awswaf, credentials
from jurisync.core.certificate import credential_names, load_configured
from jurisync.core.http import IMPERSONATE, follow_redirects, log, log_request
from jurisync.core.redaction import url_shape
from jurisync.core.session_pool import LoginFailed, LoginRejected, MissingLogin
from jurisync.core.totp import configured_account, fresh_code, next_code


def _require_available(page, config=None):
    """A 5xx login page means the portal is down, not that the login flow changed."""
    if page.status_code >= 500:
        name = f"{config.tribunal} PJe" if config else "PJe"
        raise LoginFailed(
            f"{name} is unavailable (HTTP {page.status_code}); try again later", reason="portal_unavailable"
        )


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
                raise LoginRejected(
                    f"{config.key}: the portal found no user for this certificate (Certificado não encontrado); "
                    f"the lawyer is probably not registered in this PJe. Portal message: {reason}",
                    reason="not_registered",
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


def _follow(sso_session, response, hops=6):
    return follow_redirects(sso_session, response, check=require_allowed_url, hops=hops, rewrite=https_pje, timeout=30)


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
            "registration in this PJe. Retrying will not help.",
            reason="not_registered",
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
    return configured_account(portals.pje(portal), None, data=twofa_data)


# The TOTP window: a code the SSO refuses is answered once more with the next window's code (a recent login may
# have spent the current one). Kept for callers that wait for a fresh window.
TOTP_WINDOW = 30


def _seconds_to_next_window(now):
    return TOTP_WINDOW - (now % TOTP_WINDOW) + 1


def cert_login(portal, twofa_data=None, session=None, *, pin=None, p12_path=None, sleep=time.sleep, clock=time.time):
    """Return an authenticated PJe session via A1 certificate (+ TOTP when the SSO asks).

    The login is never repeated as a whole: a refused code is answered once more on the SSO's own form, in the next
    window (``_answer_otp``), and any other stop is reported with where it stopped (``LoginFailed``).
    """
    config = portals.pje(portal)
    account = _twofa(portal, twofa_data)
    try:
        signer = native_signer.A1Signer(*load_configured(pin=pin, p12_path=p12_path))
    except MissingLogin:
        file_name, pin_name = credential_names()
        if p12_path is not None or credentials.has(file_name):
            raise
        # No certificate at all: "set the PIN" would point at a login this account never had.
        raise MissingLogin(
            f"No login is configured for {config.key}: set {config.env_name('USERNAME')}, "
            f"{config.env_name('PASSWORD')} and {config.env_name('2FA_DATA')} (or {config.env_name('2FA_QR')}) for "
            f"the password login, or {file_name} and {pin_name} for the A1 certificate"
        ) from None
    # ``sleep``/``clock`` stay for callers of the earlier whole-login retry; the code wait lives in ``_answer_otp``.
    return _login(session or requests.Session(), config, account, signer)


def a3_cert_login(portal, session=None, *, signer=None):
    """Return an authenticated session for any PJe portal via the A3 token + the portal's TOTP.

    The token is held under a cross-process lock for the whole login, so parallel calls (or another server
    using the same A3) never interleave PIN logins. The TOTP account is read only if the SSO asks for it.
    """
    config = portals.pje(portal)
    session = session or requests.Session()

    def account():
        found = _twofa(portal)
        if found is None:
            raise MissingLogin(f"SSO asks for a TOTP code: set {config.env_prefix}_2FA_DATA")
        return found

    with a3.held_token(signer) as token:
        return _login(session, config, account, token)


def _page_shape(response):
    """Where a login stopped, without identifiers: HTTP status, URL path, page title and the SSO's alert, if any."""
    soup = BeautifulSoup(getattr(response, "text", "") or "", "html.parser")
    title = " ".join((soup.title.get_text(" ") if soup.title else "").split())[:80]
    alert = alert_text(soup)
    shape = f"HTTP {response.status_code} at {url_shape(response.url)}, title {title!r}"
    return shape + (f', SSO message "{alert}"' if alert else "")


def _otp_form(response):
    return BeautifulSoup(getattr(response, "text", "") or "", "html.parser").find("form", id="kc-otp-login-form")


def _answer_otp(session, response, account, config, *, sleep=time.sleep, clock=time.time, follow=None):
    """Answer the SSO's OTP form; when the SSO shows the form again (it refused the code), once more next window.

    A form shown again after both codes is a real refusal (``LoginRejected``, ``otp_refused``) with the SSO's own
    message. Anything else is returned for the caller to recognise: no further code is ever sent.
    """
    for attempt in range(2):
        form = _otp_form(response)
        if form is None or not form.get("action"):
            return response
        account = account() if callable(account) else account
        action = urllib.parse.urljoin(response.url, form["action"])
        require_allowed_url(action)
        if attempt:
            log.info(
                "%s SSO refused the code (%s); answering with the next window's",
                config.key,
                alert_text(BeautifulSoup(response.text, "html.parser")) or "no message",
            )
        code = (
            next_code(account, sleep=sleep, now=clock()) if attempt else fresh_code(account, clock=clock, sleep=sleep)
        )
        refused = response
        response = session.post(
            action,
            data={"otp": code, "login": "Validar"},
            impersonate=IMPERSONATE,
            allow_redirects=False,
            timeout=60,
        )
        log_request("POST", action, response.status_code)
        response = follow(response) if follow else _follow(session, response)
    if _otp_form(response) is not None:
        alert = alert_text(BeautifulSoup(response.text, "html.parser")) or alert_text(
            BeautifulSoup(refused.text, "html.parser")
        )
        raise LoginRejected(
            f"{config.key}: the SSO refused the authenticator code twice, in two windows"
            + (f' (SSO message: "{alert}")' if alert else " (the form came back without a message)")
            + "; check the TOTP secret chosen for this account (OTP_PROFILE_MATCH/INDEX)",
            reason="otp_refused",
        )
    return response


def _submit_certificate(session, page, config, signer, mensagem):
    """Sign the login page's challenge and submit its form with the callback's code; the answer is not followed."""
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
    return _submit_code(session, action, token)


def sso_login(session, config, page, account, signer, *, follow, sleep=time.sleep, clock=time.time):
    """The certificate and code steps of another system's login (JPe) on the SSO ``page`` it opened.

    ``follow(response)`` follows the caller's redirects; the page they end on is returned.
    """
    _, mensagem = _challenge(page.text, config)
    response = follow(_submit_certificate(session, page, config, signer, mensagem))
    return _answer_otp(session, response, account, config, sleep=sleep, clock=clock, follow=follow)


def _login(session, config, account, signer, *, sleep=time.sleep, clock=time.time):
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
            raise LoginFailed(
                f"{config.key}: the AWS WAF still asks for a CAPTCHA after it was solved", reason="captcha"
            )
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
    response = _follow(session, _submit_certificate(session, page, config, signer, mensagem))
    _require_registered(response, config)
    if _authenticated(response, config.host, config.root):
        log.info("%s certificate login complete (no OTP asked) -> %s", config.key, url_shape(response.url))
        return session
    if _otp_form(response) is None or not _otp_form(response).get("action"):
        raise LoginFailed(
            f"{config.key}: the SSO did not advance after the certificate callback ({_page_shape(response)})",
            reason="unexpected",
        )
    if account is None:
        raise MissingLogin(f"SSO asks for a TOTP code: set {config.env_prefix}_2FA_DATA")
    final = _answer_otp(session, response, account, config, sleep=sleep, clock=clock)
    _require_registered(final, config)
    if not _authenticated(final, config.host, config.root):
        # JT portals park the flow on a /pjekz/ loader: probe an app page, whose
        # error.seam forward renders the authenticated shell when the session holds.
        landed = final
        probe = session.get(config.search_url, impersonate=IMPERSONATE, allow_redirects=False, timeout=30)
        log_request("GET", config.search_url, probe.status_code)
        final = _follow(session, probe)
        _require_registered(final, config)
        if not _authenticated(final, config.host, config.root):
            # The code was taken (no OTP form came back): this is not a refusal, and a second code would not help.
            raise LoginFailed(
                f"{config.key}: the SSO accepted the authenticator code, but the login ended on a page not "
                f"recognised as logged in ({_page_shape(landed)}; the search page then answered "
                f"{_page_shape(final)}); no other code was sent",
                reason="unexpected",
            )
    log.info("%s certificate login complete -> %s", config.key, url_shape(final.url))
    return session
