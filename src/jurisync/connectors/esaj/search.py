"""The first-instance consultation pages: throttling, and the CAPTCHA paths (HTTP-minted token, browser bridge)."""

import time
import urllib.parse

from jurisync import portals
from jurisync.connectors.esaj import common
from jurisync.core.http import IMPERSONATE, TIMEOUT, follow_redirects, log_request


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
        response = common.request(session, portal, "GET", path, **kwargs)
        if not parsers.simultaneous_queries(response.content):
            return response
    raise common.EsajBlocked(
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
        raise common.EsajBlocked(
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
        raise common.EsajBlocked(
            f"The portal needs a browser for this page and the bridge failed: {error}", "captcha_required"
        ) from None
    return BridgeResponse(page["url"], page["html"])


def captcha_uuid(session, config, application):
    """The identifier the consultation page asks for before a token is accepted; "" when the portal gives none.

    This POST sits outside ``request()`` (which allows only reads), so its redirects are followed by hand, host-checked.
    """
    url = config.url(f"{application}/captchaControleAcesso.do")
    common.require_portal_url(config, url)
    response = session.post(
        url, data={"uuidCaptcha": ""}, impersonate=IMPERSONATE, allow_redirects=False, timeout=TIMEOUT
    )
    log_request("POST", url, response.status_code)
    response = follow_redirects(
        session, response, check=lambda target: common.require_portal_url(config, target, "e-SAJ redirect")
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
    common.require_portal_url(config, url)
    parts = urllib.parse.urlsplit(url)
    start = common.request(session, portal, "GET", f"{parts.path.split('/')[1]}/open.do")
    try:
        sitekey, action = recaptcha.sitekey_and_action(start.text)
    except recaptcha.RecaptchaError as error:
        raise common.EsajBlocked(f"Consultation requires CAPTCHA: {error}", "captcha_required") from None
    identifier = captcha_uuid(session, config, parts.path.split("/")[1])
    if not identifier:
        raise common.EsajBlocked("Consultation requires CAPTCHA", "captcha_required")
    try:
        token = recaptcha.mint_token(session, sitekey, f"https://{config.host}", action, timeout=TIMEOUT)
    except recaptcha.RecaptchaError as error:
        raise common.EsajBlocked(f"Consultation requires CAPTCHA: {error}", "captcha_required") from None
    query = dict(urllib.parse.parse_qsl(parts.query))
    query.update({"recaptcha_response_token": token, "uuidCaptcha": identifier})
    return consult(session, portal, parts.path + "?" + urllib.parse.urlencode(query))


def token_fields(session, portal, application, open_page):
    """The ``recaptcha_response_token`` and ``uuidCaptcha`` a consultation request carries, minted over plain HTTP."""
    from jurisync.connectors.esaj import recaptcha

    try:
        sitekey, action = recaptcha.sitekey_and_action(open_page.text)
    except recaptcha.RecaptchaError as error:
        raise common.EsajBlocked(f"Consultation requires CAPTCHA: {error}", "captcha_required") from None
    config = portals.esaj(portal)
    identifier = captcha_uuid(session, config, application)
    if not identifier:
        raise common.EsajBlocked("Consultation requires CAPTCHA", "captcha_required")
    try:
        token = recaptcha.mint_token(session, sitekey, f"https://{config.host}", action, timeout=TIMEOUT)
    except recaptcha.RecaptchaError as error:
        raise common.EsajBlocked(f"Consultation requires CAPTCHA: {error}", "captcha_required") from None
    return {"recaptcha_response_token": token, "uuidCaptcha": identifier}


def token_search(session, portal, adapter, number, open_page):
    """The consultation with a v3 token minted over plain HTTP: no browser, no solver, same exchange the page runs."""
    from jurisync.connectors.esaj import parsers

    params = dict(consultation_params(number))
    params.update(token_fields(session, portal, adapter.CONSULTATION, open_page))
    response = consult(session, portal, f"{adapter.CONSULTATION}/search.do", params=params)
    if parsers.captcha(response.content):
        raise common.EsajBlocked("Consultation requires CAPTCHA", "captcha_required")
    return response


def consultation_search(session, portal, adapter, number):
    """``(response, source)``: plain HTTP, then an HTTP-minted v3 token, then the browser bridge as a last resort."""
    from jurisync.connectors.esaj import parsers

    start = None
    try:
        start = common.request(session, portal, "GET", f"{adapter.CONSULTATION}/open.do")
        if parsers.captcha(start.content):
            raise common.EsajBlocked("Consultation requires CAPTCHA", "captcha_required")
        response = consult(session, portal, f"{adapter.CONSULTATION}/search.do", params=consultation_params(number))
        if parsers.captcha(response.content):
            raise common.EsajBlocked("Consultation requires CAPTCHA", "captcha_required")
        return response, "http"
    except common.EsajBlocked as error:
        if error.reason not in ("captcha_required", "flow_required"):
            raise
    try:
        if start is None:
            raise common.EsajBlocked("Consultation requires CAPTCHA", "captcha_required")
        return token_search(session, portal, adapter, number, start), "http_token"
    except common.EsajBlocked as error:
        if error.reason == "rate_limited":
            raise
    return bridge_search(session, portal, number), "browser_bridge"
