"""eproc password + A3 certificate + TOTP login over HTTP, for the classic (non-Keycloak) eproc login form.

Why: after the password, classic eproc (TJMG) routes through ``priv/login_cert.php``, which asks for the client
certificate in the TLS handshake. A headless Chrome cannot answer the certificate picker / token PIN, so the browser
login hangs. Without a certificate the server answers ``principal&erro=erro_certificado`` (back to the login form).
Here the password goes over plain HTTP and only ``login_cert.php`` is fetched with the A3 token (``jurisync.core.mtls``,
key kept in the token), as a browser does when the user picks the certificate.

Flow (from a TJMG HAR): POST externo_controlador.php?acao=login_cert {txtUsuario, pwdSenha, hdnAcao=login}
-> priv/login_cert.php (mTLS) -> entrar_cert -> principal (form with txtAcessoCodigo) -> POST index.php
{txtAcessoCodigo, hdnAcao=2fa_validar} -> ... -> controlador.php?acao=painel_adv_listar.
"""

from urllib.parse import parse_qsl, urljoin, urlsplit

from bs4 import BeautifulSoup
from curl_cffi import requests

from jurisync import portals
from jurisync.connectors.eproc.a3 import _cookies_for, _store_cookies, eproc_message
from jurisync.connectors.eproc.auth import PANEL_MARKER
from jurisync.core import mtls
from jurisync.core.totp import next_code, totp

MAX_HOPS = 10


class NotClassicLogin(Exception):
    """The portal does not show the classic eproc login form: use the browser login instead (nothing was sent)."""


def _has_field(text, name):
    return BeautifulSoup(text, "html.parser").find(attrs={"name": name}) is not None


def _acao(url):
    return dict(parse_qsl(urlsplit(url).query)).get("acao", "")


def _follow(session, response, host, signer_factory, trail):
    """Follow redirects by hand; fetch ``priv/login_cert.php`` with the A3 certificate. Returns the final response."""
    for _ in range(MAX_HOPS):
        location = response.headers.get("location") if response.status_code in (301, 302, 303, 307, 308) else None
        if not location:
            return response
        url = urljoin(response.url, location)
        if urlsplit(url).hostname != host:
            raise RuntimeError(f"eproc redirected to another host: {urlsplit(url).hostname}")
        if "/priv/login_cert.php" in urlsplit(url).path:
            trail.append("login_cert(A3)")
            cert = signer_factory(url)
            _store_cookies(session, cert, host)
            if not cert.certificate_sent:
                raise RuntimeError("eproc did not ask for the certificate at login_cert.php")
            if not cert.location:
                raise RuntimeError(f"login_cert.php answered HTTP {cert.status} without a redirect")
            nxt = cert.location
            trail.append(_acao(nxt) or urlsplit(nxt).path)
            if "erro" in dict(parse_qsl(urlsplit(nxt).query)):
                raise RuntimeError(f"eproc refused the A3 certificate ({dict(parse_qsl(urlsplit(nxt).query))['erro']})")
            response = session.get(nxt, impersonate="chrome", timeout=60, allow_redirects=False)
            continue
        trail.append(_acao(url) or urlsplit(url).path)
        response = session.get(url, impersonate="chrome", timeout=60, allow_redirects=False)
    raise RuntimeError("Too many eproc redirects: " + " > ".join(trail))


def _post(session, url, data, referer):
    return session.post(
        url,
        data=data,
        headers={"Referer": referer, "Origin": "{0.scheme}://{0.netloc}".format(urlsplit(url))},
        impersonate="chrome",
        timeout=60,
        allow_redirects=False,
    )


def login(username, password, account, *, portal, signer=None):
    """Return ``(session, panel_html, panel_url)``. Raises NotClassicLogin before sending any credential."""
    from jurisync.connectors.pje.auth import a3

    if account["type"] != "totp":
        raise ValueError("Selected authenticator account is not TOTP")
    config = portals.eproc(portal)
    session = requests.Session()
    trail = []

    def with_token(url):
        cookies = _cookies_for(session, config.host)
        if signer is not None:
            return mtls.fetch(url, signer=signer, cookies=cookies)
        with a3.token_lock(), a3.A3Signer() as token:
            return mtls.fetch(url, signer=token, cookies=cookies)

    try:
        page = session.get(config.login_url, impersonate="chrome", timeout=60)
        if page.status_code >= 500:
            raise RuntimeError(f"{config.tribunal} eproc is unavailable (HTTP {page.status_code})")
        if not (_has_field(page.text, "txtUsuario") and _has_field(page.text, "pwdSenha")):
            raise NotClassicLogin(f"{config.key}: no classic eproc login form")
        if "captcha" in page.text.lower():
            raise NotClassicLogin(f"{config.key}: login page shows a CAPTCHA")

        # From here on the credentials are sent: never fall back to another login after this point.
        trail.append("login")
        response = _post(
            session,
            config.url("externo_controlador.php?acao=login_cert"),
            {"txtUsuario": username, "pwdSenha": password, "hdnAcao": "login", "hdnDebug": ""},
            page.url,
        )
        response = _follow(session, response, config.host, with_token, trail)
        if _has_field(response.text, "pwdSenha"):
            reason = eproc_message(response.url, response.text)
            raise RuntimeError(
                "eproc returned to the login form (" + " > ".join(trail) + ")" + (f": {reason}" if reason else "")
            )

        for attempt in range(2):
            if not _has_field(response.text, "txtAcessoCodigo"):
                break
            code = totp(account).now() if attempt == 0 else next_code(account)
            trail.append("2fa" if attempt == 0 else "2fa(next)")
            response = _post(
                session,
                config.url("index.php"),
                {"txtAcessoCodigo": code, "hdnAcao": "2fa_validar", "hdnDebug": ""},
                response.url,
            )
            response = _follow(session, response, config.host, with_token, trail)
        if _has_field(response.text, "txtAcessoCodigo"):
            raise RuntimeError("eproc rejected the OTP")

        if PANEL_MARKER not in response.url and PANEL_MARKER not in response.text:
            reason = eproc_message(response.url, response.text)
            title = BeautifulSoup(response.text, "html.parser").title
            where = " ".join(title.get_text().split())[:120] if title else urlsplit(response.url).path
            raise RuntimeError(
                f"{config.key} login did not reach the panel (page: {where}; "
                + " > ".join(trail)
                + ")"
                + (f": {reason}" if reason else "")
            )
        if not any(cookie.name == "PHPSESSID" for cookie in session.cookies.jar):
            raise RuntimeError("Authenticated panel loaded without a PHPSESSID cookie")
        return session, response.text, response.url
    except Exception:
        session.close()
        raise
