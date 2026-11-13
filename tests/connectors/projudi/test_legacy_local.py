"""Projudi legacy-local adapter (TJAM) against SIMULATED responses.

The login page below keeps the structure observed live on the public TJAM page (2026-11-13): ``form#formLogin`` with
hidden ``tck``/``latitude``/``longitude``/``locationGranted``, ``submitLogin()`` with a per-render salt, a commented-out
reCAPTCHA block. The salt and every answer after the login are synthetic: no authenticated session was ever opened.
"""

import dataclasses
import hashlib
import os
import unittest
from types import SimpleNamespace
from unittest import mock

from jurisync import portals
from jurisync.connectors.projudi import base, legacy, server, tjam
from jurisync.core.http import UnsafeUrlError
from jurisync.core.session_pool import LoginRejected

KEY = "tjam-1g"
APP = "https://projudi.tjam.jus.br/projudi/"
ENTRY = APP + "usuario/logon.do?actionType=inicio"
ACTION = APP + "usuario/logon.do;jsessionid=ABC123?_tj=tok1"
SALT = "ab" * 32
LOGIN = "AM123456"  # synthetic UF000-style login
PASSWORD = "synthetic-pass"
NUMBER = "0000001-23.2026.8.04.0001"
ENV = {"TJAM_1G_USERNAME": LOGIN, "TJAM_1G_PASSWORD": PASSWORD}

LOGIN_PAGE = f"""<html><head><script src="/TSPD/?type=18"></script></head><body>
<form method="post" action="/projudi/usuario/logon.do;jsessionid=ABC123?_tj=tok1" name="formLogin" id="formLogin" onsubmit="return submitLogin()">
<input type="hidden" id="latitude" name="latitude"><input type="hidden" id="longitude" name="longitude">
<input type="hidden" id="locationGranted" name="locationGranted">
<input type="text" id="login" name="login" maxlength="30"><input type="password" id="senha" name="senha" maxlength="30">
<input type="submit" id="btEntrar" value="Entrar"><input type="hidden" name="tck" id="tck" value="principal"/></form>
<!-- Recaptcha
<div class="g-recaptcha" data-sitekey="x"></div>
-->
<script>function submitLogin(){{ document.formLogin.btEntrar.disabled=true; var login =document.formLogin.login.value;
var key = hex_md5("{SALT}" + login); document.formLogin.tck.value=key; document.formLogin.submit(); return false; }}</script>
</body></html>"""
LOGGED_IN = '<html><body><a href="/projudi/usuario/logon.do?actionType=logout&forcarLogout=true">Sair</a></body></html>'
REJECTED = "<html><body>Alguns erros foram encontrados: Usuário ou senha inválidos!" + LOGIN_PAGE + "</body></html>"
WAF = "<html><body>The requested URL was rejected. Request Rejected. Your support ID is: 1</body></html>"
FRAMESET = '<html><frameset><frame name="mainFrame" src="paginaPrincipal.jsp"></frameset></html>'
MAIN = f"""<html><body><a href="{APP}processo/busca.do;jsessionid=ABC?_tj=a1">Busca Processual</a>
<a href="{APP}processo/ciencia.do?_tj=b2">Ciência</a><a href="{APP}usuario/logon.do?actionType=logout">Sair</a></body></html>"""
SEARCH = f"""<html><form action="{APP}processo/busca.do?_tj=c3" method="post"><input type="hidden" name="h" value="1">
<input type="text" name="numeroProcesso"></form></html>"""
RESULT = f"<html><h2>{NUMBER}</h2><table><tr><td>03/01/2026</td><td>Despacho sintético</td></tr></table></html>"


def response(status=200, text="", location="", url=""):
    headers = {"Location": location} if location else {}
    return SimpleNamespace(status_code=status, headers=headers, text=text, content=text.encode("iso-8859-1"), url=url)


class FakeSession:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

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


def login_routes(*, entry_page=LOGIN_PAGE, post_page=LOGGED_IN, post_status=200):
    def routes(method, url, kwargs):
        if url == ENTRY:
            return response(200, entry_page)
        if url == ACTION:
            return response(post_status, post_page)
        if url == APP + "paginaPrincipal.jsp":
            return response(200, LOGGED_IN)
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
    def test_tjam_stays_unwired_and_unregistered(self):
        self.assertFalse(portals.PORTALS[KEY].wired)
        self.assertNotIn("legacy-local", server.ADAPTERS)
        self.assertEqual(portals.PORTALS[KEY].login_url, ENTRY)

    def test_there_is_no_certificate_login(self):
        self.assertNotIn("certificate", tjam.TjamAdapter.login.__code__.co_names)
        self.assertEqual(portals.PORTALS[KEY].auth, "password")


class LoginTest(unittest.TestCase):
    def setUp(self):
        wired(self)

    def test_tck_is_the_md5_of_salt_and_login_and_the_post_has_no_button(self):
        session = FakeSession(login_routes())
        self.assertIs(tjam.ADAPTER.login(KEY, session=session), session)
        (post,) = session.posts()
        self.assertEqual(post[1], ACTION)  # the page's action, ;jsessionid and _tj included, verbatim
        self.assertEqual(
            post[2]["data"],
            {
                "latitude": "",
                "longitude": "",
                "locationGranted": "false",
                "tck": hashlib.md5((SALT + LOGIN).encode(), usedforsecurity=False).hexdigest(),
                "login": LOGIN,
                "senha": PASSWORD,
            },
        )
        self.assertNotIn("btEntrar", post[2]["data"])

    def test_the_commented_out_recaptcha_is_not_a_challenge(self):
        self.assertFalse(legacy.has_human_verification(LOGIN_PAGE))
        self.assertTrue(legacy.has_human_verification(WAF))

    def test_cpf_with_punctuation_is_sent_as_digits(self):
        with mock.patch.dict(os.environ, {"TJAM_1G_USERNAME": "529.982.247-25"}):
            self.assertEqual(tjam.login_credentials(portals.PORTALS[KEY])[0], "52998224725")

    def test_missing_credentials_send_nothing(self):
        for name in ("TJAM_1G_USERNAME", "TJAM_1G_PASSWORD"):
            with self.subTest(name=name), mock.patch.dict(os.environ, {name: ""}):
                session = FakeSession(login_routes())
                with self.assertRaisesRegex(legacy.MissingCredentials, "precisa de login e senha da cliente"):
                    tjam.ADAPTER.login(KEY, session=session)
                self.assertEqual(session.calls, [])

    def test_wrong_password_is_one_post_and_never_retried(self):
        session = FakeSession(login_routes(post_page=REJECTED))
        with self.assertRaises(LoginRejected):
            tjam.ADAPTER.login(KEY, session=session)
        self.assertEqual(len(session.posts()), 1)

    def test_a_waf_page_on_the_login_or_after_the_post_stops_for_a_person(self):
        for routes in (login_routes(entry_page=WAF), login_routes(post_page=WAF)):
            session = FakeSession(routes)
            with self.assertRaisesRegex(legacy.HumanVerificationRequired, "precisa de uma pessoa"):
                tjam.ADAPTER.login(KEY, session=session)
        self.assertEqual(len(FakeSession(login_routes(entry_page=WAF)).posts()), 0)

    def test_a_page_without_the_form_or_the_salt_fails_loudly_without_posting(self):
        for page in ("<html>sem formulário</html>", LOGIN_PAGE.replace(SALT, "")):
            session = FakeSession(login_routes(entry_page=page))
            with self.assertRaises(base.ProjudiParseError):
                tjam.ADAPTER.login(KEY, session=session)
            self.assertEqual(session.posts(), [])

    def test_a_form_that_posts_off_the_login_path_is_refused(self):
        evil = LOGIN_PAGE.replace("/projudi/usuario/logon.do;jsessionid=ABC123?_tj=tok1", "https://evil.invalid/steal")
        with self.assertRaises(UnsafeUrlError):
            tjam.ADAPTER.login(KEY, session=FakeSession(login_routes(entry_page=evil)))

    def test_a_login_that_does_not_reach_a_logged_in_page_is_an_error_not_a_success(self):
        def routes(method, url, kwargs):
            if url == APP + "paginaPrincipal.jsp":
                return response(200, LOGIN_PAGE)
            return login_routes()(method, url, kwargs)

        with self.assertRaisesRegex(RuntimeError, "authenticated page"):
            tjam.ADAPTER.login(KEY, session=FakeSession(routes))


class SessionAndReadsTest(unittest.TestCase):
    def setUp(self):
        wired(self)

    def test_session_expired_sentinel_and_login_redirects(self):
        session = FakeSession(lambda *_: response(200, "<html><title>A sessao expirou</title></html>"))
        with self.assertRaises(base.ProjudiSessionExpired):
            tjam.ADAPTER.request(session, KEY, "GET", "paginaPrincipal.jsp")
        redirect = response(302, location=APP + "usuario/logon.do?actionType=inicio", url=APP + "x.do")
        self.assertTrue(tjam.ADAPTER.is_login_redirect(redirect))
        self.assertFalse(tjam.ADAPTER.is_login_redirect(response(302, location=APP + "y.do", url=APP + "x.do")))

    def test_open_process_walks_the_menu_and_never_follows_ciencia(self):
        def routes(method, url, kwargs):
            if url == APP:
                return response(200, FRAMESET)
            if url == APP + "paginaPrincipal.jsp":
                return response(200, MAIN)
            if url == APP + "processo/busca.do?_tj=a1":
                return response(200, SEARCH)
            if url == APP + "processo/busca.do?_tj=c3":
                return response(200, RESULT)
            raise AssertionError(f"unexpected {method} {url}")

        session = FakeSession(routes)
        process = tjam.ADAPTER.open_process(session, KEY, NUMBER)
        self.assertEqual(process["movements"], [{"date": "03/01/2026", "description": "Despacho sintético"}])
        self.assertEqual(process["layout"], "unvalidated")
        self.assertEqual(
            [call for call in session.calls if call[0] == "POST"][0][2]["data"], {"h": "1", "numeroProcesso": NUMBER}
        )
        self.assertFalse(any("ciencia" in call[1] or "logout" in call[1] for call in session.calls))

    def test_documents_are_blocked(self):
        session = FakeSession(lambda *_: self.fail("a blocked read must not request anything"))
        for call in (
            lambda: tjam.ADAPTER.fetch_index(session, KEY),
            lambda: tjam.ADAPTER.download(session, KEY, "1", "h"),
            lambda: tjam.ADAPTER.download_url(KEY, "1", "h"),
        ):
            with self.assertRaises(legacy.DocumentsBlocked):
                call()


if __name__ == "__main__":
    unittest.main()
