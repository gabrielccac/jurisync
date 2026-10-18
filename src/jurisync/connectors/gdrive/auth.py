"""Service-account access to Google Drive, read-only.

The key is the JSON Google gives for a service account. It is read from GOOGLE_SERVICE_ACCOUNT_FILE (default
~/.config/jurisync/google-service-account.json) and refused when anyone but its owner can read it. The token is
obtained with the JWT bearer grant, signed RS256 with `cryptography` (already a dependency), so no Google library is
needed, and it only ever asks for the `drive.readonly` scope.
"""

import base64
import json
import os
import stat
import time
from pathlib import Path
from urllib.parse import urlencode

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

SCOPE = "https://www.googleapis.com/auth/drive.readonly"
KEY_ENV = "GOOGLE_SERVICE_ACCOUNT_FILE"
DEFAULT_KEY = Path.home() / ".config" / "jurisync" / "google-service-account.json"
TOKEN_URI = "https://oauth2.googleapis.com/token"


class DriveAuthError(RuntimeError):
    pass


def key_path():
    return Path(os.environ.get(KEY_ENV) or DEFAULT_KEY).expanduser()


def load_key(path=None):
    """The service-account key, after checking it exists, is owner-only and is a service-account key."""
    path = Path(path) if path else key_path()
    if not path.is_file():
        raise DriveAuthError(
            f"No Google service-account key at {path}. Create one (see docs/connectors/gdrive.md) and save it there, "
            f"or point {KEY_ENV} to it"
        )
    mode = path.stat().st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise DriveAuthError(f"The key {path} can be read by others ({oct(mode & 0o777)}); run: chmod 600 '{path}'")
    try:
        key = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise DriveAuthError(f"The key {path} is not valid JSON: {error}") from None
    if key.get("type") != "service_account" or not key.get("client_email") or not key.get("private_key"):
        raise DriveAuthError(f"{path} is not a Google service-account key (type service_account)")
    return key


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def signed_assertion(key, now=None):
    """The JWT the token endpoint exchanges for an access token (scope: drive.readonly only)."""
    issued = int(now if now is not None else time.time())
    header = {"alg": "RS256", "typ": "JWT", **({"kid": key["private_key_id"]} if key.get("private_key_id") else {})}
    claims = {
        "iss": key["client_email"],
        "scope": SCOPE,
        "aud": key.get("token_uri") or TOKEN_URI,
        "iat": issued,
        "exp": issued + 3600,
    }
    signing_input = f"{_b64(json.dumps(header).encode())}.{_b64(json.dumps(claims).encode())}".encode()
    private_key = serialization.load_pem_private_key(key["private_key"].encode(), password=None)
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_input.decode()}.{_b64(signature)}"


class ServiceAccount:
    """Access tokens for one service account, renewed a minute before they expire."""

    def __init__(self, http, key=None):
        self.http = http
        self.key = key if key is not None else load_key()
        self.email = self.key["client_email"]
        self._token, self._expires = None, 0

    def token(self):
        if self._token and time.time() < self._expires - 60:
            return self._token
        body = urlencode(
            {"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": signed_assertion(self.key)}
        ).encode()
        status, _, content = self.http(
            "POST",
            self.key.get("token_uri") or TOKEN_URI,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=body,
        )
        if status != 200:
            detail = content[:300].decode("utf-8", "replace")
            raise DriveAuthError(f"Google refused the service-account key (HTTP {status}): {detail}")
        answer = json.loads(content)
        self._token, self._expires = answer["access_token"], time.time() + int(answer.get("expires_in", 3600))
        return self._token
