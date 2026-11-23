"""Projudi "cnj-servlet" generation (TJBA): the CNJ Projudi 1.x servlet front end with a Tencent CAPTCHA on the login.

Observed live on 2026-11-23 (two login attempts, one per account; no session was reached):

- Entry ``/projudi/`` is a frameset; the login is ``form#formLogin`` of ``PaginaPrincipal.jsp`` (``login``, ``senha``,
  hidden ``token``; checkbox ``tipoUsuario`` switches to the party login ``LogonParte``, never used here). Its submit
  always calls ``captchaShow()``; the TCaptcha callback puts ``{"ret":0,"ticket","randstr","appid"}`` in ``token`` and
  posts ``/projudi/Logon``. The CAPTCHA was a slider both times and is answered by ``core.tencent_captcha`` in a
  headless browser; the login itself is then posted here with the browser's cookies (``JSESSIONID``, the load
  balancer's ``ADC_*``/``FADC_PROJUDI``).
- A refused login answers 200 with the login page again plus ``<script>alert('...');</script>`` after the body:
  "Usuário ou senha inválidos." (wrong user or password) or "Senha Expirada! Gentileza resetar sua senha através do
  link ..." (the mandatory reset of 19-20/10/2026). Both were seen with a CAPTCHA the widget had accepted, so the
  portal checks the password after the ticket (an answer it refused has not been seen).

NOT observed (no account could log in): the page after a successful login, a second factor, a forced password change
page, the desk, menu, lists, process page and files. ``refresh`` and the reads are HYPOTHESES from the CNJ 1.x line
(TJPI): a logged-in ``PaginaPrincipal.jsp`` has no ``formLogin``; reads use ``legacy.LegacyAdapter`` (frameset links,
a search form with a process-number field) and documents stay blocked (``DocumentsBlocked``), as opening a Projudi
file can register the ciência of a pending intimação. The portal stays ``wired=False`` until a live session proves
login + list + process + one document.

Safety rules:
- one credential POST per ``login``, sent only after Tencent accepted a CAPTCHA answer; never retried. Any alert that
  refuses the login is ``LoginRejected`` (the pool then stops sending these credentials): ``credentials_refused``,
  ``password_expired``, ``blocked``, or ``unexpected`` for a message not seen before;
- a forced password change or a second-factor page stops the login (``LoginRejected``): nothing is changed or sent;
- the widget's emergency ticket is never used (``core.tencent_captcha.accepted_answer``);
- the connector never uses the Projudi signing password (``SIGN_PASSWORD``).
"""

import re

from curl_cffi import requests
from curl_cffi.requests.exceptions import RequestException

from jurisync import portals
from jurisync.connectors.projudi import base, legacy, parsers
from jurisync.connectors.projudi.legacy import MissingCredentials
from jurisync.core import tencent_captcha
from jurisync.core.http import REDIRECTS, TIMEOUT, follow_redirects, impersonate_for, log_request, require_url
from jurisync.core.session_pool import LoginFailed, LoginRejected

LOGIN_PAGE = "PaginaPrincipal.jsp"
LOGON = "Logon"
MAX_FIELD = 25  # the form's maxlength for both fields
# The portal's answer to a refused login: a script right after the page body, outside any function.
ALERT = re.compile(r"<script[^>]*>\s*alert\(\s*'((?:[^'\\]|\\.)*)'\s*\)\s*;?\s*</script>", re.I)
OTP_FIELD = re.compile(r"otp|totp|autenticador|codigo|c[oó]digo|token", re.I)
REFUSALS = (
    (re.compile(r"senha\s+expirada|resetar\s+sua\s+senha|redefin", re.I), "password_expired"),
    (re.compile(r"bloquead|desativad|inativ|suspens", re.I), "blocked"),
    (re.compile(r"inv[aá]lid|incorret|n[aã]o\s+(encontrad|cadastrad)", re.I), "credentials_refused"),
)
CAPTCHA_ALERT = re.compile(r"captcha|verifica[cç][aã]o", re.I)


def login_credentials(config):
    """``(login, password)``; raises before any request when one is missing."""
    username = "".join(config.env("USERNAME").split())
    password = config.env("PASSWORD")
    missing = [config.env_name(name) for name, value in (("USERNAME", username), ("PASSWORD", password)) if not value]
    if missing:
        raise MissingCredentials(
            f"precisa de login e senha da cliente: set {' and '.join(missing)} (Projudi {config.tribunal})"
        )
    if len(username) > MAX_FIELD or len(password) > MAX_FIELD:
        raise ValueError(f"{config.env_name('USERNAME')} and the password must have up to {MAX_FIELD} characters")
    return username, password


def login_alert(html):
    """The text of the portal's post-login ``alert('...')``, or ``""``."""
    match = ALERT.search(html or "")
    return " ".join(match.group(1).replace("\\'", "'").split()) if match else ""


def refusal_reason(alert):
    """The ``LoginRejected`` reason of a login alert: the first refusal it names, else ``unexpected``."""
    for pattern, reason in REFUSALS:
        if pattern.search(alert):
            return reason
    return "unexpected"


def has_login_form(html):
    return legacy.soup(html).find("form", attrs={"name": "formLogin"}) is not None


def password_change_form(html):
    """A page asking for a new password (two or more password fields): the connector never fills it."""
    return any(
        len(form.find_all("input", attrs={"type": "password"})) >= 2 for form in legacy.soup(html).find_all("form")
    )


def second_factor_form(html):
    """A form asking for a code after the password (not seen on TJBA yet)."""
    for form in legacy.soup(html).find_all("form"):
        if form.get("name") in ("formLogin", "formConsultaPublica"):
            continue
        for field in form.find_all("input"):
            if field.get("type", "text").lower() in ("text", "number", "tel") and OTP_FIELD.search(
                f"{field.get('name', '')} {field.get('id', '')}"
            ):
                return True
    return False


def check_login_answer(html):
    """Raise when the page after ``POST Logon`` is not a logged-in page; return otherwise."""
    if has_login_form(html):
        alert = login_alert(html)
        if not alert:
            raise LoginFailed("Projudi TJBA returned the login page without a message", reason="unexpected")
        if CAPTCHA_ALERT.search(alert) and refusal_reason(alert) == "unexpected":
            raise LoginFailed(f"Projudi TJBA refused the CAPTCHA answer: {alert[:120]}", reason="captcha")
        raise LoginRejected(f"Projudi TJBA refused the login: {alert[:160]}", reason=refusal_reason(alert))
    if password_change_form(html):
        raise LoginRejected(
            "Projudi TJBA asks for a new password: the lawyer must change it in the portal (nothing was changed)",
            reason="password_expired",
        )
    if second_factor_form(html):
        raise LoginRejected(
            "Projudi TJBA asked for a second factor this connector has not mapped yet; nothing was sent",
            reason="unexpected",
        )


class TjbaAdapter(legacy.LegacyAdapter):
    flavor = "cnj-servlet"

    def _follow(self, session, config, response):
        return follow_redirects(
            session,
            response,
            check=lambda url: require_url(url, hosts={config.host}, label="Projudi login redirect"),
            rewrite=legacy.strip_path_params,
            impersonate=impersonate_for(config),
        )

    def login(self, portal, *, session=None, solve=tencent_captcha.solve):
        config = portals.projudi(portal)
        username, password = login_credentials(config)  # nothing is requested before the credentials exist
        page = config.url(LOGIN_PAGE)
        # Raises LoginFailed(reason="captcha") when no answer was accepted: then nothing was posted.
        token, cookies = solve(page)
        session = tencent_captcha.apply(session or requests.Session(), cookies)
        response = session.post(
            config.url(LOGON),
            data={"login": username, "senha": password, "token": token},
            headers={"Referer": page, "Origin": f"https://{config.host}"},
            impersonate=impersonate_for(config),
            allow_redirects=False,
            timeout=TIMEOUT,
        )
        log_request("POST", config.url(LOGON), response.status_code)
        result = self._follow(session, config, response) if response.status_code in REDIRECTS else response
        if result.status_code != 200:
            raise RuntimeError(f"Projudi TJBA login answered HTTP {result.status_code}")
        check_login_answer(parsers.decode(result.content))
        if self.refresh(portal, session) is None:
            raise RuntimeError("Projudi TJBA login did not reach an authenticated page")
        return session

    def refresh(self, portal, session):
        """HYPOTHESIS (no session seen): a logged-in ``PaginaPrincipal.jsp`` has no login form."""
        config = portals.projudi(portal)
        try:
            response = session.get(
                config.url(LOGIN_PAGE), impersonate=impersonate_for(config), allow_redirects=False, timeout=TIMEOUT
            )
        except RequestException:
            return None
        if response.status_code != 200 or has_login_form(parsers.decode(response.content)):
            return None
        return session

    def is_login_redirect(self, response):
        if super().is_login_redirect(response):
            return True
        if response.status_code not in REDIRECTS:
            return False
        location = (response.headers.get("Location") or "").split("?", 1)[0].rstrip("/").lower()
        return location.endswith(("paginaprincipal.jsp", "/logon"))

    def request(self, session, portal, method, path, **kwargs):
        response = super().request(session, portal, method, path, **kwargs)
        if response.status_code == 200 and has_login_form(parsers.decode(response.content)):
            raise base.ProjudiSessionExpired("Projudi TJBA answered with its login page")
        return response


ADAPTER = TjbaAdapter()
