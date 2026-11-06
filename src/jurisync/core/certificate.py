"""The A1 certificate (``.p12``/``.pfx``): loading it and presenting it in a mutual-TLS handshake.

Locally the file comes from the path in an environment variable; hosted, from the office's vault bytes
(``credentials.read_file``). The PEM pair that ``curl_cffi`` needs for ``cert=`` is written to a private temporary
directory only for the duration of the block, never kept on disk.
"""

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from jurisync.core import credentials

DEFAULT_FILE = "PJE_A1_P12"
DEFAULT_PIN = "PJE_A1_PIN"


def credential_names(prefix=""):
    """``(file_name, pin_name)`` of the A1 credentials: ``<prefix>_A1_P12``/``<prefix>_A1_PIN`` when the account sets
    that file (a certificate for one system only), otherwise the shared ``PJE_A1_P12``/``PJE_A1_PIN``."""
    if prefix and credentials.has(f"{prefix}_A1_P12"):
        own_pin = f"{prefix}_A1_PIN"
        return f"{prefix}_A1_P12", own_pin if credentials.has(own_pin) else DEFAULT_PIN
    return DEFAULT_FILE, DEFAULT_PIN


def load_a1(p12_path, pin, *, name=DEFAULT_FILE):
    """Load ``(key, cert, chain)`` from ``p12_path``, or from the account's ``name`` credential when it is None."""
    from cryptography.hazmat.primitives.serialization import pkcs12

    data = Path(p12_path).read_bytes() if p12_path else credentials.read_file(name)
    key, cert, chain = pkcs12.load_key_and_certificates(data, (pin or "").encode("utf-8"))
    if key is None or cert is None:
        raise ValueError("A1 file has no private key/certificate (wrong PIN?)")
    return key, cert, list(chain or [])


def write_pem(directory, key, cert, chain):
    """``(cert.pem, key.pem)`` paths. cert.pem carries the intermediates: the handshake fails without the chain."""
    from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

    cert_path, key_path = Path(directory) / "cert.pem", Path(directory) / "key.pem"
    cert_path.write_bytes(b"".join(item.public_bytes(Encoding.PEM) for item in (cert, *chain)))
    key_path.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    key_path.chmod(0o600)
    return str(cert_path), str(key_path)


@contextmanager
def client_certificate(key, cert, chain):
    """The ``cert=`` pair for a TLS handshake, valid only inside the block."""
    with tempfile.TemporaryDirectory() as directory:
        os.chmod(directory, 0o700)
        yield write_pem(directory, key, cert, chain)
