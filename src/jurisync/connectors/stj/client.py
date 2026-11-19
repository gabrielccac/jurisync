"""STJ Central do Processo Eletrônico (CPE): A1 login and a read-only door to its REST API.

The lawyer's area is the React app at ``cpe.web.stj.jus.br``; its API lives under ``/api``. The certificate login is a
single ``POST https://cpe-auth.web.stj.jus.br/api/auth/cert`` (body ``{}``) whose host asks the client certificate in
the TLS handshake; the certificate login has no second factor. The answer carries a JWT (``token``, about four hours)
that every later call sends raw in the ``authorization`` header; no cookie is involved.

A lawyer who never registered in the CPE gets ``novoCredenciado: true`` on that login and the app moves on to the
registration form. Registering is a change in the tribunal's records, so the login asks the public
``/api/credenciado/isCredenciado/{cpf}`` first and stops before the certificate login when the answer is not ``true``.

Read-only by construction: ``api`` sends GETs, and a POST only to the listed read endpoints (the process filter takes
its filter as a JSON body). Paths that write (early notice of an act, filings, registration, favorites, interest lists,
read marks) are refused before any request, whatever the method. Opening a document in the CPE does not register
notice: the STJ notifies lawyers through the DJEN, and early notice ("ciência antecipada") is a separate POST that this
module refuses.
"""

import base64
import json
import re
import time
import urllib.parse
from dataclasses import dataclass, field

from curl_cffi import requests

from jurisync import portals
from jurisync.core import certificate, credentials
from jurisync.core.documents import read_limited
from jurisync.core.http import IMPERSONATE, TIMEOUT, log_request
from jurisync.core.session_pool import LoginRejected, SessionExpired

CPE_HOST = "cpe.web.stj.jus.br"
AUTH_HOST = "cpe-auth.web.stj.jus.br"
CPE = f"https://{CPE_HOST}"
AUTH_URL = f"https://{AUTH_HOST}/api/auth/cert"
API = "/api/"
# POSTs that only read: the "my processes" filter sends its filter as a body.
READ_POSTS = frozenset({"/api/advogados/processosFiltro"})
# Never requested, whatever the method: early notice (it records notice and adds a movement and a certificate),
# filings, registration and account changes, favorites/interest/notes, message read marks, access-key requests.
BLOCKED = re.compile(
    r"ciencia|peticao|peticionamento|peticoes/|incluir|signup|register|reset|credenciado/(?!isCredenciado|comPerfis)"
    r"|favorito|interessado|anotacao|mensagens/lida|chaveAcesso|solicitar|sisbajud|setDataRecebimento|associar"
    r"|desvincular|enteCredenciado|auth/(?!cert)",
    re.I,
)
RENEW_BEFORE = 10 * 60  # seconds left on the JWT below which the pool logs in again
ICP_CPF = re.compile(r":(\d{11})$")


class StjError(RuntimeError):
    """The CPE answered, but not as this connector expects."""


class StjSessionExpired(SessionExpired):
    """The CPE refused the token (401): the pool logs in again once."""


class StjNotRegistered(LoginRejected):
    """The certificate holder has no CPE registration; registering is left to the lawyer."""


class StjAccessDenied(StjError):
    """The CPE refused this process or document to the logged-in lawyer (403)."""


@dataclass
class StjSession:
    http: object
    token: str = field(repr=False)
    expires: float
    cpf: str
    oabs: tuple = ()

    def close(self):
        self.http.close()


def config():
    return portals.stj("stj")


# --- Login ----------------------------------------------------------------------------------------------------------


def load_certificate(*, pin=None, p12_path=None):
    """``(key, cert, chain)`` of the A1: ``STJ_A1_P12``/``STJ_A1_PIN`` when set, otherwise the shared PJe A1."""
    file_name, pin_name = certificate.credential_names(config().env_prefix)
    pin = pin or credentials.env(pin_name)
    if not pin:
        raise RuntimeError(f"Set {pin_name} to the A1 certificate password")
    return certificate.load_a1(p12_path, pin, name=file_name)


def holder_cpf(cert):
    """The CPF of an ICP-Brasil e-CPF: the digits after ``:`` in its common name."""
    from cryptography.x509.oid import NameOID

    names = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    match = ICP_CPF.search(str(names[0].value).strip()) if names else None
    if not match:
        raise RuntimeError("The A1 is not an e-CPF (no CPF in its common name); the STJ CPE needs a lawyer's e-CPF")
    return match.group(1)


def token_expiry(token, now=None):
    """The JWT ``exp`` (epoch seconds); one hour from now when the token does not say."""
    now = time.time() if now is None else now
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return float(claims["exp"])
    except (IndexError, KeyError, TypeError, ValueError):
        return now + 3600


def _json(response, label):
    try:
        return response.json()
    except ValueError:
        raise StjError(f"STJ {label} did not answer JSON (HTTP {response.status_code})") from None


def check_registered(http, cpf):
    url = f"{CPE}/api/credenciado/isCredenciado/{cpf}"
    response = http.get(url, impersonate=IMPERSONATE, allow_redirects=False, timeout=TIMEOUT)
    log_request("GET", url, response.status_code)
    if response.status_code != 200:
        raise StjError(f"STJ registration check failed (HTTP {response.status_code})")
    if _json(response, "registration check") is not True:
        raise StjNotRegistered(
            "This certificate's holder is not registered in the STJ CPE (Central do Processo Eletrônico). "
            "The lawyer must register at https://cpe.web.stj.jus.br first; Jurisync never registers anyone."
        )


def login(*, pin=None, p12_path=None, http=None):
    """Log in with the A1 over mutual TLS and return a ``StjSession``. Never proceeds to the CPE registration."""
    key, cert, chain = load_certificate(pin=pin, p12_path=p12_path)
    cpf = holder_cpf(cert)
    http = http or requests.Session()
    headers = {"Accept": "application/json, text/plain, */*", "Origin": CPE, "Referer": f"{CPE}/"}
    check_registered(http, cpf)
    with certificate.client_certificate(key, cert, chain) as pem:
        response = http.post(
            AUTH_URL,
            json={},
            cert=pem,
            headers=headers,
            impersonate=IMPERSONATE,
            allow_redirects=False,
            timeout=120,
        )
    log_request("POST", AUTH_URL, response.status_code)
    if response.status_code in (401, 403):
        raise LoginRejected(f"STJ refused the certificate (HTTP {response.status_code})")
    if response.status_code != 200:
        raise StjError(f"STJ certificate login failed (HTTP {response.status_code})")
    answer = _json(response, "login")
    if not isinstance(answer, dict):
        raise StjError("STJ login answered an unexpected body")
    if answer.get("novoCredenciado"):
        raise StjNotRegistered(
            "The STJ CPE treats this certificate as a new registration; the lawyer must finish it at "
            "https://cpe.web.stj.jus.br. Jurisync never registers anyone."
        )
    token = answer.get("token")
    if not token or answer.get("error"):
        raise StjError("STJ login answered without a token")
    http.headers.update({**headers, "authorization": token})
    return StjSession(http=http, token=token, expires=token_expiry(token), cpf=cpf)


def refresh(session, now=None):
    """The session while its JWT has more than ``RENEW_BEFORE`` seconds left, else ``None`` (log in again)."""
    now = time.time() if now is None else now
    return session if session.expires - now > RENEW_BEFORE else None


# --- Requests -------------------------------------------------------------------------------------------------------


def require_read(method, path):
    """Raise ``PermissionError`` unless ``method path`` is a read this connector allows."""
    if not path.startswith(API) or ".." in path or "//" in path:
        raise PermissionError(f"STJ path outside the API: {path!r}")
    if BLOCKED.search(path):
        raise PermissionError(f"STJ endpoint {path!r} writes or registers; this connector is read-only")
    if method == "GET" or (method == "POST" and path in READ_POSTS):
        return
    raise PermissionError(f"STJ {method} {path!r} is not a read; this connector is read-only")


def api(session, method, path, *, params=None, body=None, stream=False):
    """One CPE API call. ``path`` starts with ``/api/``; query values go in ``params``."""
    require_read(method, path)
    url = CPE + urllib.parse.quote(path, safe="/")
    response = session.http.request(
        method,
        url,
        params=params,
        json=body,
        impersonate=IMPERSONATE,
        allow_redirects=False,
        timeout=TIMEOUT,
        stream=stream,
    )
    log_request(method, url, response.status_code)
    if response.status_code == 401:
        response.close()
        raise StjSessionExpired("STJ token expired")
    if response.status_code == 403:
        response.close()
        raise StjAccessDenied("The STJ refused this process or document to the logged-in lawyer (HTTP 403)")
    return response


def get_json(session, path, *, params=None, body=None, method="GET", empty=None):
    response = api(session, method, path, params=params, body=body)
    if response.status_code in (204, 404):
        return empty
    if response.status_code != 200:
        raise StjError(f"STJ {path.split('/')[2]} failed (HTTP {response.status_code})")
    return _json(response, path.split("/")[2])


# --- Reads ----------------------------------------------------------------------------------------------------------


def list_processes(session, status="T", page=0, page_size=20):
    """``POST /advogados/processosFiltro``: the lawyer's processes. ``status`` is S (active), N (closed) or T (all)."""
    body = {"page": page, "qtd": str(page_size), "tramitando": status}
    return get_json(session, "/api/advogados/processosFiltro", method="POST", body=body, empty=[]) or []


def records_by_cnj(session, digits):
    return get_json(session, "/api/processo/getNumeroRegistroByNumeroUnico", params={"numeroUnico": digits}, empty=[])


def records_by_class(session, sigla, numero):
    """Registrations of a class and number. The CPE app sends the class upper-cased (``ARESP``) and gets back a
    registration string or a list of them."""
    found = get_json(
        session,
        "/api/processo/getNumeroRegistroBySiglaClasseNumero",
        params={"siglaClasse": sigla.upper(), "numeroClasse": numero},
        empty=[],
    )
    found = found if isinstance(found, list) else [found] if found else []
    return [row if isinstance(row, dict) else {"numeroRegistro": str(row)} for row in found]


def process(session, registro):
    return get_json(session, f"/api/processo/{registro}", params={"chaveDeAcesso": ""})


def phases(session, registro):
    return get_json(session, f"/api/processo/{registro}/faseprocessoext", params={"chaveDeAcesso": ""}, empty=[])


def parties(session, registro):
    return get_json(session, f"/api/processo/{registro}/partesadvogados", params={"chaveDeAcesso": ""}, empty=[])


def index(session, registro):
    return get_json(session, f"/api/processo/{registro}/indiceprocesso", empty=[])


def oabs(session):
    """The lawyer's OAB codes (``GO051632``), principal first; cached on the session."""
    if not session.oabs:
        rows = get_json(session, f"/api/advogados/minhasOABs/{session.cpf}", empty=[]) or []
        rows = sorted(rows, key=lambda row: row.get("oabPrincipal") != "S")
        session.oabs = tuple(row["codigoOAB"] for row in rows if row.get("codigoOAB"))
    return session.oabs


def tramitacao_counts(session, oab):
    """``GET /advogados/{oab}/qtdprocessotramitacao``: ``[{"tramitando": "S"|"N", "qtd": n}]``."""
    return get_json(session, f"/api/advogados/{oab}/qtdprocessotramitacao", empty=[]) or []


def interest_processes(session, oab, page=1, page_size=20):
    """The processes the lawyer follows without being a party's lawyer ("Meus interesses")."""
    return get_json(
        session, f"/api/advogados/{oab}/processosInteresse", params={"page": page, "offset": page_size}, empty=[]
    )


def open_vistas(session, registro, seqs):
    """``GET /processo/{registro}/vistasEmAberto``: open vistas (the lawyer's deadline to answer) for the parties
    ``seqs`` (the ``seq`` of each party, not ``seqParteProcesso``). Reading it records nothing."""
    if not seqs:
        return []
    values = ",".join(str(seq) for seq in seqs)
    return get_json(session, f"/api/processo/{registro}/vistasEmAberto", params={"values": values}, empty=[]) or []


def next_message(session):
    """The portal's next unread notice to the lawyer, or ``None``. Marking it read is a POST this module refuses."""
    return get_json(session, "/api/mensagens/proxima", empty=None)


def publications(session, oab, page=1, page_size=20):
    return get_json(session, f"/api/advogados/{oab}/publicacoes", params={"page": page, "offset": page_size}, empty=[])


def download_piece(session, registro, sequencial_peca):
    """``(content, content_type)`` of one piece, or ``(None, reason)``.

    The CPE first hands out a one-use authorization for the piece, then serves it from its viewer; the authorization
    never leaves this function.
    """
    grant = get_json(
        session,
        f"/api/pecas/{registro}/getPecaAutorizacao",
        params={"indLocal": "P", "seq": sequencial_peca, "chaveDeAcesso": ""},
    )
    if not isinstance(grant, dict) or not grant.get("access_token"):
        return None, "not_authorized"
    response = api(
        session,
        "GET",
        f"/api/visualizador/externo/processos/{registro}/peca",
        params={"autorizacao": grant["access_token"], "token": grant.get("csrf", "")},
        stream=True,
    )
    try:
        if response.status_code == 204:
            return None, "not_found"
        if response.status_code != 200:
            return None, f"http_{response.status_code}"
        content = read_limited(response)
        if content is None:
            return None, "too_large"
        return content, response.headers.get("Content-Type", "")
    finally:
        response.close()
