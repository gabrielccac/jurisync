"""eproc browser login and PHP session extraction."""

import time
from http.cookiejar import Cookie

from curl_cffi import requests
from selenium.common.exceptions import UnexpectedAlertPresentException
from seleniumbase import Driver

from jurisync import portals
from jurisync.core.totp import next_code, parse_accounts, parse_index, select_account, totp

PANEL_MARKER = "acao=painel_adv_listar"
PANEL_READY = 'a[aria-describedby="processoscomprazoemaberto"]'
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
    data = portal.env("EXPORT_DATA")
    if not data:
        raise RuntimeError(f"Set {portal.env_name('EXPORT_DATA')} in .env")
    return select_account(
        parse_accounts(data),
        match=portal.env("OTP_PROFILE_MATCH"),
        index=parse_index(portal.env("OTP_PROFILE_INDEX"), portal.env_name("OTP_PROFILE_INDEX")),
        label=portal.env_prefix,
    )


def has(driver, selector, timeout=0.5):
    try:
        return driver.wait_for_element(selector, timeout=timeout) is not None
    except Exception:
        return False


def wait_for_step(driver, timeout=25):
    deadline = time.time() + timeout
    while time.time() < deadline:
        url = driver.get_current_url() or ""
        if any(has(driver, selector) for selector in LOGIN_FIELDS["otp"]):
            return "otp"
        if has(driver, "button:contains('Enviar')"):
            return "captcha"
        if PANEL_MARKER in url and has(driver, PANEL_READY):
            return "panel"
        if has(driver, "#username") and any(
            has(driver, selector) for selector in ("#input-error", ".alert-error", "#kc-error-message")
        ):
            return "login"
        time.sleep(0.25)
    raise RuntimeError("Timed out waiting for the next eproc login step")


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
    for selector in selectors:
        if has(driver, selector, timeout=timeout):
            return driver.wait_for_element(selector, timeout=timeout)
    return None


def _submit_otp(driver, code):
    otp_field = _first_element(driver, LOGIN_FIELDS["otp"], timeout=10)
    if otp_field is None:
        raise RuntimeError("Could not find the portal OTP field")
    otp_field.clear()
    otp_field.send_keys(code)
    otp_submit = next((selector for selector in LOGIN_FIELDS["otp_submit"] if has(driver, selector, 2)), None)
    if otp_submit:
        driver.click(otp_submit)
    else:
        otp_field.send_keys("\n")
    time.sleep(1)
    step = wait_for_step(driver, timeout=30)
    while step == "captcha":
        submit_captcha(driver)
        step = wait_for_step(driver, timeout=30)
    return step


def login(username, password, account, *, portal):
    if account["type"] != "totp":
        raise ValueError("Selected authenticator account is not TOTP")

    config = portals.eproc(portal)
    driver = Driver(uc=True, headless=True, locale="pt-BR")
    try:
        driver.get(config.login_url)
        username_field = _first_element(driver, LOGIN_FIELDS["username"], timeout=20)
        password_field = _first_element(driver, LOGIN_FIELDS["password"], timeout=20)
        if username_field is None or password_field is None:
            raise RuntimeError("Could not find the portal login fields")
        username_field.send_keys(username)
        password_field.send_keys(password)
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
            raise RuntimeError("eproc rejected the username/password")
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
