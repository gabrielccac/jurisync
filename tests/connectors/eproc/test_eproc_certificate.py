import unittest
from types import SimpleNamespace
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

    def test_sso_host_is_a_whole_word_under_the_portals_own_court(self):
        accepted = {
            "trf4-2g": ("eproc-sso.trf4.jus.br", "eproc-sso-certificado.trf4.jus.br", "sso.trf4.jus.br"),
            # The TRF4 and TRF2 sections share their tribunal's SSO.
            "trf4-1g-sc": ("eproc-sso.trf4.jus.br", "eproc-sso-certificado.trf4.jus.br", "sso.jfsc.jus.br"),
            "trf4-1g-rs": ("eproc-sso.trf4.jus.br",),
            "trf4-1g-pr": ("eproc-sso-certificado.trf4.jus.br",),
            "trf2-1g-rj": ("sso.trf2.jus.br", "sso-eproc.jfrj.jus.br"),
            "tjsc-1g": ("cert-sso.tjsc.jus.br", "sso.tjsc.jus.br"),
            "tjsp-1g": ("certificado-sso.tjsp.jus.br",),
            "tjrs-1g": ("keycloak-eks.tjrs.jus.br", "sso.eproc.tjrs.jus.br"),
            "tnu": ("sso.cjf.jus.br",),
        }
        for portal, hosts in accepted.items():
            config = portals.eproc(portal)
            for host in (*hosts, config.host, certificate.BROKER_HOST):
                with self.subTest(portal=portal, host=host):
                    certificate.require_login_url(config, f"https://{host}/realms/eproc")
        rejected = (
            "processo.trf4.jus.br",  # "sso" as a substring only: the old check accepted it
            "www.processo.trf4.jus.br",
            "assessoria.trf4.jus.br",
            "eproc.sso.trf4.jus.br",  # the SSO word must be in the first label
            "cert-sso.tjsc.jus.br",  # another court's SSO
            "sso.tjsp.jus.br",
            "sso.jus.br",
            "sso.trf4.jus.br.evil.example",
            "sso.trf4.jus.com",
            "sso-trf4.jus.br",
            "trf4.jus.br",
            "",
        )
        for host in rejected:
            with self.subTest(host=host):
                self.assertFalse(certificate.is_sso_host(self.config, host))
                with self.assertRaises(UnsafeUrlError):
                    certificate.require_login_url(self.config, f"https://{host}/realms/eproc")

    def test_certificate_host_named_by_the_login_page_must_be_an_sso_host(self):
        button = BUTTON.replace("eproc-sso-certificado.trf4.jus.br", "processo.exemplo.jus.br")
        with self.assertRaises(UnsafeUrlError):
            certificate._certificate_url(button, LOGIN, self.config)

    def test_tjms_certificate_host_is_bound_to_its_exact_login_path(self):
        config = portals.Portal(
            "tjms-eproc-1g", "eproc", "TJMS", "1g", "eproc1g.tjms.jus.br", "/eproc/", "certificate_totp"
        )
        allowed = "https://mtls.tjms.jus.br/priv/login_cert.php?state=example"
        certificate.require_login_url(config, allowed)
        for url in (allowed.replace("https:", "http:"), allowed.replace("login_cert.php", "other.php")):
            with self.subTest(url=url), self.assertRaises(UnsafeUrlError):
                certificate.require_login_url(config, url)
        with self.assertRaises(UnsafeUrlError):
            certificate.require_login_url(self.config, allowed)

    def test_forms_are_read_with_their_hidden_fields(self):
        html = '<form id="kc-x509-login-info" action="/auth?a=1"><input name="login" value="x"><input></form>'
        action, fields = certificate._form(html, LOGIN, self.config, "kc-x509-login-info")
        self.assertEqual((action, fields), ("https://eproc-sso.trf4.jus.br/auth?a=1", {"login": "x"}))
        self.assertEqual(certificate._form(html, LOGIN, self.config, "other"), (None, {}))

    def test_tjms_local_certificate_and_otp_do_not_release_device(self):
        config = portals.eproc("tjms-eproc-1g")
        page = SimpleNamespace(
            status_code=200,
            url=config.login_url,
            headers={},
            text='<form id="frmLogin"><input name="hdnAcao" type="hidden" value="login"></form>'
            '<input id="destino_v2_cert" value="externo_controlador.php?acao=login_cert">',
        )
        otp = SimpleNamespace(
            status_code=200,
            url=config.url("externo_controlador.php?acao=2fa_codigo"),
            headers={},
            text='<form id="frmLogin" action="index.php">'
            '<input id="txtAcessoCodigo" name="txtAcessoCodigo">'
            '<input name="hdnAcao" type="hidden" value="2fa_codigo">'
            '<input name="chkLiberarDispositivo" type="checkbox" value="1" checked>'
            '<input name="chkLiberarDispositivo" type="hidden" value="1">'
            '<button id="btnValidar" onclick="Submit(\'2fa_validar\')">Validar</button></form>',
        )
        panel = SimpleNamespace(
            status_code=200,
            url=config.url("controlador.php?acao=painel_adv_listar"),
            headers={},
            text='<form id="frmPainelAdvogado"></form>',
        )
        session = mock.Mock()
        session.get.return_value = page
        session.post.side_effect = [otp, panel]
        session.cookies.jar = [SimpleNamespace(name="PHPSESSID")]
        with (
            mock.patch.object(certificate, "client_certificate") as pem,
            mock.patch.object(certificate, "totp") as code,
        ):
            pem.return_value.__enter__.return_value = ("cert.pem", "key.pem")
            code.return_value.now.return_value = "123456"
            self.assertEqual(
                certificate._local_certificate_login(session, config, None, None, [], {}),
                (session, panel.text, panel.url),
            )
        self.assertEqual(session.post.call_count, 2)
        submitted = session.post.call_args_list[1].kwargs["data"]
        self.assertEqual(submitted, {"hdnAcao": "2fa_validar", "txtAcessoCodigo": "123456"})
        self.assertNotIn("chkLiberarDispositivo", submitted)

    def test_a_portal_opts_into_the_certificate_with_its_own_flag(self):
        with mock.patch.dict("os.environ", {"PJE_A1_P12": "/a1.p12", "TRF4_2G_CERT": "true"}, clear=True):
            self.assertTrue(self.config.uses_certificate())
        with mock.patch.dict("os.environ", {"TRF4_2G_CERT": "true"}, clear=True):
            self.assertFalse(self.config.uses_certificate())
        with mock.patch.dict("os.environ", {"PJE_A1_P12": "/a1.p12"}, clear=True):
            self.assertFalse(self.config.uses_certificate())


PROFILE_PAGE = """
<script>function acaoLogar(id){
  document.getElementById('frmEscolherUsuario').action = 'controlador.php?acao=pessoa_usuario_logar&acao_origem=entrar_sso' + '&id_usuario=' + id + '';
}</script>
<form id="frmEscolherUsuario" action="controlador.php?acao=&acao_origem=entrar_sso" method="post">
<button id="tr0" onclick="acaoLogar('111');" data-descricao="GO051632 / ADVOGADO"><div>GO051632</div></button>
<button id="tr1" onclick="acaoLogar('222');" data-descricao="SP552571 / ADVOGADO"><div>SP552571</div></button>
<input type="hidden" name="lista_processos" value="">
</form>
"""
PANEL = "https://eproc1g.tjsp.jus.br/eproc/controlador.php?acao=painel_adv_listar&hash=x"
TJMG_LOGIN = """
<form id="frmLogin" action="index.php" method="post">
<input type="text" name="txtUsuario"><input type="password" name="pwdSenha">
<input type="button" value="Certificado Digital" onclick="SubmitCert()">
<input type="hidden" name="hdnAcao" value="login"></form>
<input type="hidden" id="destino_v2_cert" name="destino_v2_cert"
 value="https://eproc1g.tjmg.jus.br/eproc/externo_controlador.php?acao=login_cert">
"""
TJMG_CODE = """
<form id="frmLogin" action="index.php" method="post">
<input type="text" name="txtAcessoCodigo"><input type="checkbox" name="chkLiberarDispositivo" value="S">
<input type="hidden" name="hdnAcao" value="2fa_codigo"><input type="hidden" name="hdnStrTela" value="2fa_codigo">
</form>
"""


def page(url, text="", status=200):
    return mock.Mock(status_code=status, url=url, text=text, headers={})


class FakeSession:
    """Answers each request from a list of (method, url prefix, response) in order, and records the bodies."""

    def __init__(self, steps):
        self.steps = list(steps)
        self.calls = []
        self.cookies = mock.Mock(jar=[mock.Mock()])
        self.cookies.jar[0].name = "PHPSESSID"

    def _answer(self, method, url, **options):
        self.calls.append((method, url, options.get("data"), options.get("cert")))
        expected_method, prefix, response = self.steps.pop(0)
        assert (method, url.startswith(prefix)) == (expected_method, True), (method, url)
        return response

    def get(self, url, **options):
        return self._answer("GET", url, **options)

    def post(self, url, **options):
        return self._answer("POST", url, **options)


class ProfileChoiceTest(unittest.TestCase):
    def setUp(self):
        self.config = portals.eproc("tjsp-1g")

    def test_profiles_are_read_from_the_selection_page(self):
        self.assertEqual(
            certificate.profiles(PROFILE_PAGE), [("111", "GO051632 / ADVOGADO"), ("222", "SP552571 / ADVOGADO")]
        )
        self.assertEqual(certificate.profiles("<html></html>"), [])

    def test_the_configured_profile_is_posted(self):
        session = FakeSession([("POST", "https://eproc1g.tjsp.jus.br/eproc/controlador.php", page(PANEL))])
        response = page("https://eproc1g.tjsp.jus.br/eproc/externo_controlador.php?acao=entrar_sso", PROFILE_PAGE)
        with mock.patch.dict("os.environ", {"TJSP_1G_USER_PROFILE": "sp552571"}, clear=True):
            self.assertEqual(certificate._choose_profile(session, self.config, response).url, PANEL)
        method, url, data, _ = session.calls[0]
        self.assertTrue(url.endswith("acao=pessoa_usuario_logar&acao_origem=entrar_sso&id_usuario=222"))
        self.assertEqual(data, {"lista_processos": ""})

    def test_several_lawyer_profiles_without_a_setting_stop_with_the_choices(self):
        response = page("https://eproc1g.tjsp.jus.br/eproc/externo_controlador.php?acao=entrar_sso", PROFILE_PAGE)
        with (
            mock.patch.dict("os.environ", {}, clear=True),
            self.assertRaisesRegex(certificate.LoginRejected, "GO051632 / ADVOGADO, SP552571.*TJSP_1G_USER_PROFILE"),
        ):
            certificate._choose_profile(FakeSession([]), self.config, response)

    def test_a_single_lawyer_profile_is_picked_without_a_setting(self):
        single = PROFILE_PAGE.replace('ADVOGADO"><div>SP552571', 'PERITO"><div>SP552571')
        session = FakeSession([("POST", "https://eproc1g.tjsp.jus.br/eproc/controlador.php", page(PANEL))])
        response = page("https://eproc1g.tjsp.jus.br/eproc/externo_controlador.php?acao=entrar_sso", single)
        with mock.patch.dict("os.environ", {}, clear=True):
            certificate._choose_profile(session, self.config, response)
        self.assertTrue(session.calls[0][1].endswith("id_usuario=111"))

    def test_a_page_without_the_selection_is_returned_as_is(self):
        response = page(PANEL, "<html></html>")
        self.assertIs(certificate._choose_profile(FakeSession([]), self.config, response), response)


class LoginFlowTest(unittest.TestCase):
    def run_login(self, portal, steps, env):
        session = FakeSession(steps)
        environ = {"PJE_A1_PIN": "pin", **env}
        with (
            mock.patch.dict("os.environ", environ, clear=True),
            mock.patch.object(certificate, "portal_totp_account", return_value={"secret": "JBSWY3DPEHPK3PXP"}),
            mock.patch.object(certificate, "totp", return_value=mock.Mock(now=lambda: "123456")),
            mock.patch.object(certificate.native_signer, "load_a1", return_value=("key", "cert", [])),
            mock.patch.object(certificate, "client_certificate") as pem,
        ):
            pem.return_value.__enter__.return_value = "/tmp/a1.pem"
            result = certificate.cert_login(portal, session=session)
        return session, result

    def test_classic_login_posts_the_form_to_login_cert_then_the_code(self):
        panel = "https://eproc1g.tjmg.jus.br/eproc/controlador.php?acao=painel_adv_listar&hash=x"
        session, result = self.run_login(
            "tjmg-1g",
            [
                ("GET", "https://eproc1g.tjmg.jus.br/eproc/", page("https://eproc1g.tjmg.jus.br/eproc/", TJMG_LOGIN)),
                (
                    "POST",
                    "https://eproc1g.tjmg.jus.br/eproc/externo_controlador.php?acao=login_cert",
                    page("https://eproc1g.tjmg.jus.br/eproc/index.php?acao=principal", TJMG_CODE),
                ),
                ("POST", "https://eproc1g.tjmg.jus.br/eproc/index.php", page(panel)),
            ],
            {},
        )
        self.assertEqual(result[2], panel)
        _, _, login_data, login_cert = session.calls[1]
        self.assertEqual(login_data, {"txtUsuario": "", "pwdSenha": "", "hdnAcao": "login"})
        self.assertEqual(login_cert, "/tmp/a1.pem")
        _, _, code_data, code_cert = session.calls[2]
        self.assertEqual(
            code_data, {"txtAcessoCodigo": "123456", "hdnAcao": "2fa_validar", "hdnStrTela": "2fa_codigo"}
        )  # the unchecked "trust this device" box is not sent
        self.assertIsNone(code_cert)

    def test_a_refused_classic_certificate_is_a_rejection(self):
        with self.assertRaisesRegex(certificate.LoginRejected, "did not accept the A1"):
            self.run_login(
                "tjmg-1g",
                [
                    (
                        "GET",
                        "https://eproc1g.tjmg.jus.br/eproc/",
                        page("https://eproc1g.tjmg.jus.br/eproc/", TJMG_LOGIN),
                    ),
                    (
                        "POST",
                        "https://eproc1g.tjmg.jus.br/eproc/externo_controlador.php",
                        page(
                            "https://eproc1g.tjmg.jus.br/eproc/externo_controlador.php?acao=principal&erro=erro_certificado"
                        ),
                    ),
                ],
                {},
            )

    def test_keycloak_that_asks_the_code_right_after_the_certificate(self):
        login = "https://sso.tjpr.jus.br/auth/realms/eproc/protocol/openid-connect/auth?client_id=eproc1g"
        button = "<button onclick=\"document.location.host = 'certificado-sso.tjpr.jus.br';\"></button>"
        otp_form = '<form id="kc-otp-login-form" action="https://sso.tjpr.jus.br/auth/realms/eproc/login-actions/authenticate?s=1"></form>'
        panel = "https://eproc1g.tjpr.jus.br/eproc/controlador.php?acao=painel_adv_listar&hash=x"
        session, result = self.run_login(
            "tjpr-1g",
            [
                ("GET", "https://eproc1g.tjpr.jus.br/eproc/", page(login, button)),
                (
                    "GET",
                    "https://certificado-sso.tjpr.jus.br/",
                    page(login.replace("sso.", "certificado-sso."), otp_form),
                ),
                ("POST", "https://sso.tjpr.jus.br/auth/realms/eproc/login-actions/authenticate", page(panel)),
            ],
            {},
        )
        self.assertEqual(result[2], panel)
        self.assertEqual(session.calls[2][2], {"otp": "123456", "login": "Sign In"})

    def test_tjmg_second_instance_dispatches_to_the_local_certificate_form(self):
        session = object()
        with (
            mock.patch.object(certificate, "portal_totp_account", return_value={}),
            mock.patch.object(certificate.native_signer, "load_a1", return_value=(None, None, [])),
            mock.patch.object(certificate, "_local_certificate_login", return_value="authenticated") as local,
        ):
            self.assertEqual(certificate.cert_login("tjmg-2g", pin="example", session=session), "authenticated")
        self.assertEqual(local.call_args.args[1].key, "tjmg-2g")


class TjacTests(unittest.TestCase):
    """TJAC: Keycloak SSO on sso.tjac.jus.br, certificate on certificado-sso.tjac.jus.br, one 2FA profile for 1G and 2G."""

    def test_both_instances_are_registered_and_share_the_authenticator_profile(self):
        for key, host in (("tjac-1g", "eproc1g.tjac.jus.br"), ("tjac-2g", "eproc2g.tjac.jus.br")):
            config = portals.eproc(key)
            self.assertEqual((config.host, config.root, config.justice, config.region), (host, "/eproc/", "8", "01"))
            with mock.patch.dict("os.environ", {"TJAC_EPROC_2FA_QR": "/qr.jpg"}, clear=True):
                self.assertEqual(config.env("2FA_QR"), "/qr.jpg")
        self.assertEqual(portals.for_process("0000001-00.2025.8.01.0001")[0].tribunal, "TJAC")

    def test_its_sso_hosts_are_accepted_and_another_courts_are_not(self):
        config = portals.eproc("tjac-1g")
        for host in ("sso.tjac.jus.br", "certificado-sso.tjac.jus.br"):
            certificate.require_login_url(config, f"https://{host}/realms/eproc/protocol/openid-connect/auth")
        with self.assertRaises(UnsafeUrlError):
            certificate.require_login_url(config, "https://certificado-sso.tjal.jus.br/realms/eproc")

    def test_the_panel_url_the_sso_returns_without_the_application_root_is_put_back_under_it(self):
        config = portals.eproc("tjac-1g")
        landed = "https://eproc1g.tjac.jus.br/controlador.php?acao=painel_adv_listar&hash=x"
        self.assertEqual(
            certificate.rooted(config, landed),
            "https://eproc1g.tjac.jus.br/eproc/controlador.php?acao=painel_adv_listar&hash=x",
        )
        already = "https://eproc1g.tjac.jus.br/eproc/controlador.php?acao=painel_adv_listar"
        self.assertEqual(certificate.rooted(config, already), already)
        other = "https://elsewhere.example/controlador.php"
        self.assertEqual(certificate.rooted(config, other), other)
