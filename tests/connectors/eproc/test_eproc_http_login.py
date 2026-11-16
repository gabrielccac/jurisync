"""Offline checks for the classic eproc login over HTTP (password, A3 at login_cert.php, then TOTP)."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from jurisync import portals
from jurisync.connectors.eproc import client, http_login
from jurisync.core.mtls import Response

BASE = "https://eproc1g.tjmg.jus.br/eproc/"
LOGIN_FORM = '<form id="frmLogin"><input name="txtUsuario"><input name="pwdSenha" type="password"></form>'
OTP_FORM = '<form id="frmLogin"><input name="txtAcessoCodigo"><input name="hdnAcao" value="2fa_validar"></form>'
PANEL = BASE + "controlador.php?acao=painel_adv_listar&acao_origem=principal&hash=h"
ACCOUNT = {"type": "totp", "secret": "JBSWY3DPEHPK3PXP"}


def reply(url, status=200, text="", location=None):
    return SimpleNamespace(url=url, status_code=status, text=text, headers={"location": location} if location else {})


def session_with(gets, posts):
    session = Mock()
    session.get.side_effect = gets
    session.post.side_effect = posts
    session.cookies.jar = [SimpleNamespace(name="PHPSESSID", value="p", domain="eproc1g.tjmg.jus.br")]
    return session


class ClassicFormDetection(unittest.TestCase):
    def test_portal_without_the_classic_form_sends_nothing(self):
        session = session_with([reply(BASE, text="<html>bot challenge</html>")], [])
        with patch.object(http_login.requests, "Session", return_value=session):
            with self.assertRaises(http_login.NotClassicLogin):
                http_login.login("u", "p", ACCOUNT, portal="tjmg-1g", signer=Mock())
        session.post.assert_not_called()


class CertificateFlow(unittest.TestCase):
    def test_password_then_a3_then_totp_reach_the_panel(self):
        cert = Response(
            BASE + "priv/login_cert.php?acao_origem=",
            302,
            [("Location", "../externo_controlador.php?acao=entrar_cert")],
            certificate_sent=True,
        )
        session = session_with(
            gets=[
                reply(BASE, text=LOGIN_FORM),
                reply(
                    BASE + "externo_controlador.php?acao=entrar_cert",
                    302,
                    location="externo_controlador.php?acao=principal",
                ),
                reply(BASE + "externo_controlador.php?acao=principal", text=OTP_FORM),
                reply(
                    BASE + "externo_controlador.php?acao=entrar_cert", 302, location="controlador.php?acao=principal"
                ),
                reply(BASE + "controlador.php?acao=principal", 302, location=PANEL),
                reply(PANEL, text="Painel do Advogado"),
            ],
            posts=[
                reply(
                    BASE + "externo_controlador.php?acao=login_cert", 302, location="priv/login_cert.php?acao_origem="
                ),
                reply(BASE + "index.php", 302, location=BASE + "externo_controlador.php?acao=entrar_cert"),
            ],
        )
        with (
            patch.object(http_login.requests, "Session", return_value=session),
            patch.object(http_login.mtls, "fetch", return_value=cert) as fetch,
        ):
            result = http_login.login("u", "p", ACCOUNT, portal="tjmg-1g", signer=Mock())
        self.assertEqual(result[2], PANEL)
        self.assertTrue(fetch.call_args.args[0].endswith("/eproc/priv/login_cert.php?acao_origem="))
        password_post, otp_post = session.post.call_args_list
        self.assertTrue(password_post.args[0].endswith("externo_controlador.php?acao=login_cert"))
        self.assertEqual(password_post.kwargs["data"]["hdnAcao"], "login")
        self.assertEqual(otp_post.kwargs["data"]["hdnAcao"], "2fa_validar")
        self.assertEqual(len(otp_post.kwargs["data"]["txtAcessoCodigo"]), 6)

    def test_refused_certificate_is_reported(self):
        cert = Response(
            BASE + "priv/login_cert.php?acao_origem=",
            302,
            [("Location", "../externo_controlador.php?acao=principal&erro=erro_certificado")],
            certificate_sent=True,
        )
        session = session_with(
            gets=[reply(BASE, text=LOGIN_FORM)],
            posts=[
                reply(
                    BASE + "externo_controlador.php?acao=login_cert", 302, location="priv/login_cert.php?acao_origem="
                )
            ],
        )
        with (
            patch.object(http_login.requests, "Session", return_value=session),
            patch.object(http_login.mtls, "fetch", return_value=cert),
        ):
            with self.assertRaisesRegex(RuntimeError, "erro_certificado"):
                http_login.login("u", "p", ACCOUNT, portal="tjmg-1g", signer=Mock())


class ClientRouting(unittest.TestCase):
    def test_falls_back_to_the_browser_only_before_credentials_are_sent(self):
        with (
            patch.object(client, "uses_a3", return_value=False),
            patch.object(client.portals, "eproc", return_value=SimpleNamespace(uses_certificate=lambda: False)),
            patch.object(client, "_credentials", return_value=("u", "p")),
            patch.object(client, "portal_totp_account", return_value=ACCOUNT),
            patch.object(http_login, "login", side_effect=http_login.NotClassicLogin("no form")),
            patch.object(client, "login", return_value="browser") as browser,
        ):
            self.assertEqual(client.authenticate("trf6-1g"), "browser")
        browser.assert_called_once()


if __name__ == "__main__":
    unittest.main()


TJTO = "https://eproc1.tjto.jus.br/eprocV2_prod_1grau/"
TJTO_CAPTCHA_URL = TJTO + "externo_controlador.php?acao=principal&acao_retorno=login"
# Sanitized TJTO captcha screen: the picture is a placeholder data URI.
TJTO_CAPTCHA = (
    '<form id="frmLogin" action="index.php"><input type="hidden" id="hdnStrTela" value="captcha">'
    '<div id="divInfraCaptcha"><label id="lblInfraCaptcha"><img src="data:image/png;base64,UElD"></label>'
    '<input name="txtInfraCaptcha" maxlength="4"><input type="hidden" name="hdnInfraCaptcha" value="0"></div>'
    '<input type="hidden" name="hdnAcao" value="login"><input type="hidden" name="hdnDebug" value=""></form>'
)
TJTO_PANEL = TJTO + "controlador.php?acao=painel_adv_listar&hash=h"


def tjto_session(gets, posts):
    session = session_with(gets, posts)
    session.cookies.jar = [SimpleNamespace(name="PHPSESSID", value="p", domain="eproc1.tjto.jus.br")]
    return session


class InfraCaptchaFlow(unittest.TestCase):
    def login(self, session, answers):
        solve = Mock(side_effect=answers)
        with (
            patch.object(http_login.requests, "Session", return_value=session),
            patch.object(http_login.image_captcha, "solve", solve),
        ):
            return http_login.login("TO001234", "p", ACCOUNT, portal="tjto-1g"), solve

    def test_password_to_index_then_captcha_redraw_and_answer_then_totp(self):
        session = tjto_session(
            gets=[
                reply(TJTO, text=LOGIN_FORM),
                reply(TJTO_CAPTCHA_URL, text=TJTO_CAPTCHA),  # after the password
                reply(TJTO_CAPTCHA_URL, text=TJTO_CAPTCHA),  # redrawn after an unreadable picture
                reply(TJTO_CAPTCHA_URL, text=TJTO_CAPTCHA),  # a wrong code draws a new picture
                reply(TJTO + "externo_controlador.php?acao=principal", text=OTP_FORM),
                reply(TJTO_PANEL, text="Painel do Advogado"),
            ],
            posts=[
                reply(TJTO + "index.php", 302, location="externo_controlador.php?acao=principal&acao_retorno=login"),
                reply(TJTO + "index.php", 302, location="externo_controlador.php?acao=principal&acao_retorno=login"),
                reply(TJTO + "index.php", 302, location="externo_controlador.php?acao=principal"),
                reply(TJTO + "index.php", 302, location=TJTO_PANEL),
            ],
        )
        unreadable = http_login.image_captcha.UnreadableCaptcha("no code")
        result, solve = self.login(session, [unreadable, ("aB3x", "local"), ("Zz99", "model")])
        self.assertEqual(result[2], TJTO_PANEL)
        self.assertEqual([c.kwargs["attempt"] for c in solve.call_args_list], [0, 0, 1])
        self.assertEqual(solve.call_args.args[0], b"PIC")
        password, wrong, right, otp = session.post.call_args_list
        self.assertTrue(password.args[0].endswith("/eprocV2_prod_1grau/index.php"))  # not acao=login_cert
        self.assertEqual(password.kwargs["data"]["hdnAcao"], "login")
        self.assertEqual(
            right.kwargs["data"],
            {"txtInfraCaptcha": "Zz99", "hdnInfraCaptcha": "1", "hdnAcao": "login", "hdnDebug": ""},
        )
        self.assertEqual(wrong.kwargs["data"]["txtInfraCaptcha"], "aB3x")
        self.assertEqual(otp.kwargs["data"]["hdnAcao"], "2fa_validar")

    def test_refused_password_after_the_captcha_is_reported(self):
        refused = TJTO + "externo_controlador.php?acao=principal&msg=Senha+ou+usu%E1rio+Inv%E1lidos"
        session = tjto_session(
            gets=[
                reply(TJTO, text=LOGIN_FORM),
                reply(TJTO_CAPTCHA_URL, text=TJTO_CAPTCHA),
                reply(refused, text=LOGIN_FORM),
            ],
            posts=[
                reply(TJTO + "index.php", 302, location=TJTO_CAPTCHA_URL),
                reply(TJTO + "index.php", 302, location=refused),
            ],
        )
        with self.assertRaisesRegex(
            http_login.LoginRejected, "refused the user or password.*Senha ou usuário Inválidos"
        ):
            self.login(session, [("aB3x", "local")])
        self.assertEqual(session.post.call_count, 2)

    def test_stops_after_three_wrong_codes(self):
        session = tjto_session(
            gets=[reply(TJTO, text=LOGIN_FORM)] + [reply(TJTO_CAPTCHA_URL, text=TJTO_CAPTCHA)] * 4,
            posts=[reply(TJTO + "index.php", 302, location=TJTO_CAPTCHA_URL)] * 4,
        )
        with self.assertRaisesRegex(RuntimeError, "not accepted after 3 answers"):
            self.login(session, [("aaaa", "local"), ("bbbb", "model"), ("cccc", "model")])
        self.assertEqual(session.post.call_count, 4)  # the password and three codes

    def test_a_username_outside_the_stated_format_is_not_sent(self):
        session = tjto_session(gets=[reply(TJTO, text=LOGIN_FORM)], posts=[])
        with (
            patch.object(http_login.requests, "Session", return_value=session),
            self.assertRaisesRegex(RuntimeError, "UF \\+ OAB.*TJTO_1G_USERNAME.*nothing was sent"),
        ):
            http_login.login("12345678901", "p", ACCOUNT, portal="tjto-1g")
        session.post.assert_not_called()

    def test_other_classic_portals_still_post_to_login_cert(self):
        self.assertEqual(portals.eproc("tjmg-1g").flavor, "")
        self.assertEqual(portals.eproc("tjto-2g").flavor, http_login.INFRA_CAPTCHA)
