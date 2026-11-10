"""Sign the PJe SSO challenge with an A3 token (PKCS#11), without PJeOffice.

The private key never leaves the token: the MD5 DigestInfo is built here and the token applies the RSA
PKCS#1 v1.5 padding (``CKM_RSA_PKCS``). Every PJe portal behind the national SSO accepts the same
signature, so this works for any portal registered in ``jurisync.portals``.

The PIN comes from ``PJE_A3_PIN`` or from the system keyring (``jurisync`` / ``a3-pin:<serial>``, falling
back to the gerid-adapter entry for the same token). It is tried once per login: a refused PIN raises
``PinError`` and is never retried, and a token that already counts a wrong PIN is not touched at all.
"""

import argparse
import getpass
import hashlib
import os
import sys
from contextlib import contextmanager
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding

from jurisync.connectors.pje.auth.signer import pkipath_encode

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None

DEFAULT_MODULES = {
    "darwin": "/Applications/tokenadmin.app/Contents/Frameworks/libaetpkss.dylib",
    "win32": "aetpkss1.dll",
    "linux": "/usr/lib/libaetpkss.so",
}

# DER prefix of DigestInfo for MD5 (RFC 8017 §9.2): the token pads, the caller hashes.
MD5_DIGEST_INFO = bytes.fromhex("3020300c06082a864886f70d020505000410")

# Any of these states blocks the automatic login: one more wrong PIN could lock the token.
UNSAFE_PIN_FLAGS = {
    "CKF_USER_PIN_COUNT_LOW": "a wrong PIN was already tried",
    "CKF_USER_PIN_FINAL_TRY": "only one PIN attempt is left",
    "CKF_USER_PIN_LOCKED": "the PIN is locked",
    "CKF_USER_PIN_TO_BE_CHANGED": "the PIN must be changed",
}

KEYRING_SERVICE = "jurisync"
GERID_KEYRING_SERVICE = "gerid-adapter"


class PinError(RuntimeError):
    """A PIN problem: never retried automatically."""


def module_path():
    return os.environ.get("PJE_A3_MODULE") or DEFAULT_MODULES.get(sys.platform, "")


def lock_path():
    default = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "jurisync"
    return default / "a3.lock"


@contextmanager
def token_lock():
    """One process at a time on the token (parallel MCP calls, other servers using the same A3)."""
    path = lock_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with open(path, "w") as handle:
        if fcntl is None:  # Windows: msvcrt locks a byte range and blocks while another process holds it.
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            return
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _keyring_pin(serial):
    try:
        import keyring
    except ImportError:
        return None
    for service, account in ((KEYRING_SERVICE, f"a3-pin:{serial}"), (GERID_KEYRING_SERVICE, f"pin:{serial}")):
        try:
            pin = keyring.get_password(service, account)
        except Exception:
            continue  # A locked or missing keyring falls through to the next source.
        if pin:
            return pin
    return None


def stored_pin(serial):
    pin = os.environ.get("PJE_A3_PIN", "").strip() or _keyring_pin(serial)
    if not pin:
        raise PinError(
            f"No PIN for A3 token {serial}. Store it with: python -m jurisync.connectors.pje.auth.a3 pin-set"
        )
    return pin


def certificate_chain(certs):
    """The holder's certificate (the only non-CA one) followed by its issuers found on the token."""
    leaves = []
    for ident, cert in certs:
        try:
            is_ca = cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
        except x509.ExtensionNotFound:
            is_ca = False
        if not is_ca:
            leaves.append((ident, cert))
    if len(leaves) != 1:
        raise RuntimeError(f"Expected one holder certificate on the A3 token, found {len(leaves)}")
    ident, leaf = leaves[0]
    by_subject = {cert.subject: cert for _, cert in certs}
    chain, current = [leaf], leaf
    while current.issuer != current.subject and current.issuer in by_subject:
        current = by_subject[current.issuer]
        chain.append(current)
    return ident, chain


def md5_digest_info(message):
    return MD5_DIGEST_INFO + hashlib.md5(message).digest()


class A3Signer:
    """Opens the token, logs in once and signs SSO challenges. Use as a context manager."""

    def __init__(self, module=None, pin=None):
        import PyKCS11

        self._pkcs11 = PyKCS11
        self.lib = PyKCS11.PyKCS11Lib()
        path = module or module_path()
        if not path:
            raise RuntimeError("Set PJE_A3_MODULE to the token's PKCS#11 library")
        self.lib.load(path)
        slots = self.lib.getSlotList(tokenPresent=True)
        if not slots:
            raise RuntimeError("No A3 token is plugged in")
        if len(slots) > 1:
            raise RuntimeError(f"{len(slots)} tokens are plugged in; leave only one")
        self.slot = slots[0]
        self.serial = self.lib.getTokenInfo(self.slot).serialNumber.strip()
        self.session = self.lib.openSession(self.slot, PyKCS11.CKF_SERIAL_SESSION)
        self.logged_in = False
        self._pin = pin
        self.ident, self.chain = certificate_chain(self._certificates())
        self._key = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _certificates(self):
        pkcs11 = self._pkcs11
        result = []
        for obj in self.session.findObjects([(pkcs11.CKA_CLASS, pkcs11.CKO_CERTIFICATE)]):
            value, ident = self.session.getAttributeValue(obj, [pkcs11.CKA_VALUE, pkcs11.CKA_ID])
            result.append((bytes(ident), x509.load_der_x509_certificate(bytes(value))))
        return result

    def unsafe_pin_state(self):
        flags = self.lib.getTokenInfo(self.slot).flags2text()
        return [text for flag, text in UNSAFE_PIN_FLAGS.items() if flag in flags]

    def login(self):
        """A single C_Login. A refused PIN becomes PinError and is not retried."""
        pkcs11 = self._pkcs11
        if self.logged_in:
            return
        problems = self.unsafe_pin_state()
        if problems:
            raise PinError("A3 login cancelled: " + "; ".join(problems) + ". Unlock it in tokenadmin first.")
        pin = self._pin or stored_pin(self.serial)
        try:
            self.session.login(pin, pkcs11.CKU_USER)
        except pkcs11.PyKCS11Error as error:
            if error.value == pkcs11.CKR_USER_ALREADY_LOGGED_IN:
                self.logged_in = True
                return
            if error.value in (pkcs11.CKR_PIN_INCORRECT, pkcs11.CKR_PIN_LOCKED, pkcs11.CKR_PIN_LEN_RANGE):
                raise PinError(
                    f"The A3 token refused the PIN ({pkcs11.CKR[error.value]}); not retrying. "
                    "Check the stored PIN before the next login."
                ) from None
            raise RuntimeError(f"The A3 token refused the login ({pkcs11.CKR.get(error.value, error.value)})") from None
        self.logged_in = True

    def _private_key(self):
        pkcs11 = self._pkcs11
        if self._key is None:
            keys = self.session.findObjects(
                [(pkcs11.CKA_CLASS, pkcs11.CKO_PRIVATE_KEY), (pkcs11.CKA_ID, list(self.ident))]
            )
            if len(keys) != 1:
                raise RuntimeError(f"Expected one private key for the A3 certificate, found {len(keys)}")
            self._key = keys[0]
        return self._key

    def sign(self, mensagem):
        """MD5withRSA over the challenge, as PJeOffice does."""
        if isinstance(mensagem, str):
            mensagem = mensagem.encode("utf-8")
        self.login()
        mechanism = self._pkcs11.Mechanism(self._pkcs11.CKM_RSA_PKCS, None)
        return bytes(self.session.sign(self._private_key(), md5_digest_info(mensagem), mechanism))

    def chain64(self):
        import base64

        blobs = [cert.public_bytes(Encoding.DER) for cert in self.chain]
        return base64.b64encode(pkipath_encode(blobs)).decode("ascii")

    def close(self):
        try:
            if self.logged_in:
                self.session.logout()
        finally:
            self.session.closeSession()


def _pin_set():
    with A3Signer() as token:
        serial = token.serial
    first = getpass.getpass(f"PIN of A3 token {serial}: ")
    if first != getpass.getpass("Repeat the PIN: "):
        raise SystemExit("PINs differ; nothing was stored.")
    import keyring

    keyring.set_password(KEYRING_SERVICE, f"a3-pin:{serial}", first)
    print(f"PIN stored in the system keyring ({KEYRING_SERVICE} / a3-pin:{serial}).")


def _check(sign):
    """Token, certificate and PIN state; with --sign, one offline test signature verified locally."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    with token_lock(), A3Signer() as token:
        leaf = token.chain[0]
        print(f"token: {token.serial}")
        print(f"holder: {leaf.subject.rfc4514_string()}")
        print(f"valid until: {leaf.not_valid_after_utc:%Y-%m-%d}; chain: {len(token.chain)} certificates")
        print(f"unsafe PIN state: {token.unsafe_pin_state() or 'none'}")
        print(f"PIN stored: {bool(os.environ.get('PJE_A3_PIN') or _keyring_pin(token.serial))}")
        if sign:
            message = b"jurisync-a3-selftest"
            signature = token.sign(message)
            leaf.public_key().verify(signature, message, padding.PKCS1v15(), hashes.MD5())
            print(f"signature: ok ({len(signature)} bytes, verified with the certificate)")


def main():
    parser = argparse.ArgumentParser(description="A3 token for the PJe SSO certificate login")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("pin-set", help="store the token PIN in the system keyring")
    check = sub.add_parser("check", help="token, certificate and PIN state (no PIN used)")
    check.add_argument("--sign", action="store_true", help="also log in once and verify a test signature")
    args = parser.parse_args()
    if args.command == "pin-set":
        _pin_set()
    else:
        _check(args.sign)


if __name__ == "__main__":
    main()
