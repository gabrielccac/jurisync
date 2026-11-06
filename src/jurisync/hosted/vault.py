"""Per-tenant portal credentials, encrypted at rest with the server's master key.

One file per tenant under the vault directory. AES-256-GCM with a fresh nonce on every write; the tenant id is
authenticated as associated data, so a file copied onto another tenant's name does not open. The master key comes from
``JURISYNC_VAULT_KEY`` (base64, 32 bytes) and never sits next to the files. Values are text (PINs, TOTP exports,
logins); files are bytes (the A1 certificate) and stay in memory when read.
"""

import base64
import json
import os
import re
import secrets
import tempfile
from pathlib import Path

FORMAT = "jurisync-vault"
VERSION = 1
TENANT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")


def generate_key():
    return base64.b64encode(secrets.token_bytes(32)).decode("ascii")


def load_key(value=None):
    raw = value if value is not None else os.environ.get("JURISYNC_VAULT_KEY", "")
    try:
        key = base64.b64decode(raw.strip(), validate=True)
    except ValueError:
        key = b""
    if len(key) != 32:
        raise RuntimeError("JURISYNC_VAULT_KEY must be 32 random bytes in base64 (jurisync-hosted init prints one)")
    return key


def require_tenant_id(tenant):
    if not TENANT_ID.fullmatch(tenant or ""):
        raise ValueError("Tenant id: 2 to 63 lowercase letters, digits or hyphens, starting with a letter or digit")
    return tenant


def _aad(tenant):
    return f"{FORMAT}-v{VERSION}:{tenant}".encode()


class Vault:
    def __init__(self, root, key):
        self.root = Path(root)
        self._key = key

    def _path(self, tenant):
        return self.root / f"{require_tenant_id(tenant)}.vault"

    def put(self, tenant, values=None, files=None):
        """Replace the tenant's credentials."""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        content = {
            "values": {str(name): str(value) for name, value in (values or {}).items()},
            "files": {str(name): base64.b64encode(raw).decode("ascii") for name, raw in (files or {}).items()},
        }
        nonce = secrets.token_bytes(12)
        box = AESGCM(self._key).encrypt(nonce, json.dumps(content).encode("utf-8"), _aad(tenant))
        record = {
            "format": FORMAT,
            "version": VERSION,
            "tenant": tenant,
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "ciphertext": base64.b64encode(box).decode("ascii"),
        }
        path = self._path(tenant)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=self.root)
        try:
            with os.fdopen(fd, "w") as output:
                json.dump(record, output)
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def get(self, tenant):
        """``(values, files)`` for the tenant; empty when it has nothing stored."""
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        path = self._path(tenant)
        if not path.is_file():
            return {}, {}
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("format") != FORMAT or record.get("version") != VERSION or record.get("tenant") != tenant:
            raise RuntimeError(f"Vault entry for {tenant} has an unexpected header")
        try:
            plain = AESGCM(self._key).decrypt(
                base64.b64decode(record["nonce"]), base64.b64decode(record["ciphertext"]), _aad(tenant)
            )
        except InvalidTag as error:
            raise RuntimeError(f"Vault entry for {tenant} does not open with this key") from error
        content = json.loads(plain)
        files = {name: base64.b64decode(raw) for name, raw in content.get("files", {}).items()}
        return dict(content.get("values", {})), files

    def names(self, tenant):
        """The credential names stored for a tenant, never the values."""
        values, files = self.get(tenant)
        return sorted(values), sorted(files)

    def delete(self, tenant):
        self._path(tenant).unlink(missing_ok=True)
