"""Offline checks for the PJe certificate login boundary."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from jurisync.connectors.pje.auth import certificate as cert_login


class CertificateLoginChecks(unittest.TestCase):
    def test_a1_is_loaded_and_passed_to_login(self):
        session = Mock()
        with (
            patch.object(cert_login, "load_configured", return_value=("key", "cert", [])) as load,
            patch.object(cert_login, "_login", return_value=session) as login,
        ):
            self.assertIs(
                cert_login.cert_login("trf5-1g", "JBSWY3DPEHPK3PXP", session, pin="test-pin", p12_path="a1.p12"),
                session,
            )
        load.assert_called_once_with(pin="test-pin", p12_path="a1.p12")
        signer = login.call_args.args[3]
        self.assertIsInstance(signer, cert_login.native_signer.A1Signer)
        self.assertEqual((signer.key, signer.cert, signer.chain), ("key", "cert", []))

    def test_redirects_and_authenticated_page_are_origin_bound(self):
        for location in ("https://evil.example/collect", "http://evil.example/collect"):
            with self.subTest(location=location):
                redirect = SimpleNamespace(
                    status_code=302, url="https://sso.cloud.pje.jus.br/auth", headers={"Location": location}
                )
                session = Mock()
                with self.assertRaises(RuntimeError):
                    cert_login._follow(session, redirect)
                session.get.assert_not_called()

    def test_legacy_login_names_an_unknown_certificate(self):
        config, page, signer, session, _ = self._legacy(
            "Erro:Não foi possível realizar a autenticação: Certificado não encont"
        )
        with self.assertRaisesRegex(RuntimeError, "found no user for this certificate"):
            cert_login._legacy_login(session, config, page, signer)

        html = '<a href="/pje/logout.seam">Sair</a><input name="javax.faces.ViewState">'
        response = SimpleNamespace(status_code=200, url="https://pje1g.trf1.jus.br/pje/home", text=html)
        self.assertFalse(cert_login._authenticated(response, "pje1g.trf5.jus.br"))
        response.url = "https://pje1g.trf5.jus.br/pje/home"
        self.assertTrue(cert_login._authenticated(response, "pje1g.trf5.jus.br"))

    def test_jt_shell_page_counts_as_authenticated(self):
        shell = "<html><body>Painel do advogado - Procurador MARA SUSY</body></html>"
        response = SimpleNamespace(status_code=200, url="https://pje.trt7.jus.br/primeirograu/error.seam", text=shell)
        self.assertTrue(cert_login._authenticated(response, "pje.trt7.jus.br"))
        plain = SimpleNamespace(
            status_code=200, url="https://pje.trt7.jus.br/primeirograu/error.seam", text="<html></html>"
        )
        self.assertFalse(cert_login._authenticated(plain, "pje.trt7.jus.br"))
        response.url = "https://pje.trt9.jus.br/segundograu/errorUnexpected.seam"
        self.assertFalse(cert_login._authenticated(response, "pje.trt9.jus.br"))
        with self.assertRaisesRegex(RuntimeError, "no authenticated session"):
            cert_login._require_registered(response, SimpleNamespace(key="trt9-2g"))

    def test_sso_registration_callback_is_a_clear_rejection_not_a_totp_retry(self):
        from jurisync.core.session_pool import LoginRejected

        config = SimpleNamespace(key="tjce-2g")
        callback = SimpleNamespace(url="https://pje.tjce.jus.br/pje2grau/publico/usuario/ssoCadastroCallback.seam")
        with self.assertRaisesRegex(LoginRejected, "not registered in this PJe.*ask the court"):
            cert_login._require_registered(callback, config)
        page = SimpleNamespace(url="https://pje.tjce.jus.br/pje2grau/publico/usuario/cadastro.seam")
        with self.assertRaises(LoginRejected):
            cert_login._require_registered(page, config)
        cert_login._require_registered(SimpleNamespace(url="https://pje.tjce.jus.br/pje2grau/Painel/x.seam"), config)

    def test_unregistered_user_costs_one_login_attempt(self):
        from jurisync.core.session_pool import LoginRejected

        attempts = []

        def login(*args, **kwargs):
            attempts.append(args)
            cert_login._require_registered(
                SimpleNamespace(url="https://pje.tjce.jus.br/pje2grau/publico/usuario/ssoCadastroCallback.seam"),
                SimpleNamespace(key="tjce-2g"),
            )

        sleep = Mock()
        with (
            patch.object(cert_login, "_twofa", return_value=object()),
            patch.object(cert_login, "load_configured", return_value=(None, None)),
            patch.object(cert_login.native_signer, "A1Signer"),
            patch.object(cert_login, "_login", side_effect=login),
            patch.object(cert_login.portals, "pje", return_value=SimpleNamespace(key="tjce-2g")),
        ):
            with self.assertRaises(LoginRejected):
                cert_login.cert_login("tjce-2g", pin="x", sleep=sleep)
        self.assertEqual(len(attempts), 1)
        sleep.assert_not_called()

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

    def test_legacy_challenge_extraction(self):
        html = (
            '<script>ASSINATURA_MENSAGEM = "abc123"; CONVERSATION_ID = "15881";</script>'
            '<form id="assinaturaForm" action="/2g/logarSC.seam">'
        )
        self.assertEqual(cert_login._legacy_challenge(html), ("abc123", "15881"))
        self.assertIsNone(cert_login._legacy_challenge("<html></html>"))

    def _legacy(self, answer, landing_text=""):
        from jurisync import portals

        config = portals.pje("tjrj-2g")
        page = SimpleNamespace(
            text='<script>var ASSINATURA_MENSAGEM = "abc123"; var CONVERSATION_ID = "15881";</script>'
        )
        signer = Mock()
        signer.sign.return_value = b"sig"
        signer.pkipath64.return_value = "CHAIN"
        session = Mock()
        session.post.return_value = SimpleNamespace(status_code=200, text=answer, headers={}, url="")
        landing = SimpleNamespace(
            status_code=200,
            url="https://tjrj.pje.jus.br/2g/Painel/painel_usuario/advogado.seam",
            text=landing_text,
            headers={},
        )
        session.get.return_value = landing
        return config, page, signer, session, landing

    def test_legacy_login_posts_the_pjeoffice_form_and_opens_its_home(self):
        config, page, signer, session, landing = self._legacy("Sucesso")
        self.assertIs(cert_login._legacy_login(session, config, page, signer), landing)
        url = session.post.call_args.args[0]
        self.assertEqual(url, "https://tjrj.pje.jus.br/2g/logarPJeOffice.seam?cid=15881")
        self.assertEqual(session.post.call_args.kwargs["data"], {"assinatura": "c2ln", "cadeiaCertificado": "CHAIN"})
        self.assertIn("versao", session.post.call_args.kwargs["headers"])
        signer.sign.assert_called_once_with("abc123")
        self.assertEqual(session.get.call_args.args[0], "https://tjrj.pje.jus.br/2g/homePJeOffice.seam?cid=15881")

    def test_legacy_login_reports_the_callback_refusal_and_stops(self):
        config, page, signer, session, _ = self._legacy("Erro:Não foi possível realizar a autenticação")
        with self.assertRaisesRegex(RuntimeError, "refused the certificate login: Não foi possível"):
            cert_login._legacy_login(session, config, page, signer)
        session.get.assert_not_called()

    def _trf5_legacy(self, status=302, location="/pje/QuadroAviso/listViewQuadroAvisoMensagem.seam", text=""):
        from jurisync import portals

        config, page, signer, session, landing = self._legacy("")
        config = portals.pje("trf5-legacy")
        headers = {"Location": location} if location is not None else {}
        session.post.return_value = SimpleNamespace(
            status_code=status, text=text, headers=headers, url="https://pje.trf5.jus.br/pje/logarPJeOffice.seam"
        )
        landing.url = "https://pje.trf5.jus.br/pje/QuadroAviso/listViewQuadroAvisoMensagem.seam"
        return config, page, signer, session, landing

    def test_legacy_login_follows_an_empty_302_to_the_landing_page(self):
        config, page, signer, session, landing = self._trf5_legacy()
        self.assertIs(cert_login._legacy_login(session, config, page, signer), landing)
        self.assertEqual(session.post.call_args.args[0], "https://pje.trf5.jus.br/pje/logarPJeOffice.seam?cid=15881")
        self.assertEqual(
            session.get.call_args.args[0],
            "https://pje.trf5.jus.br/pje/QuadroAviso/listViewQuadroAvisoMensagem.seam",
        )

    def test_legacy_login_accepts_301_and_an_absolute_location_on_the_same_host(self):
        config, page, signer, session, landing = self._trf5_legacy(
            status=301, location="https://pje.trf5.jus.br/pje/Painel/x.seam"
        )
        self.assertIs(cert_login._legacy_login(session, config, page, signer), landing)
        self.assertEqual(session.get.call_args.args[0], "https://pje.trf5.jus.br/pje/Painel/x.seam")

    def test_legacy_login_does_not_accept_a_redirect_back_to_the_login(self):
        config, page, signer, session, _ = self._trf5_legacy(location="/pje/login.seam")
        with self.assertRaisesRegex(RuntimeError, "refused the certificate login: HTTP 302"):
            cert_login._legacy_login(session, config, page, signer)
        session.get.assert_not_called()

    def test_legacy_login_refuses_a_redirect_off_the_allowed_hosts(self):
        config, page, signer, session, _ = self._trf5_legacy(location="https://evil.example/pje/x.seam")
        with self.assertRaises(RuntimeError):
            cert_login._legacy_login(session, config, page, signer)
        session.get.assert_not_called()

    def test_legacy_login_refuses_a_302_without_location(self):
        config, page, signer, session, _ = self._trf5_legacy(location=None)
        with self.assertRaisesRegex(RuntimeError, "HTTP 302"):
            cert_login._legacy_login(session, config, page, signer)

    def test_trf5_notice_board_counts_as_authenticated(self):
        html = (
            '<html><title>Processo Judicial Eletrônico: Quadro de Aviso</title><a href="/pje/logout.seam">Sair</a>'
            '<input type="hidden" name="javax.faces.ViewState" value="j_id1"></html>'
        )
        url = "https://pje.trf5.jus.br/pje/QuadroAviso/listViewQuadroAvisoMensagem.seam"
        response = SimpleNamespace(status_code=200, url=url, text=html)
        self.assertTrue(cert_login._authenticated(response, "pje.trf5.jus.br"))

    def test_trf5_legacy_identity_logout_link_counts_as_authenticated(self):
        html = (
            '<html><a href="/pje/home.seam?actionMethod=QuadroAviso%2FlistViewQuadroAvisoMensagem.xhtml'
            '%3Aidentity.logout&amp;cid=18012">Sair</a><input name="javax.faces.ViewState" value="j_id1"></html>'
        )
        url = "https://pje.trf5.jus.br/pje/QuadroAviso/listViewQuadroAvisoMensagem.seam"
        self.assertTrue(
            cert_login._authenticated(SimpleNamespace(status_code=200, url=url, text=html), "pje.trf5.jus.br")
        )
        login = '<form id="loginForm"><input name="javax.faces.ViewState" value="j_id1"></form>'
        self.assertFalse(
            cert_login._authenticated(SimpleNamespace(status_code=200, url=url, text=login), "pje.trf5.jus.br")
        )


class Pje1xGuards(unittest.TestCase):
    def test_trf5_legacy_is_a_pje1x_portal(self):
        from jurisync import portals

        self.assertTrue(portals.pje("trf5-legacy").pje1x)
        self.assertFalse(portals.pje("trf5-tru").pje1x)

    def test_session_check_looks_for_the_pje1x_search_form(self):
        from jurisync.connectors.pje.auth import sessions

        page = SimpleNamespace(
            status_code=200,
            url="https://pje.trf5.jus.br/pje/Processo/ConsultaProcesso/listView.seam",
            text='<form id="consultarProcessoForm"><input name="javax.faces.ViewState" value="j"></form>',
        )
        session = Mock()
        session.get.return_value = page
        self.assertTrue(sessions.is_authenticated(session, "trf5-legacy"))
        page.url = "https://pje.trf5.jus.br/pje/login.seam"
        page.text = '<form id="loginForm"><input name="javax.faces.ViewState" value="j"></form>'
        self.assertFalse(sessions.is_authenticated(session, "trf5-legacy"))

    def test_aws_waf_captcha_is_solved_before_the_login_page(self):
        waf = SimpleNamespace(
            status_code=405,
            url="https://pje.tjes.jus.br/pje/login.seam",
            headers={},
            text='<title>Human Verification</title><script src="https://x.captcha.awswaf.com/captcha.js"></script>',
        )
        config = cert_login.portals.pje("tjes-1g")
        token = [{"name": "aws-waf-token", "value": "t"}, {"name": "other", "value": "x"}]

        session = Mock()
        session.get.side_effect = [waf, waf]
        with (
            patch.object(cert_login.awswaf, "solve", return_value=token) as solve,
            self.assertRaisesRegex(RuntimeError, "still asks for a CAPTCHA") as raised,
        ):
            cert_login._login(session, config, None, Mock())
        self.assertEqual(raised.exception.reason, "captcha")
        solve.assert_called_once_with(config.login_url)
        session.cookies.set.assert_called_once_with("aws-waf-token", "t", domain="", path="/")
        self.assertEqual(session.get.call_count, 2)

        # Once the token is accepted, the login goes on with the real PJe page.
        portal = SimpleNamespace(status_code=200, url=config.login_url, headers={}, text="<html>PJe</html>")
        session = Mock()
        session.get.side_effect = [waf, portal]
        with (
            patch.object(cert_login.awswaf, "solve", return_value=token),
            patch.object(cert_login, "_require_available", side_effect=LookupError("reached")) as available,
            self.assertRaisesRegex(LookupError, "reached"),
        ):
            cert_login._login(session, config, None, Mock())
        self.assertIs(available.call_args.args[0], portal)


SSO = "https://sso.cloud.pje.jus.br/auth/realms/pje/login-actions/authenticate"
OTP_FORM = (
    "<html><head><title>PJe SSO</title></head><body>{alert}"
    f'<form id="kc-otp-login-form" action="{SSO}?execution=otp"><input name="otp"></form></body></html>'
)
REFUSED = OTP_FORM.format(alert='<span id="input-error">Código inválido.</span>')


class FakeSso:
    """The tjmt-1g login: the login page's challenge, the SSO's OTP form, then what each code gets."""

    def __init__(self, otp_answers, landing=None, probe=None):
        self.otp_answers, self.otp_posts, self.gets = list(otp_answers), [], []
        self.landing = landing
        self.probe = probe

    @staticmethod
    def page(url, text="", status=200, location=None):
        return SimpleNamespace(
            status_code=status, url=url, text=text, headers={"Location": location} if location else {}
        )

    def get(self, url, **kwargs):
        self.gets.append(url)
        if url == "https://pje.tjmt.jus.br/pje/login.seam":
            return self.page(url, f"<script>autenticar('a', 'challenge')</script><form id='loginForm' action='{SSO}'>")
        if url.startswith("https://pje.tjmt.jus.br/pje/Processo/ConsultaProcesso"):
            return self.page(url, *self.probe)
        return self.page(url, *self.landing)

    def post(self, url, data=None, **kwargs):
        if "otp" in (data or {}):
            self.otp_posts.append(data["otp"])
            return self.otp_answers.pop(0)
        return self.page(SSO, OTP_FORM.format(alert=""))


class OtpClassification(unittest.TestCase):
    def login(self, sso):
        from jurisync import portals

        signer = Mock()
        signer.sign.return_value = b"sig"
        signer.chain64.return_value = "chain"
        codes = iter(["111111", "222222"])
        with (
            patch.object(cert_login.native_signer, "post_result"),
            patch.object(cert_login, "fresh_code", side_effect=lambda *a, **k: next(codes)),
            patch.object(cert_login, "next_code", side_effect=lambda *a, **k: next(codes)),
        ):
            return cert_login._login(sso, portals.pje("tjmt-1g"), {"secret": "x"}, signer, sleep=Mock())

    def test_the_sso_refusing_both_codes_is_otp_refused_with_its_message(self):
        from jurisync.core.session_pool import LoginRejected

        sso = FakeSso([FakeSso.page(SSO, REFUSED), FakeSso.page(SSO, REFUSED)])
        with self.assertRaises(LoginRejected) as caught:
            self.login(sso)
        self.assertEqual(caught.exception.reason, "otp_refused")
        self.assertIn('SSO message: "Código inválido."', str(caught.exception))
        self.assertEqual(sso.otp_posts, ["111111", "222222"])

    def test_a_code_refused_once_is_answered_in_the_next_window_and_logs_in(self):
        home = '<a href="/pje/logout.seam">Sair</a><input name="javax.faces.ViewState" value="1">'
        accepted = FakeSso.page(SSO, status=302, location="https://pje.tjmt.jus.br/pje/Painel/home.seam")
        sso = FakeSso([FakeSso.page(SSO, REFUSED), accepted], landing=(home,))
        self.assertIs(self.login(sso), sso)
        self.assertEqual(len(sso.otp_posts), 2)

    def test_an_unrecognised_landing_is_unexpected_with_where_and_no_second_code(self):
        from jurisync.core.session_pool import LoginRejected

        accepted = FakeSso.page(SSO, status=302, location="https://pje.tjmt.jus.br/pje/Painel/aviso.seam?cid=9")
        sso = FakeSso(
            [accepted],
            landing=("<html><head><title>Quadro de avisos</title></head><body>Bem-vindo</body></html>",),
            probe=("<html><head><title>Consulta</title></head></html>",),
        )
        with self.assertRaises(cert_login.LoginFailed) as caught:
            self.login(sso)
        self.assertNotIsInstance(caught.exception, LoginRejected)
        self.assertEqual(caught.exception.reason, "unexpected")
        message = str(caught.exception)
        self.assertIn("accepted the authenticator code", message)
        self.assertIn("/pje/Painel/aviso.seam", message)
        self.assertIn("'Quadro de avisos'", message)
        self.assertNotIn("cid=9", message)
        self.assertEqual(sso.otp_posts, ["111111"])

    def test_no_otp_form_after_the_certificate_is_unexpected_with_the_page(self):
        sso = FakeSso([])
        sso.post = lambda url, data=None, **kwargs: FakeSso.page(
            SSO, '<html><head><title>Erro</title></head><div id="kc-error-message">Sessão expirada</div></html>'
        )
        with self.assertRaises(cert_login.LoginFailed) as caught:
            self.login(sso)
        self.assertEqual(caught.exception.reason, "unexpected")
        self.assertIn('SSO message "Sessão expirada"', str(caught.exception))


if __name__ == "__main__":
    unittest.main()
