"""Offline checks for the eproc A3 login (Keycloak X.509 host and Certisign authenticator)."""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from jurisync.connectors.eproc import a3
from jurisync.connectors.eproc.client import uses_a3
from jurisync.core.mtls import Response

PANEL = "https://eproc1g.tjrs.jus.br/eproc/controlador.php?acao=painel_adv_listar"


def page(url, text):
    return SimpleNamespace(url=url, text=text, status_code=200)


class SelectionChecks(unittest.TestCase):
    def test_a3_is_opt_in(self):
        with patch.dict(os.environ, {"EPROC_AUTH": "", "TJRS_1G_AUTH": "a3", "TJSC_1G_AUTH": ""}):
            self.assertTrue(uses_a3("tjrs-1g"))
            self.assertFalse(uses_a3("tjsc-1g"))
        with patch.dict(os.environ, {"EPROC_AUTH": "A3", "TJRS_1G_AUTH": "", "TJSC_1G_AUTH": ""}):
            self.assertTrue(uses_a3("tjsc-1g"))


class MessageChecks(unittest.TestCase):
    def test_eproc_msg_query_is_decoded(self):
        url = "https://eproc1g.tjsc.jus.br/eproc/externo_controlador.php?acao=principal&msg=N%E3o+foi+localizado"
        self.assertEqual(a3.eproc_message(url, ""), "Não foi localizado")

    def test_maintenance_notices_are_not_reported_as_the_error(self):
        text = '<div class="alert alert-warning">Manutenção programada</div><span id="input-error">Invalid user</span>'
        self.assertEqual(a3.eproc_message("https://sso.tjsc.jus.br/x", text), "Invalid user")


class KeycloakFlowChecks(unittest.TestCase):
    def test_certificate_host_must_be_a_sibling_court_host(self):
        login_page = page("https://keycloak-eks.tjrs.jus.br/realms/eproc/protocol/openid-connect/auth?x=1", "")
        with self.assertRaisesRegex(RuntimeError, "Unexpected certificate host"):
            a3._keycloak_x509(Mock(), login_page, "evil.example.com", Mock())

    def test_x509_confirmation_then_otp_reach_the_panel(self):
        login_page = page(
            "https://keycloak-eks.tjrs.jus.br/realms/eproc/protocol/openid-connect/auth?x=1",
            '<button id="kc-login-certificate" onclick="document.location.host = \'keycloak-httpd-mtls.tjrs.jus.br\'">',
        )
        confirm = Response(
            "https://keycloak-httpd-mtls.tjrs.jus.br/realms/eproc/protocol/openid-connect/auth?x=1",
            200,
            [("Set-Cookie", "KC_RESTART=r; Path=/")],
            b'<form id="kc-x509-login-info" action="https://keycloak-eks.tjrs.jus.br/realms/eproc/login-actions/'
            b'authenticate?session_code=s"></form>',
            certificate_sent=True,
        )
        otp = page(
            "https://keycloak-eks.tjrs.jus.br/realms/eproc/login-actions/authenticate",
            '<form id="kc-otp-login-form" action="/realms/eproc/login-actions/authenticate?execution=e"></form>',
        )
        panel = page(PANEL, "painel")
        session = Mock()
        session.get.return_value = login_page
        session.post.side_effect = [otp, panel]
        session.cookies.jar = [SimpleNamespace(name="PHPSESSID", value="p", domain="eproc1g.tjrs.jus.br")]
        with (
            patch.object(a3.requests, "Session", return_value=session),
            patch.object(a3.mtls, "fetch", return_value=confirm) as fetch,
            patch.object(a3, "portal_totp_account", return_value={"type": "totp", "secret": "JBSWY3DPEHPK3PXP"}),
        ):
            result = a3.login("tjrs-1g", signer=Mock())
        self.assertEqual(result[2], PANEL)
        self.assertEqual(fetch.call_args.args[0].split("/")[2], "keycloak-httpd-mtls.tjrs.jus.br")
        confirm_post, otp_post = session.post.call_args_list
        self.assertIn("login-actions/authenticate?session_code=s", confirm_post.args[0])
        self.assertEqual(len(otp_post.kwargs["data"]["otp"]), 6)


class CertisignFlowChecks(unittest.TestCase):
    def test_signed_token_is_posted_back_to_the_portal_only(self):
        config = SimpleNamespace(host="eproc1g.tjsc.jus.br")
        login_page = page("https://eproc1g.tjsc.jus.br/eproc/index.php", "")
        link = "https://autenticador.certisign.com.br/CertisignLogin/certificado/login?id=1"
        reply = Response(
            link,
            200,
            [],
            b'<form id="submitForm" action="https://evil.example.com/collect" method="post">'
            b'<input type="hidden" name="cb" value="token"></form>',
            certificate_sent=True,
        )
        session = Mock()
        with (
            patch.object(a3.mtls, "fetch", return_value=reply),
            self.assertRaisesRegex(RuntimeError, "unexpected host"),
        ):
            a3._certisign(session, login_page, link, config, Mock())
        session.post.assert_not_called()

    def test_certisign_error_is_reported(self):
        config = SimpleNamespace(host="eproc1g.tjsc.jus.br")
        login_page = page("https://eproc1g.tjsc.jus.br/eproc/index.php", "")
        link = "https://autenticador.certisign.com.br/CertisignLogin/certificado/login?id=1"
        reply = Response(
            link,
            200,
            [],
            b'<form action="https://eproc1g.tjsc.jus.br/eproc/externo_controlador.php?acao=login_certificado">'
            b'<input type="hidden" name="cb" value="Erro ao obter Certificado."></form>',
            certificate_sent=True,
        )
        with patch.object(a3.mtls, "fetch", return_value=reply), self.assertRaisesRegex(RuntimeError, "Certisign"):
            a3._certisign(Mock(), login_page, link, config, Mock())


if __name__ == "__main__":
    unittest.main()
