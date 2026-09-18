"""JFSC eproc browser login and PHP session extraction."""

import argparse
import hashlib
import json
import os
import time
from pathlib import Path

import pyotp
from selenium.common.exceptions import UnexpectedAlertPresentException
from seleniumbase import Driver


LOGIN_URL = "https://eproc.jfsc.jus.br/eprocV2/externo_controlador.php"
PANEL_MARKER = "acao=painel_adv_listar"
PANEL_READY = 'a[aria-describedby="processoscomprazoemaberto"]'


def load_env(path=Path(".env")):
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def required_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Set {name} in .env")
    return value


def env_account(account, account_number=None):
    secret = os.environ.get("EPROC_TOTP_SECRET", "").strip()
    if not secret or (account_number is not None and account_number != int(os.environ.get("EPROC_TOTP_ACCOUNT", "1"))):
        return account
    return {
        **account,
        "secret": secret,
        "algorithm": os.environ.get("EPROC_TOTP_ALGORITHM", account.get("algorithm", "sha1")).lower(),
        "digits": int(os.environ.get("EPROC_TOTP_DIGITS", account.get("digits", 6))),
    }


def has(driver, selector, timeout=0.5):
    try:
        return driver.wait_for_element(selector, timeout=timeout) is not None
    except Exception:
        return False


def wait_for_step(driver, timeout=25):
    deadline = time.time() + timeout
    while time.time() < deadline:
        url = driver.get_current_url() or ""
        if has(driver, "#otp"):
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


def login(username, password, account):
    if account["type"] != "totp":
        raise ValueError("Selected authenticator account is not TOTP")

    driver = Driver(uc=True, headless=True, locale="pt-BR")
    try:
        driver.get(LOGIN_URL)
        driver.wait_for_element("#username", timeout=20).send_keys(username)
        driver.wait_for_element("#password", timeout=20).send_keys(password)
        driver.click("#kc-login")
        time.sleep(1)

        step = wait_for_step(driver)
        while step == "captcha":
            submit_captcha(driver)
            step = wait_for_step(driver)
        if step == "login":
            raise RuntimeError("eproc rejected the username/password")
        if step == "otp":
            code = pyotp.TOTP(
                account["secret"],
                digits=account.get("digits", 6),
                digest=getattr(hashlib, account.get("algorithm", "sha1")),
            ).now()
            driver.wait_for_element("#otp", timeout=10).send_keys(code)
            if has(driver, "#kc-login", 2):
                driver.click("#kc-login")
            elif has(driver, "button[type='submit']", 2):
                driver.click("button[type='submit']")
            else:
                driver.send_keys("#otp", "\n")

            time.sleep(1)
            step = wait_for_step(driver, timeout=30)
            while step == "captcha":
                submit_captcha(driver)
                step = wait_for_step(driver, timeout=30)
            if step == "otp":
                raise RuntimeError("eproc rejected the OTP")
            if step == "login":
                raise RuntimeError("eproc returned to login after OTP submission")

        if step != "panel":
            raise RuntimeError(f"Unexpected eproc login step: {step}")

        phpsessid = next(
            (cookie["value"] for cookie in driver.get_cookies() if cookie.get("name") == "PHPSESSID"),
            "",
        )
        if not phpsessid:
            raise RuntimeError("Authenticated panel loaded without a PHPSESSID cookie")
        return phpsessid, driver.page_source, driver.get_current_url()
    finally:
        driver.quit()


def main():
    parser = argparse.ArgumentParser(description="Validate JFSC eproc login using accounts from a QR image")
    parser.add_argument("qr_image", type=Path)
    parser.add_argument("--accounts", type=int, nargs="+", default=[1, 3])
    args = parser.parse_args()

    load_env()
    username = required_env("EPROC_USERNAME")
    password = required_env("EPROC_PASSWORD")

    from scripts.pje_2fa_from_qr import accounts_from_image

    accounts = accounts_from_image(args.qr_image)
    failures = []
    for number in args.accounts:
        if not 1 <= number <= len(accounts):
            parser.error(f"account {number} is outside 1..{len(accounts)}")
        try:
            phpsessid, html, url = login(username, password, env_account(accounts[number - 1], number))
            print(json.dumps({
                "authenticated": True,
                "otpAccount": number,
                "issuer": accounts[number - 1]["issuer"],
                "panelUrl": url.split("&hash=", 1)[0],
                "phpsessidFound": bool(phpsessid),
                "panelHtmlLength": len(html),
            }, ensure_ascii=False, indent=2))
            return
        except RuntimeError as error:
            failures.append(f"account {number}: {error}")
    parser.error("; ".join(failures))


if __name__ == "__main__":
    main()
