"""Route DJEN requests through a bridge with a Brazilian IP when the server runs abroad.

The CNJ communication API refuses foreign IPs. A local install calls it directly. The hosted server sets
``DJEN_BRIDGE_URL`` (a Cloud Run function in São Paulo that forwards GET requests under ``/api/v1/`` to the CNJ API),
``DJEN_BRIDGE_TOKEN`` (shared secret the bridge checks) and ``DJEN_BRIDGE_SA_FILE`` (a Google service account key
allowed only to invoke that function). Each request carries a Google identity token for the bridge, minted from the
service account and cached until shortly before it expires. These are server settings, not office credentials.
"""

import base64
import json
import os
import threading
import time
import urllib.parse
import urllib.request

UPSTREAM = "https://comunicaapi.pje.jus.br"
TOKEN_HEADER = "X-Jurisync-Token"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
REFRESH_MARGIN = 300

_cache = {"token": None, "expires": 0.0, "audience": None}
_lock = threading.Lock()


def bridge_url():
    return os.environ.get("DJEN_BRIDGE_URL", "").strip().rstrip("/")


def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _signed_assertion(account, audience, now):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    header = {"alg": "RS256", "typ": "JWT", "kid": account.get("private_key_id", "")}
    claims = {
        "iss": account["client_email"],
        "sub": account["client_email"],
        "aud": account.get("token_uri") or GOOGLE_TOKEN_URL,
        "iat": int(now),
        "exp": int(now) + 3600,
        "target_audience": audience,
    }
    signing_input = f"{_b64(json.dumps(header).encode())}.{_b64(json.dumps(claims).encode())}".encode()
    key = serialization.load_pem_private_key(account["private_key"].encode(), password=None)
    signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_input.decode()}.{_b64(signature)}"


def identity_token(audience, *, clock=time.time, send=None):
    """A Google identity token for ``audience``, from the service account in DJEN_BRIDGE_SA_FILE."""
    with _lock:
        now = clock()
        if _cache["token"] and _cache["audience"] == audience and now < _cache["expires"] - REFRESH_MARGIN:
            return _cache["token"]
        path = os.environ.get("DJEN_BRIDGE_SA_FILE", "").strip()
        if not path:
            raise RuntimeError("DJEN_BRIDGE_SA_FILE is not set; the DJEN bridge needs a service account key")
        with open(path, encoding="utf-8") as handle:
            account = json.load(handle)
        body = urllib.parse.urlencode(
            {
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": _signed_assertion(account, audience, now),
            }
        ).encode()
        request = urllib.request.Request(
            account.get("token_uri") or GOOGLE_TOKEN_URL,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        send = send or (lambda req: urllib.request.urlopen(req, timeout=20))
        with send(request) as response:
            token = json.load(response)["id_token"]
        _cache.update(token=token, expires=now + 3600, audience=audience)
        return token


def request(url, headers):
    """A urllib request for a CNJ API URL, sent through the bridge when one is configured."""
    base = bridge_url()
    if not base or not url.startswith(UPSTREAM + "/"):
        return urllib.request.Request(url, headers=headers)
    routed = dict(headers)
    routed["Authorization"] = f"Bearer {identity_token(base)}"
    routed[TOKEN_HEADER] = os.environ.get("DJEN_BRIDGE_TOKEN", "")
    return urllib.request.Request(base + url[len(UPSTREAM) :], headers=routed)
