"""Projudi "legacy-local" generation (TJAM): a local login form in front of the Projudi frameset.

STATUS: built against the public login page and simulated responses only. No authenticated session was ever opened, so
the portal stays ``wired=False`` in ``portals.py`` and this adapter is not registered in ``server.ADAPTERS``. See
``docs/connectors/projudi-tjam-prep.md`` for what is real (observed live, public) and what is a hypothesis.

Login (observed on the public page, 2026-11-13): ``GET usuario/logon.do?actionType=inicio`` returns ``form#formLogin``
(``login``, ``senha``, hidden ``tck``, ``latitude``, ``longitude``, ``locationGranted``). Its ``submitLogin()`` sets
``tck = md5(<per-render salt> + login)`` and disables the submit button before posting, so a browser sends no button.
There is no A1/mTLS login: the TLS endpoint never requests a client certificate, and the Projudi certificate is
generated inside the system for signing petitions.

Safety rules (the read rules are in ``legacy.py``):
- one credential POST per ``login``; no retry (a wrong password counts toward a lock) and ``SessionPool`` blocks
  further logins after ``LoginRejected``;
- a WAF/CAPTCHA page (F5 "Request Rejected") stops the login (``HumanVerificationRequired``), never bypassed;
- the connector never asks for or uses the internal certificate password.
"""

import hashlib
import re
import urllib.parse

from curl_cffi import requests
from curl_cffi.requests.exceptions import RequestException

from jurisync import portals
from jurisync.connectors.projudi import base, legacy, parsers
from jurisync.connectors.projudi.legacy import HumanVerificationRequired, MissingCredentials
from jurisync.core.http import REDIRECTS, TIMEOUT, follow_redirects, impersonate_for, log_request, require_url
from jurisync.core.session_pool import LoginRejected

LOGIN_PATH = "usuario/logon.do"
USERNAME_PATTERN = re.compile(r"[A-Za-z0-9]{3,30}")
SALT = re.compile(r'hex_md5\(\s*"([0-9a-fA-F]{16,128})"\s*\+\s*login\s*\)')
REJECTED = ("usuário ou senha inválidos", "usuario ou senha invalidos", "bloquead")
ERROR_PAGE = "erro de acesso ao projudi"


def login_credentials(config):
    """``(login, password)``: a ``UF000`` login or a CPF/CNPJ. Raises before any request when one is missing."""
    username = "".join(config.env("USERNAME").split())
    if re.fullmatch(r"[\d.\-/]+", username):
        username = re.sub(r"\D", "", username)
    password = config.env("PASSWORD")
    missing = [config.env_name(name) for name, value in (("USERNAME", username), ("PASSWORD", password)) if not value]
    if missing:
        raise MissingCredentials(
            f"precisa de login e senha da cliente: set {' and '.join(missing)} (Projudi {config.tribunal})"
        )
    if not USERNAME_PATTERN.fullmatch(username) or len(password) > 30:
        raise ValueError(f"{config.env_name('USERNAME')} must be a UF000 login or a CPF/CNPJ; password up to 30 chars")
    return username, password


def login_key(salt, username):
    """The ``tck`` field, as the page's ``hex_md5(salt + login)`` computes it (a standard MD5 over 8-bit characters)."""
    return hashlib.md5((salt + username).encode("latin-1"), usedforsecurity=False).hexdigest()


def is_logged_in_page(html):
    """An authenticated Projudi page: no login form and a logout link. HYPOTHESIS: confirm with a real session."""
    return "formlogin" not in html.casefold() and "actionType=logout" in html


class TjamAdapter(legacy.LegacyAdapter):
    flavor = "legacy-local"
    menu_labels = ("Busca Processual Simples", "Busca Processual", "Busca de Processos", "Meus Processos")

    def _follow(self, session, config, response):
        return follow_redirects(
            session,
            response,
            check=lambda url: require_url(url, hosts={config.host}, label="Projudi login redirect"),
            rewrite=legacy.strip_path_params,
            impersonate=impersonate_for(config),
        )

    def login(self, portal, *, session=None):
        config = portals.projudi(portal)
        username, password = login_credentials(config)  # nothing is requested before the credentials exist
        session = session or requests.Session()
        first = session.get(
            config.url(f"{LOGIN_PATH}?actionType=inicio"),
            impersonate=impersonate_for(config),
            allow_redirects=False,
            timeout=TIMEOUT,
        )
        page = self._follow(session, config, first)
        html = parsers.decode(page.content)
        if legacy.has_human_verification(html):
            raise HumanVerificationRequired("precisa de uma pessoa: o TJAM bloqueou a página (WAF/CAPTCHA)")
        form = legacy.soup(html).find("form", id="formLogin")
        salt = SALT.search(html)
        if page.status_code != 200 or form is None or salt is None:
            raise base.ProjudiParseError("TJAM login page has no login form or salt")
        # The action keeps ``;jsessionid`` and ``_tj`` exactly as the page wrote them; only its target is validated.
        action = urllib.parse.urljoin(page.url, form.get("action") or "")
        require_url(
            legacy.strip_path_params(action),
            hosts={config.host},
            path=f"{config.root}{LOGIN_PATH}",
            label="Projudi login form",
        )
        data = {
            **legacy.hidden_fields(form),
            "login": username,
            "senha": password,
            "tck": login_key(salt.group(1), username),
            "locationGranted": "false",
        }
        response = session.post(
            action, data=data, impersonate=impersonate_for(config), allow_redirects=False, timeout=TIMEOUT
        )
        log_request("POST", legacy.strip_path_params(action), response.status_code)
        result = self._follow(session, config, response) if response.status_code in REDIRECTS else response
        text = parsers.decode(result.content)
        lowered = text.casefold()
        if legacy.has_human_verification(text):
            raise HumanVerificationRequired("precisa de uma pessoa: o TJAM pediu verificação (WAF/CAPTCHA)")
        if any(marker in lowered for marker in REJECTED):
            raise LoginRejected("Projudi did not accept the login and password")
        if ERROR_PAGE in lowered:
            raise RuntimeError("Projudi answered a generic access error to the login")
        if self.refresh(portal, session) is None:
            raise RuntimeError("Projudi login did not reach an authenticated page")
        return session

    def refresh(self, portal, session):
        config = portals.projudi(portal)
        try:
            response = session.get(
                config.url("paginaPrincipal.jsp"),
                impersonate=impersonate_for(config),
                allow_redirects=False,
                timeout=TIMEOUT,
            )
        except RequestException:
            return None
        if response.status_code != 200:
            return None
        return session if is_logged_in_page(parsers.decode(response.content)) else None


ADAPTER = TjamAdapter()
