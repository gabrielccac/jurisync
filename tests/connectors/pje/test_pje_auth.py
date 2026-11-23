import unittest
from unittest import mock

from jurisync.connectors.pje.auth.sessions import auth_method


class AuthMethodTest(unittest.TestCase):
    def test_trf5_uses_the_password_flow_only_without_a_certificate(self):
        password = {
            "PJE_TRF5_1G_USERNAME": "u",
            "PJE_TRF5_1G_PASSWORD": "p",
            "PJE_TRF5_1G_2FA_DATA": "JBSWY3DPEHPK3PXP",
        }
        with mock.patch.dict("os.environ", password, clear=True):
            self.assertEqual(auth_method("trf5-1g"), "password_totp")
        with mock.patch.dict("os.environ", {**password, "PJE_A1_P12": "/x/a1.p12"}, clear=True):
            self.assertEqual(auth_method("trf5-1g"), "certificate_totp")
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(auth_method("trf5-1g"), "certificate_totp")
        with mock.patch.dict("os.environ", {"PJE_1G_USERNAME": "u"}, clear=True):
            self.assertEqual(auth_method("trf1-1g"), "password_totp")

    def test_other_pje_portals_use_the_certificate_only_when_they_opt_in(self):
        certificate = {
            "PJE_A1_P12": "/x/a1.p12",
            "PJE_1G_USERNAME": "u",
            "PJE_1G_PASSWORD": "p",
            "PJE_1G_2FA_DATA": "s",
        }
        with mock.patch.dict("os.environ", certificate, clear=True):
            self.assertEqual(auth_method("trf1-1g"), "password_totp")
        with mock.patch.dict("os.environ", {**certificate, "PJE_1G_CERT": "true"}, clear=True):
            self.assertEqual(auth_method("trf1-1g"), "certificate_totp")
        with mock.patch.dict("os.environ", {"PJE_1G_CERT": "true"}, clear=True):
            self.assertEqual(auth_method("trf1-1g"), "password_totp")


class PasswordWithoutAuthenticatorTest(unittest.TestCase):
    """eliane: TRT username and password, no authenticator export, no A1. The error must name the authenticator."""

    ENV = {"TRT7_1G_USERNAME": "u", "TRT7_1G_PASSWORD": "p"}

    def test_a_password_without_authenticator_is_still_the_password_login(self):
        with mock.patch.dict("os.environ", self.ENV, clear=True):
            self.assertEqual(auth_method("trt7-1g"), "password_totp")
        with mock.patch.dict("os.environ", {**self.ENV, "PJE_A1_P12": "/x/a1.p12"}, clear=True):
            self.assertEqual(auth_method("trt7-1g"), "certificate_totp")

    def test_the_login_names_the_missing_authenticator_variables(self):
        from jurisync.connectors.pje import process
        from jurisync.core.session_pool import MissingLogin, login_reason

        with mock.patch.dict("os.environ", self.ENV, clear=True):
            with mock.patch.object(process, "load_session", return_value=None):
                with self.assertRaises(MissingLogin) as raised:
                    process.login_portal("trt7-1g")
        message = str(raised.exception)
        self.assertEqual(login_reason(raised.exception), "login_missing")
        self.assertIn("TRT7_1G_2FA_DATA", message)
        self.assertIn("TRT7_1G_2FA_QR", message)
        self.assertIn("authenticator", message)
        self.assertNotIn("A1_PIN", message)

    def test_without_any_login_both_ways_are_named(self):
        from jurisync.connectors.pje.auth.certificate import cert_login
        from jurisync.core.session_pool import MissingLogin

        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(MissingLogin) as raised:
                cert_login("trt7-1g")
        message = str(raised.exception)
        for name in ("TRT7_1G_USERNAME", "TRT7_1G_2FA_DATA", "PJE_A1_P12", "PJE_A1_PIN"):
            self.assertIn(name, message)


class CertificateTwofaTest(unittest.TestCase):
    def test_totp_is_optional_and_trf5_keeps_its_earlier_variable_names(self):
        from jurisync.connectors.pje.auth.certificate import _twofa

        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(_twofa("trf1-1g"))
        with mock.patch.dict("os.environ", {"PJE_TRF5_2FA_DATA": "JBSWY3DPEHPK3PXP"}, clear=True):
            self.assertIsNotNone(_twofa("trf5-1g"))
            self.assertIsNone(_twofa("trf1-2g"))
