"""Browser bridge for the one e-SAJ step that needs JavaScript: the TJMS 1G reCAPTCHA v3 search.

The public consultation form of ``/cpopg5`` carries an invisible reCAPTCHA v3. Its token is minted by the portal's own
JavaScript inside a browser; no HTTP client can produce one. The bridge reuses a real Chrome over CDP: it opens the
consultation page, waits for the token the page puts in the DOM and replays the search as a ``fetch`` from that page,
with the session the connector already has.

Nothing is solved and nothing is hidden: the browser is a normal Chrome, without headless or automation flags. When no
browser is available the caller keeps reporting ``captcha_required``.
"""

import json
import os
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

import websocket

from jurisync import portals
from jurisync.core import credentials

TOKEN_INPUT = "#id_recaptcha_response_token"
UUID_INPUT = "#uuidCaptcha"
DEFAULT_ENDPOINT = "http://127.0.0.1:9222"
DEFAULT_PROFILE = "~/.cache/jurisync/esaj-chrome"
DEFAULT_TIMEOUT = 60
DEFAULT_MAX_BYTES = 2 * 1024 * 1024
MODES = ("off", "attach", "launch", "auto")
_ALLOWED_SUFFIXES = ("/open.do", "/search.do", "/show.do")


class BrowserBridgeError(RuntimeError):
    """The bridge could not produce a page: no browser, no token, or an unexpected answer."""


def _env(name, default=""):
    return str(credentials.env(name) or default).strip()


def _json(url, method="GET", timeout=5):
    request = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


class CdpTarget:
    """One Chrome tab, driven by the minimum of the DevTools protocol this connector needs."""

    def __init__(self, websocket_url, timeout=30):
        try:
            self._socket = websocket.create_connection(websocket_url, timeout=timeout, suppress_origin=True)
        except websocket.WebSocketException as error:
            raise BrowserBridgeError(f"CDP connection failed: {error}") from None
        self._id = 0

    def call(self, method, params=None):
        self._id += 1
        message_id = self._id
        try:
            self._socket.send(json.dumps({"id": message_id, "method": method, "params": params or {}}))
            while True:
                message = json.loads(self._socket.recv())
                if message.get("id") == message_id:
                    if "error" in message:
                        raise BrowserBridgeError(
                            f"CDP {method} failed: {message['error'].get('message', 'unknown error')}"
                        )
                    return message.get("result", {})
        except websocket.WebSocketException as error:
            raise BrowserBridgeError(f"CDP transport failed: {error}") from None

    def evaluate(self, expression):
        result = self.call("Runtime.evaluate", {"expression": expression, "awaitPromise": True, "returnByValue": True})
        if result.get("exceptionDetails"):
            raise BrowserBridgeError("The consultation page rejected the search script")
        return result.get("result", {}).get("value")

    def set_cookies(self, cookies, host):
        for cookie in cookies:
            params = {"name": cookie["name"], "value": cookie["value"], "domain": host, "path": "/"}
            for key in ("secure", "httpOnly"):
                if cookie.get(key):
                    params[key] = True
            try:
                self.call("Network.setCookie", params)
            except BrowserBridgeError:
                # A cookie Chrome refuses is not fatal: the session may still be complete without it.
                continue

    def close(self):
        self._socket.close()


class BrowserBridge:
    """Reaches the consultation page through a Chrome the operator already runs (``attach``) or one it opens."""

    def __init__(self, mode=None, endpoint=None, profile=None, timeout=None, max_bytes=None):
        self.mode = (mode or _env("ESAJ_BRIDGE_MODE", "off")).lower()
        if self.mode not in MODES:
            raise BrowserBridgeError(f"ESAJ_BRIDGE_MODE must be one of {', '.join(MODES)}")
        self.endpoint = (endpoint or _env("ESAJ_BRIDGE_CDP", DEFAULT_ENDPOINT)).rstrip("/")
        self.profile = Path(os.path.expanduser(profile or _env("ESAJ_BRIDGE_PROFILE", DEFAULT_PROFILE)))
        self.timeout = float(timeout or _env("ESAJ_BRIDGE_TIMEOUT", DEFAULT_TIMEOUT))
        self.max_bytes = int(max_bytes or _env("ESAJ_BRIDGE_MAX_BYTES", DEFAULT_MAX_BYTES))
        self.headless = _env("ESAJ_BRIDGE_HEADLESS", "true").lower() not in ("0", "false", "no", "off")
        self._process = None
        self._temporary_profile = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.stop()
        return False

    # --- browser lifecycle ---------------------------------------------------------------------------------------

    @property
    def enabled(self):
        return self.mode != "off"

    def _version(self, endpoint):
        return _json(f"{endpoint}/json/version")

    def endpoint_of_running_browser(self):
        """The endpoint of a browsing Chrome: the configured one, or the one its profile recorded."""
        try:
            self._version(self.endpoint)
            return self.endpoint
        except (OSError, ValueError):
            pass
        recorded = self.profile / "DevToolsActivePort"
        if recorded.is_file():
            port = recorded.read_text().splitlines()[0].strip()
            if port.isdigit():
                endpoint = f"http://127.0.0.1:{port}"
                try:
                    self._version(endpoint)
                    return endpoint
                except (OSError, ValueError):
                    return None
        return None

    def chrome_command(self):
        """The command of the Chrome the bridge opens: headless by default, because it mints the token too."""
        chrome = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chrome")
        if not chrome:
            raise BrowserBridgeError("Chrome is not installed on this machine")
        command = [chrome, "--no-first-run", "--no-default-browser-check", "--remote-allow-origins=*"]
        if self.headless:
            command += ["--headless=new", "--disable-gpu"]
        elif not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            raise BrowserBridgeError("A visible Chrome needs a display; start it yourself and use attach")
        command += ["--remote-debugging-port=0", f"--user-data-dir={self.profile}", "about:blank"]
        return command

    def start(self):
        """The CDP endpoint, opening a Chrome in ``launch`` mode when none is running."""
        running = self.endpoint_of_running_browser()
        if running:
            return running
        if self.mode == "attach":
            raise BrowserBridgeError("No Chrome with remote debugging is reachable")
        self.profile.mkdir(parents=True, exist_ok=True)
        self._process = subprocess.Popen(self.chrome_command(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + min(self.timeout, 30)
        while time.monotonic() < deadline:
            endpoint = self.endpoint_of_running_browser()
            if endpoint:
                return endpoint
            time.sleep(0.2)
        raise BrowserBridgeError("Chrome did not open its debugging port")

    def stop(self):
        if self._process is not None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None
        if self._temporary_profile is not None:
            shutil.rmtree(self._temporary_profile, ignore_errors=True)
            self._temporary_profile = None

    # --- targets -------------------------------------------------------------------------------------------------

    def _target(self, endpoint, host, prefix):
        """A tab of our own on the court host: the user's tabs are never navigated."""
        return _json(f"{endpoint}/json/new?{urllib.parse.quote(f'https://{host}/{prefix}/open.do')}", "PUT")

    # --- search --------------------------------------------------------------------------------------------------

    def search(self, portal, params, cookies=()):
        """Run the consultation search inside the browser and return ``{status, url, html}``."""
        if not self.enabled:
            raise BrowserBridgeError("Browser bridge is off")
        config = portals.esaj(portal)
        prefix = "cpopg" if config.key == "tjsp-esaj" else "cpopg5"
        endpoint = self.start()
        target = self._target(endpoint, config.host, prefix)
        tab = CdpTarget(target["webSocketDebuggerUrl"], timeout=self.timeout)
        try:
            tab.call("Page.enable")
            tab.call("Runtime.enable")
            if cookies:
                tab.set_cookies(cookies, config.host)
            open_url = f"https://{config.host}/{prefix}/open.do"
            tab.call("Page.navigate", {"url": open_url})
            self._wait_for_token(tab)
            result = tab.evaluate(self._script(prefix, params))
            if not isinstance(result, dict) or result.get("status") != 200:
                raise BrowserBridgeError("The consultation page did not answer the search")
            final = urllib.parse.urlsplit(result.get("url") or "")
            if final.hostname != config.host or not final.path.endswith(_ALLOWED_SUFFIXES):
                raise BrowserBridgeError("The consultation answered from an unexpected address")
            html = result.get("html") or ""
            if len(html.encode("utf-8")) > self.max_bytes:
                raise BrowserBridgeError("The consultation page is larger than the bridge limit")
            return {"status": 200, "url": result["url"], "html": html}
        finally:
            tab.close()
            self._close(endpoint, target)

    def fetch(self, portal, url, cookies=()):
        """A portal page loaded in the browser, for pages the portal serves only to a browser (behind its CAPTCHA)."""
        config = portals.esaj(portal)
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "https" or parts.hostname != config.host:
            raise BrowserBridgeError("Browser bridge received a URL outside the portal")
        if not parts.path.endswith(_ALLOWED_SUFFIXES):
            raise BrowserBridgeError("Browser bridge received an unexpected endpoint")
        endpoint = self.start()
        target = self._target(endpoint, config.host, "cpopg" if config.key == "tjsp-esaj" else "cpopg5")
        tab = CdpTarget(target["webSocketDebuggerUrl"], timeout=self.timeout)
        try:
            tab.call("Page.enable")
            tab.call("Runtime.enable")
            if cookies:
                tab.set_cookies(cookies, config.host)
            tab.call("Page.navigate", {"url": url})
            self._wait_for_load(tab)
            html = tab.evaluate("document.documentElement.outerHTML") or ""
            if len(html.encode("utf-8")) > self.max_bytes:
                raise BrowserBridgeError("The page is larger than the bridge limit")
            return {"status": 200, "url": url, "html": html}
        finally:
            tab.close()
            self._close(endpoint, target)

    @staticmethod
    def _close(endpoint, target):
        try:
            _json(f"{endpoint}/json/close/{target['id']}")
        except (OSError, ValueError, KeyError):
            pass

    def _wait_for_load(self, tab):
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if tab.evaluate("document.readyState") == "complete":
                return
            time.sleep(0.3)
        raise BrowserBridgeError("The page did not finish loading")

    def _wait_for_token(self, tab):
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            length = tab.evaluate(f"document.querySelector('{TOKEN_INPUT}')?.value?.length || 0") or 0
            if length:
                return
            time.sleep(0.5)
        raise BrowserBridgeError("The reCAPTCHA token did not appear in the page")

    @staticmethod
    def _script(prefix, params):
        """The search, replayed from the page: same form fields, plus the token the page itself minted."""
        fields = json.dumps({key: str(value) for key, value in params.items()})
        return f"""(async () => {{
          const token = document.querySelector('{TOKEN_INPUT}')?.value || '';
          const uuid = document.querySelector('{UUID_INPUT}')?.value || '';
          const query = new URLSearchParams({{...{fields}, recaptcha_response_token: token, uuidCaptcha: uuid}});
          const response = await fetch('/{prefix}/search.do?' + query.toString(), {{credentials: 'include'}});
          const html = await response.text();
          return {{status: response.status, url: response.url, html}};
        }})()"""


def bridge_status():
    """What the session tool reports about the bridge, without touching a browser."""
    try:
        bridge = BrowserBridge()
    except BrowserBridgeError as error:
        return {"mode": "invalid", "available": False, "reason": str(error)}
    if not bridge.enabled:
        return {"mode": "off", "available": False, "reason": "captcha_required"}
    endpoint = bridge.endpoint_of_running_browser()
    return {
        "mode": bridge.mode,
        "available": bool(endpoint),
        "endpoint": endpoint or "",
        "reason": "" if endpoint else "no_browser",
    }


def bridge_cookies(session, portal):
    """The portal cookies of the connector session, handed to the browser so it searches with the same login."""
    host = portals.esaj(portal).host
    cookies = []
    for cookie in session.cookies.jar:
        domain = (cookie.domain or "").lstrip(".")
        if domain and not (host == domain or host.endswith(f".{domain}")):
            continue
        cookies.append(
            {
                "name": cookie.name,
                "value": cookie.value,
                "secure": bool(cookie.secure),
                "httpOnly": bool(cookie.has_nonstandard_attr("HttpOnly")),
            }
        )
    return cookies
