"""Shared e-SAJ plumbing: the connector's errors, the host guard and the one guarded request every read goes through.

Everything here is read-only. Pages that answer 403 (captcha, or a flow the portal wants to start itself) are reported
as ``EsajBlocked`` and never worked around.
"""

import re
import urllib.parse

from jurisync import portals
from jurisync.core.http import IMPERSONATE, REDIRECTS, TIMEOUT, follow_redirects, log_request, require_url
from jurisync.core.session_pool import SessionExpired


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


def process_number(value, portal):
    config = portals.esaj(portal)
    if not isinstance(value, str) or not portals.CNJ.fullmatch(value):
        raise ValueError("processNumber must be a formatted CNJ number")
    match = portals.CNJ.fullmatch(value)
    if match[1] != config.justice or match[2] != config.region:
        raise ValueError("processNumber does not belong to this court")
    return value


def adapter_for(portal):
    from jurisync.connectors.esaj import tjal, tjam, tjms, tjsp

    return {tjsp.PORTAL: tjsp, tjms.PORTAL: tjms, tjal.PORTAL: tjal, tjam.PORTAL: tjam}[portals.esaj(portal).key]
