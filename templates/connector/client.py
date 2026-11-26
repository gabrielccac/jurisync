"""Exemplo portal client: login and the read endpoints. Nothing here knows MCP.

Every request goes through ``request``, so the allowed host, the timeout and the expired-session check live in one place.
The portal answers JSON; HTTP 401 means the session is gone, which the ``SessionPool`` answers with one new login.
"""

from dataclasses import dataclass

from curl_cffi import requests

from jurisync.core import credentials
from jurisync.core.http import TIMEOUT, log_request, require_url
from jurisync.core.session_pool import LoginRejected, MissingLogin, SessionExpired

PORTAL = "exemplo"
BASE_URL = "https://portal.exemplo.jus.br"
HOSTS = ("portal.exemplo.jus.br",)
USERNAME, PASSWORD = "EXEMPLO_USERNAME", "EXEMPLO_PASSWORD"


@dataclass
class Session:
    http: requests.Session
    token: str

    def close(self):
        self.http.close()


def configured():
    return bool(credentials.env(USERNAME) and credentials.env(PASSWORD))


def request(session, method, path, *, token=None, **kwargs):
    url = require_url(BASE_URL + path, hosts=HOSTS)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    response = session.request(method, url, headers=headers, timeout=TIMEOUT, allow_redirects=False, **kwargs)
    log_request(method, url, response.status_code)
    if 300 <= response.status_code < 400:
        raise RuntimeError(f"exemplo answered {response.status_code}: redirects are not followed")
    if response.status_code == 401:
        raise SessionExpired("exemplo session expired")
    response.raise_for_status()
    return response.json()


def login(portal):
    """A new ``Session`` for ``portal``; the pool calls it once per office and reuses the result."""
    if not configured():
        raise MissingLogin(f"set {USERNAME} and {PASSWORD}")
    http = requests.Session(impersonate="chrome")
    try:
        payload = request(
            http, "POST", "/api/login", json={"login": credentials.env(USERNAME), "senha": credentials.env(PASSWORD)}
        )
    except SessionExpired:
        payload = {}
    except Exception:
        http.close()
        raise
    if not payload.get("token"):
        http.close()
        raise LoginRejected("the portal refused the user or the password", reason="credentials_refused")
    return Session(http, payload["token"])


def refresh(portal, session):
    """The session when it is still valid, else None (the pool logs in again)."""
    try:
        request(session.http, "GET", "/api/sessao", token=session.token)
    except SessionExpired:
        return None
    return session


def intimacoes(session):
    return request(session.http, "GET", "/api/intimacoes", token=session.token)["dados"]


def process(session, number):
    return request(session.http, "GET", "/api/processos", token=session.token, params={"numero": number})["dados"]
