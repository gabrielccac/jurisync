"""Offline checks for the native SSO signer (self-signed fixture, no A1)."""

import base64
import datetime
import os
import tempfile
import unittest
from unittest.mock import Mock

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.serialization import BestAvailableEncryption, NoEncryption, pkcs12
from cryptography.x509.oid import NameOID

from jurisync.connectors.pje.auth import signer as native_signer


def make_p12(pin):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Teste")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    data = pkcs12.serialize_key_and_certificates(
        b"teste", key, cert, None, BestAvailableEncryption(pin.encode()) if pin else NoEncryption()
    )
    path = os.path.join(tempfile.mkdtemp(), "t.p12")
    with open(path, "wb") as fh:
        fh.write(data)
    return path


class NativeSignerChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p12 = make_p12("1234")
        cls.key, cls.cert, cls.chain = native_signer.load_a1(cls.p12, "1234")

    def test_wrong_pin_rejected(self):
        with self.assertRaises(Exception):
            native_signer.load_a1(self.p12, "errada")

    def test_sign_verifies(self):
        message = "0.9582867978871357"
        signed = native_signer.sign_mensagem(message, self.key)
        self.cert.public_key().verify(signed, message.encode(), padding.PKCS1v15(), hashes.MD5())
        self.assertEqual(len(signed), self.key.key_size // 8)

    def test_payload_shape(self):
        payload = native_signer.build_payload("t1", "m", b"signature", native_signer.certificate_chain64(self.cert, []))
        self.assertEqual(set(payload), {"uuid", "mensagem", "assinatura", "certChain"})
        self.assertEqual(payload["uuid"], "t1")
        self.assertEqual(base64.b64decode(payload["assinatura"]), b"signature")
        base64.b64decode(payload["certChain"])

    def test_post_result_uses_sso_session(self):
        session = Mock()
        session.post.return_value.status_code = 200
        native_signer.post_result(session, {"uuid": "t1"})
        self.assertEqual(session.post.call_args.args[0], native_signer.SSO_CALLBACK)
        self.assertEqual(session.post.call_args.kwargs["json"], {"uuid": "t1"})
        session.post.return_value.status_code = 400
        with self.assertRaises(RuntimeError):
            native_signer.post_result(session, {"uuid": "t1"})

    def test_chain64_includes_intermediates(self):
        chain64 = native_signer.certificate_chain64(self.cert, self.chain)
        self.assertTrue(base64.b64decode(chain64).startswith(b"0\x82"))


if __name__ == "__main__":
    unittest.main()
