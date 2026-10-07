#!/usr/bin/env python3
"""Shared TRF1 1G/2G SSO and TOTP authentication."""

import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from curl_cffi import requests

from jurisync import portals
from jurisync.core.http import IMPERSONATE, TIMEOUT, log_request, require_url
from jurisync.core.redaction import url_shape
from jurisync.core.totp import totp

SSO_HOST = "sso.cloud.pje.jus.br"
ALLOWED_HOSTS = portals.hosts("pje") | {SSO_HOST}


def require_allowed_url(value):
    """PJe and its SSO only: a login flow must never be redirected elsewhere."""
    return require_url(value, hosts=ALLOWED_HOSTS, label="PJe login redirect")


def form_action(html, page_url, form_id):
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form", id=form_id)
    if not form or not form.get("action"):
        raise RuntimeError(f"Form {form_id!r} was not returned by {url_shape(page_url)}")
    action = urljoin(page_url, form["action"])
    require_allowed_url(action)
    return action


def credential_failure(html):
    """Explain why the credential POST did not reach the OTP step without dumping HTML."""
    soup = BeautifulSoup(html, "html.parser")
    otp_fields = soup.find_all("input", attrs={"name": "otp"})
    if otp_fields:
        parent = otp_fields[0].find_parent("form")
        form_id = parent.get("id") if parent else None
        return f"OTP field was returned under unexpected form {form_id!r}"
    text = " ".join(soup.stripped_strings).lower()
    if "senha expirada" in text:
        return "The portal reports an expired password; reset it through the portal and update the configured password"
    if any(marker in text for marker in ("captcha", "aws waf", "access denied", "challenge")):
        return "The credential response looks like a WAF/challenge page"
    if any(marker in text for marker in ("invalid", "incorreto", "inválido", "usuário ou senha", "senha inválida")):
        return "The portal rejected the username/password; use the CPF/username from the browser request"
    return "The portal returned no OTP form; credentials may be rejected or the login page changed"


def cookie_inventory(session):
    return sorted({cookie.name for cookie in session.cookies.jar})


def save_cookies(session, path):
    values = []
    for cookie in session.cookies.jar:
        values.append(
            {
                "name": cookie.name,
                "value": cookie.value,
                "domain": cookie.domain,
                "path": cookie.path,
            }
        )
    Path(path).write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")


def get_with_log(session, url):
    require_allowed_url(url)
    response = session.get(url, allow_redirects=False, impersonate=IMPERSONATE, timeout=TIMEOUT)
    log_request("GET", url, response.status_code)
    return response


def login_session(username, password, account, app_url=None, expected_app_host=None):
    config = portals.pje("trf1-2g")
    app_url = app_url or config.login_url
    expected_app_host = expected_app_host or config.host
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
    log_request("POST", login_url, response.status_code)
    try:
        otp_url = form_action(response.text, response.url, "kc-otp-login-form")
    except RuntimeError as error:
        raise RuntimeError(f"{credential_failure(response.text)} ({error})") from None
    otp = totp(account).now()

    response = session.post(
        otp_url,
        data={"otp": otp, "login": "Validar"},
        allow_redirects=False,
        impersonate="chrome",
    )
    log_request("POST", otp_url, response.status_code)
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
