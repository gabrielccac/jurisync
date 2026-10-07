"""GERID login through the user's Chrome profile (A3 certificate + TOTP).

Chrome is launched with a CDP port and driven by Playwright. Headless mode
reuses an unexpired browser credential; otherwise Chrome reopens headed so
the user can pick the A3 certificate and enter its PIN.
"""

import asyncio
import base64
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from playwright.async_api import async_playwright

from jurisync.core.totp import accounts_from_image, parse_accounts, select_account, totp

LOGIN_URL = "https://atendimento.inss.gov.br/auth/login?loginwith=gerid"
DEFAULT_CDP_PORT = 9338
AUTH_KEYS = ("access_token", "gerid_token", "ifs_auth", "ifs_at", "ifs_ts", "ifs_iat")
GERID_CERTIFICATE_POLICY = json.dumps(
    {"pattern": "https://geridinss.dataprev.gov.br:8443", "filter": {}}, separators=(",", ":")
)


def default_profile():
    return Path.cwd() / "data" / "inss-browser-profile"


def gerid_code(secret):
    account = select_account(parse_accounts(secret), label="INSS_GERID_TOTP_SECRET")
    overrides = {
        "digits": "INSS_GERID_TOTP_DIGITS",
        "algorithm": "INSS_GERID_TOTP_ALGORITHM",
        "period": "INSS_GERID_TOTP_PERIOD",
    }
    for key, name in overrides.items():
        if os.environ.get(name):
            account = {**account, key: os.environ[name].lower() if key == "algorithm" else int(os.environ[name])}
    return totp(account).now()


def qr_secret(image, account=1):
    """Secret of the 1-based account in an authenticator QR image."""
    accounts = accounts_from_image(image)
    return select_account(accounts, index=int(account) - 1, label="QR")["secret"]


def safe_url(value):
    try:
        parts = urlsplit(value)
        query = [
            (key, item)
            for key, items in parse_qs(parts.query, keep_blank_values=True).items()
            for item in items
            if key not in ("ticket", "access_token", "refresh_token")
        ]
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))
    except ValueError:
        return "[unavailable]"


def jwt_claims(token):
    segment = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))


def is_reusable_auth(auth, now_ms=None):
    if not auth:
        return False
    now_ms = time.time() * 1000 if now_ms is None else now_ms
    if len(auth.split(".")) != 3:
        return True
    try:
        exp = jwt_claims(auth).get("exp")
    except (ValueError, IndexError):
        return False
    return not isinstance(exp, (int, float)) or exp * 1000 > now_ms + 30_000


def chrome_executable():
    candidates = [os.environ.get("INSS_CHROME_PATH")]
    if sys.platform == "win32":
        candidates += [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
    elif sys.platform == "darwin":
        candidates.append("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    else:
        candidates += ["/usr/bin/google-chrome", "/usr/bin/chromium"]
    executable = next((candidate for candidate in candidates if candidate and Path(candidate).exists()), None)
    if not executable:
        raise RuntimeError("Google Chrome executable not found; set INSS_CHROME_PATH")
    return executable


def configure_mac_certificate_auto_select(log):
    if sys.platform != "darwin":
        return
    domain, key = "com.google.Chrome", "AutoSelectCertificateForUrls"
    current = subprocess.run(["/usr/bin/defaults", "read", domain, key], capture_output=True, text=True)
    if current.returncode == 0 and "geridinss.dataprev.gov.br" in current.stdout:
        return
    operation = "-array-add" if current.returncode == 0 else "-array"
    subprocess.run(
        ["/usr/bin/defaults", "write", domain, key, operation, "-string", GERID_CERTIFICATE_POLICY],
        check=True,
        capture_output=True,
    )
    log("Configured Chrome to auto-select the client certificate for GERID.")


class Browser:
    """A Chrome process plus the Playwright connection driving it."""

    def __init__(self, process, playwright, browser, context, page):
        self.process, self.playwright, self.browser, self.context, self.page = (
            process,
            playwright,
            browser,
            context,
            page,
        )

    async def close(self):
        for close in (self.browser.close, self.playwright.stop):
            try:
                await close()
            except Exception:
                pass  # Closing must continue to the Chrome process below.
        if self.process.poll() is None:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/PID", str(self.process.pid), "/T", "/F"], capture_output=True)
            else:
                self.process.terminate()
                try:
                    await asyncio.to_thread(self.process.wait, 5)
                except subprocess.TimeoutExpired:
                    self.process.kill()


def _cdp_ready(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as response:
            return response.status == 200
    except OSError:
        return False


async def launch_chrome(profile, profile_directory, timeout_ms, headless, log):
    try:
        configure_mac_certificate_auto_select(log)
    except (OSError, subprocess.CalledProcessError) as error:
        log(f"Could not configure Chrome certificate auto-selection: {error}")
    port = int(os.environ.get("INSS_CDP_PORT") or DEFAULT_CDP_PORT)
    args = [
        chrome_executable(),
        f"--remote-debugging-port={port}",
        f"--user-data-dir={Path(profile).resolve()}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-component-update",
        "--disable-background-networking",
        "--disable-sync",
        "--disable-translate",
        "--disable-default-apps",
    ]
    if headless:
        args.append("--headless=new")
    if profile_directory:
        args.append(f"--profile-directory={profile_directory}")
    args.append("about:blank")
    process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    deadline = time.monotonic() + timeout_ms / 1000
    while not await asyncio.to_thread(_cdp_ready, port):
        if process.poll() is not None:
            details = process.stderr.read().decode(errors="replace").strip()[-4000:]
            raise RuntimeError(
                "Chrome exited before its CDP endpoint became available" + (f": {details}" if details else "")
            )
        if time.monotonic() >= deadline:
            process.kill()
            raise RuntimeError(f"Chrome CDP endpoint did not start on port {port}")
        await asyncio.sleep(0.25)
    playwright = await async_playwright().start()
    try:
        browser = await playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
    except Exception:
        await playwright.stop()
        process.kill()
        raise
    context = browser.contexts[0]
    page = context.pages[0] if context.pages else await context.new_page()
    return Browser(process, playwright, browser, context, page)


async def _first_option(page, selector):
    option = page.locator(selector + ' option:not([value=""])').first
    await option.wait_for(state="attached")
    value = await option.get_attribute("value")
    if not value:
        raise RuntimeError(f"No selectable option found for {selector}")
    await page.locator(selector).select_option(value)
    return value


async def _first_step(page, timeout_ms):
    """Wait for whichever login state the portal shows first."""
    waits = {
        "certificate": page.locator("#botaoCertificadoDigital").wait_for(state="visible", timeout=timeout_ms),
        "token": page.locator("#token").wait_for(state="visible", timeout=timeout_ms),
        "url": page.wait_for_url(
            lambda url: urlsplit(url).hostname == "atendimento.inss.gov.br" and urlsplit(url).path != "/auth/login",
            timeout=timeout_ms,
        ),
        "storage": page.wait_for_function(
            "() => Boolean(localStorage.getItem('ifs_auth') || localStorage.getItem('gerid_token'))",
            polling=250,
            timeout=timeout_ms,
        ),
    }
    tasks = {asyncio.ensure_future(wait): name for name, wait in waits.items()}
    try:
        while tasks:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                name = tasks.pop(task)
                if task.exception() is None:
                    return "authenticated" if name in ("url", "storage") else name
        raise RuntimeError("GERID login page did not reach a known state")
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def login_and_authorize(
    *, profile, profile_directory=None, secret=None, otp=None, timeout_ms=120_000, headless=False, log=print
):
    browser = await launch_chrome(profile, profile_directory, timeout_ms, headless, log)
    page = browser.page
    page.set_default_timeout(timeout_ms)
    try:
        await page.goto(LOGIN_URL, wait_until="commit", timeout=timeout_ms)
        if headless:
            try:
                handle = await page.wait_for_function(
                    "() => localStorage.getItem('ifs_auth') || localStorage.getItem('gerid_token')",
                    polling=250,
                    timeout=min(timeout_ms, 5000),
                )
                auth = await handle.json_value()
            except Exception:
                auth = None
            if not is_reusable_auth(auth):
                log("No reusable browser session; reopening Chrome headed for A3 login.")
                await browser.close()
                return await login_and_authorize(
                    profile=profile,
                    profile_directory=profile_directory,
                    secret=secret,
                    otp=otp,
                    timeout_ms=timeout_ms,
                    headless=False,
                    log=log,
                )
            step = "authenticated"
        else:
            step = await _first_step(page, timeout_ms)

        fresh_login = step != "authenticated"
        if fresh_login:
            if step == "certificate":
                log("Select the A3 certificate in the native browser/OS prompt if requested.")
                await page.locator("#botaoCertificadoDigital").click()
            await page.locator("#token").wait_for(state="visible", timeout=timeout_ms)
            if not otp and not secret:
                raise RuntimeError("Set INSS_GERID_TOTP_SECRET, INSS_GERID_OTP, or INSS_GERID_QR_IMAGE")
            await page.locator("#token").fill(otp or gerid_code(secret))
            await page.click('input[name="_eventId_submit"]')
            await page.wait_for_url(lambda url: urlsplit(url).hostname == "atendimento.inss.gov.br", timeout=timeout_ms)
        else:
            log("Reusing authenticated browser session.")

        scope = role = None
        scope_select = page.locator("#dtpSelectAbrangencia")
        try:
            await scope_select.wait_for(state="visible", timeout=timeout_ms if fresh_login else min(timeout_ms, 5000))
        except Exception:
            if fresh_login:
                raise
        if await scope_select.is_visible():
            scope = await _first_option(page, "#dtpSelectAbrangencia")
            log(f"Selected scope: {scope}")
            role = await _first_option(page, "#dtpSelectPapel")
            log(f"Selected role: {role}")
            await page.wait_for_function("() => !document.querySelector('#btnAutorizo')?.disabled")
            await page.click("#btnAutorizo")
            notice = page.locator('#btnAutorizo[title="OK"]')
            try:
                await notice.wait_for(state="visible", timeout=min(timeout_ms, 10_000))
            except Exception:
                pass
            if await notice.is_visible():
                await notice.click()
                await notice.wait_for(state="hidden", timeout=timeout_ms)
        await page.wait_for_timeout(1500)
        storage = await page.evaluate(
            """(authKeys) => ({
            keys: Object.keys(localStorage).sort(),
            apiBase: localStorage.getItem("base_url"),
            credentials: Object.fromEntries(authKeys.map((key) => [key, localStorage.getItem(key)])
                .filter(([, value]) => value !== null)),
        })""",
            list(AUTH_KEYS),
        )
        browser.credentials = storage["credentials"]
        browser.summary = {
            "authenticated": "ifs_auth" in storage["keys"] or "gerid_token" in storage["keys"],
            "url": safe_url(page.url),
            "scopeSelected": bool(scope),
            "roleSelected": bool(role),
            "apiBase": storage["apiBase"],
            "authKeysPresent": [key for key in storage["keys"] if key in AUTH_KEYS],
        }
        return browser
    except BaseException:
        await browser.close()
        raise
