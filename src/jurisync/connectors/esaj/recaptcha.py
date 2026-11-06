"""reCAPTCHA v3 token for the TJMS public consultation, minted over plain HTTP.

The consultation form of ``/cpopg5`` carries an invisible reCAPTCHA v3 whose token the page mints with
``grecaptcha.execute(sitekey, {action: 'consulta'})``. That call is two Google requests: ``GET anchor`` and
``POST reload`` with a small protobuf body. This module replays exactly that exchange over HTTP, with no browser,
no JavaScript engine and no third-party solver.

Protocol credit: EvickaStudio/BypassV3 (Apache-2.0), whose anchor/reload client was adapted here, with two fixes
found live against Google: the ``co`` origin parameter needs ``.`` padding instead of ``=``, and the anchor request
needs a real browser User-Agent. Only the action the page itself declares is ever sent.
"""

import base64
import json
import re
import urllib.parse

SITEKEY = re.compile(r"['\"](6L[A-Za-z0-9_-]{38})['\"]")
ACTION = re.compile(r"execute\(\s*['\"][^'\"]+['\"]\s*,\s*\{\s*action:\s*['\"]([^'\"]+)")
ANCHOR_VERSION = re.compile(r"recaptcha/releases/([^/\"']+)")
TOKEN_INPUT = re.compile(r'type="hidden" id="recaptcha-token" value="([^"]+)"')
DEFAULT_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


class RecaptchaError(RuntimeError):
    """Google did not hand out a token."""


def _encode_varint(value):
    if value < 0:
        value += 1 << 64
    out = bytearray()
    while value >= 0x80:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def _field(field, wire, payload):
    tag = _encode_varint((field << 3) | wire)
    if wire == 2:
        return tag + _encode_varint(len(payload)) + payload
    return tag + payload


def encode_reload_body(*, v, c, k, action, reason="q"):
    body = bytearray()
    body += _field(1, 2, v.encode())
    body += _field(2, 2, c.encode())
    body += _field(6, 2, reason.encode())
    if action:
        body += _field(8, 2, action.encode())
    body += _field(14, 2, k.encode())
    return bytes(body)


def parse_reload_response(text):
    text = text.strip().removeprefix(")]}'").strip()
    try:
        items = json.loads(text)
    except ValueError:
        match = re.search(r'\["rresp","(.*?)"', text)
        return match[1] if match else None
    if isinstance(items, list) and len(items) >= 2 and items[0] == "rresp":
        return items[1] if isinstance(items[1], str) else None
    return None


def sitekey_and_action(html):
    """The ``(sitekey, action)`` the consultation page itself declares to its own script."""
    key = SITEKEY.search(html or "")
    action = ACTION.search(html or "")
    if not key or not action:
        raise RecaptchaError("The consultation page has no reCAPTCHA sitekey")
    return key.group(1), action.group(1)


def encode_origin(origin):
    """Google's ``co`` form: base64 of ``scheme://host:port``, padded with ``.`` instead of ``=``."""
    parts = urllib.parse.urlsplit(origin)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    raw = base64.b64encode(f"{parts.scheme}://{parts.hostname}:{port}".encode()).decode()
    return raw.rstrip("=") + "." * (len(raw) - len(raw.rstrip("=")))


def anchor_url(sitekey, origin, version):
    params = {"ar": "1", "k": sitekey, "co": encode_origin(origin), "hl": "en", "v": version, "size": "invisible"}
    return "https://www.google.com/recaptcha/api2/anchor?" + urllib.parse.urlencode(params)


def script_version(http, sitekey, timeout):
    """The current reCAPTCHA release, read from Google's own ``api.js`` so a stale version is never sent."""
    response = http.get(
        f"https://www.google.com/recaptcha/api.js?render={sitekey}",
        headers={"User-Agent": DEFAULT_UA},
        impersonate="chrome",
        timeout=timeout,
    )
    if response.status_code != 200:
        raise RecaptchaError(f"Google api.js failed (HTTP {response.status_code})")
    match = ANCHOR_VERSION.search(response.text)
    if not match:
        raise RecaptchaError("Google api.js has no release version")
    return match.group(1)


def mint_token(http, sitekey, origin, action, timeout=30, tries=3):
    """A v3 token with the page's action bound into it, via anchor + reload over plain HTTP."""
    version = script_version(http, sitekey, timeout)
    url = anchor_url(sitekey, origin, version)
    for _ in range(tries):
        anchor = http.get(
            url,
            headers={
                "User-Agent": DEFAULT_UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": origin + "/",
                "Sec-Fetch-Dest": "iframe",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "cross-site",
            },
            impersonate="chrome",
            timeout=timeout,
        )
        if anchor.status_code != 200:
            continue
        match = TOKEN_INPUT.search(anchor.text)
        if not match:
            continue
        reload = http.post(
            f"https://www.google.com/recaptcha/api2/reload?k={sitekey}",
            content=encode_reload_body(v=version, c=match.group(1), k=sitekey, action=action),
            headers={
                "User-Agent": DEFAULT_UA,
                "Content-Type": "application/x-protobuffer",
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.9",
                "Origin": "https://www.google.com",
                "Referer": url,
            },
            impersonate="chrome",
            timeout=timeout,
        )
        if reload.status_code == 200 and (token := parse_reload_response(reload.text)):
            return token
    raise RecaptchaError("Google did not hand out a reCAPTCHA token")
