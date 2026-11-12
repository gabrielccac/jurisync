import unittest
from unittest import mock

from jurisync import portals
from jurisync.connectors.eproc import certificate
from jurisync.core.http import UnsafeUrlError

LOGIN = "https://eproc-sso.trf4.jus.br/realms/eproc/protocol/openid-connect/auth?scope=openid&state=s"
BUTTON = "<button onclick=\"document.location.host = 'eproc-sso-certificado.trf4.jus.br';\"></button>"


class CertificateLoginTest(unittest.TestCase):
    def setUp(self):
        self.config = portals.eproc("trf4-2g")

    def test_certificate_url_keeps_the_login_page_and_swaps_the_host(self):
        url = certificate._certificate_url(BUTTON, LOGIN, self.config)
        self.assertEqual(url, LOGIN.replace("eproc-sso.", "eproc-sso-certificado."))
        with self.assertRaisesRegex(RuntimeError, "no certificate option"):
            certificate._certificate_url("<html></html>", LOGIN, self.config)

    def test_login_redirects_stay_on_the_portal_sso_and_broker(self):
        for url in (
            "https://eproc.trf4.jus.br/eproc2trf4/index.php",
            LOGIN,
            "https://eproc-sso-certificado.trf4.jus.br/realms/eproc",
            "https://sso.cloud.pje.jus.br/auth/realms/pje",
        ):
            certificate.require_login_url(self.config, url)
        for url in (
            "https://evil.example/collect",
            "https://eproc-sso.evil.example/x",
            "https://eproc-sso.trf4.jus.br.evil.example/x",
            "http://eproc.trf4.jus.br/eproc2trf4/",
        ):
            with self.subTest(url=url), self.assertRaises(UnsafeUrlError):
                certificate.require_login_url(self.config, url)

    def test_forms_are_read_with_their_hidden_fields(self):
        html = '<form id="kc-x509-login-info" action="/auth?a=1"><input name="login" value="x"><input></form>'
        action, fields = certificate._form(html, LOGIN, self.config, "kc-x509-login-info")
        self.assertEqual((action, fields), ("https://eproc-sso.trf4.jus.br/auth?a=1", {"login": "x"}))
        self.assertEqual(certificate._form(html, LOGIN, self.config, "other"), (None, {}))

    def test_a_portal_opts_into_the_certificate_with_its_own_flag(self):
        with mock.patch.dict("os.environ", {"PJE_A1_P12": "/a1.p12", "TRF4_2G_CERT": "true"}, clear=True):
            self.assertTrue(self.config.uses_certificate())
        with mock.patch.dict("os.environ", {"TRF4_2G_CERT": "true"}, clear=True):
            self.assertFalse(self.config.uses_certificate())
        with mock.patch.dict("os.environ", {"PJE_A1_P12": "/a1.p12"}, clear=True):
            self.assertFalse(self.config.uses_certificate())
