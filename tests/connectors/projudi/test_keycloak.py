"""Projudi legacy-keycloak adapter (TJPR) against SIMULATED responses.

Every page below is synthetic, written from the public Projudi/Keycloak pages and docs/connectors/projudi-tjpr.md.
Nothing here proves the real portal behaves this way: no authenticated session was ever opened.
"""

import dataclasses
import os
import unittest
from types import SimpleNamespace
from unittest import mock

from jurisync import portals
from jurisync.connectors.projudi import base, keycloak, server
from jurisync.core.http import UnsafeUrlError
from jurisync.core.session_pool import LoginRejected

KEY = "tjpr-projudi-1g"
APP = "https://projudi.tjpr.jus.br/projudi/"
SSO = "https://sso.projudi.tjpr.jus.br/auth/realms/projudi/"
LOGIN_ACTION = SSO + "login-actions/authenticate?session_code=S1&execution=E1&client_id=projudi&tab_id=T1"
OTP_ACTION = SSO + "login-actions/authenticate?session_code=S2&execution=E2&client_id=projudi&tab_id=T2"
NUMBER = "0000001-23.2026.8.16.0001"
USERNAME = "52998224725"  # synthetic CPF-shaped value
PASSWORD = "synthetic-password"
OTP_SECRET = "JBSWY3DPEHPK3PXP"  # the public example secret, not a real one
ENV = {
    "TJPR_PROJUDI_1G_USERNAME": "529.982.247-25",
    "TJPR_PROJUDI_1G_PASSWORD": PASSWORD,
    "TJPR_PROJUDI_1G_2FA_DATA": f"otpauth://totp/PROJUDI%20PR?secret={OTP_SECRET}",
}

LOGIN_PAGE = f"""<html><body><form id="kc-form-login" action="{LOGIN_ACTION}" method="post">
<input id="username" name="username" type="text"><input id="password" name="password" type="password">
<input id="rememberMe" name="rememberMe" type="checkbox"><input type="hidden" name="credentialId">
<input name="login" id="kc-login" type="submit" value="Entrar"></form></body></html>"""
OTP_PAGE = f"""<html><body><form id="kc-otp-login-form" action="{OTP_ACTION}" method="post">
<input id="otp" name="otp" type="text"><input type="hidden" name="credentialId" value="cred-1">
<input name="login" type="submit" value="Entrar"></form></body></html>"""
CAPTCHA_PAGE = '<html><body><div class="g-recaptcha"></div><form id="kc-form-login" action="/x"></form></body></html>'
TOTP_SETUP_PAGE = '<html><body><form id="kc-totp-settings-form" action="/x"></form></body></html>'
PUBLIC_HOME = "<html><body><h1>Acesso ao Sistema</h1><a href='autenticacao.do'>Entrar</a></body></html>"
FRAMESET = '<html><frameset><frame name="topFrame" src="blank.html"><frame name="mainFrame" src="home.do?r=1"></frameset></html>'
HOME = f"""<html><body>
<a href="{APP}processo/buscaProcessual.do;jsessionid=ABC?_tj=aaa111">Busca Processual Simples</a>
<a href="{APP}processo/ciencia.do?_tj=bbb222">Ciência de intimações</a>
<a href="{APP}processo/peticionar.do?_tj=ccc333">Peticionar</a></body></html>"""
SEARCH_PAGE = f"""<html><body><form action="{APP}processo/buscaProcessual.do?_tj=ddd444" method="post">
<input type="hidden" name="_tjForm" value="h1"><input type="text" name="numeroProcesso">
<input type="submit" name="buscar" value="Buscar"></form></body></html>"""
RESULT_PAGE = f"""<html><body><h2>Processo {NUMBER}</h2><table>
<tr><th>Data</th><th>Movimento</th></tr>
<tr><td>02/01/2026</td><td>Decisão sintética</td></tr>
<tr><td>01/01/2026</td><td>Petição inicial sintética</td></tr></table></body></html>"""


def response(status=200, text="", location="", url=""):
    headers = {"Location": location} if location else {}
    return SimpleNamespace(status_code=status, headers=headers, text=text, content=text.encode(), url=url)


class FakeSession:
    """Answers each request with ``routes(method, url, kwargs)`` and records it."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def _answer(self, method, url, kwargs):
        self.calls.append((method, url, kwargs))
        result = self.routes(method, url, kwargs)
        result.url = result.url or url
        return result

    def get(self, url, **kwargs):
        return self._answer("GET", url, kwargs)

    def post(self, url, **kwargs):
        return self._answer("POST", url, kwargs)

    def request(self, method, url, **kwargs):
        return self._answer(method, url, kwargs)

    def posts(self):
        return [call for call in self.calls if call[0] == "POST"]


def login_routes(*, credentials_result="otp", otp_result="app"):
    """The simulated login: entry -> SSO login form -> credentials -> (OTP) -> back to Projudi."""
    pages = {"otp": OTP_PAGE, "login": LOGIN_PAGE, "captcha": CAPTCHA_PAGE, "setup": TOTP_SETUP_PAGE}

    def routes(method, url, kwargs):
        if url == APP + "autenticacao.do":
            return response(302, location=SSO + "protocol/openid-connect/auth?client_id=projudi")
        if url.startswith(SSO + "protocol/openid-connect/auth"):
            return response(200, LOGIN_PAGE)
        if url == LOGIN_ACTION:
            if credentials_result == "app":
                return response(302, location=APP + "autenticacao.do?code=C&state=S")
            return response(200, pages[credentials_result])
        if url == OTP_ACTION:
            if otp_result == "app":
                return response(302, location=APP + "autenticacao.do?code=C&state=S")
            return response(200, pages[otp_result])
        if url.startswith(APP + "autenticacao.do?code="):
            return response(302, location=APP + ";jsessionid=ABC")
        if url == APP:
            return response(200, FRAMESET)
        if url == APP + "home.do":
            return response(200, HOME.replace("<body>", "<body><!-- _tj= -->"))
        raise AssertionError(f"unexpected {method} {url}")

    return routes


def wired(test):
    patcher = mock.patch.dict(portals.PORTALS, {KEY: dataclasses.replace(portals.PORTALS[KEY], wired=True)})
    env = mock.patch.dict(os.environ, ENV)
    test.addCleanup(patcher.stop)
    test.addCleanup(env.stop)
    patcher.start()
    env.start()


class RegistryTest(unittest.TestCase):
    def test_tjpr_projudi_stays_unwired_and_unregistered(self):
        self.assertFalse(portals.PORTALS[KEY].wired)
        self.assertEqual(portals.PORTALS[KEY].flavor, "legacy-keycloak")
        self.assertNotIn("legacy-keycloak", server.ADAPTERS)  # no MCP tool reaches it until a live smoke proves it
        self.assertEqual(portals.PORTALS[KEY].login_url, APP + "autenticacao.do")

    def test_other_keycloak_courts_have_no_configuration(self):
        with self.assertRaisesRegex(NotImplementedError, "no Keycloak configuration"):
            keycloak.sso_for(portals.PORTALS["tjrr-1g"])

    def test_the_adapter_has_no_certificate_login(self):
        self.assertFalse(hasattr(keycloak, "load_certificate"))
        self.assertNotIn("certificate", keycloak.KeycloakAdapter.login.__code__.co_names)


class LoginTest(unittest.TestCase):
    def setUp(self):
        wired(self)

    def test_login_posts_only_what_a_browser_sends_and_stops_at_two_posts(self):
        session = FakeSession(login_routes())
        self.assertIs(keycloak.ADAPTER.login(KEY, session=session), session)
        credentials, otp = [call[2]["data"] for call in session.posts()]
        self.assertEqual(credentials, {"credentialId": "", "username": USERNAME, "password": PASSWORD})
        self.assertEqual(set(otp), {"credentialId", "otp"})
        self.assertRegex(otp["otp"], r"^\d{6}$")
        for call in session.calls:
            self.assertIs(call[2]["allow_redirects"], False)
        self.assertEqual(len(session.posts()), 2)

    def test_login_without_a_totp_step_when_the_portal_does_not_ask(self):
        session = FakeSession(login_routes(credentials_result="app"))
        keycloak.ADAPTER.login(KEY, session=session)
        self.assertEqual(len(session.posts()), 1)

    def test_missing_credentials_send_nothing(self):
        for missing in ("TJPR_PROJUDI_1G_USERNAME", "TJPR_PROJUDI_1G_PASSWORD"):
            with self.subTest(missing=missing), mock.patch.dict(os.environ, {missing: ""}):
                session = FakeSession(login_routes())
                with self.assertRaisesRegex(keycloak.MissingCredentials, "precisa de login e senha da cliente"):
                    keycloak.ADAPTER.login(KEY, session=session)
                self.assertEqual(session.calls, [])

    def test_username_must_look_like_a_cpf_or_cnpj_before_any_request(self):
        with mock.patch.dict(os.environ, {"TJPR_PROJUDI_1G_USERNAME": "joao"}):
            session = FakeSession(login_routes())
            with self.assertRaisesRegex(ValueError, "CPF"):
                keycloak.ADAPTER.login(KEY, session=session)
            self.assertEqual(session.calls, [])

    def test_wrong_password_is_rejected_after_one_post_and_never_retried(self):
        session = FakeSession(login_routes(credentials_result="login"))
        with self.assertRaises(LoginRejected):
            keycloak.ADAPTER.login(KEY, session=session)
        self.assertEqual(len(session.posts()), 1)

    def test_wrong_otp_is_rejected_after_two_posts_and_never_retried(self):
        session = FakeSession(login_routes(otp_result="otp"))
        with self.assertRaises(LoginRejected):
            keycloak.ADAPTER.login(KEY, session=session)
        self.assertEqual(len(session.posts()), 2)

    def test_otp_step_without_an_authenticator_configured_stops_after_the_credentials_post(self):
        with mock.patch.dict(os.environ, {"TJPR_PROJUDI_1G_2FA_DATA": ""}):
            session = FakeSession(login_routes())
            with self.assertRaisesRegex(keycloak.MissingCredentials, "TOTP"):
                keycloak.ADAPTER.login(KEY, session=session)
            self.assertEqual(len(session.posts()), 1)

    def test_captcha_before_the_credentials_stops_with_zero_posts(self):
        def routes(method, url, kwargs):
            if url.startswith(SSO + "protocol"):
                return response(200, CAPTCHA_PAGE)
            return login_routes()(method, url, kwargs)

        session = FakeSession(routes)
        with self.assertRaisesRegex(keycloak.HumanVerificationRequired, "precisa de uma pessoa"):
            keycloak.ADAPTER.login(KEY, session=session)
        self.assertEqual(session.posts(), [])

    def test_captcha_after_the_credentials_is_not_solved(self):
        session = FakeSession(login_routes(credentials_result="captcha"))
        with self.assertRaises(keycloak.HumanVerificationRequired):
            keycloak.ADAPTER.login(KEY, session=session)
        self.assertEqual(len(session.posts()), 1)

    def test_authenticator_setup_is_never_taken(self):
        session = FakeSession(login_routes(credentials_result="setup"))
        with self.assertRaises(keycloak.AccountActionRequired):
            keycloak.ADAPTER.login(KEY, session=session)
        self.assertEqual(len(session.posts()), 1)

    def test_a_redirect_off_the_portal_and_its_sso_is_refused(self):
        session = FakeSession(lambda *_: response(302, location="https://evil.invalid/steal"))
        with self.assertRaises(UnsafeUrlError):
            keycloak.ADAPTER.login(KEY, session=session)
        self.assertEqual(len(session.calls), 1)

    def test_a_form_action_off_the_sso_realm_is_refused(self):
        evil = LOGIN_PAGE.replace(LOGIN_ACTION, "https://sso.projudi.tjpr.jus.br/auth/realms/other/x")

        def routes(method, url, kwargs):
            if url.startswith(SSO + "protocol"):
                return response(200, evil)
            return login_routes()(method, url, kwargs)

        with self.assertRaises(UnsafeUrlError):
            keycloak.ADAPTER.login(KEY, session=FakeSession(routes))

    def test_login_that_lands_on_the_public_home_is_not_authenticated(self):
        def routes(method, url, kwargs):
            if url == APP + "home.do":
                return response(200, PUBLIC_HOME)
            return login_routes()(method, url, kwargs)

        with self.assertRaisesRegex(RuntimeError, "authenticated page"):
            keycloak.ADAPTER.login(KEY, session=FakeSession(routes))


class SessionTest(unittest.TestCase):
    def setUp(self):
        wired(self)

    def test_redirects_off_the_portal_or_to_the_login_entry_mean_expired(self):
        def redirect(location):
            return response(302, location=location, url=APP + "processo/x.do")

        self.assertTrue(
            keycloak.ADAPTER.is_login_redirect(redirect("https://sso.tjpr.jus.br/auth/realms/production/x"))
        )
        self.assertTrue(keycloak.ADAPTER.is_login_redirect(redirect(APP + "autenticacao.do")))
        self.assertFalse(keycloak.ADAPTER.is_login_redirect(redirect(APP + "processo/y.do")))
        self.assertFalse(keycloak.ADAPTER.is_login_redirect(response(200)))

    def test_session_expired_page_and_invalid_link_page(self):
        expired = FakeSession(lambda *_: response(200, "<p>A sessao expirou</p>"))
        with self.assertRaises(base.ProjudiSessionExpired):
            keycloak.ADAPTER.request(expired, KEY, "GET", "home.do")
        invalid = FakeSession(lambda *_: response(200, "<p>Conteúdo inacessível. O endereço ...</p>"))
        with self.assertRaises(base.ProjudiParseError):
            keycloak.ADAPTER.request(invalid, KEY, "GET", "home.do")

    def test_refresh_tells_a_live_session_from_the_public_home(self):
        live = FakeSession(lambda *_: response(200, "<a href='x?_tj=1'>x</a>"))
        public = FakeSession(lambda *_: response(200, PUBLIC_HOME))
        self.assertIs(keycloak.ADAPTER.refresh(KEY, live), live)
        self.assertIsNone(keycloak.ADAPTER.refresh(KEY, public))


def reads_routes(search_result=RESULT_PAGE):
    def routes(method, url, kwargs):
        if url == APP:
            return response(200, FRAMESET)
        if url == APP + "blank.html":
            return response(200, "<html></html>")
        if url.startswith(APP + "home.do"):
            return response(200, HOME)
        if url == APP + "processo/buscaProcessual.do?_tj=aaa111":
            return response(200, SEARCH_PAGE)
        if url.startswith(APP + "processo/buscaProcessual.do?_tj=ddd444"):
            return response(200, search_result)
        raise AssertionError(f"unexpected {method} {url}")

    return routes


class ReadsTest(unittest.TestCase):
    """SIMULATED reads: the menu, search form and result layout are hypotheses, not observed on the real portal."""

    def setUp(self):
        wired(self)

    def test_open_process_walks_the_menu_with_the_tj_link_and_posts_only_the_number(self):
        session = FakeSession(reads_routes())
        process = keycloak.ADAPTER.open_process(session, KEY, NUMBER)
        self.assertEqual(process["number"], NUMBER)
        self.assertEqual([m["date"] for m in process["movements"]], ["02/01/2026", "01/01/2026"])
        self.assertEqual(process["layout"], "unvalidated")
        search = [call for call in session.calls if call[0] == "POST"][0]
        self.assertEqual(search[2]["data"], {"_tjForm": "h1", "numeroProcesso": NUMBER})
        urls = [call[1] for call in session.calls]
        self.assertFalse(any("ciencia" in url or "peticionar" in url for url in urls))
        self.assertTrue(all("jsessionid" not in url for url in urls))

    def test_a_result_list_opens_the_one_entry_that_carries_the_number(self):
        listing = f'<html><a href="{APP}processo/detalhe.do?_tj=eee555">{NUMBER}</a></html>'

        def routes(method, url, kwargs):
            if url == APP + "processo/detalhe.do?_tj=eee555":
                return response(200, RESULT_PAGE)
            return reads_routes(listing)(method, url, kwargs)

        process = keycloak.ADAPTER.open_process(FakeSession(routes), KEY, NUMBER)
        self.assertEqual(len(process["movements"]), 2)

    def test_a_process_that_is_not_in_the_answer_is_not_found(self):
        with self.assertRaisesRegex(ValueError, "not found"):
            keycloak.ADAPTER.open_process(FakeSession(reads_routes("<html>Nenhum processo</html>")), KEY, NUMBER)

    def test_missing_menu_entry_fails_loudly(self):
        def routes(method, url, kwargs):
            return response(200, FRAMESET if url == APP else "<html><a href='x'>Outra coisa</a></html>")

        with self.assertRaisesRegex(base.ProjudiParseError, "Busca Processual Simples"):
            keycloak.ADAPTER.open_process(FakeSession(routes), KEY, NUMBER)

    def test_a_captcha_on_the_search_page_stops_for_a_person(self):
        captcha_search = SEARCH_PAGE.replace("</form>", '<div class="g-recaptcha"></div></form>')

        def routes(method, url, kwargs):
            if url == APP + "processo/buscaProcessual.do?_tj=aaa111":
                return response(200, captcha_search)
            return reads_routes()(method, url, kwargs)

        session = FakeSession(routes)
        with self.assertRaises(keycloak.HumanVerificationRequired):
            keycloak.ADAPTER.open_process(session, KEY, NUMBER)
        self.assertEqual([call for call in session.calls if call[0] == "POST"], [])

    def test_a_link_on_another_host_is_never_followed(self):
        home = HOME.replace(APP + "processo/buscaProcessual.do;jsessionid=ABC?_tj=aaa111", "https://evil.invalid/x")

        def routes(method, url, kwargs):
            if url.startswith(APP + "home.do"):
                return response(200, home)
            return reads_routes()(method, url, kwargs)

        session = FakeSession(routes)
        with self.assertRaises(base.ProjudiParseError):
            keycloak.ADAPTER.open_process(session, KEY, NUMBER)
        self.assertFalse(any("evil" in call[1] for call in session.calls))

    def test_documents_are_blocked_and_nothing_is_requested(self):
        session = FakeSession(lambda *_: self.fail("a blocked document read must not request anything"))
        adapter = keycloak.ADAPTER
        for call in (
            lambda: adapter.fetch_index(session, KEY),
            lambda: adapter.movement_files(session, KEY, "1"),
            lambda: adapter.download_url(KEY, "1", "h"),
            lambda: adapter.download(session, KEY, "1", "h"),
            lambda: base.load_process(adapter, FakeSession(reads_routes()), KEY, NUMBER),
        ):
            with self.subTest(call=call), self.assertRaises(keycloak.DocumentsBlocked):
                call()
        self.assertEqual(session.calls, [])


if __name__ == "__main__":
    unittest.main()
