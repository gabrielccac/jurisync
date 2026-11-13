"""Projudi "legacy-keycloak" generation (TJPR, TJRR): a Keycloak SSO in front of the Projudi frameset.

STATUS: built against public pages and simulated responses only. No authenticated session was ever opened, so the
portals stay ``wired=False`` in ``portals.py`` and this adapter is not registered in ``server.ADAPTERS``. What is
real (observed on public pages or live) and what is a hypothesis is in ``docs/connectors/projudi-tjpr-prep.md`` and
``docs/connectors/projudi-tjrr-prep.md``.

Login: the Projudi entry page redirects to a Keycloak (realm ``projudi``) that asks CPF/CNPJ + password
(``form#kc-form-login``) and, when enforced, a TOTP (``form#kc-otp-login-form``). TJPR: ``sso.projudi.tjpr.jus.br``
under ``/auth``; TJRR: ``keycloak.tjrr.jus.br`` without ``/auth`` and entry ``tjpr/autenticacao.do``. There is no A1
login: TJPR's certificate button (``projudicrt``) answered a real A1 with ERROA3=601 "Certificado utilizado não é A3"
(2026-11-13), and TJRR offers no certificate login (its TLS endpoints never request a client certificate).

Safety rules of this adapter (the read rules are in ``legacy.py``):
- one credential POST and at most one TOTP POST per ``login``; no retry (a few wrong passwords lock the account) and
  ``SessionPool`` blocks further logins after ``LoginRejected``;
- only what a browser sends is posted: hidden fields, user, password, code. Never "remember me" / "trust device";
- a CAPTCHA or human-verification page stops the login (``HumanVerificationRequired``), never solved or bypassed;
- a Keycloak required action (set up the authenticator, update the password, accept terms) stops the login
  (``AccountActionRequired``); this adapter never registers a TOTP or changes the account.
"""

import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from curl_cffi import requests
from curl_cffi.requests.exceptions import RequestException

from jurisync import portals
from jurisync.connectors.projudi import legacy, parsers
from jurisync.connectors.projudi.legacy import (  # noqa: F401 - re-exported: callers import them from here
    DocumentsBlocked,
    HumanVerificationRequired,
    MissingCredentials,
    hidden_fields,
    soup,
    strip_path_params,
    visible_text,
)
from jurisync.core.http import TIMEOUT, follow_redirects, impersonate_for, log_request, require_url
from jurisync.core.session_pool import LoginRejected
from jurisync.core.totp import accounts_from_image, parse_accounts, portal_account, totp

USERNAME_PATTERN = re.compile(r"(\d{11}|[A-Z0-9]{12}\d{2})")
PUBLIC_HOME_MARKER = "acesso ao sistema"
ACCOUNT_ACTION_FORMS = ("kc-totp-settings-form", "kc-passwd-update-form", "kc-terms-form", "kc-update-profile-form")
ACCOUNT_ACTION_TEXT = re.compile(
    r"configura[cç][aã]o do autenticador|mobile authenticator setup|atualizar senha|update password|termos de uso",
    re.I,
)


class AccountActionRequired(RuntimeError):
    """Keycloak asks for an account action (authenticator setup, new password, terms) that this adapter never takes."""


@dataclass(frozen=True)
class Sso:
    host: str
    realm: str
    entry: str = "autenticacao.do"  # the Projudi page that starts the OIDC redirect
    auth_prefix: str = "/auth"  # Keycloak 17+ serves under /realms without /auth

    @property
    def path_prefix(self):
        return f"{self.auth_prefix}/realms/{self.realm}/"


# One entry per portal: this adapter is configured, not copied.
SSO = {
    "tjpr-projudi-1g": Sso("sso.projudi.tjpr.jus.br", "projudi"),
    "tjrr-1g": Sso("keycloak.tjrr.jus.br", "projudi", entry="tjpr/autenticacao.do", auth_prefix=""),
}


def sso_for(config):
    sso = SSO.get(config.key)
    if sso is None:
        raise NotImplementedError(f"Projudi {config.tribunal} ({config.key}) has no Keycloak configuration yet")
    return sso


def login_credentials(config):
    """``(username, password)`` from the customer's configuration; raises before any request when one is missing."""
    username = re.sub(r"[.\-/\s]", "", config.env("USERNAME")).upper()
    password = config.env("PASSWORD")
    missing = [config.env_name(name) for name, value in (("USERNAME", username), ("PASSWORD", password)) if not value]
    if missing:
        raise MissingCredentials(
            f"precisa de login e senha da cliente: set {' and '.join(missing)} (Projudi {config.tribunal})"
        )
    if not USERNAME_PATTERN.fullmatch(username):
        raise ValueError(f"{config.env_name('USERNAME')} must be a CPF (11 digits) or a CNPJ")
    return username, password


def otp_account(config):
    data, qr_path = config.env("2FA_DATA"), config.env("2FA_QR")
    if not data and not qr_path:
        raise MissingCredentials(
            f"Projudi asks for a TOTP code: set {config.env_name('2FA_DATA')} or {config.env_name('2FA_QR')} "
            f"(and {config.env_name('OTP_PROFILE_MATCH')} when the export holds several profiles)"
        )
    return portal_account(config, parse_accounts(data) if data else accounts_from_image(Path(qr_path)))


def classify_sso_page(html):
    """What a Keycloak page is: ``login``, ``otp``, ``human``, ``action`` or ``other``."""
    page = soup(html)
    if legacy.has_human_verification(html):
        return "human"
    if any(page.find("form", id=form_id) for form_id in ACCOUNT_ACTION_FORMS) or ACCOUNT_ACTION_TEXT.search(
        visible_text(page)
    ):
        return "action"
    if page.find("form", id="kc-otp-login-form"):
        return "otp"
    if page.find("form", id="kc-form-login"):
        return "login"
    return "other"


def is_logged_in_page(html):
    """An authenticated Projudi page: not the public "Acesso ao Sistema" home and carrying ``_tj`` navigation links.

    HYPOTHESIS from public pages (every internal link carries ``_tj``); confirm with a real session.
    """
    lowered = html.casefold()
    return PUBLIC_HOME_MARKER not in lowered and "_tj=" in html and "kc-form-login" not in lowered


class KeycloakAdapter(legacy.LegacyAdapter):
    flavor = "legacy-keycloak"

    def _hosts(self, config):
        return {config.host, sso_for(config).host}

    def _follow(self, session, config, response, *, cert=None):
        hosts = self._hosts(config)
        return follow_redirects(
            session,
            response,
            check=lambda url: require_url(url, hosts=hosts, label="Projudi login redirect"),
            rewrite=strip_path_params,
            cert=cert,
            impersonate=impersonate_for(config),
        )

    def _post(self, session, config, url, data):
        sso = sso_for(config)
        require_url(url, hosts={sso.host}, path_prefix=sso.path_prefix, label="Projudi SSO form")
        response = session.post(
            url, data=data, impersonate=impersonate_for(config), allow_redirects=False, timeout=TIMEOUT
        )
        log_request("POST", url, response.status_code)
        return self._follow(session, config, response)

    def _classify(self, config, response):
        """The state after an SSO step: ``app`` (back on Projudi) or the kind of SSO page; the unsafe ones raise."""
        host = (urllib.parse.urlsplit(response.url or "").hostname or "").lower()
        if host == config.host:
            return "app"
        if host != sso_for(config).host or response.status_code != 200:
            raise RuntimeError(f"Projudi login ended on an unexpected page (HTTP {response.status_code})")
        kind = classify_sso_page(response.text)
        if kind == "human":
            raise HumanVerificationRequired("precisa de uma pessoa: Projudi pediu verificação humana (CAPTCHA)")
        if kind == "action":
            raise AccountActionRequired(
                "Projudi pediu uma ação na conta (autenticador, senha ou termos); o conector não executa isso"
            )
        if kind == "other":
            raise RuntimeError("Projudi SSO returned a page the connector does not recognize")
        return kind

    def login(self, portal, *, session=None):
        config = portals.projudi(portal)
        sso = sso_for(config)
        username, password = login_credentials(config)  # nothing is requested before the credentials exist
        session = session or requests.Session()
        first = session.get(
            config.url(sso.entry), impersonate=impersonate_for(config), allow_redirects=False, timeout=TIMEOUT
        )
        page = self._follow(session, config, first)
        if self._classify(config, page) != "login":
            raise RuntimeError("Projudi did not open the SSO login form")
        form = soup(page.text).find("form", id="kc-form-login")
        action = urllib.parse.urljoin(page.url, form.get("action") or "")
        # Browser payload only: hidden fields + user + password (no "remember me", no submit button).
        fields = {**hidden_fields(form), "username": username, "password": password}
        response = self._post(session, config, action, fields)
        state = self._classify(config, response)
        if state == "login":
            raise LoginRejected("Projudi did not accept the login and password")
        if state == "otp":
            account = otp_account(config)
            form = soup(response.text).find("form", id="kc-otp-login-form")
            action = urllib.parse.urljoin(response.url, form.get("action") or "")
            response = self._post(session, config, action, {**hidden_fields(form), "otp": totp(account).now()})
            state = self._classify(config, response)
            if state in ("login", "otp"):
                raise LoginRejected("Projudi did not accept the authenticator code")
        if state != "app":
            raise RuntimeError("Projudi login did not return to the portal")
        if self.refresh(portal, session) is None:
            raise RuntimeError("Projudi login did not reach an authenticated page")
        return session

    def refresh(self, portal, session):
        config = portals.projudi(portal)
        try:
            response = session.get(
                config.url("home.do"), impersonate=impersonate_for(config), allow_redirects=False, timeout=TIMEOUT
            )
        except RequestException:
            return None
        if response.status_code != 200:
            return None
        return session if is_logged_in_page(parsers.decode(response.content)) else None


ADAPTER = KeycloakAdapter()
