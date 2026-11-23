"""e-SAJ plumbing shared by every court on the Softplan portal (TJSP, TJMS).

Login goes through the CAS (``/sajcas``): the login page carries a SHA-256 challenge, the A1 certificate signs it
(RSA PKCS#1 v1.5 over the digest) and the form is posted with the signature and the certificate. There is no mutual
TLS and no authenticator. The session is the CAS ticket cookie plus the application's own cookie.

Everything here is read-only. Pages that answer 403 (captcha, or a flow the portal wants to start itself) are reported
as ``EsajBlocked`` and never worked around.
"""

import base64
import re
import time
import urllib.parse

from bs4 import BeautifulSoup
from curl_cffi import requests

from jurisync import portals
from jurisync.core import certificate, credentials
from jurisync.core.http import IMPERSONATE, REDIRECTS, TIMEOUT, follow_redirects, log_request, require_url
from jurisync.core.session_pool import LoginRejected, SessionExpired

SERVICE_PATH = "tarefas-adv/j_spring_cas_security_check"
CHALLENGE = re.compile(r"hashDesafio\s*=\s*'([A-Za-z0-9+/=]+)'")
# The login form fields the browser submits for a certificate login. Nothing else goes: an unchecked
# "magistrado" box or the submit buttons make the CAS refuse a login it would otherwise accept.
FORM_FIELDS = ("lt", "execution", "_eventId", "token")


class EsajSessionExpired(SessionExpired):
    """The portal sent the session back to the CAS login."""


class EsajParseError(RuntimeError):
    """The portal answered, but not with the page or JSON this connector reads."""


class EsajBlocked(RuntimeError):
    """The portal refused to serve a page without a step only a person can do (captcha, its own start flow)."""

    def __init__(self, message, reason="flow_required"):
        super().__init__(message)
        self.reason = reason


def require_portal_url(config, value, label="e-SAJ URL"):
    return require_url(value, hosts={config.host}, label=label)


def consultation(key):
    """The first-instance consultation application: TJMS serves ``cpopg5``, the other e-SAJ courts ``cpopg``."""
    return "cpopg5" if key == "tjms-esaj" else "cpopg"


JSESSIONID = re.compile(r";jsessionid=[^/?#;]*$", re.IGNORECASE)


def upgrade_scheme(config):
    """A redirect rewriter for portals behind a TLS-terminating balancer (TJAL, TJAM): their CAS sends the ticket
    redirect to ``http://`` on the portal's own host. Only that scheme is upgraded; the host check still runs on the
    result. A trailing ``;jsessionid=`` path parameter (TJAM puts one on its redirects) is dropped too: the session
    cookie already carries it, and the guard refuses any other ``;`` in a path."""

    def rewrite(url):
        parts = urllib.parse.urlsplit(url)
        if (parts.hostname or "").lower() != config.host:
            return url
        if parts.scheme == "http" and parts.port in (None, 80):
            parts = parts._replace(scheme="https", netloc=config.host)
        elif parts.scheme != "https":
            return url
        return urllib.parse.urlunsplit(parts._replace(path=JSESSIONID.sub("", parts.path)))

    return rewrite


# --- Login ----------------------------------------------------------------------------------------------------------


def has_certificate():
    """Whether the account sets the A1 file and its PIN (``load_certificate`` reads both)."""
    return certificate.is_configured("ESAJ")


def load_certificate(*, pin=None, p12_path=None):
    return certificate.load_configured("ESAJ", pin=pin, p12_path=p12_path)


def challenge_from(html):
    """The base64 SHA-256 challenge the login page asks to be signed."""
    match = CHALLENGE.search(html or "")
    if not match:
        raise EsajParseError("e-SAJ login page has no certificate challenge")
    return match.group(1)


def sign_challenge(key, challenge):
    """Base64 signature of the challenge digest: RSA PKCS#1 v1.5 with SHA-256, over the digest as given."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, utils

    try:
        digest = base64.b64decode(challenge, validate=True)
    except ValueError:
        raise EsajParseError("e-SAJ challenge is not valid base64") from None
    if len(digest) != hashes.SHA256.digest_size:
        raise EsajParseError("e-SAJ challenge is not a SHA-256 digest")
    return base64.b64encode(key.sign(digest, padding.PKCS1v15(), utils.Prehashed(hashes.SHA256()))).decode("ascii")


def certificate_der_b64(cert):
    from cryptography.hazmat.primitives.serialization import Encoding

    return base64.b64encode(cert.public_bytes(Encoding.DER)).decode("ascii")


def login_form(html, page_url, config):
    """``(action_url, fields)`` of the certificate form, with only the hidden fields the browser sends."""
    form = BeautifulSoup(html, "html.parser").find("form", id="formCertificado")
    if form is None or not form.get("action"):
        raise EsajParseError("e-SAJ login page has no certificate form")
    action = urllib.parse.urljoin(page_url, form["action"])
    # The action carries a ";jsessionid=..." path parameter; the session cookie already says the same.
    action = urllib.parse.urlunsplit(
        urllib.parse.urlsplit(action)._replace(path=urllib.parse.urlsplit(action).path.split(";")[0])
    )
    require_portal_url(config, action, "e-SAJ login form")
    fields = {}
    for name in FORM_FIELDS:
        field = form.find("input", attrs={"name": name})
        if field is None:
            raise EsajParseError(f"e-SAJ login form has no {name} field")
        fields[name] = field.get("value", "")
    return action, fields


def login_page(session, config):
    """The CAS login page, reached the way a browser does: through the task panel, which names its own ``service``.

    The CAS checks the ticket against that exact string, and the courts do not all spell it alike (TJAM's panel asks
    for ``tarefas-adv//j_spring_cas_security_check``, with two slashes). A ticket issued for another spelling is refused
    by the panel, which then answers with its ``erroDesconhecido.do`` page. When the panel does not lead to the CAS
    login page, the standard service is asked for directly.
    """
    check = lambda url: require_portal_url(config, url)  # noqa: E731
    page = session.get(config.url("tarefas-adv/"), impersonate=IMPERSONATE, allow_redirects=False, timeout=TIMEOUT)
    page = follow_redirects(session, page, check=check, rewrite=upgrade_scheme(config))
    log_request("GET", page.url, page.status_code)
    if is_cas_login_page(page) and "service=" in (urllib.parse.urlsplit(page.url).query or ""):
        return page
    page = session.get(
        config.url("sajcas/login"),
        params={"service": config.url(SERVICE_PATH)},
        impersonate=IMPERSONATE,
        allow_redirects=False,
        timeout=TIMEOUT,
    )
    page = follow_redirects(session, page, check=check, rewrite=upgrade_scheme(config))
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
    """Password login when the account has no A1 certificate and sets ``<PREFIX>_USERNAME``/``_PASSWORD``."""
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
    if not password_login:
        key, cert, _chain = load_certificate(pin=pin, p12_path=p12_path)
    session = session or requests.Session()
    page = login_page(session, config)
    if password_login:
        action, fields = password_form(page.text, page.url, config, *password_credentials(config))
    else:
        action, fields = login_form(page.text, page.url, config)
        fields["certificadoSelecionado"] = certificate_der_b64(cert)
        fields["signature"] = sign_challenge(key, challenge_from(page.text))
    response = session.post(action, data=fields, impersonate=IMPERSONATE, allow_redirects=False, timeout=TIMEOUT)
    log_request("POST", action, response.status_code)
    response = follow_redirects(
        session,
        response,
        check=lambda url: require_portal_url(config, url, "e-SAJ login redirect"),
        rewrite=upgrade_scheme(config),
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


# --- Requests --------------------------------------------------------------------------------------------------------


def is_cas_login_page(response):
    """The CAS login page itself: where an expired session ends up after the single sign-on redirects."""
    path = urllib.parse.urlsplit(response.url or "").path
    return response.status_code == 200 and path.split(";")[0].rstrip("/").endswith("/sajcas/login")


def request(session, portal, method, path, *, accept=(), **kwargs):
    """One guarded request on the portal host.

    Each e-SAJ application (caixapostal, intimacoesweb, ...) first redirects to the CAS, which, with a valid session,
    sends the browser straight back with a ticket; the redirects are followed here, host-checked. Ending on the CAS login
    page means the session expired. A 403 is a flow the portal will not serve without a person (a captcha), unless the
    caller reads that answer itself (``accept=(403,)``: a check whose refusal page says why).
    """
    if method != "GET" and not (method == "POST" and path == "pastadigital/validarAberturaIntimacaoNaoRecebida.do"):
        raise ValueError("e-SAJ connector allows only reads and the document permission check")
    config = portals.esaj(portal)
    url = config.url(path)
    require_portal_url(config, url)
    kwargs.setdefault("timeout", TIMEOUT)
    response = session.request(method, url, impersonate=IMPERSONATE, allow_redirects=False, **kwargs)
    log_request(method, url, response.status_code)
    if kwargs.get("stream"):
        for _ in range(10):
            if response.status_code not in REDIRECTS or not response.headers.get("Location"):
                break
            target = upgrade_scheme(config)(urllib.parse.urljoin(response.url, response.headers["Location"]))
            try:
                require_portal_url(config, target, "e-SAJ redirect")
            finally:
                response.close()
            response = session.get(target, impersonate=IMPERSONATE, allow_redirects=False, stream=True, timeout=TIMEOUT)
    else:
        response = follow_redirects(
            session,
            response,
            check=lambda target: require_portal_url(config, target, "e-SAJ redirect"),
            rewrite=upgrade_scheme(config),
        )
    if kwargs.get("stream") and (is_cas_login_page(response) or response.status_code in (401, 403, *REDIRECTS)):
        response.close()
    if is_cas_login_page(response) or response.status_code == 401:
        raise EsajSessionExpired("e-SAJ session expired")
    if response.status_code == 403 and 403 not in accept:
        raise EsajBlocked("e-SAJ refused this page (403): it needs a step only a person can do, such as a captcha")
    if response.status_code in REDIRECTS:
        raise RuntimeError(f"e-SAJ redirected too many times (HTTP {response.status_code})")
    return response


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


# --- Read operations -------------------------------------------------------------------------------------------------


def json_response(response):
    if response.status_code != 200:
        raise EsajParseError(f"e-SAJ API failed (HTTP {response.status_code})")
    try:
        value = response.json()
    except ValueError:
        raise EsajParseError("e-SAJ API did not return JSON") from None
    if not isinstance(value, (dict, list)):
        raise EsajParseError("e-SAJ API returned an unexpected JSON shape")
    return value


def get_session(session, portal):
    from jurisync.connectors.esaj import bridge
    from jurisync.connectors.esaj.parsers import public_json

    response = request(session, portal, "GET", "esaj/api/auth/session")
    if response.status_code == 404:
        # The older portal (TJAM) has no session API: the CAS script answers, without OABs or services.
        if not cas_logged_in(session, portals.esaj(portal)):
            raise EsajSessionExpired("e-SAJ session expired")
        return {"usuarioLogado": True, "sessionSource": "sajcas", "captchaBridge": bridge.bridge_status()}
    value = json_response(response)
    if not isinstance(value, dict) or not value.get("usuarioLogado"):
        raise EsajSessionExpired("e-SAJ session expired")
    result = public_json({k: value[k] for k in ("usuarioLogado", "oabs", "servicosDisponiveis") if k in value})
    result["captchaBridge"] = bridge.bridge_status()
    return result


def list_messages(session, portal, page=1):
    from jurisync.connectors.esaj.parsers import messages

    if not isinstance(page, int) or isinstance(page, bool) or not 1 <= page <= 1000:
        raise ValueError("page must be between 1 and 1000")
    # Opening a message can change its read status; only the mailbox listing is fetched.
    response = request(session, portal, "GET", "caixapostal/abrirCaixaPostal.do")
    if page > 1:
        response = request(session, portal, "GET", "caixapostal/listaPaginada.do", params={"pag": page})
    if response.status_code != 200:
        raise EsajParseError(f"e-SAJ mailbox failed (HTTP {response.status_code})")
    # The mailbox is served as ISO-8859-1 behind two conflicting <meta> tags (UTF-8 first): the HTTP header's charset
    # (what ``response.text`` follows) decides, otherwise subjects come out as mojibake ("Eletr˘nico").
    return {"page": page, "items": messages(response.text)}


def list_tasks(session, portal, kind, state="AGUARDANDO_ASSINATURA"):
    from jurisync.connectors.esaj.parsers import public_json

    if kind not in ("intimacoes", "audiencias", "peticoes"):
        raise ValueError("kind must be intimacoes, audiencias or peticoes")
    if state not in PETITION_STATES:
        raise ValueError("Invalid petition state")
    if kind == "intimacoes":
        return {"kind": kind, "items": public_json(intimations(session, portal))}
    params = {"situacao": state} if kind == "peticoes" else {}
    return {"kind": kind, "items": public_json(json_response(panel(session, portal, kind, params=params)))}


# TJSP's panel assembles the account's lists on the first API request of a session, whichever list it is: seen live,
# the first ``peticoes`` call took 57 s and the first ``intimacoes`` 28 s (other days 65 s and over 3 minutes), then
# both answered in under a second. The API takes no paging or period parameter (the panel's own page sends a plain
# GET), so every panel call gets this longer wait and a timeout is reported as such.
PANEL_TIMEOUT = 150
INTIMATIONS_TIMEOUT = PANEL_TIMEOUT


def panel(session, portal, name, params=None):
    """One ``tarefas-adv/api/<name>`` request with the panel's wait; a timeout is ``portal_timeout``."""
    try:
        return request(session, portal, "GET", f"tarefas-adv/api/{name}", params=params, timeout=PANEL_TIMEOUT)
    except requests.exceptions.Timeout:
        raise EsajBlocked(
            f"The portal did not finish the panel's {name} list within {PANEL_TIMEOUT} s. The court assembles the "
            "lists on the first request of a session and answers the next ones quickly: try again in a few minutes",
            "portal_timeout",
        ) from None


def intimations(session, portal):
    """The panel's intimações (each with its process code and the ``ciencia`` flag). Never marks any of them."""
    items = json_response(panel(session, portal, "intimacoes"))
    if not isinstance(items, list):
        raise EsajParseError("Unexpected intimações response")
    return items


def pending_intimation_numbers(session, portal):
    """Digits of every process with an intimação the panel still shows without ciência.

    The intimações carry only the process code; the panel resolves codes to numbers the way its own page does
    (``processos?cdsProcesso=...``). A code the panel does not resolve makes the answer unusable, so it raises.
    """
    items = intimations(session, portal)
    if not all(isinstance(item, dict) for item in items):
        raise EsajParseError("Unexpected intimações response")
    codes = sorted({str(item.get("cdProcesso") or "") for item in items if item.get("ciencia") is not True})
    if not codes:
        return set()
    if "" in codes:
        raise EsajBlocked(
            "An intimação without ciência has no process code: documents stay closed", "intimacao_pending"
        )
    resolved = {}
    for start in range(0, len(codes), 50):
        batch = codes[start : start + 50]
        for item in dashboard_lookup(session, portal, [("cdsProcesso", code) for code in batch]):
            if isinstance(item, dict) and item.get("cdProcesso") in batch:
                resolved[item["cdProcesso"]] = re.sub(r"\D", "", str(item.get("nuProcesso") or ""))
    if set(codes) - set(resolved) or not all(resolved.values()):
        raise EsajBlocked(
            "The panel did not resolve every intimação without ciência to a process: documents stay closed",
            "intimacao_pending",
        )
    return set(resolved.values())


def ensure_no_pending_intimation(session, portal, number):
    """Refuse before the document viewer opens when the process has an intimação without ciência."""
    if re.sub(r"\D", "", number) in pending_intimation_numbers(session, portal):
        raise EsajBlocked(
            "This process has an intimação without ciência in the panel. Opening its documents could register the "
            "ciência and start the deadline, so the connector does not open them. Open it in the portal if the user "
            "decides to receive it.",
            "intimacao_pending",
        )


def process_number(value, portal):
    config = portals.esaj(portal)
    if not isinstance(value, str) or not portals.CNJ.fullmatch(value):
        raise ValueError("processNumber must be a formatted CNJ number")
    match = portals.CNJ.fullmatch(value)
    if match[1] != config.justice or match[2] != config.region:
        raise ValueError("processNumber does not belong to this court")
    return value


class BridgeResponse:
    """The shape of a portal response, for a page the browser bridge fetched."""

    def __init__(self, url, html):
        self.url = url
        self.content = html.encode("utf-8")
        self.status_code = 200


# TJSP answers a consultation asked less than about a second after the previous one with the search form and "Foram
# identificadas multiplas consultas simultâneas" instead of the process. Seen live: four searches 0.1 s apart all got it,
# the same search 1 s later was served. The page is asked again after a pause, as a person would; a portal that keeps
# answering so is reported as such, not as a missing process.
THROTTLE_PAUSE = 2
THROTTLE_RETRIES = 2


def consult(session, portal, path, **kwargs):
    """A consultation page (``search.do``, ``show.do``), asked again after a pause while the portal throttles it."""
    from jurisync.connectors.esaj import parsers

    for attempt in range(THROTTLE_RETRIES + 1):
        if attempt:
            time.sleep(THROTTLE_PAUSE * attempt)
        response = request(session, portal, "GET", path, **kwargs)
        if not parsers.simultaneous_queries(response.content):
            return response
    raise EsajBlocked(
        'The portal is refusing consultations as simultaneous queries ("múltiplas consultas simultâneas"): '
        "another consultation with this account is running. Try again in a few seconds",
        "rate_limited",
    )


def consultation_params(number):
    """The fields of the public consultation form, shared by the HTTP path and the browser bridge."""
    return {
        "conversationId": "",
        "cbPesquisa": "NUMPROC",
        "dadosConsulta.valorConsulta": number,
        "dadosConsulta.valorConsultaNuUnificado": number,
        "dadosConsulta.tipoNuProcesso": "UNIFICADO",
        "numeroDigitoAnoUnificado": number[:15],
        "foroNumeroUnificado": number[-4:],
        "cdForo": "-1",
    }


def bridge_search(session, portal, number):
    """The consultation page fetched inside a real browser, for the portals that answer the form only with a CAPTCHA."""
    from jurisync.connectors.esaj import bridge

    try:
        with bridge.BrowserBridge() as browser:
            if not browser.enabled:
                raise bridge.BrowserBridgeError("Browser bridge is off")
            page = browser.search(portal, consultation_params(number), bridge.bridge_cookies(session, portal))
    except (bridge.BrowserBridgeError, OSError, ValueError) as error:
        raise EsajBlocked(
            f"Consultation requires CAPTCHA and the browser bridge failed: {error}", "captcha_required"
        ) from None
    return BridgeResponse(page["url"], page["html"])


def bridge_page(session, portal, url):
    """A page the portal serves only to a browser (TJMS answers the detail URL with a CAPTCHA page otherwise)."""
    from jurisync.connectors.esaj import bridge

    try:
        with bridge.BrowserBridge() as browser:
            if not browser.enabled:
                raise bridge.BrowserBridgeError("Browser bridge is off")
            page = browser.fetch(portal, url, bridge.bridge_cookies(session, portal))
    except bridge.BrowserBridgeError as error:
        raise EsajBlocked(
            f"The portal needs a browser for this page and the bridge failed: {error}", "captcha_required"
        ) from None
    return BridgeResponse(page["url"], page["html"])


def captcha_uuid(session, config, application):
    """The identifier the consultation page asks for before a token is accepted; "" when the portal gives none.

    This POST sits outside ``request()`` (which allows only reads), so its redirects are followed by hand, host-checked.
    """
    url = config.url(f"{application}/captchaControleAcesso.do")
    require_portal_url(config, url)
    response = session.post(
        url, data={"uuidCaptcha": ""}, impersonate=IMPERSONATE, allow_redirects=False, timeout=TIMEOUT
    )
    log_request("POST", url, response.status_code)
    response = follow_redirects(
        session, response, check=lambda target: require_portal_url(config, target, "e-SAJ redirect")
    )
    try:
        identifier = response.json().get("uuidCaptcha", "")
    except (ValueError, AttributeError):
        identifier = ""
    return identifier if isinstance(identifier, str) else ""


def token_page(session, portal, url):
    """A consultation page fetched with an HTTP-minted v3 token appended: no browser, same exchange the page runs."""
    from jurisync.connectors.esaj import recaptcha

    config = portals.esaj(portal)
    require_portal_url(config, url)
    parts = urllib.parse.urlsplit(url)
    start = request(session, portal, "GET", f"{parts.path.split('/')[1]}/open.do")
    try:
        sitekey, action = recaptcha.sitekey_and_action(start.text)
    except recaptcha.RecaptchaError as error:
        raise EsajBlocked(f"Consultation requires CAPTCHA: {error}", "captcha_required") from None
    identifier = captcha_uuid(session, config, parts.path.split("/")[1])
    if not identifier:
        raise EsajBlocked("Consultation requires CAPTCHA", "captcha_required")
    try:
        token = recaptcha.mint_token(session, sitekey, f"https://{config.host}", action, timeout=TIMEOUT)
    except recaptcha.RecaptchaError as error:
        raise EsajBlocked(f"Consultation requires CAPTCHA: {error}", "captcha_required") from None
    query = dict(urllib.parse.parse_qsl(parts.query))
    query.update({"recaptcha_response_token": token, "uuidCaptcha": identifier})
    return consult(session, portal, parts.path + "?" + urllib.parse.urlencode(query))


def token_fields(session, portal, application, open_page):
    """The ``recaptcha_response_token`` and ``uuidCaptcha`` a consultation request carries, minted over plain HTTP."""
    from jurisync.connectors.esaj import recaptcha

    try:
        sitekey, action = recaptcha.sitekey_and_action(open_page.text)
    except recaptcha.RecaptchaError as error:
        raise EsajBlocked(f"Consultation requires CAPTCHA: {error}", "captcha_required") from None
    config = portals.esaj(portal)
    identifier = captcha_uuid(session, config, application)
    if not identifier:
        raise EsajBlocked("Consultation requires CAPTCHA", "captcha_required")
    try:
        token = recaptcha.mint_token(session, sitekey, f"https://{config.host}", action, timeout=TIMEOUT)
    except recaptcha.RecaptchaError as error:
        raise EsajBlocked(f"Consultation requires CAPTCHA: {error}", "captcha_required") from None
    return {"recaptcha_response_token": token, "uuidCaptcha": identifier}


def token_search(session, portal, adapter, number, open_page):
    """The consultation with a v3 token minted over plain HTTP: no browser, no solver, same exchange the page runs."""
    from jurisync.connectors.esaj import parsers

    params = dict(consultation_params(number))
    params.update(token_fields(session, portal, adapter.CONSULTATION, open_page))
    response = consult(session, portal, f"{adapter.CONSULTATION}/search.do", params=params)
    if parsers.captcha(response.content):
        raise EsajBlocked("Consultation requires CAPTCHA", "captcha_required")
    return response


def consultation_search(session, portal, adapter, number):
    """``(response, source)``: plain HTTP, then an HTTP-minted v3 token, then the browser bridge as a last resort."""
    from jurisync.connectors.esaj import parsers

    start = None
    try:
        start = request(session, portal, "GET", f"{adapter.CONSULTATION}/open.do")
        if parsers.captcha(start.content):
            raise EsajBlocked("Consultation requires CAPTCHA", "captcha_required")
        response = consult(session, portal, f"{adapter.CONSULTATION}/search.do", params=consultation_params(number))
        if parsers.captcha(response.content):
            raise EsajBlocked("Consultation requires CAPTCHA", "captcha_required")
        return response, "http"
    except EsajBlocked as error:
        if error.reason not in ("captcha_required", "flow_required"):
            raise
    try:
        if start is None:
            raise EsajBlocked("Consultation requires CAPTCHA", "captcha_required")
        return token_search(session, portal, adapter, number, start), "http_token"
    except EsajBlocked as error:
        if error.reason == "rate_limited":
            raise
    return bridge_search(session, portal, number), "browser_bridge"


# --- The account's processes: the consultation by the lawyer's OABs -------------------------------------------------
#
# Neither the panel (tarefas-adv: tasks only) nor the account menu has a "my processes" list. The first-instance
# consultation searches by OAB (``cbPesquisa=NUMOAB``) and lists every case where that OAB is registered, 25 a page
# (``trocarPagina.do?paginaConsulta=N``); the account's OABs come from ``esaj/api/auth/session``. It is the public
# consultation: nothing in it opens an intimação or a document.

MAX_LIST_PAGES = 40
LIST_PAUSE = 1  # seconds between pages: TJSP refuses consultations asked less than about a second apart


def account_oabs(session, portal):
    """The OABs of the logged-in lawyer, as the consultation takes them (``"123456MG"``)."""
    response = request(session, portal, "GET", "esaj/api/auth/session")
    if response.status_code == 404:
        raise EsajBlocked(
            "This e-SAJ (the older portal) has no account API with the lawyer's OABs, so it cannot list the "
            "account's processes",
            "not_supported",
        )
    value = json_response(response)
    if not isinstance(value, dict) or not value.get("usuarioLogado"):
        raise EsajSessionExpired("e-SAJ session expired")
    oabs = []
    for item in value.get("oabs") or []:
        oab = re.sub(r"[^0-9A-Za-z]", "", str(item.get("stringOab") or "")) if isinstance(item, dict) else ""
        if oab and oab not in [known["oab"] for known in oabs]:
            oabs.append({"oab": oab, "regular": item.get("regular")})
    return oabs


def oab_params(oab):
    return {
        "conversationId": "",
        "cbPesquisa": "NUMOAB",
        "dadosConsulta.valorConsulta": oab,
        "dadosConsulta.tipoNuProcesso": "UNIFICADO",
        "cdForo": "-1",
    }


def detail_item(response, application):
    """The one process a search answered with its detail page instead of a list (a single match)."""
    from jurisync.connectors.esaj import parsers

    query = urllib.parse.parse_qs(urllib.parse.urlsplit(response.url).query)
    page = parsers.soup(response.content)
    shown = page.select_one("#numeroProcesso, #nuProcessoUnificadoFormatado")
    number = parsers.CNJ.search(shown.get_text(" ", strip=True)) if shown else None
    if number is None:
        raise EsajParseError(f"e-SAJ {application} answered a detail page without a process number")
    fields = parsers.process(response.content, number.group(0))["fields"]
    return {
        "processNumber": number.group(0),
        "codigo": query.get("processo.codigo", [None])[0],
        "foro": query.get("processo.foro", [None])[0],
        "parte": None,
        "classe": fields.get("classeProcesso"),
        "assunto": fields.get("assuntoProcesso"),
        "recebidoEm": None,
        "local": fields.get("varaProcesso") or fields.get("foroProcesso"),
    }


def oab_page(session, portal, adapter, oab, page):
    """``(items, total)`` of one page of the consultation by ``oab``; a CAPTCHA is answered with a minted token."""
    from jurisync.connectors.esaj import parsers

    application = adapter.CONSULTATION
    path = f"{application}/search.do" if page == 1 else f"{application}/trocarPagina.do"
    params = oab_params(oab) | ({"paginaConsulta": str(page)} if page > 1 else {})

    def understood(response):
        if parsers.captcha(response.content):
            return None
        if urllib.parse.urlsplit(response.url).path.endswith(f"/{application}/show.do"):
            return [detail_item(response, application)], 1
        try:
            return parsers.process_listing(response.content, application)
        except EsajParseError:
            return None  # the search form again: the portal wants the token its page sends

    if not getattr(adapter, "CAPTCHA", False):
        answer = understood(consult(session, portal, path, params=params))
        if answer is not None:
            return answer
    start = request(session, portal, "GET", f"{application}/open.do")
    tokens = token_fields(session, portal, application, start)
    answer = understood(consult(session, portal, path, params=params | tokens))
    if answer is None:
        raise EsajBlocked("The consultation by OAB answered with a CAPTCHA", "captcha_required")
    return answer


def list_processes(session, portal, max_pages=10):
    """The account's first-instance processes: the consultation by each of the lawyer's OABs, all pages up to
    ``max_pages`` per OAB, merged (a case found by two OABs is listed once, with both)."""
    if not isinstance(max_pages, int) or isinstance(max_pages, bool) or not 1 <= max_pages <= MAX_LIST_PAGES:
        raise ValueError(f"maxPages must be between 1 and {MAX_LIST_PAGES}")
    adapter = adapter_for(portal)
    merged, searches = {}, []
    for account in account_oabs(session, portal):
        oab, page, total, found = account["oab"], 1, None, []
        while True:
            if page > 1 or searches:
                time.sleep(LIST_PAUSE)
            items, count = oab_page(session, portal, adapter, oab, page)
            total = count if total is None else total
            known = {(item["codigo"], item["processNumber"]) for item in found}
            fresh = [item for item in items if (item["codigo"], item["processNumber"]) not in known]
            found.extend(fresh)
            if not fresh or total is None or len(found) >= total or page >= max_pages:
                break
            page += 1
        complete = total is not None and len(found) >= total
        searches.append(
            {
                "oab": oab,
                "regular": account["regular"],
                "total": total,
                "returned": len(found),
                "pages": page,
                "complete": complete,
            }
        )
        for item in found:
            key = item["codigo"] or item["processNumber"]
            merged.setdefault(key, {**item, "oabs": []})["oabs"].append(oab)
    items = list(merged.values())
    return {
        "instance": "1g",
        "consultation": adapter.CONSULTATION,
        "oabs": searches,
        "total": len(items),
        "complete": bool(searches) and all(search["complete"] for search in searches),
        "items": items,
    }


def adapter_for(portal):
    from jurisync.connectors.esaj import tjal, tjam, tjms, tjsp

    return {tjsp.PORTAL: tjsp, tjms.PORTAL: tjms, tjal.PORTAL: tjal, tjam.PORTAL: tjam}[portals.esaj(portal).key]


def load_process(session, portal, number):
    from jurisync.connectors.esaj import parsers, tjsp

    number = process_number(number, portal)
    adapter = adapter_for(portal)
    detail_response = None
    source = "dashboard"
    # The panel resolves a number only once its task lists are loaded; reloading them pays off only where the
    # consultation answers with a CAPTCHA (TJMS). Elsewhere the plain consultation is the cheaper way in.
    data = dashboard_process(session, portal, number, petitions=getattr(adapter, "CAPTCHA", False))
    link = data.get("urlCpo") if data else None
    if link and urllib.parse.urlsplit(link).path != f"/{adapter.CONSULTATION}/show.do":
        link = None  # only a second-instance entry (cposg): the first-instance consultation below finds the case
    if not link:
        response, source = consultation_search(session, portal, adapter, number)
        if "/show.do" in urllib.parse.urlsplit(response.url).path:
            detail_response, link = response, response.url
        else:
            links = parsers.process_links(response.content, adapter.CONSULTATION)
            if len(links) != 1:
                # The plain search answered without a result (form page): retry with an HTTP-minted token.
                try:
                    start = request(session, portal, "GET", f"{adapter.CONSULTATION}/open.do")
                    response = token_search(session, portal, adapter, number, start)
                    source = "http_token"
                except EsajBlocked as error:
                    if error.reason == "rate_limited":
                        raise
                    if parsers.not_found(response.content):
                        raise EsajBlocked(
                            "This e-SAJ has no process with this number (it may be on another system of the court)",
                            "not_found",
                        ) from None
                    raise EsajBlocked("No unique process detail available to this account") from None
                if "/show.do" in urllib.parse.urlsplit(response.url).path:
                    detail_response, link = response, response.url
                else:
                    links = parsers.process_links(response.content, adapter.CONSULTATION)
                    if len(links) != 1:
                        raise EsajBlocked("No unique process detail available to this account")
                    link = urllib.parse.urljoin(response.url, links[0])
            else:
                link = urllib.parse.urljoin(response.url, links[0])
    config = portals.esaj(portal)
    require_portal_url(config, link)
    parts = urllib.parse.urlsplit(link)
    if parts.path != f"/{adapter.CONSULTATION}/show.do":
        raise EsajParseError("Unexpected process detail endpoint")
    response = detail_response or consult(session, portal, parts.path + ("?" + parts.query if parts.query else ""))
    if parsers.captcha(response.content):
        try:
            response = token_page(session, portal, link)
            source = "http_token"
        except EsajBlocked as error:
            if error.reason == "rate_limited":
                raise
            response = bridge_page(session, portal, link)
            source = "browser_bridge"
        if parsers.captcha(response.content):
            raise EsajBlocked("Consultation requires CAPTCHA", "captcha_required")
    result = parsers.process(response.content, number)
    code = urllib.parse.parse_qs(parts.query).get("processo.codigo", [""])[0]
    if code and (adapter is tjsp or parsers.movements_on_demand(response.content)):
        movements, complete = paged_movements(session, portal, adapter.CONSULTATION, code)
        if movements:
            result["movements"] = movements
            if not complete:
                result["movementsTruncated"] = True
    result["consultationSource"] = source
    return result


# Pages of the on-demand movements list (TJSP serves 5 movements a page); a longer history is reported as truncated.
MAX_MOVEMENT_PAGES = 200


def paged_movements(session, portal, application, code):
    """``(movements, complete)``: every page of ``carregarMovimentacoesAjax.do``, as the page's "Mais" link reads them.

    The first answer holds the first page and an opaque cursor; each next page brings the cursor of the one after it,
    and the last brings none. The process page must have been opened in this session first.
    """
    from jurisync.connectors.esaj import parsers

    result, cursor = [], ""
    for _ in range(MAX_MOVEMENT_PAGES):
        response = request(
            session,
            portal,
            "GET",
            f"{application}/carregarMovimentacoesAjax.do",
            params={"processo.codigo": code, "cursor": cursor},
        )
        if response.status_code != 200:
            return result, False
        try:
            page, cursor = parsers.movement_page(response.content)
        except EsajParseError:
            return result, False
        result.extend(page)
        if not cursor:
            return result, True
    return result, False


PETITION_STATES = ("AGUARDANDO_ASSINATURA", "AGUARDANDO_DISTRIBUICAO", "AGUARDANDO_JUNTADA", "CONCLUIDA")


def dashboard_lookup(session, portal, params):
    """The panel's ``processos`` answer for ``numerosProcesso`` or ``cdsProcesso`` (repeatable) parameters."""
    items = json_response(panel(session, portal, "processos", params=params))
    if not isinstance(items, list):
        raise EsajParseError("Unexpected process dashboard response")
    return items


def dashboard_entry(items, digits):
    """The entry of the process, first instance before second: the panel lists both, in no fixed order."""
    matches = [
        item
        for item in items
        if isinstance(item, dict) and re.sub(r"\D", "", str(item.get("nuProcesso") or "")) == digits
    ]
    return min(matches, key=lambda item: item.get("instancia") != "PG", default=None)


PROCESS_CODE = re.compile(r"[A-Za-z0-9]{1,40}")


def dashboard_process(session, portal, number, code=None, *, petitions=True):
    """The account panel's entry for a process (``urlCpo``, ``urlPasta``), or None.

    By number the panel resolves only the cases loaded into this session's task panels (enough on TJMS); by process
    code it resolves the account's cases directly, which is what its own page does (needed on TJSP and TJAL). The code
    comes from the caller (the consultation's ``processo.codigo``) or from a petition the panel lists.
    """
    digits = re.sub(r"\D", "", number)
    item = dashboard_entry(dashboard_lookup(session, portal, {"numerosProcesso": digits}), digits)
    if not item and isinstance(code, str) and PROCESS_CODE.fullmatch(code):
        item = dashboard_entry(dashboard_lookup(session, portal, {"cdsProcesso": code}), digits)
    if item or not petitions:
        return item
    tried = {code}
    for state in PETITION_STATES:
        listed = json_response(panel(session, portal, "peticoes", params={"situacao": state}))
        for petition in listed if isinstance(listed, list) else []:
            process = petition.get("processo") if isinstance(petition, dict) else None
            if not isinstance(process, dict) or re.sub(r"\D", "", str(process.get("nuProcesso") or "")) != digits:
                continue
            value = process.get("cdProcesso")
            if isinstance(value, str) and PROCESS_CODE.fullmatch(value) and value not in tried:
                tried.add(value)
                item = dashboard_entry(dashboard_lookup(session, portal, {"cdsProcesso": value}), digits)
                if item:
                    return item
        item = dashboard_entry(dashboard_lookup(session, portal, {"numerosProcesso": digits}), digits)
        if item:
            return item
    return None


def consultation_code(session, portal, number):
    """``(code, detail)``: the process code the first-instance consultation shows, and its detail page when the search
    landed on it (None otherwise)."""
    from jurisync.connectors.esaj import parsers

    adapter = adapter_for(portal)
    response, _source = consultation_search(session, portal, adapter, number)
    link, detail = response.url, response
    if "/show.do" not in urllib.parse.urlsplit(link).path:
        links = parsers.process_links(response.content, adapter.CONSULTATION)
        if len(links) != 1:
            return None, None
        link, detail = urllib.parse.urljoin(response.url, links[0]), None
    code = urllib.parse.parse_qs(urllib.parse.urlsplit(link).query).get("processo.codigo", [""])[0]
    return (code, detail) if PROCESS_CODE.fullmatch(code) else (None, detail)


# The panel links the viewer through the consultation of the instance where the process is.
VIEWER_PATHS = frozenset(f"/{app}/abrirPastaDigitalIntegracao.do" for app in ("cpopg", "cpopg5", "cposg", "cposg5"))


VIEWER_PAGE = "/pastadigital/abrirPastaProcessoDigital.do"


SECOND_INSTANCE_DETAIL = frozenset({"/cposg/show.do", "/cposg5/show.do"})


def second_instance_records(session, portal, number, detail):
    """The viewer-URL answer for a case "em grau de recurso": its first-instance page has no "Visualizar autos", only
    "Consultar em 2ª instância". That link opens the second-instance page, whose button asks
    ``verificarAcessoPastaDigital.do?cdProcesso=`` (the page's hidden ``cdProcesso``) as the page's own script does: the
    answer is the viewer URL; 406 is the court's daily limit on unlinked cases; any other refusal is the
    process-password dialog (the account is not linked to the case in the second instance).
    """
    from jurisync.connectors.esaj import parsers

    adapter = adapter_for(portal)
    link = parsers.second_instance_link(detail.content, adapter.CONSULTATION, number)
    if not link:
        raise EsajBlocked("No authorized document viewer link in the account dashboard or the process page")
    parts = urllib.parse.urlsplit(urllib.parse.urljoin(detail.url, link))
    page = consult(session, portal, f"{parts.path}?{parts.query}")
    application = urllib.parse.urlsplit(page.url).path
    if page.status_code != 200 or application not in SECOND_INSTANCE_DETAIL:
        raise EsajBlocked("The second-instance page of this process is unavailable to this account")
    code = parsers.hidden_process_code(page.content)
    if not code or not PROCESS_CODE.fullmatch(code):
        raise EsajParseError("The second-instance page has no process code")
    answer = request(
        session,
        portal,
        "GET",
        application.replace("/show.do", "/verificarAcessoPastaDigital.do"),
        params={"cdProcesso": code},
        accept=(403,),
    )
    if answer.status_code == 406:
        raise EsajBlocked(
            "The court's daily limit of document-viewer openings for cases the account is not linked to was reached",
            "access_limit",
        )
    if answer.status_code != 200:
        raise EsajBlocked(
            "The process is in the second instance and the portal asks for the process password to show its "
            "records there: the account is not linked to the case in the second instance. The password comes from "
            "the court registry; the connector does not ask for it",
            "password_required",
        )
    return answer


def detail_viewer(session, portal, number, code, detail=None):
    """The document viewer reached the way the detail page's "Visualizar autos" button reaches it.

    The button's ``abrirPastaDigital.do?processo.codigo=`` answers with the viewer's own URL as plain text (with a
    one-time ``ticket``), which the page then opens. That URL is followed only on the portal host, on the viewer path and
    for this process number. A detail page whose button opens only the process-password dialog is ``password_required``.
    """
    from jurisync.connectors.esaj import parsers

    adapter = adapter_for(portal)
    config = portals.esaj(portal)
    if detail is None:
        detail = consult(session, portal, f"{adapter.CONSULTATION}/show.do", params={"processo.codigo": code})
    if parsers.records_need_password(detail.content):
        raise EsajBlocked(
            "The portal asks for the process password to show its records: the account is not linked to this "
            "process in e-SAJ. The password comes from the court registry; the connector does not ask for it",
            "password_required",
        )
    link = parsers.records_link(detail.content, adapter.CONSULTATION)
    if link:
        parts = urllib.parse.urlsplit(urllib.parse.urljoin(detail.url, link))
        if urllib.parse.parse_qs(parts.query).get("processo.codigo") != [code]:
            raise EsajParseError("The process page links the records of another process")
        answer = request(session, portal, "GET", f"{parts.path}?{parts.query}")
    else:
        try:
            answer = second_instance_records(session, portal, number, detail)
        except EsajBlocked as refusal:
            if refusal.reason == "access_limit":
                raise
            # A case in the second instance keeps its first-instance records where the account is linked: the page
            # hides their button while the case is away, but the same route still serves them to a linked account
            # (seen live on TJSP). Whatever the portal answers decides; a refusal there keeps the first one.
            answer = request(
                session,
                portal,
                "GET",
                f"{adapter.CONSULTATION}/abrirPastaDigital.do",
                params={"processo.codigo": code},
                accept=(403,),
            )
            if answer.status_code != 200 or not (answer.text or "").strip().startswith("https://"):
                raise refusal from None
    target = (answer.text or "").strip() if answer.status_code == 200 else ""
    if not target.startswith("https://") or any(character.isspace() for character in target):
        raise EsajBlocked("Document viewer is unavailable to this account")
    require_portal_url(config, target, "e-SAJ document viewer")
    viewer = urllib.parse.urlsplit(target)
    numbers = urllib.parse.parse_qs(viewer.query).get("nuProcesso", [])
    if viewer.path != VIEWER_PAGE or [re.sub(r"\D", "", value) for value in numbers] != [re.sub(r"\D", "", number)]:
        raise EsajParseError("Unexpected document viewer endpoint")
    response = request(session, portal, "GET", f"{viewer.path}?{viewer.query}")
    if response.status_code != 200 or not urllib.parse.urlsplit(response.url).path.startswith("/pastadigital/"):
        raise EsajBlocked("Document viewer is unavailable to this account")
    return response


def document_inventory(session, portal, number):
    from jurisync.connectors.esaj import parsers

    number = process_number(number, portal)
    # Checked before anything of the process opens: a pending intimação keeps all its documents closed.
    ensure_no_pending_intimation(session, portal, number)
    item = dashboard_process(session, portal, number, petitions=False)
    if not item or not item.get("urlPasta"):
        # The consultation names the process code, which the panel resolves for the cases on the account's panel.
        # A case the panel does not list opens through its detail page's "Visualizar autos", as in the browser.
        # Without a code (consultation behind a CAPTCHA), the petitions the panel lists may still name one.
        try:
            code, detail = consultation_code(session, portal, number)
        except EsajBlocked as error:
            if error.reason == "rate_limited":
                raise
            code, detail = None, None
        item = dashboard_process(session, portal, number, code=code, petitions=code is None)
        if (not item or not item.get("urlPasta")) and code:
            return parsers.viewer_documents(detail_viewer(session, portal, number, code, detail).text, number)
    if not item or not item.get("urlPasta"):
        raise EsajBlocked("No authorized document viewer link in the account dashboard")
    link = item["urlPasta"]
    require_portal_url(portals.esaj(portal), link)
    parts = urllib.parse.urlsplit(link)
    if parts.path not in VIEWER_PATHS:
        raise EsajParseError("Unexpected document viewer endpoint")
    response = request(session, portal, "GET", parts.path + "?" + parts.query)
    if response.status_code != 200 or not urllib.parse.urlsplit(response.url).path.startswith("/pastadigital/"):
        raise EsajBlocked("Document viewer is unavailable to this account")
    return parsers.viewer_documents(response.text, number)


def list_documents(session, portal, number):
    inventory = document_inventory(session, portal, number)
    return {
        "processNumber": number,
        "items": [{k: v for k, v in item.items() if k != "parameters"} for item in inventory],
    }


def permission_refusal(response):
    """Why the "intimação não recebida" check does not clear a document, or None when it does.

    The portal answers 204 (or an empty 200) for a document that opens without a receipt. Any body means the page wants
    something first (in the browser: the dialog that confirms receiving the intimação), so it is never treated as a yes.
    """
    body = getattr(response, "content", b"") or b""
    if isinstance(body, str):
        body = body.encode("utf-8", "replace")
    if response.status_code == 204 or (response.status_code == 200 and not body.strip()):
        return None
    from jurisync.connectors.esaj.parsers import redact_text

    text = " ".join(BeautifulSoup(body[:20000], "html.parser").get_text(" ", strip=True).split())
    return f"HTTP {response.status_code}" + (f": {redact_text(text)[:200]}" if text else "")


def document(session, portal, number, document_id, *, download=False):
    from jurisync.connectors.esaj.parsers import redact_text
    from jurisync.core.document_text import extract_document_text
    from jurisync.core.documents import MAX_DOCUMENT_BYTES, read_limited, validate_document_ids
    from jurisync.core.documents_dir import save_document

    validate_document_ids([document_id])
    inventory = document_inventory(session, portal, number)
    entry = next((item for item in inventory if item["documentId"] == document_id), None)
    if entry is None:
        raise ValueError("documentId is not in the current process inventory")
    if not entry.get("parameters"):
        raise EsajBlocked("Document requires a password or restricted access flow", "password_required")
    # This POST only checks permission. Receipt/signature endpoints are never invoked, and there is no way to
    # acknowledge a receipt through this connector: anything but a clean "yes" stops before the PDF is requested.
    permission = request(
        session,
        portal,
        "POST",
        "pastadigital/validarAberturaIntimacaoNaoRecebida.do",
        data={"parametros": entry["parameters"]},
    )
    refusal = permission_refusal(permission)
    if refusal:
        raise EsajBlocked(
            "Opening this document may register the receipt of an intimação and start its deadline; "
            f"the connector stopped before opening it (portal answered {refusal}). Open it in the portal if the "
            "user decides to receive it.",
            "intimacao_pending",
        )
    response = request(session, portal, "GET", "pastadigital/getPDF.do?" + entry["parameters"], stream=True)
    try:
        content = read_limited(response)
        if content is None:
            raise ValueError(f"Document exceeds {MAX_DOCUMENT_BYTES} bytes")
        if response.status_code != 200 or not content.startswith(b"%PDF-"):
            raise EsajBlocked("The document endpoint did not return an authorized PDF")
    finally:
        response.close()
    result = {
        "processNumber": number,
        "documentId": document_id,
        "contentType": "application/pdf",
        "bytes": len(content),
    }
    if download:
        import uuid

        result["savedAs"] = save_document(f"esaj-{uuid.uuid4().hex}.pdf", content)
    else:
        status, text = extract_document_text(content, "application/pdf", number)
        result.update(extractionStatus=status, text=redact_text(text))
    return result
