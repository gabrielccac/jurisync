#!/usr/bin/env python3
"""Shared TRF1 1G/2G SSO and TOTP authentication."""

import base64
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

import pyotp
from bs4 import BeautifulSoup
from curl_cffi import requests


APP_URL = "https://pje2g.trf1.jus.br/pje/login.seam"
APP_HOST = "pje2g.trf1.jus.br"
APP_HOST_1G = "pje1g.trf1.jus.br"
SSO_HOST = "sso.cloud.pje.jus.br"
ALLOWED_HOSTS = {APP_HOST, APP_HOST_1G, SSO_HOST}



def safe_url(value):
    """Return a URL without query parameters, codes, or tokens."""
    parts = urlsplit(value)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


def require_allowed_url(value):
    if urlsplit(value).hostname not in ALLOWED_HOSTS:
        raise RuntimeError(f"Unexpected redirect host: {urlsplit(value).hostname}")


def form_action(html, page_url, form_id):
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form", id=form_id)
    if not form or not form.get("action"):
        raise RuntimeError(f"Form {form_id!r} was not returned by {safe_url(page_url)}")
    return urljoin(page_url, form["action"])


def credential_failure(html):
    """Explain why the credential POST did not reach the OTP step without dumping HTML."""
    soup = BeautifulSoup(html, "html.parser")
    otp_fields = soup.find_all("input", attrs={"name": "otp"})
    if otp_fields:
        parent = otp_fields[0].find_parent("form")
        form_id = parent.get("id") if parent else None
        return f"OTP field was returned under unexpected form {form_id!r}"
    text = " ".join(soup.stripped_strings).lower()
    if any(marker in text for marker in ("captcha", "aws waf", "access denied", "challenge")):
        return "The credential response looks like a WAF/challenge page"
    if any(marker in text for marker in ("invalid", "incorreto", "inválido", "usuário ou senha", "senha inválida")):
        return "The portal rejected the username/password; use the CPF/username from the browser request"
    return "The portal returned no OTP form; credentials may be rejected or the login page changed"


def varint(data, offset):
    value = 0
    shift = 0
    while offset < len(data):
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, offset
        shift += 7
        if shift > 63:
            break
    raise ValueError("Invalid protobuf varint")


def proto_fields(data):
    """Yield (field_number, wire_type, value) for a protobuf message."""
    offset = 0
    while offset < len(data):
        key, offset = varint(data, offset)
        field_number, wire_type = key >> 3, key & 7
        if wire_type == 0:
            value, offset = varint(data, offset)
        elif wire_type == 1:
            value, offset = data[offset:offset + 8], offset + 8
        elif wire_type == 2:
            length, offset = varint(data, offset)
            value, offset = data[offset:offset + length], offset + length
        elif wire_type == 5:
            value, offset = data[offset:offset + 4], offset + 4
        else:
            raise ValueError(f"Unsupported protobuf wire type: {wire_type}")
        if offset > len(data):
            raise ValueError("Truncated protobuf message")
        yield field_number, wire_type, value


def migration_accounts(encoded):
    if encoded.startswith("otpauth-migration://"):
        encoded = parse_qs(urlsplit(encoded).query).get("data", [""])[0]
    raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    result = []
    for field, wire, payload in proto_fields(raw):
        if field != 1 or wire != 2:
            continue
        account = {"name": "", "issuer": "", "algorithm": "sha1", "digits": 6, "type": "totp"}
        for nested_field, nested_wire, nested_value in proto_fields(payload):
            if nested_field == 1 and nested_wire == 2:
                account["secret"] = base64.b32encode(nested_value).decode()
            elif nested_field == 2 and nested_wire == 2:
                account["name"] = nested_value.decode("utf-8", errors="replace")
            elif nested_field == 3 and nested_wire == 2:
                account["issuer"] = nested_value.decode("utf-8", errors="replace")
            elif nested_field == 4 and nested_wire == 0:
                account["algorithm"] = {2: "sha256", 3: "sha512"}.get(nested_value, "sha1")
            elif nested_field == 5 and nested_wire == 0:
                account["digits"] = 8 if nested_value == 2 else 6
            elif nested_field == 6 and nested_wire == 0:
                account["type"] = "hotp" if nested_value == 1 else "totp"
        if account.get("secret"):
            result.append(account)
    if not result:
        raise ValueError("No TOTP account found in migration data")
    return result


def migration_totps(encoded):
    result = migration_accounts(encoded)
    if len(result) > 1:
        raise ValueError("2FA migration data contains multiple accounts; provide one secret or export one account")
    account = result[0]
    if account["type"] != "totp":
        raise ValueError("2FA migration data contains an HOTP account, not TOTP")
    return pyotp.TOTP(
        account["secret"],
        digits=account["digits"],
        digest=getattr(hashlib, account["algorithm"]),
    )


def totp_from_input(value):
    value = unquote(value.strip())
    if value.startswith("otpauth://totp/"):
        return pyotp.parse_uri(value)
    if value.startswith("otpauth-migration://"):
        value = parse_qs(urlsplit(value).query).get("data", [""])[0]
    if "data=" in value and not re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", value):
        value = parse_qs(value).get("data", [value])[0]
    if re.fullmatch(r"[A-Za-z2-7 ]+={0,6}", value, re.IGNORECASE):
        return pyotp.TOTP(value.replace(" ", "").upper())
    return migration_totps(value)


def cookie_inventory(session):
    return sorted({cookie.name for cookie in session.cookies.jar})


def save_cookies(session, path):
    values = []
    for cookie in session.cookies.jar:
        values.append({
            "name": cookie.name,
            "value": cookie.value,
            "domain": cookie.domain,
            "path": cookie.path,
        })
    Path(path).write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")


def get_with_log(session, url):
    require_allowed_url(url)
    response = session.get(url, allow_redirects=False, impersonate="chrome")
    print(f"GET  {safe_url(url)} -> {response.status_code} {safe_url(response.url)}")
    return response


def login_session(username, password, twofa_data, app_url=APP_URL, expected_app_host=APP_HOST):
    session = requests.Session()
    response = get_with_log(session, app_url)
    if response.status_code not in {301, 302, 303, 307, 308}:
        raise RuntimeError(f"Initial PJe page did not redirect (HTTP {response.status_code})")

    sso_url = urljoin(response.url, response.headers["Location"])
    response = get_with_log(session, sso_url)
    login_url = form_action(response.text, response.url, "loginForm")
    response = session.post(
        login_url,
        data={"username": username, "password": password, "pjeoffice-code": "", "phrase": ""},
        allow_redirects=False,
        impersonate="chrome",
    )
    print(f"POST {safe_url(login_url)} -> {response.status_code}")
    try:
        otp_url = form_action(response.text, response.url, "kc-otp-login-form")
    except RuntimeError as error:
        raise RuntimeError(f"{credential_failure(response.text)} ({error})") from None
    otp = totp_from_input(twofa_data).now()

    response = session.post(
        otp_url,
        data={"otp": otp, "login": "Validar"},
        allow_redirects=False,
        impersonate="chrome",
    )
    print(f"POST {safe_url(otp_url)} -> {response.status_code}")
    if response.status_code not in {301, 302, 303, 307, 308}:
        raise RuntimeError("OTP submission did not redirect; the code may be invalid or expired")

    for _ in range(8):
        location = response.headers.get("Location")
        if response.status_code not in {301, 302, 303, 307, 308} or not location:
            break
        response = get_with_log(session, urljoin(response.url, location))

    soup = BeautifulSoup(response.text, "html.parser")
    authenticated = (
        response.status_code == 200
        and urlsplit(response.url).hostname == expected_app_host
        and bool(soup.find("a", href=re.compile(r"/pje/logout\.seam")))
        and bool(soup.find("input", attrs={"name": "javax.faces.ViewState"}))
        and not soup.find("form", id="kc-otp-login-form")
    )
    if not authenticated:
        raise RuntimeError(f"Login did not reach an authenticated PJe page (HTTP {response.status_code})")
    return session, response
