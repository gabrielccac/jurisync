"""eproc login with the A1 certificate over mutual TLS, without a browser.

Keycloak portals (TRF4, TJSP, TJSC, TJPR...): the login page offers a certificate button that moves to a sibling
SSO host (``eproc-sso-certificado``) which asks the client certificate during the TLS handshake. That host answers
with a "Continue" form (TJPR goes straight on), then the SSO asks the authenticator code like the password flow.

Classic portals (TJMG): the local login form's "Certificado Digital" button posts the same form to
``destino_v2_cert`` (``acao=login_cert``), which sends to ``priv/login_cert.php`` (mutual TLS), then to eproc's own
code screen (``txtAcessoCodigo``, ``hdnAcao=2fa_validar``).

Either way, a user with several profiles (one per OAB registration) lands on "Seleção de perfil" and the profile
named by ``<PREFIX>_USER_PROFILE`` is picked; a single lawyer profile is picked without it.
"""

import re
import time
import urllib.parse

from bs4 import BeautifulSoup
from curl_cffi import requests

from jurisync import portals
from jurisync.connectors.eproc.auth import is_panel, panel_link, portal_totp_account
from jurisync.connectors.pje.auth import signer as native_signer
from jurisync.core import credentials
from jurisync.core.certificate import client_certificate
from jurisync.core.http import IMPERSONATE, TIMEOUT, follow_redirects, require_url
from jurisync.core.session_pool import LoginRejected
from jurisync.core.totp import next_code, totp

CERT_HOST = re.compile(r"document\.location\.host\s*=\s*['\"]([a-z0-9.-]+)['\"]", re.I)
BROKER_HOST = "sso.cloud.pje.jus.br"
CLASSIC_CERT_FIELD = "destino_v2_cert"
CLASSIC_CODE_FIELD = "txtAcessoCodigo"
PROFILE_FORM = "frmEscolherUsuario"
PROFILE_BUTTON = re.compile(r"acaoLogar\('([^']+)'\)")
PROFILE_ACTION = re.compile(r"\.action\s*=\s*'([^']*pessoa_usuario_logar[^']*)'")


# Sections whose single sign-on lives under their tribunal's domain instead of their own.
SSO_PARENTS = {
    "jfsc.jus.br": "trf4.jus.br",
    "jfrs.jus.br": "trf4.jus.br",
    "jfpr.jus.br": "trf4.jus.br",
    "jfrj.jus.br": "trf2.jus.br",
    "jfes.jus.br": "trf2.jus.br",
}
SSO_WORDS = {"sso", "keycloak"}


def is_sso_host(config, host):
    """Whether ``host`` is a single sign-on host of this portal's own court.

    The host must sit under the court domain of the configured portal host (or of its tribunal, for the federal
    sections) and its first label must carry ``sso`` or ``keycloak`` as a whole word: ``eproc-sso.trf4``,
    ``eproc-sso-certificado.trf4``, ``cert-sso.tjsc``, ``certificado-sso.tjsp``, ``keycloak-eks.tjrs``. A substring is
    not enough (``processo.trf4.jus.br`` is not an SSO host), nor is another court's SSO.
    """
    court = ".".join(config.host.lower().rsplit(".", 3)[-3:])
    if not court.endswith(".jus.br"):
        return False
    for domain in {court, SSO_PARENTS.get(court, court)}:
        if host.endswith("." + domain):
            first = host[: -len(domain) - 1].split(".")[0]
            if SSO_WORDS & set(first.split("-")):
                return True
    return False


def require_login_url(config, value):
    """A login redirect must stay on the portal, an SSO host of its own court or the national broker.

    The A1 certificate is presented and the authenticator code posted only to hosts accepted here.
    """
    hosts = {config.host, BROKER_HOST}
    host = (urllib.parse.urlsplit(value or "").hostname or "").lower()
    if config.key == "tjms-eproc-1g" and host == "mtls.tjms.jus.br":
        return require_url(value, hosts={host}, path="/priv/login_cert.php", label="TJMS certificate login")
    if is_sso_host(config, host):
        hosts.add(host)
    return require_url(value, hosts=hosts, label="eproc login redirect")


def _follow(session, config, response, *, cert=None, hops=14):
    return follow_redirects(session, response, check=lambda url: require_login_url(config, url), cert=cert, hops=hops)


def _form(html, page_url, config, form_id):
    form = BeautifulSoup(html, "html.parser").find("form", id=form_id)
    if form is None or not form.get("action"):
        return None, {}
    action = urllib.parse.urljoin(page_url, form["action"])
    require_login_url(config, action)
    fields = {
        field["name"]: field.get("value", "")
        for field in form.find_all("input")
        if field.get("name") and (field.get("type") not in ("checkbox", "radio") or field.has_attr("checked"))
    }
    return action, fields


def _certificate_url(html, page_url, config):
    """The login page URL moved to the host that asks for the client certificate."""
    match = CERT_HOST.search(html)
    if not match:
        raise RuntimeError("eproc login page has no certificate option")
    parts = urllib.parse.urlsplit(page_url)
    url = urllib.parse.urlunsplit(parts._replace(netloc=match.group(1).lower()))
    require_login_url(config, url)
    return url


def _login_page(session, config):
    """The SSO login page. The portal root sometimes serves its home page instead of redirecting to it."""
    for url in (config.login_url, config.url("externo_controlador.php?acao=SSO/login")):
        page = _follow(
            session, config, session.get(url, impersonate=IMPERSONATE, allow_redirects=False, timeout=TIMEOUT)
        )
        if CERT_HOST.search(page.text) or _classic_certificate_url(page, config):
            break
    return page


def _classic_certificate_url(page, config):
    """The ``login_cert`` address behind a classic login form's certificate button, if the page has one."""
    field = BeautifulSoup(page.text, "html.parser").find("input", attrs={"name": CLASSIC_CERT_FIELD})
    if field is None or not field.get("value"):
        return None
    return require_login_url(config, urllib.parse.urljoin(page.url, field["value"]))


def _post(session, url, data, referer, **options):
    origin = "{0.scheme}://{0.netloc}".format(urllib.parse.urlsplit(url))
    return session.post(
        url,
        data=data,
        headers={"Referer": referer, "Origin": origin},
        impersonate=IMPERSONATE,
        allow_redirects=False,
        timeout=TIMEOUT,
        **options,
    )


def _classic_login(session, config, page, pem):
    """Post the local login form to its certificate address (what ``SubmitCert()`` does), without a password."""
    _, fields = _form(page.text, page.url, config, "frmLogin")
    response = _follow(
        session, config, _post(session, _classic_certificate_url(page, config), fields, page.url, cert=pem), cert=pem
    )
    if "erro" in urllib.parse.parse_qs(urllib.parse.urlsplit(response.url).query):
        raise LoginRejected("eproc did not accept the A1 certificate")
    return response


def _code_form(response):
    field = BeautifulSoup(response.text, "html.parser").find("input", attrs={"name": CLASSIC_CODE_FIELD})
    return field.find_parent("form") if field is not None else None


def _classic_otp_step(session, config, response, account):
    """eproc's own code screen (after a classic certificate login): ``hdnAcao=2fa_validar``."""
    if _code_form(response) is None:
        return response
    for code in (totp(account).now(), None):
        code = code or next_code(account)  # a code already used in this window by a recent login
        form = _code_form(response)
        action, fields = _form(response.text, response.url, config, form.get("id"))
        fields.update({CLASSIC_CODE_FIELD: code, "hdnAcao": "2fa_validar"})
        response = _follow(session, config, _post(session, action, fields, response.url))
        if _code_form(response) is None:
            return response
        time.sleep(1)
    raise LoginRejected("eproc rejected the OTP")


def profiles(html):
    """``[(id, label)]`` of the "Seleção de perfil" page, e.g. ``("6117...", "GO051632 / ADVOGADO")``."""
    form = BeautifulSoup(html, "html.parser").find("form", id=PROFILE_FORM)
    if form is None:
        return []
    found = []
    for button in form.find_all("button"):
        match = PROFILE_BUTTON.search(button.get("onclick") or "")
        if match:
            label = button.get("data-descricao") or " / ".join(button.stripped_strings)
            found.append((match.group(1), " ".join(label.split())))
    return found


def _choose_profile(session, config, response):
    """Pick the configured profile on "Seleção de perfil" (navigation only: never "Definir como padrão")."""
    options = profiles(response.text)
    if not options:
        return response
    wanted = config.env("USER_PROFILE").upper()
    if wanted:
        chosen = [option for option in options if wanted in option[1].upper()]
    else:
        chosen = [option for option in options if "ADVOGADO" in option[1].upper()]
    if len(chosen) != 1:
        names = ", ".join(label for _, label in options)
        raise LoginRejected(
            f"eproc asks which profile to use ({names}); set {config.env_name('USER_PROFILE')} to one of them"
        )
    action = PROFILE_ACTION.search(response.text)
    path = action.group(1) if action else "controlador.php?acao=pessoa_usuario_logar"
    url = urllib.parse.urljoin(response.url, f"{path}&id_usuario={chosen[0][0]}")
    _, fields = _form(response.text, response.url, config, PROFILE_FORM)
    return _follow(session, config, _post(session, require_login_url(config, url), fields, response.url))


def _otp_step(session, config, response, account):
    action, _ = _form(response.text, response.url, config, "kc-otp-login-form")
    if action is None:
        return response
    for code in (totp(account).now(), None):
        code = code or next_code(account)  # a code already used in this window by a recent login
        submitted = session.post(
            action,
            data={"otp": code, "login": "Sign In"},
            impersonate=IMPERSONATE,
            allow_redirects=False,
            timeout=TIMEOUT,
        )
        response = _follow(session, config, submitted)
        action, _ = _form(response.text, response.url, config, "kc-otp-login-form")
        if action is None:
            return response
        time.sleep(1)
    raise LoginRejected("eproc rejected the OTP")


def _tjms_login(session, config, key, cert, chain, account):
    """TJMS 1G uses the local certificate form, not Keycloak."""
    page = _follow(
        session, config, session.get(config.login_url, impersonate=IMPERSONATE, allow_redirects=False, timeout=TIMEOUT)
    )
    soup = BeautifulSoup(page.text, "html.parser")
    form, target = soup.select_one("form#frmLogin"), soup.select_one("#destino_v2_cert")
    if form is None or target is None:
        raise RuntimeError("TJMS certificate login form was not returned")
    action = urllib.parse.urljoin(page.url, target.get("value", ""))
    require_login_url(config, action)
    if action != config.url("externo_controlador.php?acao=login_cert"):
        raise RuntimeError("TJMS certificate form returned an unexpected action")
    fields = {
        i["name"]: i.get("value", "")
        for i in form.select("input[name]")
        if i.get("type", "text") in {"hidden", "text", "password"}
    }
    with client_certificate(key, cert, chain) as pem:
        response = _follow(
            session,
            config,
            session.post(
                action, data=fields, cert=pem, impersonate=IMPERSONATE, allow_redirects=False, timeout=TIMEOUT
            ),
            cert=pem,
        )
    soup = BeautifulSoup(response.text, "html.parser")
    field = soup.select_one("#txtAcessoCodigo")
    if field is not None:
        form = field.find_parent("form")
        button = soup.select_one("#btnValidar")
        action_match = re.search(r"Submit\(['\"]([^'\"]+)", button.get("onclick", "")) if button else None
        if form is None or action_match is None or action_match[1] != "2fa_validar":
            raise RuntimeError("TJMS TOTP form was not recognized")
        action = urllib.parse.urljoin(response.url, form.get("action") or "index.php")
        require_login_url(config, action)
        parts = urllib.parse.urlsplit(action)
        if parts.path not in {"/index.php", config.root + "index.php"} or parts.query:
            raise RuntimeError("TJMS TOTP form returned an unexpected action")
        fields = {i["name"]: i.get("value", "") for i in form.select('input[type="hidden"][name]')}
        fields.pop("chkLiberarDispositivo", None)
        fields.update(hdnAcao=action_match[1])
        fields[field["name"]] = totp(account).now()
        response = _follow(
            session,
            config,
            session.post(action, data=fields, impersonate=IMPERSONATE, allow_redirects=False, timeout=TIMEOUT),
        )
    if not is_panel(response.url) or not any(c.name == "PHPSESSID" for c in session.cookies.jar):
        raise RuntimeError("TJMS certificate login did not reach the panel")
    return session, response.text, response.url


def cert_login(portal, *, pin=None, p12_path=None, session=None):
    """Return ``(session, panel_html, panel_url)`` like the browser login, using the A1 certificate."""
    config = portals.eproc(portal)
    pin = pin or credentials.env("PJE_A1_PIN")
    if not pin:
        raise RuntimeError("Set PJE_A1_PIN to the A1 certificate password")
    account = portal_totp_account(config)
    key, cert, chain = native_signer.load_a1(p12_path, pin)
    session = session or requests.Session()
    if config.key == "tjms-eproc-1g":
        return _tjms_login(session, config, key, cert, chain, account)
    with client_certificate(key, cert, chain) as pem:
        page = _login_page(session, config)
        if _classic_certificate_url(page, config):
            response = _classic_otp_step(session, config, _classic_login(session, config, page, pem), account)
        else:
            response = _follow(
                session,
                config,
                session.get(
                    _certificate_url(page.text, page.url, config),
                    impersonate=IMPERSONATE,
                    cert=pem,
                    allow_redirects=False,
                    timeout=TIMEOUT,
                ),
                cert=pem,
            )
    if not _classic_certificate_url(page, config):
        action, fields = _form(response.text, response.url, config, "kc-x509-login-info")
        if action is not None:
            response = _follow(
                session,
                config,
                session.post(action, data=fields, impersonate=IMPERSONATE, allow_redirects=False, timeout=TIMEOUT),
            )
        elif _form(response.text, response.url, config, "kc-otp-login-form")[0] is None:
            # TJPR skips the "Continue" form and asks the code at once; anything else is a refusal.
            raise LoginRejected("eproc did not accept the A1 certificate")
        response = _otp_step(session, config, response, account)
    response = _choose_profile(session, config, response)
    if not is_panel(response.url):
        link = panel_link(response.text, response.url)  # landed on another page of the logged-in system
        if link:
            response = _follow(
                session,
                config,
                session.get(
                    require_login_url(config, link), impersonate=IMPERSONATE, allow_redirects=False, timeout=TIMEOUT
                ),
            )
    if not is_panel(response.url) or not any(c.name == "PHPSESSID" for c in session.cookies.jar):
        raise RuntimeError("eproc certificate login did not reach the panel")
    return session, response.text, response.url
