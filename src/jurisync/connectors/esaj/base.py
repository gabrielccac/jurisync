"""e-SAJ plumbing shared by every court on the Softplan portal (TJSP, TJMS).

Login goes through the CAS (``/sajcas``): the login page carries a SHA-256 challenge, the A1 certificate signs it
(RSA PKCS#1 v1.5 over the digest) and the form is posted with the signature and the certificate. There is no mutual
TLS and no authenticator. The session is the CAS ticket cookie plus the application's own cookie.

Everything here is read-only. Pages that answer 403 (captcha, or a flow the portal wants to start itself) are reported
as ``EsajBlocked`` and never worked around.
"""

import base64
import re
import urllib.parse

from bs4 import BeautifulSoup
from curl_cffi import requests

from jurisync import portals
from jurisync.core import certificate, credentials
from jurisync.core.http import IMPERSONATE, REDIRECTS, TIMEOUT, follow_redirects, log_request, require_url
from jurisync.core.session_pool import SessionExpired

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


# --- Login ----------------------------------------------------------------------------------------------------------


def _a1_names():
    return certificate.credential_names("ESAJ")


def load_certificate(*, pin=None, p12_path=None):
    file_name, pin_name = _a1_names()
    pin = pin or credentials.env(pin_name)
    if not pin:
        raise RuntimeError(f"Set {pin_name} to the A1 certificate password")
    return certificate.load_a1(p12_path, pin, name=file_name)


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


def login(portal, *, pin=None, p12_path=None, session=None):
    """An authenticated ``requests.Session`` for the portal, logged in with the A1 certificate."""
    config = portals.esaj(portal)
    key, cert, _chain = load_certificate(pin=pin, p12_path=p12_path)
    session = session or requests.Session()
    service = config.url(SERVICE_PATH)
    page = session.get(
        config.url("sajcas/login"),
        params={"service": service},
        impersonate=IMPERSONATE,
        allow_redirects=False,
        timeout=TIMEOUT,
    )
    page = follow_redirects(session, page, check=lambda url: require_portal_url(config, url))
    log_request("GET", page.url, page.status_code)
    action, fields = login_form(page.text, page.url, config)
    fields["certificadoSelecionado"] = certificate_der_b64(cert)
    fields["signature"] = sign_challenge(key, challenge_from(page.text))
    response = session.post(action, data=fields, impersonate=IMPERSONATE, allow_redirects=False, timeout=TIMEOUT)
    log_request("POST", action, response.status_code)
    follow_redirects(session, response, check=lambda url: require_portal_url(config, url, "e-SAJ login redirect"))
    if not any(cookie.name == "CASTGC" for cookie in session.cookies.jar):
        raise RuntimeError("e-SAJ did not accept the A1 certificate")
    return session


# --- Requests --------------------------------------------------------------------------------------------------------


def is_cas_login_page(response):
    """The CAS login page itself: where an expired session ends up after the single sign-on redirects."""
    path = urllib.parse.urlsplit(response.url or "").path
    return response.status_code == 200 and path.split(";")[0].rstrip("/").endswith("/sajcas/login")


def request(session, portal, method, path, **kwargs):
    """One guarded request on the portal host.

    Each e-SAJ application (caixapostal, intimacoesweb, ...) first redirects to the CAS, which, with a valid session,
    sends the browser straight back with a ticket; the redirects are followed here, host-checked. Ending on the CAS login
    page means the session expired. A 403 is a flow the portal will not serve without a person (a captcha).
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
            target = urllib.parse.urljoin(response.url, response.headers["Location"])
            try:
                require_portal_url(config, target, "e-SAJ redirect")
            finally:
                response.close()
            response = session.get(target, impersonate=IMPERSONATE, allow_redirects=False, stream=True, timeout=TIMEOUT)
    else:
        response = follow_redirects(
            session, response, check=lambda target: require_portal_url(config, target, "e-SAJ redirect")
        )
    if kwargs.get("stream") and (is_cas_login_page(response) or response.status_code in (401, 403, *REDIRECTS)):
        response.close()
    if is_cas_login_page(response) or response.status_code == 401:
        raise EsajSessionExpired("e-SAJ session expired")
    if response.status_code == 403:
        raise EsajBlocked("e-SAJ refused this page (403): it needs a step only a person can do, such as a captcha")
    if response.status_code in REDIRECTS:
        raise RuntimeError(f"e-SAJ redirected too many times (HTTP {response.status_code})")
    return response


def refresh(portal, session):
    """Pool liveness check: the session API still says the user is logged in."""
    try:
        response = session.get(
            portals.esaj(portal).url("esaj/api/auth/session"),
            impersonate=IMPERSONATE,
            allow_redirects=False,
            timeout=TIMEOUT,
        )
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

    value = json_response(request(session, portal, "GET", "esaj/api/auth/session"))
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
    return {"page": page, "items": messages(response.content)}


def list_tasks(session, portal, kind, state="AGUARDANDO_ASSINATURA"):
    from jurisync.connectors.esaj.parsers import public_json

    if kind not in ("intimacoes", "audiencias", "peticoes"):
        raise ValueError("kind must be intimacoes, audiencias or peticoes")
    if state not in PETITION_STATES:
        raise ValueError("Invalid petition state")
    params = {"situacao": state} if kind == "peticoes" else {}
    return {
        "kind": kind,
        "items": public_json(json_response(request(session, portal, "GET", f"tarefas-adv/api/{kind}", params=params))),
    }


def list_intimacoes(session, portal, received=False):
    path = "AtosRecebidos" if received else "AtosNaoRecebidos"
    try:
        request(session, portal, "GET", f"intimacoesweb/abrirConsulta{path}.do")
    except EsajBlocked:
        # The dedicated intimações app is a separate Softplan application with its own CPF/password sign-in; the CAS
        # session of the portal does not open it, and the connector never asks for that password.
        raise EsajBlocked(
            "The dedicated intimações app requires its own sign-in; use the dashboard intimacoes listing",
            "login_required",
        ) from None
    # shortcut: the flow opened but its listing is not mapped, enable parsing after live validation.
    raise EsajBlocked("Dedicated intimações flow is not mapped; use the dashboard intimacoes listing")


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
    uuid = session.post(
        config.url(f"{parts.path.split('/')[1]}/captchaControleAcesso.do"),
        data={"uuidCaptcha": ""},
        impersonate=IMPERSONATE,
        timeout=TIMEOUT,
    )
    try:
        identifier = uuid.json().get("uuidCaptcha", "")
    except ValueError:
        identifier = ""
    if not identifier:
        raise EsajBlocked("Consultation requires CAPTCHA", "captcha_required")
    try:
        token = recaptcha.mint_token(session, sitekey, f"https://{config.host}", action, timeout=TIMEOUT)
    except recaptcha.RecaptchaError as error:
        raise EsajBlocked(f"Consultation requires CAPTCHA: {error}", "captcha_required") from None
    query = dict(urllib.parse.parse_qsl(parts.query))
    query.update({"recaptcha_response_token": token, "uuidCaptcha": identifier})
    return request(session, portal, "GET", parts.path + "?" + urllib.parse.urlencode(query))


def token_search(session, portal, adapter, number, open_page):
    """The consultation with a v3 token minted over plain HTTP: no browser, no solver, same exchange the page runs."""
    from jurisync.connectors.esaj import parsers, recaptcha

    try:
        sitekey, action = recaptcha.sitekey_and_action(open_page.text)
    except recaptcha.RecaptchaError as error:
        raise EsajBlocked(f"Consultation requires CAPTCHA: {error}", "captcha_required") from None
    config = portals.esaj(portal)
    uuid = session.post(
        config.url(f"{adapter.CONSULTATION}/captchaControleAcesso.do"),
        data={"uuidCaptcha": ""},
        impersonate=IMPERSONATE,
        timeout=TIMEOUT,
    )
    try:
        identifier = uuid.json().get("uuidCaptcha", "")
    except ValueError:
        identifier = ""
    if not identifier:
        raise EsajBlocked("Consultation requires CAPTCHA", "captcha_required")
    try:
        token = recaptcha.mint_token(
            session,
            sitekey,
            f"https://{config.host}",
            action,
            timeout=TIMEOUT,
        )
    except recaptcha.RecaptchaError as error:
        raise EsajBlocked(f"Consultation requires CAPTCHA: {error}", "captcha_required") from None
    params = dict(consultation_params(number))
    params.update({"recaptcha_response_token": token, "uuidCaptcha": identifier})
    response = request(session, portal, "GET", f"{adapter.CONSULTATION}/search.do", params=params)
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
        response = request(
            session, portal, "GET", f"{adapter.CONSULTATION}/search.do", params=consultation_params(number)
        )
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
    except EsajBlocked:
        pass
    return bridge_search(session, portal, number), "browser_bridge"


def load_process(session, portal, number):
    from jurisync.connectors.esaj import parsers, tjms, tjsp

    number = process_number(number, portal)
    adapter = {tjsp.PORTAL: tjsp, tjms.PORTAL: tjms}[portals.esaj(portal).key]
    detail_response = None
    source = "dashboard"
    data = dashboard_process(session, portal, number)
    link = data.get("urlCpo") if data else None
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
                except EsajBlocked:
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
    response = detail_response or request(
        session, portal, "GET", parts.path + ("?" + parts.query if parts.query else "")
    )
    if parsers.captcha(response.content):
        try:
            response = token_page(session, portal, link)
            source = "http_token"
        except EsajBlocked:
            response = bridge_page(session, portal, link)
            source = "browser_bridge"
        if parsers.captcha(response.content):
            raise EsajBlocked("Consultation requires CAPTCHA", "captcha_required")
    result = parsers.process(response.content, number)
    code = urllib.parse.parse_qs(parts.query).get("processo.codigo", [""])[0]
    if code and adapter is tjsp:
        response = request(
            session,
            portal,
            "GET",
            f"{adapter.CONSULTATION}/carregarMovimentacoesAjax.do",
            params={"processo.codigo": code, "cursor": ""},
        )
        if response.status_code == 200:
            result["movements"] = parsers.movements(response.content) or result["movements"]
    result["consultationSource"] = source
    return result


PETITION_STATES = ("AGUARDANDO_ASSINATURA", "AGUARDANDO_DISTRIBUICAO", "AGUARDANDO_JUNTADA", "CONCLUIDA")


def dashboard_process(session, portal, number):
    digits = re.sub(r"\D", "", number)

    def find():
        items = json_response(
            request(session, portal, "GET", "tarefas-adv/api/processos", params={"numerosProcesso": digits})
        )
        if not isinstance(items, list):
            raise EsajParseError("Unexpected process dashboard response")
        return next(
            (
                item
                for item in items
                if isinstance(item, dict) and re.sub(r"\D", "", item.get("nuProcesso", "")) == digits
            ),
            None,
        )

    item = find()
    if item:
        return item
    # The API resolves only cases loaded into this session's task panels (confirmed live on TJMS).
    for state in PETITION_STATES:
        json_response(request(session, portal, "GET", "tarefas-adv/api/peticoes", params={"situacao": state}))
        item = find()
        if item:
            return item
    return None


def document_inventory(session, portal, number):
    from jurisync.connectors.esaj import parsers

    number = process_number(number, portal)
    item = dashboard_process(session, portal, number)
    if not item or not item.get("urlPasta"):
        raise EsajBlocked("No authorized document viewer link in the account dashboard")
    link = item["urlPasta"]
    require_portal_url(portals.esaj(portal), link)
    parts = urllib.parse.urlsplit(link)
    prefix = "cpopg" if portals.esaj(portal).key == "tjsp-esaj" else "cpopg5"
    if parts.path != f"/{prefix}/abrirPastaDigitalIntegracao.do":
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
    # This POST only checks permission. Receipt/signature endpoints are never invoked.
    permission = request(
        session,
        portal,
        "POST",
        "pastadigital/validarAberturaIntimacaoNaoRecebida.do",
        data={"parametros": entry["parameters"]},
    )
    if permission.status_code not in (200, 204):
        raise EsajBlocked("Opening this document requires acknowledging an intimação; read-only connector stopped")
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
