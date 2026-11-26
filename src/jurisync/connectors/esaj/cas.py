"""Login through the e-SAJ CAS (``/sajcas``) and the pool's liveness check.

The login page carries a SHA-256 challenge, the A1 certificate signs it (RSA PKCS#1 v1.5 over the digest) and the form
is posted with the signature and the certificate. There is no mutual TLS and no authenticator. Accounts without an A1
use the login/password tab. The session is the CAS ticket cookie plus the application's own cookie.
"""

import base64
import re
import urllib.parse

from bs4 import BeautifulSoup
from curl_cffi import requests

from jurisync import customers, portals
from jurisync.connectors.esaj import common
from jurisync.core import certificate, credentials
from jurisync.core.http import IMPERSONATE, TIMEOUT, follow_redirects, log_request
from jurisync.core.session_pool import LoginRejected, MissingLogin

SERVICE_PATH = "tarefas-adv/j_spring_cas_security_check"


CHALLENGE = re.compile(r"hashDesafio\s*=\s*'([A-Za-z0-9+/=]+)'")


# The login form fields the browser submits for a certificate login. Nothing else goes: an unchecked
# "magistrado" box or the submit buttons make the CAS refuse a login it would otherwise accept.
FORM_FIELDS = ("lt", "execution", "_eventId", "token")


def has_certificate():
    """Whether the account sets the A1 file and its PIN (``load_certificate`` reads both)."""
    return certificate.is_configured("ESAJ")


def load_certificate(*, pin=None, p12_path=None):
    return certificate.load_configured("ESAJ", pin=pin, p12_path=p12_path)


def challenge_from(html):
    """The base64 SHA-256 challenge the login page asks to be signed."""
    match = CHALLENGE.search(html or "")
    if not match:
        raise common.EsajParseError("e-SAJ login page has no certificate challenge")
    return match.group(1)


def sign_challenge(key, challenge):
    """Base64 signature of the challenge digest: RSA PKCS#1 v1.5 with SHA-256, over the digest as given."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, utils

    try:
        digest = base64.b64decode(challenge, validate=True)
    except ValueError:
        raise common.EsajParseError("e-SAJ challenge is not valid base64") from None
    if len(digest) != hashes.SHA256.digest_size:
        raise common.EsajParseError("e-SAJ challenge is not a SHA-256 digest")
    return base64.b64encode(key.sign(digest, padding.PKCS1v15(), utils.Prehashed(hashes.SHA256()))).decode("ascii")


def certificate_der_b64(cert):
    from cryptography.hazmat.primitives.serialization import Encoding

    return base64.b64encode(cert.public_bytes(Encoding.DER)).decode("ascii")


def login_form(html, page_url, config):
    """``(action_url, fields)`` of the certificate form, with only the hidden fields the browser sends."""
    form = BeautifulSoup(html, "html.parser").find("form", id="formCertificado")
    if form is None or not form.get("action"):
        raise common.EsajParseError("e-SAJ login page has no certificate form")
    action = urllib.parse.urljoin(page_url, form["action"])
    # The action carries a ";jsessionid=..." path parameter; the session cookie already says the same.
    action = urllib.parse.urlunsplit(
        urllib.parse.urlsplit(action)._replace(path=urllib.parse.urlsplit(action).path.split(";")[0])
    )
    common.require_portal_url(config, action, "e-SAJ login form")
    fields = {}
    for name in FORM_FIELDS:
        field = form.find("input", attrs={"name": name})
        if field is None:
            raise common.EsajParseError(f"e-SAJ login form has no {name} field")
        fields[name] = field.get("value", "")
    return action, fields


def login_page(session, config):
    """The CAS login page, reached the way a browser does: through the task panel, which names its own ``service``.

    The CAS checks the ticket against that exact string, and the courts do not all spell it alike (TJAM's panel asks
    for ``tarefas-adv//j_spring_cas_security_check``, with two slashes). A ticket issued for another spelling is refused
    by the panel, which then answers with its ``erroDesconhecido.do`` page. When the panel does not lead to the CAS
    login page, the standard service is asked for directly.
    """
    check = lambda url: common.require_portal_url(config, url)  # noqa: E731
    page = session.get(config.url("tarefas-adv/"), impersonate=IMPERSONATE, allow_redirects=False, timeout=TIMEOUT)
    page = follow_redirects(session, page, check=check, rewrite=common.upgrade_scheme(config))
    log_request("GET", page.url, page.status_code)
    if common.is_cas_login_page(page) and "service=" in (urllib.parse.urlsplit(page.url).query or ""):
        return page
    page = session.get(
        config.url("sajcas/login"),
        params={"service": config.url(SERVICE_PATH)},
        impersonate=IMPERSONATE,
        allow_redirects=False,
        timeout=TIMEOUT,
    )
    page = follow_redirects(session, page, check=check, rewrite=common.upgrade_scheme(config))
    log_request("GET", page.url, page.status_code)
    return page


class EsajTwoFactorRequired(LoginRejected):
    """The CAS accepted the password and e-mailed a validation code: only a person with that mailbox can go on.

    A ``LoginRejected`` so the pool waits out its cool-down instead of having the portal send another e-mail on the
    next call.
    """

    reason = "unexpected"


TWO_FACTOR_ON = re.compile(r"\$\.saj\.cas\.DuploFatorHabilitado\s*=\s*true\b")


PASSWORD_BUTTON = "pbEntrar"


def password_credentials(config):
    """``(username, password)`` from ``<PREFIX>_USERNAME``/``_PASSWORD``, or None when the account has none."""
    username, password = config.env("USERNAME").strip(), config.env("PASSWORD")
    if len(username) == 11 and username.isdigit():
        # The CAS field is masked: a bare CPF is refused as a wrong password, the formatted one is accepted.
        username = f"{username[:3]}.{username[3:6]}.{username[6:9]}-{username[9:]}"
    return (username, password) if username and password else None


def uses_password(config):
    """Password login when the customer's ``portals.toml`` declares a method other than a ``certificate_*`` one for the
    portal; without a declaration, when the account has no A1 certificate and sets ``<PREFIX>_USERNAME``/``_PASSWORD``."""
    declared = customers.declared_method(config)
    if declared:
        return not declared.startswith("certificate")
    file_name, _pin = certificate.credential_names("ESAJ")
    return not credentials.has(file_name) and password_credentials(config) is not None


def password_form(html, page_url, config, username, password):
    """``(action_url, fields)``: the fields a browser posts when "Entrar" is clicked on the login/password tab."""
    action, fields = login_form(html, page_url, config)
    button = BeautifulSoup(html, "html.parser").find("input", attrs={"name": PASSWORD_BUTTON, "type": "submit"})
    fields.update(username=username, password=password, signature="", certificadoSelecionado="")
    if button is not None:
        fields[PASSWORD_BUTTON] = button.get("value", "")
    return action, fields


def login_refusal(html):
    """The CAS's own message for a refused login, redacted, or a generic one."""
    from jurisync.connectors.esaj.parsers import redact_text

    page = BeautifulSoup(html or "", "html.parser")
    for node in page.select("#mensagemRetorno, .mensagemRetorno, .alert, .erro, .msgErro, span.error"):
        text = " ".join(node.get_text(" ").split())
        if text:
            return redact_text(text)[:200]
    return "e-SAJ did not accept the login and password"


def login(portal, *, pin=None, p12_path=None, session=None):
    """An authenticated ``requests.Session``: the A1 certificate when the account has one, else login and password."""
    config = portals.esaj(portal)
    password_login = uses_password(config)
    if password_login:
        account = password_credentials(config)
        if account is None:
            raise MissingLogin(f"Set {config.env_name('USERNAME')} and {config.env_name('PASSWORD')} in .env")
    else:
        key, cert, _chain = load_certificate(pin=pin, p12_path=p12_path)
    session = session or requests.Session()
    page = login_page(session, config)
    if password_login:
        action, fields = password_form(page.text, page.url, config, *account)
    else:
        action, fields = login_form(page.text, page.url, config)
        fields["certificadoSelecionado"] = certificate_der_b64(cert)
        fields["signature"] = sign_challenge(key, challenge_from(page.text))
    response = session.post(action, data=fields, impersonate=IMPERSONATE, allow_redirects=False, timeout=TIMEOUT)
    log_request("POST", action, response.status_code)
    response = follow_redirects(
        session,
        response,
        check=lambda url: common.require_portal_url(config, url, "e-SAJ login redirect"),
        rewrite=common.upgrade_scheme(config),
    )
    if any(cookie.name == "CASTGC" for cookie in session.cookies.jar):
        return session
    if not password_login:
        raise LoginRejected("e-SAJ did not accept the A1 certificate", reason="certificate_refused")
    if TWO_FACTOR_ON.search(response.text or ""):
        raise EsajTwoFactorRequired(
            "e-SAJ accepted the password and sent a validation code to the account's e-mail: the connector cannot "
            "read it. Use the A1 certificate (no code) or have a person log in"
        )
    raise LoginRejected(login_refusal(response.text))


CAS_LOGGED_IN = re.compile(r"usuarioLogadoNoCasServer\s*:\s*(true|false)")


def cas_logged_in(session, config):
    """What the CAS says about its own ticket cookie (``sajcas/verificarLogin.js``, the script every e-SAJ page loads).

    Courts on the older portal (TJAM) have no ``esaj/api/auth/session``; this script is their session check.
    Returns True or False, or None when the answer is not the expected script.
    """
    response = session.get(
        config.url("sajcas/verificarLogin.js"), impersonate=IMPERSONATE, allow_redirects=False, timeout=TIMEOUT
    )
    log_request("GET", response.url, response.status_code)
    match = CAS_LOGGED_IN.search(response.text or "") if response.status_code == 200 else None
    return None if match is None else match.group(1) == "true"


def refresh(portal, session):
    """Pool liveness check: the session API still says the user is logged in (the CAS script where there is no API)."""
    config = portals.esaj(portal)
    try:
        response = session.get(
            config.url("esaj/api/auth/session"), impersonate=IMPERSONATE, allow_redirects=False, timeout=TIMEOUT
        )
        if response.status_code == 404:
            return session if cas_logged_in(session, config) else None
        return session if response.status_code == 200 and response.json().get("usuarioLogado") else None
    except (requests.exceptions.RequestException, ValueError):
        return None
