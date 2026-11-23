"""Tucujuris (TJAP) client: login, the request guard and the read endpoints of its JSON API.

Tucujuris is TJAP's own system (not its PJe): a Knockout/RequireJS SPA under ``/pages/`` over a JSON API under
``/api/``, behind Cloudflare (the default curl_cffi Chrome fingerprint passes; plain curl gets a challenge). Mapped on
2026-11-16 from the public bundles (``js/ds.js``, ``servicos/*.js``, ``pages/*/*.js``):

- Every answer is an envelope ``{"ambiente", "status": "OK", "dados": ...}`` or ``{"status": "ERRO", "mensagem"}``;
  HTTP 401 means the session is gone. Authenticated calls carry the login token in an ``Auth-Token`` header (the SPA
  keeps it in localStorage, there is no session cookie) plus ``Tucujuris-Front-Url`` with the page making the call.
- Login: ``POST /api/publico/login`` with ``{login, senha, permanente, fonte: "tucujuris"}``. ``dados.token`` is the
  session; ``dados.situcao_segundo_fator`` (sic) is ``verificacao_pendente`` when the authenticator code is due,
  answered by ``POST /api/usuario/segundo-fator-verificar {codigo}``. ``configuracao_pendente`` would mean enrolling
  a new authenticator, which the connector never does. Advogados log in with the OAB number + seccional ("1234AP");
  a CPF is a login only for other profiles (a lawyer's CPF answers "Essa conta de usuário não existe").
- An account with several lotações picks one (``definir-acesso-vara``) before using the system; a lawyer has none.

Safety: the client sends a fixed set of GETs and exactly two POSTs (the login and the code). Everything else is
refused before any request, in particular ``comunicacoes-processuais/processar-comunicacao`` ("Marcar como lido"),
which registers the ciência of a comunicação processual (Res. TJAP 1074, art. 18). See ``guard``.
"""

import json
import re
import time
from dataclasses import dataclass, field

from curl_cffi import requests

from jurisync import portals
from jurisync.core.documents import read_limited
from jurisync.core.http import TIMEOUT, impersonate_for, log_request, require_url
from jurisync.core.session_pool import LoginRejected, MissingLogin, SessionExpired
from jurisync.core.totp import configured_account, fresh_code

PORTAL = "tjap-tucujuris"
API = "/api/"
REPOSITORY = "/repositorio/"

LOGIN = "publico/login"
VERIFY_CODE = "usuario/segundo-fator-verificar"
POSTS = frozenset({LOGIN, VERIFY_CODE})
READS = frozenset(
    {
        "usuario/segundo-fator-situacao",
        "usuario/buscar-varas-disponiveis",
        "publico/buscar-passe-captcha",
        "autos/buscar-autos-pesssoa-esta-envolvida",  # sic: the portal's route has three s
        "publico/buscar-autos-consulta-publica",
        "publico/buscar-detalhes-autos-consulta-publica",
        "publico/buscar-movimentos-autos-consulta-publica",
        "publico/buscar-anexos-processo-consulta-publica",
        "publico/buscar-anexos-movimento-consulta-publica",
        "publico/download-anexo-movimento-consulta-publica",
        "comunicacoes-processuais/buscar-periodos-comunicacoes",
        "comunicacoes-processuais/buscar-tipos-comunicacao-periodo",
        "comunicacoes-processuais/buscar-comunicacoes",
    }
)
# Known routes that change something in the portal. None is in POSTS/READS; they are named so the refusal says why.
CIENCIA = "comunicacoes-processuais/processar-comunicacao"  # "Marcar como lido": registers the ciência
FORBIDDEN = frozenset(
    {
        CIENCIA,
        "usuario/segundo-fator-confirmar",  # enrolls a new authenticator
        "usuario/segundo-fator-resetar",  # drops the authenticator
        "usuario/definir-acesso-vara",
        "publico/seguir-processo",
        "publico/deixar-de-seguir-processo",
        "publico/gerar-chave-consumo",  # needs a solved CAPTCHA
        "publico/salvar-senha",
    }
)

TOKEN = re.compile(r"[A-Za-z0-9._~+/=-]{8,4096}")
CPF = re.compile(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}")
OAB_NUMBER_FIRST = re.compile(r"(?:OAB)?[\s/.-]*(\d{1,7}[A-Z]?)[\s/.-]*([A-Z]{2})")
OAB_STATE_FIRST = re.compile(r"(?:OAB)?[\s/.-]*([A-Z]{2})[\s/.-]*(\d{1,7}[A-Z]?)")


class TucujurisError(RuntimeError):
    """The portal answered ``status: ERRO`` (its message is in the text)."""


class TucujurisSessionExpired(SessionExpired):
    pass


class HumanVerificationRequired(RuntimeError):
    """Cloudflare (or the portal) asked for a CAPTCHA/challenge: never solved or bypassed."""

    reason = "captcha"


class MissingCredentials(MissingLogin):
    pass


class UnsafeRequest(RuntimeError):
    """A request outside the read-only allow-list; refused before anything is sent."""


@dataclass
class Session:
    http: object
    token: str
    portal: object
    passe: str | None = None
    cache: dict = field(default_factory=dict)

    def close(self):
        self.http.close()


def config():
    return portals.tucujuris(PORTAL)


def guard(method, path):
    """Allow only the read GETs and the two login POSTs. The ciência route is refused for every method."""
    method = method.upper()
    path = path.strip("/")
    if path == CIENCIA:
        raise UnsafeRequest(
            "refused: processar-comunicacao marks a comunicação as read, which registers its ciência in Tucujuris"
        )
    if path in FORBIDDEN:
        raise UnsafeRequest(f"refused: {path} changes the account or the portal")
    allowed = POSTS if method == "POST" else READS if method == "GET" else frozenset()
    if path not in allowed:
        raise UnsafeRequest(f"refused: {method} {path} is not one of the connector's read-only requests")
    return path


def login_name(value):
    """The login as the portal expects it: OAB number + seccional ("1234AP"), CPF digits, or as written (e-mail)."""
    text = (value or "").strip()
    if CPF.fullmatch(text):
        return re.sub(r"\D", "", text)
    upper = re.sub(r"\s+", " ", text.upper())
    for pattern, number_first in ((OAB_NUMBER_FIRST, True), (OAB_STATE_FIRST, False)):
        match = pattern.fullmatch(upper)
        if match:
            number, state = match.groups() if number_first else match.groups()[::-1]
            return f"{number.lstrip('0') or '0'}{state}"
    return text


def headers(portal, page, token=None):
    front = portal.url(f"pages/{page}")
    result = {"Accept": "application/json", "Origin": portal.base_url.rstrip("/"), "Referer": front}
    result["Tucujuris-Front-Url"] = front
    if token:
        result["Auth-Token"] = token
    return result


def api_url(portal, path):
    return require_url(portal.url(API + path), hosts={portal.host}, path=API + path, label="Tucujuris")


def _challenged(response):
    return response.status_code in (403, 429, 503) and (
        response.headers.get("cf-mitigated") or "challenge" in response.text[:4000].lower()
    )


def envelope(response):
    """``dados`` of an OK answer; ``TucujurisError`` with the portal's message otherwise."""
    if response.status_code == 401:
        raise TucujurisSessionExpired("Tucujuris session expired")
    if _challenged(response):
        raise HumanVerificationRequired("Tucujuris answered with a Cloudflare challenge; nothing was bypassed")
    try:
        data = response.json()
    except ValueError:
        raise TucujurisError(f"Tucujuris answered HTTP {response.status_code} without JSON") from None
    if not isinstance(data, dict) or data.get("status") not in ("OK", "ERRO"):
        raise TucujurisError(f"Tucujuris answered HTTP {response.status_code} outside its envelope")
    if data["status"] == "ERRO":
        raise TucujurisError(str(data.get("mensagem") or "unknown error")[:300])
    return data.get("dados")


def encode(params):
    """jQuery's encoding: lists as ``name[]`` repeated, ``None`` left out."""
    pairs = []
    for key, value in (params or {}).items():
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            pairs.extend((f"{key}[]", str(item)) for item in value)
        elif isinstance(value, bool):
            pairs.append((key, "true" if value else "false"))
        else:
            pairs.append((key, str(value)))
    return pairs


def call(http, portal, method, path, *, params=None, body=None, page="home/home.html", token=None):
    path = guard(method, path)
    url = api_url(portal, path)
    response = http.request(
        method.upper(),
        url,
        params=encode(params) or None,
        json=body,
        headers=headers(portal, page, token),
        impersonate=impersonate_for(portal),
        timeout=TIMEOUT,
        allow_redirects=False,
    )
    log_request(method.upper(), url, response.status_code)
    return envelope(response)


def get(session, path, params=None, page="home/home.html"):
    return call(session.http, session.portal, "GET", path, params=params, page=page, token=session.token)


# --- Login ------------------------------------------------------------------------------------------------------------


def otp_account(portal):
    return configured_account(portal, MissingCredentials)


def login(portal_key=PORTAL, *, http_factory=requests.Session, clock=time.time, sleep=time.sleep):
    """One login: the credential POST is sent once; a refusal is ``LoginRejected`` and is never retried."""
    portal = portals.tucujuris(portal_key)
    username, password = login_name(portal.env("USERNAME")), portal.env("PASSWORD")
    if not username or not password:
        raise MissingCredentials(f"Set {portal.env_name('USERNAME')} and {portal.env_name('PASSWORD')}")
    account = otp_account(portal)  # a configuration error must not cost a login
    http = http_factory()
    try:
        page = require_url(portal.login_url, hosts={portal.host}, label="Tucujuris")
        response = http.get(page, impersonate=impersonate_for(portal), timeout=TIMEOUT, allow_redirects=False)
        log_request("GET", page, response.status_code)
        if _challenged(response):
            raise HumanVerificationRequired("Tucujuris answered with a Cloudflare challenge; nothing was bypassed")
        body = {"login": username, "senha": password, "permanente": False, "fonte": "tucujuris"}
        try:
            data = call(http, portal, "POST", LOGIN, body=body, page="login/login.html")
        except TucujurisError as error:
            raise LoginRejected(f"Tucujuris refused the login: {error}") from None
        token = (data or {}).get("token") if isinstance(data, dict) else None
        if not isinstance(token, str) or not TOKEN.fullmatch(token):
            raise TucujurisError("Tucujuris login answered without a session token")
        if data.get("requer_trocar_senha"):
            raise LoginRejected(
                "Tucujuris asks for a password change: change it in the portal, then update the .env",
                reason="password_expired",
            )
        state = data.get("situcao_segundo_fator") or data.get("situacao_segundo_fator") or ""
        if state == "configuracao_pendente":
            raise LoginRejected(
                "Tucujuris asks to enroll a new authenticator; the connector never does that", reason="unexpected"
            )
        if state == "verificacao_pendente":
            # The account picks the second factor: an authenticator ("otp") or a code e-mailed at each login, which
            # only a person with that mailbox can answer. The page asks the method before showing the code field.
            method = data.get("segundo_fator_metodo") or (
                call(
                    http, portal, "GET", "usuario/segundo-fator-situacao",
                    page="segundo-fator-verificar/segundo-fator-verificar.html", token=token,
                ) or {}
            ).get("segundo_fator_metodo")  # fmt: skip
            if method == "email":
                raise LoginRejected(
                    "Tucujuris sends this account's second-factor code by e-mail; switch it to the authenticator app "
                    "in the portal (Meu perfil) to log in unattended",
                    reason="unexpected",
                )
            try:
                result = call(
                    http, portal, "POST", VERIFY_CODE, body={"codigo": fresh_code(account, clock=clock, sleep=sleep)},
                    page="segundo-fator-verificar/segundo-fator-verificar.html", token=token,
                )  # fmt: skip
            except TucujurisError as error:
                raise LoginRejected(
                    f"Tucujuris refused the authenticator code: {error}", reason="otp_refused"
                ) from None
            if (result or {}).get("situacao") not in ("verificado", ""):
                raise LoginRejected("Tucujuris did not accept the authenticator code", reason="otp_refused")
        elif state not in ("", "verificado"):
            raise TucujurisError(f"Tucujuris second factor in an unknown state: {state[:40]}")
        session = Session(http, token, portal)
        units = get(session, "usuario/buscar-varas-disponiveis", page="definir-acesso-vara/definir-acesso-vara.html")
        if isinstance(units, list) and len(units) > 1:
            raise TucujurisError("this account must choose a lotação at every login; not supported (lawyers have none)")
        return session
    except BaseException:
        http.close()
        raise


def refresh(portal_key, session):
    """The session when its token still answers; ``None`` makes the pool log in again."""
    try:
        state = get(session, "usuario/segundo-fator-situacao")
    except (TucujurisSessionExpired, TucujurisError):
        return None
    situacao = (state or {}).get("situacao") if isinstance(state, dict) else None
    return session if situacao in (None, "", "verificado") else None


# --- Files ------------------------------------------------------------------------------------------------------------


def download(session, file):
    """``(content, content_type)`` of one attachment, or ``(None, reason)``. ``file`` comes from the process index."""
    portal = session.portal
    if file.get("stream"):
        url = portal.url(REPOSITORY.strip("/") + "/" + file["stream"].lstrip("/"))
        url = require_url(url, hosts={portal.host}, path_prefix=REPOSITORY, label="Tucujuris", allow_fragment=False)
        params = None
    elif file.get("external"):
        return None, "outside_tucujuris"
    else:
        path = guard("GET", "publico/download-anexo-movimento-consulta-publica")
        url = api_url(portal, path)
        params = encode(
            {
                "nome_tabela": file["table"],
                "id_tabela": file["row"],
                "nome_campo": file["column"],
                "chave_consumo": file.get("key"),
            }
        )
    response = session.http.get(
        url,
        params=params,
        headers={**headers(portal, "consultar-processo/consultar-processo.html", session.token), "Accept": "*/*"},
        impersonate=impersonate_for(portal),
        timeout=TIMEOUT,
        allow_redirects=False,
        stream=True,
    )
    try:
        log_request("GET", url, response.status_code)
        if response.status_code == 401:
            raise TucujurisSessionExpired("Tucujuris session expired")
        if response.status_code in (403, 404):
            return None, "access_denied"
        if response.status_code != 200:
            return None, "request_failed"
        content_type = response.headers.get("content-type", "")
        content = read_limited(response)
        if content is None:
            return None, "too_large"
        if "json" in content_type:
            # A refusal comes back as the JSON envelope instead of the file.
            try:
                refused = json.loads(content).get("status") == "ERRO"
            except (ValueError, AttributeError):
                refused = False
            return None, "access_denied" if refused else "request_failed"
        return content, content_type
    finally:
        response.close()
