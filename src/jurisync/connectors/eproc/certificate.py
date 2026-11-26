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
from types import SimpleNamespace

from bs4 import BeautifulSoup
from curl_cffi import requests

from jurisync import portals
from jurisync.connectors.eproc.auth import is_panel, panel_link, portal_totp_account, session_from_cookies
from jurisync.core.certificate import client_certificate, load_configured
from jurisync.core.http import IMPERSONATE, TIMEOUT, UnsafeUrlError, follow_redirects, require_url
from jurisync.core.redaction import url_shape
from jurisync.core.session_pool import LoginFailed, LoginRejected
from jurisync.core.totp import answer_code, totp

CERT_HOST = re.compile(r"document\.location\.host\s*=\s*['\"]([a-z0-9.-]+)['\"]", re.I)
BROKER_HOST = "sso.cloud.pje.jus.br"
CLASSIC_CERT_FIELD = "destino_v2_cert"
CLASSIC_CODE_FIELD = "txtAcessoCodigo"
PROFILE_FORM = "frmEscolherUsuario"
PROFILE_BUTTON = re.compile(r"acaoLogar\(\s*['\"]?([^'\")\s]+)['\"]?\s*\)")
PROFILE_ACTION = re.compile(r"\.action\s*=\s*'([^']*pessoa_usuario_logar[^']*)'")
OAB = re.compile(r"\b[A-Z]{2}[\s./-]*[0-9][0-9.]*(?:-[A-Z])?\b")
# The classic portals' answer to an accepted certificate whose CPF has no user there (TJBA, 13/11/2026).
UNKNOWN_CPF = re.compile(r"N[ãa]o foi localizado usu[áa]rio com este CPF", re.I)
PERSISTENT_CHOICE = re.compile(r"padr[aã]o|liberar|remember|trust|device", re.I)


# Sections whose single sign-on lives under their tribunal's domain instead of their own.
SSO_PARENTS = {
    "jfsc.jus.br": "trf4.jus.br",
    "jfrs.jus.br": "trf4.jus.br",
    "jfpr.jus.br": "trf4.jus.br",
    "jfrj.jus.br": "trf2.jus.br",
    "jfes.jus.br": "trf2.jus.br",
    # TNU (CJF) logs in at the TRF4 eproc realm (eproc-sso.trf4, certificate at eproc-sso-certificado.trf4).
    "cjf.jus.br": "trf4.jus.br",
}
SSO_WORDS = {"sso", "keycloak"}

# Courts whose certificate button moves to a separate mutual-TLS host that is not an SSO. TJTO's
# ``acao=login_cert`` answers 302 to ``mtls.tjto.jus.br<root>externo_controlador.php?acao=login_cert&host=...``;
# only that page and ``priv/login_cert.php`` under the portal root are accepted there.
MTLS_HOSTS = {"tjto-1g": "mtls.tjto.jus.br", "tjto-2g": "mtls.tjto.jus.br"}
MTLS_PAGES = ("externo_controlador.php", "priv/login_cert.php")


# Courts whose classic ``index.php`` / ``login_cert`` path answers ``erro_certificado`` to a valid A1 (TJSC, 19/11/2026);
# their login starts at the SSO entry and never takes the classic form.
SSO_ONLY = {"tjsc-1g", "tjsc-2g"}

# Courts whose eproc entry answers an HTTP client with a JavaScript bot challenge (TRF6: F5 "bobcmn"/TSPD page, even
# with a browser's cookies). A headless browser only opens the entry, which redirects to the Keycloak login page, and
# hands its cookies over; the certificate host (mutual TLS), the code, the profile and every read go over HTTP like
# any other Keycloak portal. The browser types nothing and clicks nothing.
BROWSER_ENTRY = {"trf6-1g", "trf6-2g"}
BROWSER_ENTRY_TIMEOUT = 60
SSO_ENTRY = "externo_controlador.php?acao=SSO/login"
# A page still answering with a bot challenge (TRF6: F5 "bobcmn"/TSPD script; the other vendors' markers too) and the
# F5 refusal page ("The requested URL was rejected"). The challenge page itself carries "Your support ID" in its
# <noscript>, so only the refusal's own sentence marks a refusal.
BOT_CHALLENGE = re.compile(
    r"bobcmn|TSPD|cf-chl|challenge-platform|_Incapsula_Resource|verify you are human|captcha", re.I
)
BOT_REFUSAL = re.compile(r"The requested URL was rejected|Request Rejected", re.I)
# The portal's own home keeps the F5 "TSPD" script after the challenge is cleared (TRF6, 25/11/2026): its login form
# tells it from a challenge.
PORTAL_HOME = re.compile(r"""id=["']?frmLogin\b""")
# A page that is neither a challenge nor the certificate option (the portal's own home, a classic form) is given this
# long before the browser opens the SSO entry instead, as the HTTP login does.
BROWSER_SETTLE = 8


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
    if host and host == MTLS_HOSTS.get(config.key):
        path = urllib.parse.urlsplit(value).path
        if path not in {config.root + page for page in MTLS_PAGES}:
            raise UnsafeUrlError(f"Unexpected {config.tribunal} certificate login URL path: {url_shape(value)}")
        return require_url(value, hosts={host}, label=f"{config.tribunal} certificate login")
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
    entries = [config.login_url, config.url(SSO_ENTRY)]
    if config.key in SSO_ONLY:
        entries.reverse()  # SSO entry first
    for url in entries:
        page = _follow(
            session, config, session.get(url, impersonate=IMPERSONATE, allow_redirects=False, timeout=TIMEOUT)
        )
        if CERT_HOST.search(page.text) or _classic_certificate_url(page, config):
            break
    return page


def _browser_cookies(driver):
    """Every cookie of the browser, for all hosts (the entry's own bot cookies too, not only the current page's)."""
    try:
        cookies = driver.execute_cdp_cmd("Network.getAllCookies", {})["cookies"]
    except Exception:  # noqa: BLE001 - a driver without CDP: the current page's cookies
        return driver.get_cookies()
    result = []
    for cookie in cookies:
        cookie = dict(cookie)
        expires = cookie.pop("expires", None)
        if isinstance(expires, (int, float)) and expires > 0 and not cookie.get("session"):
            cookie["expiry"] = int(expires)
        result.append(cookie)
    return result


def _browser_seen(driver):
    """What the browser shows, for an error message: address shape, HTTP status of the page and its title."""
    try:
        status = driver.execute_script(
            "var e = performance.getEntriesByType('navigation')[0]; return e ? e.responseStatus : null"
        )
    except Exception:  # noqa: BLE001 - a page without the API (Chrome's own error page)
        status = None
    try:
        title = " ".join((driver.title or "").split())[:80]
    except Exception:  # noqa: BLE001
        title = ""
    seen = url_shape(driver.get_current_url())
    if status:
        seen += f", HTTP {status}"
    return seen + (f", title {title!r}" if title else "")


def _challenged(html):
    return bool(BOT_CHALLENGE.search(html)) and not PORTAL_HOME.search(html)


def _browser_failure(config, driver, html):
    """``LoginFailed`` for an entry that never offered the certificate, with what the browser was showing."""
    seen = _browser_seen(driver)
    if _challenged(html):
        return LoginFailed(
            f"{config.tribunal} eproc login page did not open in the browser: the portal's bot challenge did not let "
            f"it through in {BROWSER_ENTRY_TIMEOUT} s ({seen}); nothing was sent",
            reason="captcha",
        )
    return LoginFailed(
        f"{config.tribunal} eproc login page did not open in the browser: the portal answered no login page "
        f"in {BROWSER_ENTRY_TIMEOUT} s ({seen}); nothing was sent",
        reason="portal_unavailable",
    )


def _browser_open(config, driver, url):
    try:
        driver.get(url)
    except Exception as error:  # noqa: BLE001 - selenium's page-load timeout or network error
        raise LoginFailed(
            f"{config.tribunal} eproc entry did not load in the browser ({type(error).__name__}: {url_shape(url)})",
            reason="portal_unavailable",
        ) from error


def _browser_login_page(session, config):
    """The SSO login page as a headless browser reaches it past the entry's bot challenge; its cookies go to ``session``.

    Only navigation: the browser opens the portal entry and waits for the page that offers the certificate. A page that
    settles without a challenge and without that option sends it to the SSO entry once (as ``_login_page`` does over
    HTTP); a challenge that never clears, or the F5 refusal, stops with ``captcha``, anything else with
    ``portal_unavailable``. Nothing is typed or clicked, and a refusal is never tried again.
    """
    from seleniumbase import Driver  # imported here: other portals never pay for it

    driver = Driver(uc=True, headless=True, locale="pt-BR")
    try:
        entries = [config.login_url, config.url(SSO_ENTRY)]
        _browser_open(config, driver, entries.pop(0))
        deadline = time.monotonic() + BROWSER_ENTRY_TIMEOUT
        settled = None
        while True:
            html = driver.page_source or ""
            if CERT_HOST.search(html):
                break
            if BOT_REFUSAL.search(html):
                raise LoginFailed(
                    f"{config.tribunal} eproc refused the browser at its bot check ({_browser_seen(driver)}); "
                    "nothing was sent",
                    reason="captcha",
                )
            now = time.monotonic()
            if _challenged(html):
                settled = None
            elif settled is None:
                settled = now
            elif entries and now - settled >= BROWSER_SETTLE:
                _browser_open(config, driver, entries.pop(0))
                settled = None
                continue
            if now > deadline:
                raise _browser_failure(config, driver, html)
            time.sleep(0.5)
        url = require_login_url(config, driver.get_current_url())
        for cookie in session_from_cookies(_browser_cookies(driver)).cookies.jar:
            session.cookies.jar.set_cookie(cookie)
        return SimpleNamespace(text=html, url=url, status_code=200)
    finally:
        driver.quit()


def _classic_certificate_url(page, config):
    """The ``login_cert`` address behind a classic login form's certificate button, if the page has one."""
    if config.key in SSO_ONLY:
        return None
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
    try:
        response = _follow(
            session,
            config,
            _post(session, _classic_certificate_url(page, config), fields, page.url, cert=pem),
            cert=pem,
        )
    except requests.exceptions.DNSError as error:
        host = MTLS_HOSTS.get(config.key)
        if not host or host not in str(error):
            raise
        # Seen 13-16/11/2026: the court sends the certificate login to a host it has not published in DNS.
        raise LoginFailed(
            f"{config.tribunal} certificate login host {host} does not resolve (not published in DNS by the court); "
            "certificate login is unavailable until it is",
            reason="portal_unavailable",
        ) from None
    if "erro" in urllib.parse.parse_qs(urllib.parse.urlsplit(response.url).query):
        raise LoginRejected("eproc did not accept the A1 certificate", reason="certificate_refused")
    return response


def _code_form(response):
    field = BeautifulSoup(response.text, "html.parser").find("input", attrs={"name": CLASSIC_CODE_FIELD})
    return field.find_parent("form") if field is not None else None


def _classic_otp_step(session, config, response, account):
    """eproc's own code screen (after a classic certificate login): ``hdnAcao=2fa_validar``."""

    def submit(page, form, code):
        action, fields = _form(page.text, page.url, config, form.get("id"))
        fields.update({CLASSIC_CODE_FIELD: code, "hdnAcao": "2fa_validar"})
        return _follow(session, config, _post(session, action, fields, page.url))

    return answer_code(response, account, _code_form, submit, label="eproc", pause=1)


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


def _oab(value):
    """``GO051632``, ``go 51.632`` and ``GO-51632`` all read as ``GO51632``."""
    compact = re.sub(r"[^A-Z0-9]", "", value.upper())
    return re.sub(r"^([A-Z]{2})0+(?=[0-9])", r"\1", compact)


def _landing(response):
    """Where a login stopped, safe to report: the page address without its query and the page title."""
    title = BeautifulSoup(response.text or "", "html.parser").title
    return f"{url_shape(response.url)}, HTTP {response.status_code}, title {(title.get_text(' ', strip=True) if title else '')[:80]!r}"


def _choose_profile(session, config, response):
    """Pick the configured profile on "Seleção de perfil" (navigation only: never "Definir como padrão").

    ``<PREFIX>_USER_PROFILE`` names the profile by its OAB (``GO051632``) or by a word of its label; without it the
    single lawyer profile is taken. Several matches stop the login with the choices instead of guessing.
    """
    options = profiles(response.text)
    if not options:
        return response
    lawyers = [option for option in options if "ADVOGADO" in option[1].upper() and "TITULAR" not in option[1].upper()]
    wanted = config.env("USER_PROFILE").strip()
    if wanted:
        oabs = {option: {_oab(found) for found in OAB.findall(option[1].upper())} for option in options}
        chosen = [option for option in options if _oab(wanted) in oabs[option]]
        if not chosen:
            chosen = [option for option in options if wanted.upper() in option[1].upper()]
    else:
        chosen = lawyers
    if len(chosen) != 1:
        names = ", ".join(label for _, label in options)
        raise LoginRejected(
            f"eproc asks which profile to use ({names}); set {config.env_name('USER_PROFILE')} to one of them",
            reason="unexpected",
        )
    action = PROFILE_ACTION.search(response.text)
    path = action.group(1) if action else "controlador.php?acao=pessoa_usuario_logar"
    url = urllib.parse.urljoin(response.url, f"{path}&id_usuario={chosen[0][0]}")
    _, fields = _form(response.text, response.url, config, PROFILE_FORM)
    # Only the navigation: never "use as default" or "trust this device", even when the page pre-checks them.
    fields = {name: value for name, value in fields.items() if not PERSISTENT_CHOICE.search(name)}
    return _follow(session, config, _post(session, require_login_url(config, url), fields, response.url))


def _otp_step(session, config, response, account):
    def action(page):
        return _form(page.text, page.url, config, "kc-otp-login-form")[0]

    def submit(page, url, code):
        data = {"otp": code, "login": "Sign In"}
        return _follow(
            session,
            config,
            session.post(url, data=data, impersonate=IMPERSONATE, allow_redirects=False, timeout=TIMEOUT),
        )

    return answer_code(response, account, action, submit, label="eproc", pause=1)


def _local_certificate_login(session, config, key, cert, chain, account):
    """TJMS 1G and TJMG 2G use the local certificate form, not Keycloak."""
    page = _follow(
        session, config, session.get(config.login_url, impersonate=IMPERSONATE, allow_redirects=False, timeout=TIMEOUT)
    )
    soup = BeautifulSoup(page.text, "html.parser")
    form, target = soup.select_one("form#frmLogin"), soup.select_one("#destino_v2_cert")
    if form is None or target is None:
        raise RuntimeError("Local eproc certificate login form was not returned")
    action = urllib.parse.urljoin(page.url, target.get("value", ""))
    require_login_url(config, action)
    if action != config.url("externo_controlador.php?acao=login_cert"):
        raise RuntimeError("Local eproc certificate form returned an unexpected action")
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
            raise RuntimeError("Local eproc TOTP form was not recognized")
        action = urllib.parse.urljoin(response.url, form.get("action") or "index.php")
        require_login_url(config, action)
        parts = urllib.parse.urlsplit(action)
        if parts.path not in {"/index.php", config.root + "index.php"} or parts.query:
            raise RuntimeError("Local eproc TOTP form returned an unexpected action")
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
        raise RuntimeError("Local eproc certificate login did not reach the panel")
    return session, response.text, response.url


def rooted(config, url):
    """The panel URL under the portal's application root.

    TJAC's SSO sends the browser back to ``/controlador.php`` while the application (and every link it serves) lives under
    ``/eproc/``; relative links resolved against the first would leave the root the route checks require.
    """
    parts = urllib.parse.urlsplit(url)
    if parts.hostname == config.host and not parts.path.startswith(config.root):
        return urllib.parse.urlunsplit(parts._replace(path=config.root + parts.path.rsplit("/", 1)[-1]))
    return url


def cert_login(portal, *, pin=None, p12_path=None, session=None):
    """Return ``(session, panel_html, panel_url)`` like the browser login, using the A1 certificate."""
    config = portals.eproc(portal)
    key, cert, chain = load_configured(pin=pin, p12_path=p12_path)
    account = portal_totp_account(config)
    session = session or requests.Session()
    if config.key in {"tjms-eproc-1g", "tjmg-2g"}:
        return _local_certificate_login(session, config, key, cert, chain, account)
    page = _browser_login_page(session, config) if config.key in BROWSER_ENTRY else _login_page(session, config)
    with client_certificate(key, cert, chain) as pem:
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
            raise LoginRejected("eproc did not accept the A1 certificate", reason="certificate_refused")
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
    # TJBA sends this notice in UTF-8 inside a hidden infra textarea while the page is read as Latin-1
    if UNKNOWN_CPF.search(response.content.decode("utf-8", errors="replace")):
        raise LoginRejected(
            f"{config.key}: the portal has no user for this certificate's CPF (Não foi localizado usuário com este CPF); "
            "the lawyer has to register in this eproc. Retrying will not help.",
            reason="not_registered",
        )
    if not is_panel(response.url) or not any(c.name == "PHPSESSID" for c in session.cookies.jar):
        raise RuntimeError(f"eproc certificate login did not reach the panel ({_landing(response)})")
    return session, response.text, rooted(config, response.url)
