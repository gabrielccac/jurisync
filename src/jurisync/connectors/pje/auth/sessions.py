"""Private PJe cookies scoped to the project, account, and portal."""

import hashlib
import json
import os
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from curl_cffi import requests

from jurisync import portals


def auth_method(portal):
    """How this install logs in to the portal.

    A portal registered with a certificate (TRF5) also accepts its own username, password and TOTP: the
    password flow is used only when no A1 certificate is configured (``PJE_A1_P12``) and all three are set.

    Any other PJe portal uses the certificate when one is configured (``PJE_A1_P12``) and the portal
    opts in with ``<PREFIX>_CERT=true``; the certificate flow asks for TOTP only if the SSO does.
    """
    config = portals.pje(portal)
    certificate = bool(os.environ.get("PJE_A1_P12"))
    password_ready = all(config.env(name) for name in ("USERNAME", "PASSWORD", "2FA_DATA"))
    if config.auth == "certificate_totp":
        return "password_totp" if password_ready and not certificate else "certificate_totp"
    if config.uses_certificate():
        return "certificate_totp"
    return config.auth


def cache_identity(portal):
    config = portals.pje(portal)
    account = config.env("SESSION_ACCOUNT")
    username = config.env("USERNAME") if auth_method(portal) == "password_totp" else ""
    if not account and not username:
        # The certificate is not part of the identity. Reuse is safe only
        # when the caller identifies that account; fresh login still works.
        return None
    identity = json.dumps([str(Path.cwd().resolve()), config.host, username, account])
    return hashlib.sha256(identity.encode()).hexdigest()


def cache_path(portal):
    identity = cache_identity(portal)
    if identity is None:
        return None
    default = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "jurisync" / "pje"
    root = Path(os.environ.get("PJE_SESSION_DIR") or default)
    return root / f"{portal}-{identity}.json"


def is_authenticated(session, portal):
    """Return whether the session still opens the authenticated process search."""
    config = portals.pje(portal)
    try:
        response = session.get(config.search_url, impersonate="chrome", allow_redirects=False, timeout=20)
    except requests.exceptions.RequestException:
        return False
    soup = BeautifulSoup(response.text, "html.parser")
    return bool(
        response.status_code == 200
        and urlsplit(response.url).hostname == config.host
        and soup.find("form", id="fPP")
        and soup.find("input", attrs={"name": "javax.faces.ViewState"})
    )


def load_session(portal):
    path = cache_path(portal)
    if path is None or not path.is_file():
        return None
    session = requests.Session()
    try:
        data = json.loads(path.read_text())
        if data["portal"] != portal or data.get("identity") != cache_identity(portal) or not data["cookies"]:
            raise ValueError("Wrong or empty PJe session cache")
        for cookie in data["cookies"]:
            session.cookies.set(
                cookie["name"], cookie["value"], domain=cookie["domain"], path=cookie["path"], secure=cookie["secure"]
            )
        if is_authenticated(session, portal):
            return session
    except (OSError, ValueError, TypeError, KeyError):
        pass  # A missing, invalid, or expired cache requires a fresh login.
    session.close()
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    return None


def save_session(portal, session):
    path = cache_path(portal)
    if path is None:
        return
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    cookies = [
        {
            "name": cookie.name,
            "value": cookie.value,
            "domain": cookie.domain,
            "path": cookie.path,
            "secure": cookie.secure,
        }
        for cookie in session.cookies.jar
    ]
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as output:
            json.dump({"portal": portal, "identity": cache_identity(portal), "cookies": cookies}, output)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
