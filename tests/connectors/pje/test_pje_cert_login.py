"""Offline checks for the TRF5 certificate login boundary."""

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
                cert_login.trf5_cert_login("JBSWY3DPEHPK3PXP", session, pin="test-pin", p12_path="a1.p12"), session
            )
        load.assert_called_once_with("a1.p12", "test-pin")
        self.assertEqual(login.call_args.args[3:], ("key", "cert", []))

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


if __name__ == "__main__":
    unittest.main()
