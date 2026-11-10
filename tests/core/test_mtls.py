"""Offline checks for the token-backed mTLS client (renegotiation and the token key)."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from tlslite.constants import CipherSuite, ContentType, ExtensionType, HandshakeType
from tlslite.messages import ClientHello, Finished
from tlslite.utils.codec import Parser

from jurisync.core import mtls


def _raw_pkcs1(key, digest_info):
    numbers = key.private_numbers()
    size = (key.key_size + 7) // 8
    block = b"\x00\x01" + b"\xff" * (size - len(digest_info) - 3) + b"\x00" + bytes(digest_info)
    return pow(int.from_bytes(block, "big"), numbers.d, numbers.public_numbers.n).to_bytes(size, "big")


class TokenKeyChecks(unittest.TestCase):
    def test_tls_signature_is_made_by_the_token_and_verifies(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        signer = SimpleNamespace(
            chain=[SimpleNamespace(public_key=key.public_key)],
            sign_digest_info=lambda digest_info: _raw_pkcs1(key, digest_info),
        )
        token_key = mtls.TokenRSAKey(signer)
        message = b"handshake transcript"
        digest = hashes.Hash(hashes.SHA256())
        digest.update(message)
        signature = token_key.sign(bytearray(digest.finalize()), padding="pkcs1", hashAlg="sha256")
        key.public_key().verify(bytes(signature), message, padding.PKCS1v15(), hashes.SHA256())
        self.assertEqual(token_key.signatures, 1)
        with self.assertRaises(NotImplementedError):
            token_key.write()


class RenegotiationChecks(unittest.TestCase):
    def connection(self):
        conn = mtls.RenegotiatingTLSConnection.__new__(mtls.RenegotiatingTLSConnection)
        conn.client_credentials = None
        conn.renegotiations = 0
        conn._client_verify_data = None
        conn._renegotiating = False
        return conn

    def test_renegotiation_hello_carries_client_verify_data_and_no_scsv(self):
        conn = self.connection()
        sent = []
        base = mtls.TLSConnection._sendMsg
        mtls.TLSConnection._sendMsg = lambda self, msg, *a: iter(sent.append(msg) or ())
        try:
            list(conn._sendMsg(Finished((3, 3)).create(bytearray(b"v" * 12))))
            hello = ClientHello().create(
                (3, 3), bytearray(32), bytearray(0), [CipherSuite.TLS_EMPTY_RENEGOTIATION_INFO_SCSV, 0x002F]
            )
            conn._renegotiating = True
            list(conn._sendMsg(hello))
        finally:
            mtls.TLSConnection._sendMsg = base
        hello = sent[-1]
        self.assertNotIn(CipherSuite.TLS_EMPTY_RENEGOTIATION_INFO_SCSV, hello.cipher_suites)
        ext = hello.getExtension(ExtensionType.renegotiation_info)
        self.assertEqual(ext.renegotiated_connection, bytearray(b"v" * 12))

    def test_hello_request_without_a_certificate_is_refused(self):
        conn = self.connection()
        conn.session = object()
        record = (
            SimpleNamespace(type=ContentType.handshake),
            Parser(bytearray([HandshakeType.hello_request, 0, 0, 0])),
        )
        base = mtls.TLSConnection._getNextRecord
        mtls.TLSConnection._getNextRecord = lambda self: iter([record])
        try:
            with self.assertRaisesRegex(RuntimeError, "renegotiation"):
                list(conn._getNextRecord())
        finally:
            mtls.TLSConnection._getNextRecord = base

    def test_application_records_pass_through(self):
        conn = self.connection()
        conn.session = object()
        record = (SimpleNamespace(type=ContentType.application_data), Parser(bytearray(b"\x00data")))
        base = mtls.TLSConnection._getNextRecord
        mtls.TLSConnection._getNextRecord = lambda self: iter([record])
        try:
            self.assertEqual(list(conn._getNextRecord()), [record])
        finally:
            mtls.TLSConnection._getNextRecord = base


class ResponseChecks(unittest.TestCase):
    def test_chunked_response_and_cookies_are_parsed(self):
        raw = (
            b"HTTP/1.1 302 Found\r\nLocation: /next\r\nSet-Cookie: A=1; Path=/\r\nTransfer-Encoding: chunked\r\n\r\n"
            b"3\r\nabc\r\n0\r\n\r\n"
        )
        response = mtls._parse("https://host.jus.br/start", raw)
        self.assertEqual((response.status, response.body, response.cookies()), (302, b"abc", {"A": "1"}))
        self.assertEqual(response.location, "https://host.jus.br/next")

    def test_empty_reply_is_an_error_not_a_crash(self):
        with self.assertRaisesRegex(RuntimeError, "without an HTTP response"):
            mtls._parse("https://host.jus.br/", b"")

    def test_only_https_is_accepted(self):
        with self.assertRaises(ValueError):
            mtls.fetch("http://host.jus.br/", signer=Mock())


if __name__ == "__main__":
    unittest.main()
