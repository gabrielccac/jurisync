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


def _issued(subject, issuer_name, issuer_key, ca):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer_name or subject)]))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .sign(issuer_key or key, hashes.SHA256())
    )
    return key, cert


class JavaPkiPathChecks(unittest.TestCase):
    """PJeOffice's ``cadeiaCertificado`` is Java's PkiPath: root first, so Java decodes the holder at index 0."""

    @classmethod
    def setUpClass(cls):
        root_key, cls.root = _issued("Raiz", None, None, True)
        mid_key, cls.mid = _issued("AC Intermediaria", "Raiz", root_key, True)
        _, cls.leaf = _issued("Titular", "AC Intermediaria", mid_key, False)

    def _decoded(self, value):
        from cryptography.hazmat.primitives.serialization import Encoding

        der = base64.b64decode(value)
        body, certs = der[2 + (der[1] & 0x7F if der[1] & 0x80 else 0) :], []
        for cert in (self.root, self.mid, self.leaf):
            blob = cert.public_bytes(Encoding.DER)
            certs.append((body.index(blob), cert))
        return [cert for _, cert in sorted(certs, key=lambda item: item[0])]

    def test_issuer_path_orders_a_shuffled_p12_chain_from_the_holder(self):
        self.assertEqual(native_signer.issuer_path(self.leaf, [self.root, self.mid]), [self.leaf, self.mid, self.root])
        unrelated = _issued("Outra", None, None, True)[1]
        self.assertEqual(native_signer.issuer_path(self.leaf, [unrelated]), [self.leaf])

    def test_java_pkipath_is_written_root_first(self):
        value = native_signer.java_pkipath64([self.leaf, self.mid, self.root])
        self.assertEqual(self._decoded(value), [self.root, self.mid, self.leaf])


if __name__ == "__main__":
    unittest.main()
