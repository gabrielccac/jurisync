"""Offline checks for the A3 (PKCS#11) certificate login, generic over PJe portals."""

import datetime
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

from jurisync import portals
from jurisync.connectors.pje.auth import a3
from jurisync.connectors.pje.auth import certificate as cert_login
from jurisync.connectors.pje.auth.sessions import auth_method, cache_identity


def _cert(subject, issuer, key, signing_key, ca):
    now = datetime.datetime.now(datetime.UTC)
    return (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer)]))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .sign(signing_key, hashes.SHA256())
    )


def _raw_pkcs1(key, digest_info):
    """What CKM_RSA_PKCS does inside the token: EMSA-PKCS1-v1_5 padding, then the private RSA operation."""
    numbers = key.private_numbers()
    size = (key.key_size + 7) // 8
    block = b"\x00\x01" + b"\xff" * (size - len(digest_info) - 3) + b"\x00" + digest_info
    return pow(int.from_bytes(block, "big"), numbers.d, numbers.public_numbers.n).to_bytes(size, "big")


class A3SignatureChecks(unittest.TestCase):
    def test_md5_digest_info_signed_by_the_token_verifies_as_md5_with_rsa(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        message = b"Y8fHLWlTEgLBexS0Bjn4PA"
        signature = _raw_pkcs1(key, a3.md5_digest_info(message))
        key.public_key().verify(signature, message, padding.PKCS1v15(), hashes.MD5())

    def test_chain_starts_at_the_holder_and_climbs_to_the_root(self):
        root_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        mid_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        root = _cert("Root", "Root", root_key, root_key, True)
        mid = _cert("AC", "Root", mid_key, root_key, True)
        leaf = _cert("Holder", "AC", leaf_key, mid_key, False)
        ident, chain = a3.certificate_chain([(b"r", root), (b"l", leaf), (b"m", mid)])
        self.assertEqual(ident, b"l")
        self.assertEqual(chain, [leaf, mid, root])

    def test_two_holder_certificates_are_refused(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        one = _cert("A", "Root", key, key, False)
        two = _cert("B", "Root", key, key, False)
        with self.assertRaises(RuntimeError):
            a3.certificate_chain([(b"1", one), (b"2", two)])


class A3PinChecks(unittest.TestCase):
    def test_missing_pin_is_a_pin_error_with_the_setup_command(self):
        with patch.dict(os.environ, {"PJE_A3_PIN": ""}), patch.object(a3, "_keyring_pin", return_value=None):
            with self.assertRaisesRegex(a3.PinError, "pin-set"):
                a3.stored_pin("SERIAL")

    def test_unsafe_token_state_blocks_the_login_before_any_pin_is_sent(self):
        token = a3.A3Signer.__new__(a3.A3Signer)
        token.logged_in = False
        token._pkcs11 = Mock()
        token.session = Mock()
        token.unsafe_pin_state = lambda: ["a wrong PIN was already tried"]
        with self.assertRaises(a3.PinError):
            token.login()
        token.session.login.assert_not_called()


class A3SelectionChecks(unittest.TestCase):
    def test_a3_is_opt_in_per_portal_or_for_all(self):
        with patch.dict(os.environ, {"PJE_AUTH": "", "PJE_1G_AUTH": "a3", "TJAP_1G_AUTH": ""}):
            self.assertEqual(auth_method("trf1-1g"), "a3_totp")
            self.assertNotEqual(auth_method("tjap-1g"), "a3_totp")
        with patch.dict(os.environ, {"PJE_AUTH": "A3", "PJE_1G_AUTH": "", "TJAP_1G_AUTH": ""}):
            self.assertEqual(auth_method("tjap-1g"), "a3_totp")
            self.assertEqual(auth_method("trf1-2g"), "a3_totp")

    def test_a3_sessions_are_cached_apart_from_password_sessions(self):
        env = {"PJE_AUTH": "", "PJE_1G_AUTH": "", "PJE_1G_USERNAME": "123", "PJE_1G_PASSWORD": "x"}
        env["PJE_1G_2FA_DATA"] = "JBSWY3DPEHPK3PXP"
        with patch.dict(os.environ, env):
            password_identity = cache_identity("trf1-1g")
        with patch.dict(os.environ, {**env, "PJE_1G_AUTH": "a3"}):
            self.assertNotEqual(cache_identity("trf1-1g"), password_identity)


class GenericLoginChecks(unittest.TestCase):
    def test_tjap_authenticated_page_uses_its_own_root(self):
        html = '<a href="/1g/logout.seam">Sair</a><input name="javax.faces.ViewState">'
        response = SimpleNamespace(status_code=200, url="https://pje.tjap.jus.br/1g/home", text=html)
        config = portals.pje("tjap-1g")
        self.assertTrue(cert_login._authenticated(response, config.host, config.root))
        self.assertFalse(cert_login._authenticated(response, config.host))

    def test_login_signs_the_page_challenge_and_skips_otp_when_not_asked(self):
        config = portals.pje("trf1-2g")
        login_page = SimpleNamespace(
            status_code=200,
            url="https://sso.cloud.pje.jus.br/auth/realms/pje/protocol/openid-connect/auth",
            headers={},
            text="<script>autenticar('https://x', 'CHALLENGE')</script>"
            '<form id="loginForm" action="/auth/realms/pje/login-actions/authenticate"></form>',
        )
        home = SimpleNamespace(
            status_code=200,
            url="https://pje2g.trf1.jus.br/pje/home.seam",
            headers={},
            text='<a href="/pje/logout.seam">Sair</a><input name="javax.faces.ViewState">',
        )
        session = Mock()
        session.get.return_value = login_page
        session.post.return_value = home
        signer = Mock()
        signer.sign.return_value = b"sig"
        signer.chain64.return_value = "CHAIN"
        account = Mock(side_effect=AssertionError("OTP must not be read when the SSO does not ask"))
        with patch.object(cert_login.native_signer, "post_result") as post:
            self.assertIs(cert_login._login(session, config, account, signer), session)
        signer.sign.assert_called_once_with("CHALLENGE")
        self.assertEqual(post.call_args.args[1]["mensagem"], "CHALLENGE")
        self.assertEqual(post.call_args.args[1]["certChain"], "CHAIN")
        submitted = session.post.call_args.kwargs["data"]
        self.assertEqual(submitted["pjeoffice-code"], post.call_args.args[1]["uuid"])


if __name__ == "__main__":
    unittest.main()
