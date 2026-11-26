"""ADVBOX cookie session over plain HTTP (no browser).

The session is cached in a private file and reused until it expires. Reads
retry once on 429; writes are never replayed. All calls on one server share
one session and are serialized, matching ADVBOX's per-account rate limits.

When ADVBOX does not trust the device it sends a code to the account holder and the login stops on /user/2fa. That
attempt is kept per office and account until the code comes back (``verify_device``) or it expires; meanwhile no new
login is tried, so ADVBOX does not send a code on every call.
"""

import hashlib
import json
import os
import re
import secrets
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit

from curl_cffi import CurlMime, requests

from jurisync.connectors.advbox.parsers import (
    BASE_URL,
    parse_csrf_token,
    parse_hidden_inputs,
    parse_verification_form,
    parse_verification_recipient,
)
from jurisync.core import credentials
from jurisync.core.session_pool import LoginRejected, MissingLogin
from jurisync.paths import STATE_DIR


class SessionExpiredError(RuntimeError):
    pass


class CsrfStaleError(RuntimeError):
    pass


class AdvboxDeviceVerification(RuntimeError):
    """ADVBOX sent a code to verify this device; the login waits for it."""

    def __init__(self, sent_to):
        self.sent_to = sent_to
        super().__init__(
            f"ADVBOX asked to verify this device and sent a code to {sent_to}. "
            "Ask the client for the code and call advbox_verify_device with it."
        )


class AdvboxDeviceMissing(MissingLogin):
    """The account has no device id yet: nothing was sent to ADVBOX."""

    def __init__(self):
        super().__init__(
            "This ADVBOX account has no verified device yet. Tell the user ADVBOX will send a code to the account's "
            "e-mail; once they agree and can read it, call advbox_verify_device with newDevice=true, then pass the code."
        )


class CodeRefused(RuntimeError):
    def __init__(self, left):
        self.left = left
        super().__init__(f"ADVBOX refused the code; check it with the client ({left} attempt(s) left)")


VERIFICATION_SECONDS = 15 * 60
VERIFICATION_TRIES = 3


@dataclass
class _Verification:
    jar: dict
    form: dict | None
    device: str
    sent_to: str
    started: float
    problem: str = ""
    tries: int = 0


_pending = {}
_pending_lock = threading.Lock()


def _account_key():
    return credentials.tenant(), (credentials.env("ADVBOX_EMAIL") or "").strip().lower()


def _drop_expired():
    """Forget every expired verification, of any office (call with ``_pending_lock`` held): a long-running server
    never keeps the ones nobody came back for."""
    now = time.time()
    for key in [key for key, pending in _pending.items() if now - pending.started > VERIFICATION_SECONDS]:
        del _pending[key]


def pending_verification():
    """The device verification waiting for a code for this office and account, or None."""
    key = _account_key()
    with _pending_lock:
        _drop_expired()
        return _pending.get(key)


def _drop_verification():
    with _pending_lock:
        _pending.pop(_account_key(), None)


def require_writes():
    if credentials.env("ADVBOX_ALLOW_WRITES") != "true":
        raise PermissionError(
            "ADVBOX writes are disabled. Set ADVBOX_ALLOW_WRITES=true after confirming the operation."
        )


def _required(name):
    value = (credentials.env(name) or "").strip()
    if not value:
        raise MissingLogin(f"Set {name} in .env")
    return value


def _now_ms():
    return time.time() * 1000


def apply_set_cookies(jar, headers, now_ms=None):
    now_ms = _now_ms() if now_ms is None else now_ms
    for raw in headers:
        pair, *attributes = raw.split(";")
        index = pair.find("=")
        if index < 1:
            continue
        name, value = pair[:index].strip(), pair[index + 1 :].strip()
        if value in ("", "deleted") or re.match(r"expires", value, re.I):
            jar.pop(name, None)
            continue
        entry = {"value": value}
        for attribute in attributes:
            key, _, rest = attribute.strip().partition("=")
            if key.lower() == "expires" and rest:
                try:
                    entry["expiresAt"] = parsedate_to_datetime(rest).timestamp() * 1000
                except (TypeError, ValueError):
                    pass
        if entry.get("expiresAt") is not None and entry["expiresAt"] <= now_ms:
            jar.pop(name, None)
            continue
        jar[name] = entry
    return jar


def cookie_header(jar):
    return "; ".join(f"{name}={entry['value']}" for name, entry in jar.items())


def is_expired_session(cache, now_ms=None):
    now_ms = _now_ms() if now_ms is None else now_ms
    if not (cache or {}).get("cookies"):
        return True
    return cache.get("expiresAt") is not None and cache["expiresAt"] <= now_ms + 30_000


def session_file():
    account = (credentials.env("ADVBOX_EMAIL") or "").strip().lower()
    name = f"advbox-{hashlib.sha256(account.encode()).hexdigest()[:16]}.json" if account else "advbox-session.json"
    tenant = credentials.tenant()
    if tenant:
        # Hosted: each office's cookie lives in its own folder; the server-wide override does not apply.
        return STATE_DIR / "data" / "tenants" / tenant / name
    return Path(os.environ.get("ADVBOX_SESSION_FILE") or STATE_DIR / "data" / name)


def _ok(response):
    return 200 <= response.status_code < 300


def _path(request_path):
    return request_path if request_path.startswith("/") else f"/{request_path}"


def _label(method, request_path):
    return f"ADVBOX {method} {request_path.split('?')[0]}"


class AdvboxSession:
    def __init__(self):
        self.jar = None
        self.csrf = None
        self.cooldown_until = 0.0
        self.lock = threading.RLock()
        # Waiting on this condition releases every level of the re-entrant lock, which a plain sleep would keep.
        self._cooldown = threading.Condition(self.lock)
        self._exclusive = 0
        self.http = requests.Session()

    @property
    def timeout(self):
        return float(os.environ.get("ADVBOX_TIMEOUT_SECONDS") or 60)

    def close(self):
        self.http.close()

    def _send(self, method, url, *, headers=None, use_cookies=True, **kwargs):
        headers = dict(headers or {})
        if use_cookies:
            headers["Cookie"] = cookie_header(self.jar)
        # discard_cookies: the explicit jar stays the only cookie source.
        return self.http.request(
            method, url, headers=headers, allow_redirects=False, timeout=self.timeout, discard_cookies=True, **kwargs
        )

    # -- login ---------------------------------------------------------------

    def _send_following(self, url, **kwargs):
        method = kwargs.pop("method", "GET")
        current = url
        for _ in range(6):
            response = self._send(method, current, **kwargs)
            apply_set_cookies(self.jar, response.headers.get_list("set-cookie"))
            location = response.headers.get("location")
            if 300 <= response.status_code < 400 and location:
                current = urljoin(current, location)
                method, kwargs = "GET", {"headers": kwargs.get("headers")}
                continue
            break
        return response, current

    def _login(self, device=None):
        email, password = _required("ADVBOX_EMAIL"), _required("ADVBOX_PASSWORD")
        pending = pending_verification()
        if pending:
            self.jar = None
            if pending.problem:
                raise RuntimeError(pending.problem)
            raise AdvboxDeviceVerification(pending.sent_to)
        device = device or (credentials.env("ADVBOX_DEVICE_ID") or "").strip()
        if not device:
            raise AdvboxDeviceMissing()
        self.jar = {}
        page, _ = self._send_following(f"{BASE_URL}/login")
        if not _ok(page):
            raise RuntimeError(f"ADVBOX login page returned HTTP {page.status_code}")
        body = urlencode({**parse_hidden_inputs(page.text), "email": email, "password": password, "_device": device})
        response, final_url = self._send_following(
            f"{BASE_URL}/login", method="POST", data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        pathname = urlsplit(final_url).path
        if pathname.startswith("/user/2fa"):
            self._hold_verification(response.text, final_url, device, email)
        if pathname.startswith("/login"):
            raise LoginRejected("ADVBOX login failed; verify ADVBOX_EMAIL and ADVBOX_PASSWORD")
        if "advbox_session" not in self.jar:
            raise RuntimeError(f"ADVBOX login did not return a session cookie (landed on {pathname})")
        self._save()

    def _hold_verification(self, html, page_url, device, email):
        sent_to = parse_verification_recipient(html) or email
        form, problem = None, ""
        try:
            form = parse_verification_form(html, page_url)
        except RuntimeError as error:
            # Kept anyway: logging in again would only make ADVBOX send another code.
            problem = str(error)
        with _pending_lock:
            _drop_expired()
            _pending[_account_key()] = _Verification(dict(self.jar), form, device, sent_to, time.time(), problem)
        self.jar = None
        if problem:
            raise RuntimeError(problem)
        raise AdvboxDeviceVerification(sent_to)

    def verify_device(self, code):
        """Send the code ADVBOX sent for the pending verification; on success keep the session and the device id.

        A refused code raises ``CodeRefused`` and the attempt stays for another try, up to ``VERIFICATION_TRIES``.
        """
        with self.lock:
            pending = pending_verification()
            if pending is None:
                raise RuntimeError("No ADVBOX device verification is pending for this account")
            if pending.problem:
                raise RuntimeError(pending.problem)
            code = re.sub(r"\s+", "", code or "")
            if not code:
                raise ValueError("Pass the code ADVBOX sent")
            form = pending.form
            fields = {**form["fields"], form["codeField"]: code}
            if "_device" in fields and not fields["_device"]:
                # The page leaves _device empty for its FingerprintJS to fill: without it ADVBOX refuses a right code.
                fields["_device"] = pending.device
            fields = urlencode(fields)
            self.jar, self.csrf = dict(pending.jar), None
            if form["method"] == "GET":
                response, final_url = self._send_following(f"{form['action'].split('?')[0]}?{fields}")
            else:
                response, final_url = self._send_following(
                    form["action"],
                    method=form["method"],
                    data=fields,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
            pathname = urlsplit(final_url).path
            if not pathname.startswith(("/login", "/user/2fa")) and "advbox_session" in self.jar:
                _drop_verification()
                self._save()
                changed = pending.device != (credentials.env("ADVBOX_DEVICE_ID") or "").strip()
                if changed:
                    try:
                        credentials.save("ADVBOX_DEVICE_ID", pending.device)
                    except (RuntimeError, OSError) as error:
                        raise RuntimeError(
                            f"ADVBOX verified this device, but its id could not be stored ({error}); "
                            "the session works until it expires"
                        ) from error
                return {"verified": True, "deviceSaved": changed}
            jar, self.jar = self.jar, None
            pending.tries += 1
            if pathname.startswith("/login") or pending.tries >= VERIFICATION_TRIES:
                _drop_verification()
                raise RuntimeError(
                    "ADVBOX refused the code and ended the verification; try again later to receive a new code"
                )
            pending.jar = jar
            if pathname.startswith("/user/2fa"):
                try:
                    pending.form = parse_verification_form(response.text, final_url)
                except RuntimeError:
                    pass
            raise CodeRefused(VERIFICATION_TRIES - pending.tries)

    def start_new_device(self):
        """Log in with a fresh device id, so ADVBOX sends a code to verify it. A login it accepts at once keeps it."""
        with self.lock:
            device = secrets.token_hex(16)
            self.jar, self.csrf = None, None
            self._login(device=device)
            credentials.save("ADVBOX_DEVICE_ID", device)
            return {"verified": True, "deviceSaved": True}

    def _save(self):
        expiries = [entry["expiresAt"] for entry in self.jar.values() if entry.get("expiresAt") is not None]
        cache = {
            "cookies": [[name, entry["value"]] for name, entry in self.jar.items()],
            "savedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            **({"expiresAt": min(expiries)} if expiries else {}),
        }
        path = session_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as output:
            output.write(json.dumps(cache, indent=2) + "\n")

    def _obtain(self, force_login=False):
        if not force_login and self.jar is not None:
            return
        if not force_login:
            try:
                cache = json.loads(session_file().read_text())
            except (OSError, ValueError):
                cache = None
            if cache and not is_expired_session(cache):
                self.jar = {name: {"value": value} for name, value in cache["cookies"]}
                return
        self._login()

    def using(self, handler):
        """Run handler(self) with a session; on expiry, log in once and retry."""
        with self.lock:
            self._obtain()
            try:
                return handler(self)
            except SessionExpiredError:
                self.jar, self.csrf = None, None
                self._obtain(force_login=True)
                return handler(self)

    @contextmanager
    def exclusive(self):
        """Hold the office lock across several requests that must not interleave with another call of this office:
        a composite write (check, stage, post). Re-entrant, like ``using``. A 429 cool-down inside it keeps the lock,
        because letting another call in would undo the point of holding it."""
        with self.lock:
            self._exclusive += 1
            try:
                yield self
            finally:
                self._exclusive -= 1

    # -- requests ------------------------------------------------------------

    def _cool_down(self, seconds):
        """Wait out a 429 cool-down without keeping the office lock, so the wait of one call does not queue the
        others behind it (each of them waits for the same cool-down on its own)."""
        if self._exclusive:
            time.sleep(seconds)
            return
        try:
            self._cooldown.wait(seconds)
        except RuntimeError:  # called without the lock: there is nothing to release
            time.sleep(seconds)

    def _read(self, method, url, label, **kwargs):
        """Retry one read after a 429; replaying a write could duplicate it."""
        for attempt in range(2):
            remaining = self.cooldown_until - time.time()
            if remaining > 0:
                self._cool_down(remaining)
            response = self._send(method, url, **kwargs)
            if response.status_code != 429:
                return response
            retry_after = response.headers.get("retry-after")
            delay = 30.0
            if retry_after:
                try:
                    delay = max(0.0, float(retry_after))
                except ValueError:
                    try:
                        delay = max(0.0, parsedate_to_datetime(retry_after).timestamp() - time.time())
                    except (TypeError, ValueError):
                        pass
            body = re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", response.text))[:300]
            detail = f"retry in {-(-delay // 1):.0f}s (Retry-After: {retry_after or 'absent'})" + (
                f"; response: {body}" if body else ""
            )
            if delay <= 45:
                self.cooldown_until = max(self.cooldown_until, time.time() + delay)
            if attempt or delay > 45:
                raise RuntimeError(f"{label} failed (429); {detail}")
        raise AssertionError("unreachable")

    @staticmethod
    def _check(response, label, *, csrf=False, json_expected=False, detail_chars=0):
        if csrf and response.status_code == 419:
            raise CsrfStaleError("ADVBOX rejected the CSRF token")
        if response.status_code == 401 or 300 <= response.status_code < 400:
            raise SessionExpiredError(f"ADVBOX session expired while requesting {label.split(' ', 2)[-1]}")
        if not _ok(response):
            detail = f": {response.text[:detail_chars]}" if detail_chars else ""
            raise RuntimeError(f"{label} failed ({response.status_code}){detail}")
        if json_expected and "json" not in response.headers.get("content-type", ""):
            raise SessionExpiredError(f"ADVBOX session expired while requesting {label.split(' ', 2)[-1]}")

    def get_json(self, request_path):
        if not request_path.startswith("/content/"):
            raise ValueError("ADVBOX request path must start with /content/")
        label = _label("GET", request_path)
        response = self._read(
            "GET",
            f"{BASE_URL}{request_path}",
            label,
            headers={"Accept": "application/json, text/javascript, */*; q=0.01", "X-Requested-With": "XMLHttpRequest"},
        )
        self._check(response, label, json_expected=True)
        return response.json()

    def get_html(self, request_path):
        label = _label("GET", request_path)
        response = self._read("GET", f"{BASE_URL}{request_path}", label, headers={"X-Requested-With": "XMLHttpRequest"})
        self._check(response, label)
        return response.text

    def _post(self, request_path, body, headers, retry_429):
        url, label = f"{BASE_URL}{request_path}", _label("POST", request_path)
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            **headers,
        }
        if retry_429:
            return self._read("POST", url, label, headers=headers, data=body), label
        return self._send("POST", url, headers=headers, data=body), label

    def post_content(self, request_path, body, retry_429=False):
        if not request_path.startswith("/content/"):
            raise ValueError("ADVBOX request path must start with /content/")
        response, label = self._post(
            request_path, body, {"Accept": "application/json, text/javascript, */*; q=0.01"}, retry_429
        )
        self._check(response, label, csrf=True, json_expected=True)
        return response.json()

    def post_form(self, request_path, body, retry_429=False):
        response, label = self._post(request_path, body, {}, retry_429)
        self._check(response, label, csrf=True, detail_chars=500)
        return response.text

    def csrf_token(self, force=False):
        if self.csrf and not force:
            return self.csrf
        label = "ADVBOX GET /posts"
        response = self._read("GET", f"{BASE_URL}/posts", label)
        self._check(response, label)
        token = parse_csrf_token(response.text)
        if not token:
            raise RuntimeError("ADVBOX page did not expose a csrf-token meta tag")
        self.csrf = token
        return token

    def with_csrf(self, build):
        try:
            return build(self.csrf_token())
        except CsrfStaleError:
            return build(self.csrf_token(force=True))

    def post(self, request_path, params=None):
        """CSRF-protected form POST used for reads and writes alike."""
        request_path = _path(request_path)

        def build(token):
            pairs = [("_token", token)]
            for key, value in (params or {}).items():
                if key == "_token":
                    continue
                pairs += [(key, str(item)) for item in value] if isinstance(value, list) else [(key, str(value))]
            body = urlencode(pairs)
            if request_path.startswith("/content/"):
                return self.post_content(request_path, body)
            return self.post_form(request_path, body)

        return self.using(lambda session: session.with_csrf(build))

    def post_multipart(self, request_path, fields):
        require_writes()
        request_path = _path(request_path)

        def build(token):
            form = CurlMime()
            try:
                for key, value in fields.items():
                    if isinstance(value, dict) and "bytes" in value:
                        form.addpart(
                            name=key,
                            data=value["bytes"],
                            filename=value.get("filename") or "file",
                            content_type=value.get("contentType") or "application/octet-stream",
                        )
                    else:
                        for item in value if isinstance(value, list) else [value]:
                            form.addpart(name=key, data=str(item).encode())
                response = self._send(
                    "POST",
                    f"{BASE_URL}{request_path}",
                    multipart=form,
                    headers={"Accept": "*/*", "X-CSRF-TOKEN": token, "X-Requested-With": "XMLHttpRequest"},
                )
            finally:
                form.close()
            self._check(response, _label("POST", request_path), csrf=True, detail_chars=500)
            return response.text

        return self.using(lambda session: session.with_csrf(build))

    def stream(self, url, *, use_cookies):
        headers = {"Cookie": cookie_header(self.jar)} if use_cookies else {}
        return self.http.stream(
            "GET", url, headers=headers, allow_redirects=False, timeout=self.timeout, discard_cookies=True
        )
