"""The A3 token (PKCS#11) that signs certificate logins without PJeOffice: the PJe SSO challenge and the Eproc
mutual-TLS handshakes (``core.mtls``) use the same signer and the same cross-process lock (``held_token``).

The private key never leaves the token: the MD5 DigestInfo is built here and the token applies the RSA
PKCS#1 v1.5 padding (``CKM_RSA_PKCS``). Every PJe portal behind the national SSO accepts the same
signature, so this works for any portal registered in ``jurisync.portals``.

The PIN comes from ``PJE_A3_PIN`` or from the system keyring (``jurisync`` / ``a3-pin:<serial>``, falling
back to the gerid-adapter entry for the same token). It is tried once per login: a refused PIN raises
``PinError`` and is never retried, and a token that already counts a wrong PIN is not touched at all.

A cloud A3 works the same way when its app ships a PKCS#11 module: ``PJE_A3_MODULE=certisign`` loads Certisign's
desktopID (RemoteID). Such a token has a protected authentication path: the login is authorised in the vendor's app
(desktopID opens its own window asking the PIN and the app's e-token code, or sends a push to the phone), so no PIN is
read or sent here; a login or signature not authorised within ``PJE_A3_APPROVAL_TIMEOUT`` seconds (300) gives up and
releases the token lock. It also carries the holder's
certificate alone; the issuers come from the system's certificate store.
"""

import argparse
import getpass
import hashlib
import logging
import os
import ssl
import sys
import threading
import warnings
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding

from jurisync.core.certificate import java_pkipath64, pkipath_encode

log = logging.getLogger(__name__)

DEFAULT_MODULES = {
    "darwin": "/Applications/tokenadmin.app/Contents/Frameworks/libaetpkss.dylib",
    "win32": "aetpkss1.dll",
    "linux": "/usr/lib/libaetpkss.so",
}
# Cloud A3 apps whose PKCS#11 module PJE_A3_MODULE takes by name; each one authorises the login in the app, never with
# a PIN. desktopID_Provider.dll is the Windows provider of Certisign's desktopID (RemoteID), a PKCS#11 module as well.
CLOUD_MODULES = {"certisign": "desktopID_Provider.dll"}
# Seconds a login on a token with a protected authentication path waits for the person (PJE_A3_APPROVAL_TIMEOUT).
APPROVAL_TIMEOUT = 300

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
    value = os.environ.get("PJE_A3_MODULE", "").strip()
    return CLOUD_MODULES.get(value.lower(), value) or DEFAULT_MODULES.get(sys.platform, "")


def lock_path():
    default = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "jurisync"
    return default / "a3.lock"


@contextmanager
def token_lock():
    """One process at a time on the token (parallel MCP calls, other servers using the same A3)."""
    path = lock_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        if sys.platform == "win32":  # msvcrt locks a byte range and blocks while another process holds it.
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            return
        import fcntl

        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


@contextmanager
def held_token(signer=None):
    """``signer`` as given (tests, a caller that already holds the token), else the A3 token opened under
    ``token_lock`` for the whole block, so parallel logins never interleave PIN logins on it."""
    if signer is not None:
        yield signer
        return
    with token_lock(), A3Signer() as token:
        yield token


def _keyring_pin(serial):
    try:
        import keyring
    except ImportError:
        return None
    for service, account in ((KEYRING_SERVICE, f"a3-pin:{serial}"), (GERID_KEYRING_SERVICE, f"pin:{serial}")):
        try:
            pin = keyring.get_password(service, account)
        except Exception:  # noqa: BLE001 - a locked or missing keyring falls through to the next source
            continue  # A locked or missing keyring falls through to the next source.
        if pin:
            return pin
    return None


def stored_pin(serial):
    pin = os.environ.get("PJE_A3_PIN", "").strip() or _keyring_pin(serial)
    if not pin:
        raise PinError(f"No PIN for A3 token {serial}. Store it with: python -m jurisync.core.a3 pin-set")
    return pin


@lru_cache(maxsize=1)
def system_issuers():
    """The CA and root certificates of the Windows store (none elsewhere), to complete a chain the token lacks."""
    if sys.platform != "win32":
        return ()
    found = []
    with warnings.catch_warnings():  # the store keeps a few old roots with negative serials
        warnings.simplefilter("ignore")
        for store in ("CA", "ROOT"):
            for der, encoding, _trust in ssl.enum_certificates(store):
                if encoding != "x509_asn":
                    continue
                try:
                    found.append(x509.load_der_x509_certificate(der))
                except ValueError:
                    continue
    return tuple(found)


def certificate_chain(certs, issuers=()):
    """The holder's certificate (the only non-CA one) followed by its issuers found on the token, then in
    ``issuers`` for the ones the token does not carry."""
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
    by_subject = {cert.subject: cert for cert in issuers} | {cert.subject: cert for _, cert in certs}
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
        info = self.lib.getTokenInfo(self.slot)
        self.serial = (info.serialNumber or "").strip()
        # The token authorises the login itself (PIN pad, or the cloud A3 app's push / OTP): no PIN is sent.
        self.protected = "CKF_PROTECTED_AUTHENTICATION_PATH" in info.flags2text()
        self.session = self.lib.openSession(self.slot, PyKCS11.CKF_SERIAL_SESSION)
        self.logged_in = False
        self.abandoned = False  # a protected login still waiting in its thread: the session is not touched again
        self._pin = pin
        certs = self._certificates()
        self.ident, self.chain = certificate_chain(certs)
        if len(self.chain) == 1:  # a cloud A3 carries the holder's certificate alone
            self.ident, self.chain = certificate_chain(certs, system_issuers())
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
        pin = None if self.protected else self._pin or stored_pin(self.serial)
        try:
            self._on_token(lambda: self.session.login(pin, pkcs11.CKU_USER))
        except pkcs11.PyKCS11Error as error:
            if error.value == pkcs11.CKR_USER_ALREADY_LOGGED_IN:
                self.logged_in = True
                return
            if self.protected and error.value in (pkcs11.CKR_FUNCTION_CANCELED, pkcs11.CKR_PIN_INCORRECT):
                raise PinError(
                    f"The A3 authorisation was cancelled or refused ({pkcs11.CKR[error.value]}); not retrying. "
                    "Approve the request in the certificate's app on the next login."
                ) from None
            if error.value in (pkcs11.CKR_PIN_INCORRECT, pkcs11.CKR_PIN_LOCKED, pkcs11.CKR_PIN_LEN_RANGE):
                raise PinError(
                    f"The A3 token refused the PIN ({pkcs11.CKR[error.value]}); not retrying. "
                    "Check the stored PIN before the next login."
                ) from None
            raise RuntimeError(f"The A3 token refused the login ({pkcs11.CKR.get(error.value, error.value)})") from None
        self.logged_in = True

    def _on_token(self, call):
        """``call()``; on a token that authorises by itself, given up after PJE_A3_APPROVAL_TIMEOUT seconds.

        Such a token (the cloud A3's window asking its PIN and app code, a PIN pad) waits for a person, possibly
        forever, at the login or the first signature. The PKCS#11 call cannot be interrupted, so it is left in a daemon
        thread and this signer stops using the session, which releases the token lock for the next login.
        """
        if not self.protected:
            return call()
        timeout = float(os.environ.get("PJE_A3_APPROVAL_TIMEOUT") or APPROVAL_TIMEOUT)
        log.info("A3 waits up to %g s for the authorisation on the token or in its app", timeout)
        outcome = {}

        def run():
            try:
                outcome["value"] = call()
            except Exception as error:  # noqa: BLE001 - re-raised in the caller's thread below
                outcome["error"] = error

        worker = threading.Thread(target=run, name="a3-approval", daemon=True)
        worker.start()
        worker.join(timeout)
        if worker.is_alive():
            self.abandoned = True
            raise PinError(
                f"The A3 was not authorised within {timeout:g} s (was the certificate app's window answered?); "
                "not retrying. Answer it on the next login."
            )
        if "error" in outcome:
            raise outcome["error"]
        return outcome["value"]

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

    def sign_digest_info(self, digest_info):
        """RSA PKCS#1 v1.5 over a DigestInfo built by the caller (CKM_RSA_PKCS); used by TLS client auth."""
        self.login()
        mechanism = self._pkcs11.Mechanism(self._pkcs11.CKM_RSA_PKCS, None)
        key = self._private_key()
        return bytes(self._on_token(lambda: self.session.sign(key, bytes(digest_info), mechanism)))

    def sign(self, mensagem):
        """MD5withRSA over the challenge, as PJeOffice does."""
        if isinstance(mensagem, str):
            mensagem = mensagem.encode("utf-8")
        return self.sign_digest_info(md5_digest_info(mensagem))

    def chain64(self):
        import base64

        blobs = [cert.public_bytes(Encoding.DER) for cert in self.chain]
        return base64.b64encode(pkipath_encode(blobs)).decode("ascii")

    def pkipath64(self):
        return java_pkipath64(self.chain)

    def close(self):
        if self.abandoned:
            return
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

    with held_token() as token:
        leaf = token.chain[0]
        print(f"token: {token.serial}")
        print(f"holder: {leaf.subject.rfc4514_string()}")
        print(f"valid until: {leaf.not_valid_after_utc:%Y-%m-%d}; chain: {len(token.chain)} certificates")
        print(f"unsafe PIN state: {token.unsafe_pin_state() or 'none'}")
        if token.protected:
            print("PIN: none here; the token or its app asks for it (desktopID: its window, PIN and e-token, or push)")
        else:
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
    check.add_argument(
        "--sign", action="store_true", help="also log in once (or ask the token's app) and verify a test signature"
    )
    args = parser.parse_args()
    if args.command == "pin-set":
        _pin_set()
    else:
        _check(args.sign)


if __name__ == "__main__":
    main()
