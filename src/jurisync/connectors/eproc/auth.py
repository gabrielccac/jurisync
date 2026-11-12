"""eproc browser login and PHP session extraction."""

import time
from http.cookiejar import Cookie
from pathlib import Path

from curl_cffi import requests
from selenium.common.exceptions import StaleElementReferenceException, UnexpectedAlertPresentException
from seleniumbase import Driver

from jurisync import portals
from jurisync.core.totp import accounts_from_image, next_code, parse_accounts, parse_index, select_account, totp

PANEL_MARKER = "acao=painel_adv_listar"
PANEL_READY = 'a[aria-describedby="processoscomprazoemaberto"]'
# TJMG's panel builds the deadline table in JS without that aria attribute; its panel form is always present.
PANEL_READY_ALT = ("#frmPainelAdvogado",)
LOGIN_FIELDS = {
    "username": ("#username", "#txtUsuario"),
    "password": ("#password", "#pwdSenha"),
    "submit": ("#kc-login", "#sbmEntrar"),
    "otp": ("#otp", "#txtAcessoCodigo"),
    "otp_submit": ("#kc-login", "#btnValidar"),
}


def portal_totp_account(portal):
    """Select this portal's TOTP account from its own authenticator export."""
    portal = portals.eproc(portal)
    data, qr_path = portal.env("2FA_DATA"), portal.env("2FA_QR")
    if not data and not qr_path:
        raise RuntimeError(f"Set {portal.env_name('2FA_DATA')} or {portal.env_name('2FA_QR')} in .env")
    return select_account(
        parse_accounts(data) if data else accounts_from_image(Path(qr_path)),
        match=portal.env("OTP_PROFILE_MATCH"),
        index=parse_index(portal.env("OTP_PROFILE_INDEX"), portal.env_name("OTP_PROFILE_INDEX")),
        label=portal.env_prefix,
    )


def dismiss_alert(driver):
    """Accept a blocking JS alert and return its text (some portals confirm new devices this way)."""
    try:
        alert = driver.switch_to.alert
    except Exception:
        return ""
    try:
        text = alert.text
    except Exception:
        text = ""
    try:
        alert.accept()
    except Exception:
        pass
    return text


def has(driver, selector, timeout=0.5):
    try:
        return driver.wait_for_element(selector, timeout=timeout) is not None
    except UnexpectedAlertPresentException:
        dismiss_alert(driver)
        try:
            return driver.wait_for_element(selector, timeout=timeout) is not None
        except Exception:
            return False
    except Exception:
        return False


def page_hint(driver, limit=160):
    """A short single-line excerpt of what the browser shows, for failure messages (never page source)."""
    for selector in ("#input-error", ".alert-error", "#kc-error-message", ".alert", "body"):
        try:
            if has(driver, selector, 0.5):
                text = " ".join((driver.get_text(selector) or "").split())
                if text:
                    return text[:limit]
        except Exception:  # noqa: BLE001 - best effort diagnostics
            continue
    return ""


def wait_for_step(driver, timeout=25):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            url = driver.get_current_url() or ""
        except UnexpectedAlertPresentException:
            text = dismiss_alert(driver)
            if "confirma" in text.lower():
                raise RuntimeError(
                    "eproc asks for a confirmation code (new device/email check); "
                    "fetch the code and continue the login with it"
                ) from None
            continue
        if any(has(driver, selector) for selector in LOGIN_FIELDS["otp"]):
            return "otp"
        if has(driver, "button:contains('Enviar')"):
            return "captcha"
        if PANEL_MARKER in url and any(has(driver, selector) for selector in (PANEL_READY, *PANEL_READY_ALT)):
            return "panel"
        if has(driver, "#username") and any(
            has(driver, selector) for selector in ("#input-error", ".alert-error", "#kc-error-message")
        ):
            return "login"
        time.sleep(0.25)
    hint = page_hint(driver)
    raise RuntimeError("Timed out waiting for the next eproc login step" + (f" (page: {hint})" if hint else ""))


def submit_captcha(driver):
    time.sleep(8)
    for attempt in range(5):
        try:
            driver.click("button:contains('Enviar')")
            return
        except UnexpectedAlertPresentException:
            try:
                driver.switch_to.alert.accept()
            except Exception:
                pass
            time.sleep(1.5)
        except Exception:
            if attempt == 4:
                raise RuntimeError("Could not submit the eproc CAPTCHA") from None


def session_from_cookies(cookies):
    """Copy the complete browser cookie jar into an in-memory HTTP session."""
    session = requests.Session()
    for cookie in cookies:
        name, value = cookie.get("name"), cookie.get("value")
        if not name:
            continue
        domain = cookie.get("domain") or ""
        path = cookie.get("path") or "/"
        expiry = cookie.get("expiry", cookie.get("expires"))
        rest = cookie.get("rest") or {key: cookie[key] for key in ("httpOnly", "sameSite") if key in cookie}
        session.cookies.jar.set_cookie(
            Cookie(
                version=0,
                name=name,
                value=value or "",
                port=None,
                port_specified=False,
                domain=domain,
                domain_specified=bool(domain),
                domain_initial_dot=domain.startswith("."),
                path=path,
                path_specified=True,
                secure=bool(cookie.get("secure")),
                expires=expiry,
                discard=expiry is None,
                comment=None,
                comment_url=None,
                rest=rest,
                rfc2109=False,
            )
        )
    return session


def _first_element(driver, selectors, timeout=10):
    # Poll alternatives together instead of spending the full timeout on absent SSO fields.
    deadline = time.monotonic() + timeout
    while selectors and time.monotonic() < deadline:
        for selector in selectors:
            if has(driver, selector, timeout=0.1):
                return driver.wait_for_element(selector, timeout=timeout)
    return None


def _fill_field(driver, selectors, value, *, timeout=10, attempts=4):
    """Type into the first matching field, finding it again if the page re-renders under us.

    Keycloak (and TJRS in particular) can replace the form between locating a field and typing in it,
    which raises StaleElementReferenceException. Returns the field, or None when none was found.
    """
    for attempt in range(attempts):
        try:
            field = _first_element(driver, selectors, timeout=timeout)
            if field is None:
                return None
            field.clear()
            field.send_keys(value)
            return field
        except StaleElementReferenceException:
            if attempt == attempts - 1:
                raise
            time.sleep(0.5)
    return None


def _submit_otp(driver, code):
    otp_field = _fill_field(driver, LOGIN_FIELDS["otp"], code, timeout=10)
    if otp_field is None:
        raise RuntimeError("Could not find the portal OTP field")
    otp_submit = next((selector for selector in LOGIN_FIELDS["otp_submit"] if has(driver, selector, 2)), None)
    if otp_submit:
        driver.click(otp_submit)
    else:
        try:
            otp_field.send_keys("\n")
        except StaleElementReferenceException:
            pass  # the page already moved on after the code was typed
    time.sleep(1)
    step = wait_for_step(driver, timeout=30)
    while step == "captcha":
        submit_captcha(driver)
        step = wait_for_step(driver, timeout=30)
    return step


_POST_LOGIN_JS = """
const [user, pass, url, done] = arguments;
const body = new URLSearchParams({txtUsuario: user, pwdSenha: pass, hdnAcao: 'login', hdnDebug: ''});
fetch(url, {method: 'POST', body, redirect: 'manual', credentials: 'include',
            headers: {'Content-Type': 'application/x-www-form-urlencoded'}})
  .then(r => done(r.type + ':' + r.status)).catch(e => done('error:' + e));
"""


def _classic_cert_login(driver, config):
    """Classic eproc asks for the client certificate at priv/login_cert.php after the password.

    Headless Chrome cannot answer the certificate picker, so: the password is posted from inside the page (keeping
    the bot-protection cookies valid), login_cert.php is fetched with the A3 token over mTLS, and the browser resumes
    at the URL it redirects to (entrar_cert -> principal, the 2FA form). Returns the next login step.
    """
    from urllib.parse import parse_qsl, urlsplit

    from jurisync.connectors.pje.auth import a3
    from jurisync.core import mtls

    cert_url = config.url("priv/login_cert.php?acao_origem=")
    cookies = {c["name"]: c["value"] for c in driver.get_cookies()}
    with a3.token_lock(), a3.A3Signer() as token:
        cert = mtls.fetch(cert_url, signer=token, cookies=cookies)
    if not cert.certificate_sent:
        raise RuntimeError("eproc did not ask for the certificate at login_cert.php")
    target = cert.location
    if not target:
        raise RuntimeError(f"login_cert.php answered HTTP {cert.status} without a redirect")
    query = dict(parse_qsl(urlsplit(target).query))
    if "erro" in query:
        raise RuntimeError(f"eproc refused the login at the certificate step (erro={query['erro']}; password or A3)")
    for name, value in cert.cookies().items():
        try:
            driver.add_cookie({"name": name, "value": value, "path": "/"})
        except Exception:  # noqa: BLE001 - a cookie the browser refuses is not fatal here
            pass
    driver.get(target)
    time.sleep(1)
    return wait_for_step(driver)


def login(username, password, account, *, portal):
    if account["type"] != "totp":
        raise ValueError("Selected authenticator account is not TOTP")

    config = portals.eproc(portal)
    driver = Driver(uc=True, headless=True, locale="pt-BR")
    try:
        driver.get(config.login_url)
        classic = _first_element(driver, ("#txtUsuario", "#username"), timeout=20)
        if classic is not None and has(driver, "#txtUsuario", 0.5) and has(driver, "#pwdSenha", 0.5):
            # Classic eproc form (TRF6, TJMG): password, then the A3 certificate, then the TOTP.
            result = driver.execute_async_script(
                _POST_LOGIN_JS, username, password, config.url("externo_controlador.php?acao=login_cert")
            )
            if not str(result).startswith("opaqueredirect"):
                raise RuntimeError(f"Unexpected answer to the eproc password step: {result}")
            step = _classic_cert_login(driver, config)
            if step == "otp":
                step = _submit_otp(driver, totp(account).now())
                if step == "otp":
                    step = _submit_otp(driver, next_code(account))
                if step == "otp":
                    raise RuntimeError("eproc rejected the OTP")
            if step != "panel":
                raise RuntimeError(f"Unexpected eproc login step after the certificate: {step}")
            session = session_from_cookies(driver.get_cookies())
            if not any(cookie.name == "PHPSESSID" for cookie in session.cookies.jar):
                session.close()
                raise RuntimeError("Authenticated panel loaded without a PHPSESSID cookie")
            return session, driver.page_source, driver.get_current_url()
        if _fill_field(driver, LOGIN_FIELDS["username"], username, timeout=20) is None:
            raise RuntimeError("Could not find the portal login fields")
        if _fill_field(driver, LOGIN_FIELDS["password"], password, timeout=20) is None:
            raise RuntimeError("Could not find the portal login fields")
        submit = next((selector for selector in LOGIN_FIELDS["submit"] if has(driver, selector, 1)), None)
        if not submit:
            raise RuntimeError("Could not find the portal login button")
        driver.click(submit)
        time.sleep(1)

        step = wait_for_step(driver)
        while step == "captcha":
            submit_captcha(driver)
            step = wait_for_step(driver)
        if step == "login":
            hint = page_hint(driver)
            raise RuntimeError("eproc rejected the username/password" + (f" (page: {hint})" if hint else ""))
        if step == "otp":
            step = _submit_otp(driver, totp(account).now())
            if step == "otp":
                # Usually a code already used in this window by a recent login.
                step = _submit_otp(driver, next_code(account))
            if step == "otp":
                raise RuntimeError("eproc rejected the OTP")
            if step == "login":
                raise RuntimeError("eproc returned to login after OTP submission")

        if step != "panel":
            raise RuntimeError(f"Unexpected eproc login step: {step}")

        session = session_from_cookies(driver.get_cookies())
        if not any(cookie.name == "PHPSESSID" for cookie in session.cookies.jar):
            session.close()
            raise RuntimeError("Authenticated panel loaded without a PHPSESSID cookie")
        return session, driver.page_source, driver.get_current_url()
    finally:
        driver.quit()
