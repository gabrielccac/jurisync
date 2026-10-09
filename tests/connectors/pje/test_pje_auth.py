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
