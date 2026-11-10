"""Offline checks for the PJe certificate login boundary."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from jurisync.connectors.pje.auth import certificate as cert_login


class CertificateLoginChecks(unittest.TestCase):
    def test_a1_is_loaded_and_passed_to_login(self):
        session = Mock()
        with (
            patch.object(cert_login.native_signer, "load_a1", return_value=("key", "cert", [])) as load,
            patch.object(cert_login, "_login", return_value=session) as login,
        ):
            self.assertIs(
                cert_login.cert_login("trf5-1g", "JBSWY3DPEHPK3PXP", session, pin="test-pin", p12_path="a1.p12"),
                session,
            )
        load.assert_called_once_with("a1.p12", "test-pin")
        signer = login.call_args.args[3]
        self.assertIsInstance(signer, cert_login.native_signer.A1Signer)
        self.assertEqual((signer.key, signer.cert, signer.chain), ("key", "cert", []))

    def test_redirects_and_authenticated_page_are_origin_bound(self):
        redirect = SimpleNamespace(
            status_code=302,
            url="https://sso.cloud.pje.jus.br/auth",
            headers={"Location": "https://evil.example/collect"},
        )
        session = Mock()
        with self.assertRaises(RuntimeError):
            cert_login._follow(session, redirect)
        session.get.assert_not_called()

        html = '<a href="/pje/logout.seam">Sair</a><input name="javax.faces.ViewState">'
        response = SimpleNamespace(status_code=200, url="https://pje1g.trf1.jus.br/pje/home", text=html)
        self.assertFalse(cert_login._authenticated(response, "pje1g.trf5.jus.br"))
        response.url = "https://pje1g.trf5.jus.br/pje/home"
        self.assertTrue(cert_login._authenticated(response, "pje1g.trf5.jus.br"))

    def test_jt_shell_page_counts_as_authenticated(self):
        shell = "<html><body>Painel do advogado - Procurador MARA SUSY</body></html>"
        response = SimpleNamespace(status_code=200, url="https://pje.trt7.jus.br/primeirograu/error.seam", text=shell)
        self.assertTrue(cert_login._authenticated(response, "pje.trt7.jus.br"))
        plain = SimpleNamespace(status_code=200, url="https://pje.trt7.jus.br/primeirograu/error.seam", text="<html></html>")
        self.assertFalse(cert_login._authenticated(plain, "pje.trt7.jus.br"))

    def test_http_redirect_on_portal_host_is_upgraded_to_https(self):
        redirect = SimpleNamespace(
            status_code=302,
            url="https://sso.cloud.pje.jus.br/auth",
            headers={"Location": "http://pje.trt7.jus.br/pjekz/"},
        )
        final = SimpleNamespace(status_code=200, url="https://pje.trt7.jus.br/pjekz/", headers={})
        session = Mock()
        session.get.return_value = final
        self.assertIs(cert_login._follow(session, redirect), final)
        called_url = session.get.call_args.args[0]
        self.assertTrue(called_url.startswith("https://pje.trt7.jus.br/pjekz/"))

    def test_http_redirect_off_portal_hosts_still_fails(self):
        redirect = SimpleNamespace(
            status_code=302,
            url="https://sso.cloud.pje.jus.br/auth",
            headers={"Location": "http://evil.example/collect"},
        )
        with self.assertRaises(RuntimeError):
            cert_login._follow(Mock(), redirect)

    def test_legacy_challenge_extraction(self):
        html = ('<script>ASSINATURA_MENSAGEM = "abc123"; CONVERSATION_ID = "15881";</script>'
                '<form id="assinaturaForm" action="/2g/logarSC.seam">')
        self.assertEqual(
            cert_login._legacy_challenge(html), ("abc123", "/logarPJeOffice.seam?cid=15881")
        )
        self.assertIsNone(cert_login._legacy_challenge("<html></html>"))


if __name__ == "__main__":
    unittest.main()
