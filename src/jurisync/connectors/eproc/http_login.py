"""eproc password (+ A3 certificate) + TOTP login over HTTP, for the classic (non-Keycloak) eproc login form.

The form has two buttons. "Entrar" (``Submit('login')``) posts user and password to ``index.php``: the password
alone, then the TOTP. "Certificado Digital" (``SubmitCert()``) posts the same fields to
``externo_controlador.php?acao=login_cert``, which then asks for the client certificate. The password route is the
default; the certificate route is taken only when the customer logs in with the A3 token (``<PREFIX>_AUTH=a3``).

Why the certificate route goes over HTTP: after the password, classic eproc (TJMG) routes through
``priv/login_cert.php``, which asks for the client certificate in the TLS handshake. A headless Chrome cannot answer
the certificate picker / token PIN, so the browser login hangs. Without a certificate the server answers
``principal&erro=erro_certificado`` (back to the login form). Here the password goes over plain HTTP and only
``login_cert.php`` is fetched with the A3 token (``jurisync.core.mtls``, key kept in the token), as a browser does
when the user picks the certificate.

Certificate flow (from a TJMG HAR): POST externo_controlador.php?acao=login_cert {txtUsuario, pwdSenha, hdnAcao=login}
-> priv/login_cert.php (mTLS) -> entrar_cert -> principal (form with txtAcessoCodigo) -> POST index.php
{txtAcessoCodigo, hdnAcao=2fa_validar} -> ... -> controlador.php?acao=painel_adv_listar.

Infra CAPTCHA: the password post may be answered with an image CAPTCHA (``txtInfraCaptcha``) before the password
is checked; TJTO (``flavor="infra-captcha"``) always asks, other classic portals (TJMG) now and then. A wrong code
only draws a new picture, a right one lets the portal check the password (``msg=Senha ou usuário Inválidos`` back on
the login form) and go on to the code screen. TJTO's ``acao=login_cert`` leaves for a separate mutual-TLS host, so
there the password always goes to ``index.php``.
"""

import re
from urllib.parse import parse_qsl, urljoin, urlsplit

from bs4 import BeautifulSoup
from curl_cffi import requests

from jurisync import portals
from jurisync.connectors.eproc.a3 import _cookies_for, _store_cookies, eproc_message
from jurisync.connectors.eproc.auth import is_panel, panel_link
from jurisync.core import image_captcha, mtls
from jurisync.core.session_pool import LoginRejected
from jurisync.core.totp import next_code, totp

MAX_HOPS = 10
INFRA_CAPTCHA = "infra-captcha"
CAPTCHA_FIELD = "txtInfraCaptcha"
MAX_CAPTCHA_ANSWERS = 3  # wrong codes sent in one login; a wrong code only redraws the picture
MAX_CAPTCHA_PICTURES = 6  # pictures looked at in one login, counting the unreadable ones
# Usernames a portal's login page states it accepts; anything else is refused there, so it is not sent.
USERNAME_FORMATS = {"TJTO": (re.compile(r"[A-Z]{2}[0-9]{6}"), "UF + OAB number with 6 digits, e.g. TO001234")}


class NotClassicLogin(Exception):
    """The portal does not show the classic eproc login form: use the browser login instead (nothing was sent)."""


def _has_field(text, name):
    return BeautifulSoup(text, "html.parser").find(attrs={"name": name}) is not None


def _has_login_form(text):
    """Whether the page shows the classic form: visible ``txtUsuario`` and ``pwdSenha`` inputs.

    The TRF4 Keycloak page (TRF4, its sections and TNU) carries a hidden ``frm-login`` with the same two names; posting
    the password to the portal's ``login_cert`` from there would send the credentials down the classic route.
    """
    soup = BeautifulSoup(text, "html.parser")
    return all(
        any(field.get("type", "text").lower() != "hidden" for field in soup.find_all("input", attrs={"name": name}))
        for name in ("txtUsuario", "pwdSenha")
    )


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


def _captcha_step(session, response, config, signer_factory, trail):
    """Answer the Infra image CAPTCHA until the portal moves on; returns the response after it."""
    answers = 0
    for _ in range(MAX_CAPTCHA_PICTURES):
        soup = BeautifulSoup(response.text, "html.parser")
        field = soup.find(attrs={"name": CAPTCHA_FIELD})
        if field is None:
            return response
        if answers >= MAX_CAPTCHA_ANSWERS:
            break
        image = soup.select_one("#divInfraCaptcha img") or soup.select_one("#lblInfraCaptcha img")
        try:
            code, source = image_captcha.solve(
                image_captcha.data_uri_bytes(image.get("src") if image else ""), attempt=answers
            )
        except image_captcha.UnreadableCaptcha:
            trail.append("captcha(redraw)")
            response = session.get(response.url, impersonate="chrome", timeout=60, allow_redirects=False)
            response = _follow(session, response, config.host, signer_factory, trail)
            continue
        form = field.find_parent("form")
        fields = {i["name"]: i.get("value", "") for i in form.find_all("input") if i.get("name")} if form else {}
        fields.update({CAPTCHA_FIELD: code, "hdnInfraCaptcha": "1", "hdnAcao": fields.get("hdnAcao") or "login"})
        answers += 1
        trail.append(f"captcha({source})")
        response = _post(session, config.url("index.php"), fields, response.url)
        response = _follow(session, response, config.host, signer_factory, trail)
    raise RuntimeError(f"{config.key}: the image CAPTCHA was not accepted after {answers} answers")


def _post(session, url, data, referer):
    return session.post(
        url,
        data=data,
        headers={"Referer": referer, "Origin": "{0.scheme}://{0.netloc}".format(urlsplit(url))},
        impersonate="chrome",
        timeout=60,
        allow_redirects=False,
    )


def login(username, password, account, *, portal, signer=None, certificate=False):
    """Return ``(session, panel_html, panel_url)``. Raises NotClassicLogin before sending any credential.

    ``certificate``: take the "Certificado Digital" route (password, then the A3 token at ``login_cert.php``);
    otherwise "Entrar" (the password alone).
    """
    from jurisync.connectors.pje.auth import a3

    if account["type"] != "totp":
        raise ValueError("Selected authenticator account is not TOTP")
    config = portals.eproc(portal)
    session = requests.Session()
    trail = []

    def with_token(url):
        if not certificate:
            raise RuntimeError(
                f"{config.key}: the portal asks for the certificate after the password; log in with the A3 token "
                f"({config.env_name('AUTH')}=a3)"
            )
        cookies = _cookies_for(session, config.host)
        if signer is not None:
            return mtls.fetch(url, signer=signer, cookies=cookies)
        with a3.token_lock(), a3.A3Signer() as token:
            return mtls.fetch(url, signer=token, cookies=cookies)

    try:
        page = session.get(config.login_url, impersonate="chrome", timeout=60)
        if page.status_code >= 500:
            raise RuntimeError(f"{config.tribunal} eproc is unavailable (HTTP {page.status_code})")
        if not _has_login_form(page.text):
            raise NotClassicLogin(f"{config.key}: no classic eproc login form")
        if "captcha" in page.text.lower():
            raise NotClassicLogin(f"{config.key}: login page shows a CAPTCHA")

        expected = USERNAME_FORMATS.get(config.tribunal)
        if expected and not expected[0].fullmatch(username.strip().upper()):
            raise RuntimeError(
                f"{config.key}: the username must be {expected[1]}; "
                f"fix {config.env_name('USERNAME')} (nothing was sent)"
            )

        # From here on the credentials are sent: never fall back to another login after this point.
        trail.append("login")
        certificate_route = certificate and config.flavor != INFRA_CAPTCHA
        response = _post(
            session,
            config.url("externo_controlador.php?acao=login_cert" if certificate_route else "index.php"),
            {"txtUsuario": username, "pwdSenha": password, "hdnAcao": "login", "hdnDebug": ""},
            page.url,
        )
        response = _follow(session, response, config.host, with_token, trail)
        response = _captcha_step(session, response, config, with_token, trail)
        if _has_field(response.text, "pwdSenha"):
            reason = eproc_message(response.url, response.text)
            if not certificate_route:
                # On the password route (past any CAPTCHA) the form comes back only when the portal checked and refused
                # the password: never send it again on its own (the session pool keeps a cool-down for LoginRejected).
                raise LoginRejected(
                    f"{config.key}: eproc refused the user or password" + (f" ({reason})" if reason else "")
                )
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

        link = None if is_panel(response.url) else panel_link(response.text, response.url)
        if link:
            # Logged in, but landed on another page (TJMG's "Alterar Cadastro"): plain navigation, nothing is saved.
            trail.append(_acao(link) or "painel")
            response = session.get(link, impersonate="chrome", timeout=60, allow_redirects=False)
            response = _follow(session, response, config.host, with_token, trail)
        if not is_panel(response.url) and not is_panel(response.text):
            if _acao(response.url) == "pessoa_alterar":
                raise RuntimeError(
                    f"{config.key}: the portal holds the login on its registration update page (Alterar Cadastro); "
                    "the lawyer has to log in once in a browser and confirm the registration data"
                )
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
