"""Keycloak, the SSO in front of the PDPJ (PJe, JPe), the Keycloak Eproc portals and Projudi TJPR/TJRR: what its
pages look like, in one place. The form ids of each step, where each theme puts the message of a refused step, which
wording is a refusal (password, expired password, blocked account) and which is only a broken flow, and the username
the PDPJ theme expects. Connectors keep their own requests and their own route checks; they read the pages here.
"""

import re

from bs4 import BeautifulSoup

from jurisync.core.session_pool import LoginFailed, LoginRejected

PDPJ_SSO_HOST = "sso.cloud.pje.jus.br"  # the national SSO of the PDPJ (realm pje)
LOGIN_FORM = "kc-form-login"  # user and password (PDPJ, Eproc, Projudi themes)
TRF_LOGIN_FORM = "loginForm"  # the TRF-style theme of the PDPJ login
OTP_FORM = "kc-otp-login-form"  # the authenticator code
LOGIN_BUTTON = "kc-login"  # the submit button of the login and code forms
ERROR_MESSAGE = "kc-error-message"  # the error page's message
FEEDBACK_TEXT = "kc-feedback-text"  # the feedback alert of the pje-v2 theme
X509_FORM = "kc-x509-login-info"  # "continue with this certificate" after the mutual-TLS handshake
# Required actions Keycloak may put before the login ends (set up the authenticator, new password, terms, profile):
# a connector stops there, it never acts on the account.
ACCOUNT_ACTION_FORMS = ("kc-totp-settings-form", "kc-passwd-update-form", "kc-terms-form", "kc-update-profile-form")


def find_form(html, form_id):
    """The ``form`` with this id in a page, or None."""
    return BeautifulSoup(html or "", "html.parser").find("form", id=form_id)


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
# Keycloak's own flow messages (pt-BR and en): the login session or its cookie was lost or timed out, the request
# was out of step ("Invalid Request", "Você já está autenticado"). None says anything about the password.
FLOW_ERRORS = (
    "cookie",
    "expirou",
    "expirad",
    "expired",
    "tempo esgotado",
    "timeout",
    "timed out",
    "sessão",
    "sessao",
    "session",
    "requisição inválida",
    "solicitação inválida",
    "invalid request",
    "já está autenticado",
    "já está logado",
    "already logged in",
    "erro inesperado",
    "unexpected error",
)


def mask(text):
    """The text with every CPF/CNPJ and e-mail replaced by […]: what the SSO echoes back never leaves in a message."""
    return IDENTIFIERS.sub("[…]", text)


def alert_text(soup):
    """The SSO's own alert on the page (one line, at most 200 characters, identifiers masked), else ""."""
    for selector in ALERT_SELECTORS:
        for tag in soup.select(selector):
            for link in tag.find_all("a"):
                link.decompose()  # "Voltar para login"
            text = " ".join(" ".join(tag.stripped_strings).split())
            if text:
                return mask(text)[:200]
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


# What each refused ``credential_failure`` kind is in the shared login vocabulary (``session_pool.REASONS``).
REFUSED_KINDS = {"credentials": "credentials_refused", "expired": "password_expired", "blocked": "blocked"}


def credential_refusal(html, other=LoginFailed, detail=""):
    """The exception for a credential POST that did not reach the OTP step (PJe and JPe share this SSO).

    A refusal is ``LoginRejected`` with its reason; anything else is ``other`` (``captcha`` for a WAF page). A
    Keycloak flow error (lost login session or cookie, timeout, out-of-step request) is marked ``flow_error``:
    nothing was refused, so one fresh login may follow.
    """
    kind, message = credential_failure(html)
    message = f"{message} ({detail})" if detail else message
    if kind in REFUSED_KINDS:
        return LoginRejected(message, reason=REFUSED_KINDS[kind])
    error = other(message)
    error.reason = "captcha" if kind == "waf" else "unexpected"
    error.flow_error = kind == "flow"
    return error


def sso_username(username, form_id):
    """The username as the PDPJ theme of the PJe-JT login (``kc-form-login``) expects it: a CPF/CNPJ as digits.

    Its page asks for "CPF/CNPJ" and its own submit script strips ``.``, ``-`` and ``/``; the TRF-style theme
    (``loginForm``) gets the value as configured, which those portals already accept.
    """
    value = username.strip()
    if form_id == LOGIN_FORM and re.fullmatch(r"[\d.\-/ ]+", value):
        digits = re.sub(r"\D", "", value)
        if len(digits) in (11, 14):
            return digits
    return value


def login_form_id(html):
    """The SSO theme's login form: ``loginForm`` (TRF-style) or ``kc-form-login`` (PDPJ)."""
    return TRF_LOGIN_FORM if find_form(html, TRF_LOGIN_FORM) else LOGIN_FORM


def credentials_form(username, password, form_id):
    """What the SSO's login form posts: the user and password, the PJeOffice fields empty."""
    return {"username": sso_username(username, form_id), "password": password, "pjeoffice-code": "", "phrase": ""}
