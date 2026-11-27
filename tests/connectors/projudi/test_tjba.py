"""Projudi cnj-servlet adapter (TJBA): login against synthetic pages with the markup observed live on 2026-11-23.

The login form and the two refusal alerts ("Usuário ou senha inválidos." and "Senha Expirada! ...") keep the real
markup; the CAPTCHA answer, cookies, logins and passwords are synthetic. No logged-in page was observed, so the
"logged in" pages here are hypotheses, as in the adapter.
"""

import dataclasses
import json
import os
import unittest
from types import SimpleNamespace
from unittest import mock

from jurisync import portals
from jurisync.connectors.projudi import base, legacy, server, tjba
from jurisync.core.session_pool import LoginFailed, LoginRejected

KEY = "tjba-projudi-1g"
APP = "https://projudi.tjba.jus.br/projudi/"
LOGIN = "ba12345"
PASSWORD = "synthetic-pass"
ENV = {"TJBA_PROJUDI_USERNAME": LOGIN, "TJBA_PROJUDI_PASSWORD": PASSWORD}
ANSWER = json.dumps({"ret": 0, "ticket": "tr03synthetic", "randstr": "@abc", "appid": "189907471"})
COOKIES = [{"name": "JSESSIONID", "value": "synthetic", "domain": "projudi.tjba.jus.br", "path": "/projudi"}]

LOGIN_PAGE = """<html><head><script>
function submeter(event){ if(event == 'acesso'){ if (document.getElementById('senha').value == ''){
  alert('Informe a Senha.'); return; } document.formLogin.action = "/projudi/Logon"; captchaShow(); } }
function onSubmit(token) { document.formLogin.token.value = token; document.formLogin.submit(); }
</script><script language="JavaScript1.2" src="/projudi/scripts/captcha/tcentCaptcha.js" data-captcha-id="1"></script>
</head><body>
<form method="post" name="formLogin" id="formLogin" action="/projudi/Logon" onkeypress="submeterEnter()" onsubmit="return false;">
<input type="checkbox" name="tipoUsuario" id="tipoUsuario"/>
<input type="text" name="login" id="login" maxlength="25" size="25"/>
<input type="password" name="senha" id="senha" size="25" maxlength="25" title="Senha"/>
<input type="hidden" id="token" name="token"/><input type="submit" style="display:none;" ></form>
<form method="post" name="formConsultaPublica" id="formConsultaPublica" action="/projudi/buscas/ProcessosParte">
<input type="text" name="numeroProcesso" id="numeroProcesso"/><img src="/projudi/captcha.jpg"/>
<input type="text" name="captcha" id="captcha"/></form>
<div id="versao" align="right" style="color:#ddd"> Vers&atilde;o 10/11/2026 19:04 </div>
</body>
{alert}
</html>"""
EXPIRED = LOGIN_PAGE.replace(
    "{alert}",
    """ \t<script>alert('Senha Expirada! Gentileza resetar sua senha através do link "Não consegue acessar o sistema?"');</script> """,
)
INVALID = LOGIN_PAGE.replace("{alert}", "<script>alert('Usuário ou senha inválidos.');</script>")
NO_ALERT = LOGIN_PAGE.replace("{alert}", "")
# Hypotheses: no logged-in TJBA page has been seen.
DESK = '<html><frameset><frame src="/projudi/listagens/Exemplo"></frameset><a href="/projudi/Logoff">Sair</a></html>'
NEW_PASSWORD = """<html><form name="formSenha" action="/projudi/ModificarSenha" method="post">
<input type="password" name="novaSenha"><input type="password" name="confirmaSenha"></form></html>"""
SECOND_FACTOR = """<html><form name="formCodigo" action="/projudi/ValidarCodigo" method="post">
<input type="text" name="codigoAutenticacao"></form></html>"""


def response(text, status=200, url=APP + "Logon", headers=None):
    return SimpleNamespace(status_code=status, content=text.encode("utf-8"), headers=headers or {}, url=url)


class FakeSession:
    def __init__(self, post, gets=()):
        self.cookies = SimpleNamespace(set=mock.Mock())
        self._post, self._gets = post, list(gets)
        self.posts, self.urls = [], []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return self._post

    def get(self, url, **kwargs):
        self.urls.append(url)
        return self._gets.pop(0)


def wired(test, env=ENV):
    patcher = mock.patch.dict(portals.PORTALS, {KEY: dataclasses.replace(portals.PORTALS[KEY], wired=True)})
    environment = mock.patch.dict(os.environ, env)
    test.addCleanup(patcher.stop)
    test.addCleanup(environment.stop)
    patcher.start()
    environment.start()


class RegistryTest(unittest.TestCase):
    def test_tjba_projudi_is_known_but_unwired(self):
        portal = portals.PORTALS[KEY]
        self.assertFalse(portal.wired)
        self.assertEqual(
            (portal.flavor, portal.host, portal.root, portal.auth, portal.env_prefix, portal.region, portal.justice),
            ("cnj-servlet", "projudi.tjba.jus.br", "/projudi/", "password", "TJBA_PROJUDI_1G", "05", "8"),
        )
        self.assertEqual(portal.login_url, APP + "PaginaPrincipal.jsp")
        self.assertFalse(portal.guess_by_number)
        self.assertIs(server.ADAPTERS["cnj-servlet"], tjba.ADAPTER)
        self.assertNotIn(KEY, portals.keys("projudi"))
        self.assertEqual(portals.PORTALS["tjba-1g"].system, "pje")  # the PJe keeps the plain key

    def test_readiness_needs_login_and_password(self):
        wired(self, env={"TJBA_PROJUDI_USERNAME": LOGIN, "TJBA_PROJUDI_PASSWORD": ""})
        self.assertFalse(server.has_login(portals.projudi(KEY)))
        with mock.patch.dict(os.environ, ENV):
            self.assertTrue(server.has_login(portals.projudi(KEY)))


class LoginAnswerTest(unittest.TestCase):
    def test_alerts_map_to_refusal_reasons(self):
        self.assertEqual(tjba.login_alert(EXPIRED)[:15], "Senha Expirada!")
        self.assertEqual(tjba.login_alert(NO_ALERT), "")  # alerts inside functions are not answers
        cases = (
            (EXPIRED, "password_expired"),
            (INVALID, "credentials_refused"),
            (LOGIN_PAGE.replace("{alert}", "<script>alert('Usuário bloqueado.');</script>"), "blocked"),
            (LOGIN_PAGE.replace("{alert}", "<script>alert('Erro desconhecido.');</script>"), "unexpected"),
        )
        for page, reason in cases:
            with self.subTest(reason=reason), self.assertRaises(LoginRejected) as raised:
                tjba.check_login_answer(page)
            self.assertEqual(raised.exception.reason, reason)

    def test_a_captcha_message_is_not_a_credential_refusal(self):
        page = LOGIN_PAGE.replace("{alert}", "<script>alert('Captcha falhou!');</script>")
        with self.assertRaises(LoginFailed) as raised:
            tjba.check_login_answer(page)
        self.assertNotIsInstance(raised.exception, LoginRejected)
        self.assertEqual(raised.exception.reason, "captcha")

    def test_the_login_page_without_a_message_is_unexpected(self):
        with self.assertRaises(LoginFailed) as raised:
            tjba.check_login_answer(NO_ALERT)
        self.assertNotIsInstance(raised.exception, LoginRejected)

    def test_password_change_and_second_factor_pages_stop_the_login(self):
        for page, reason in ((NEW_PASSWORD, "password_expired"), (SECOND_FACTOR, "unexpected")):
            with self.subTest(reason=reason), self.assertRaises(LoginRejected) as raised:
                tjba.check_login_answer(page)
            self.assertEqual(raised.exception.reason, reason)
        tjba.check_login_answer(DESK)  # anything else passes on to the session check


class LoginTest(unittest.TestCase):
    def setUp(self):
        wired(self)

    def test_missing_credentials_send_nothing(self):
        solve = mock.Mock()
        with mock.patch.dict(os.environ, {"TJBA_PROJUDI_PASSWORD": ""}), self.assertRaises(legacy.MissingCredentials):
            tjba.ADAPTER.login(KEY, session=FakeSession(None), solve=solve)
        solve.assert_not_called()

    def test_no_password_is_sent_when_the_captcha_was_not_accepted(self):
        session = FakeSession(None)
        solve = mock.Mock(side_effect=LoginFailed("refused", reason="captcha"))
        with self.assertRaises(LoginFailed) as raised:
            tjba.ADAPTER.login(KEY, session=session, solve=solve)
        self.assertEqual(raised.exception.reason, "captcha")
        self.assertEqual(session.posts, [])

    def test_one_post_with_the_captcha_answer_then_the_session_check(self):
        session = FakeSession(response(DESK), gets=[response(DESK, url=APP + "PaginaPrincipal.jsp")])
        solve = mock.Mock(return_value=(ANSWER, COOKIES))
        self.assertIs(tjba.ADAPTER.login(KEY, session=session, solve=solve), session)
        solve.assert_called_once_with(APP + "PaginaPrincipal.jsp")
        session.cookies.set.assert_called_once_with(
            "JSESSIONID", "synthetic", domain="projudi.tjba.jus.br", path="/projudi"
        )
        [(url, kwargs)] = session.posts
        self.assertEqual(url, APP + "Logon")
        self.assertEqual(kwargs["data"], {"login": LOGIN, "senha": PASSWORD, "token": ANSWER})
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(session.urls, [APP + "PaginaPrincipal.jsp"])

    def test_refusals_are_rejections_and_are_not_retried(self):
        for page, reason in ((EXPIRED, "password_expired"), (INVALID, "credentials_refused")):
            with self.subTest(reason=reason):
                session = FakeSession(response(page))
                with self.assertRaises(LoginRejected) as raised:
                    tjba.ADAPTER.login(KEY, session=session, solve=mock.Mock(return_value=(ANSWER, COOKIES)))
                self.assertEqual(raised.exception.reason, reason)
                self.assertEqual(len(session.posts), 1)
                self.assertEqual(session.urls, [])
                self.assertNotIn(PASSWORD, str(raised.exception))

    def test_a_login_page_on_a_read_is_an_expired_session(self):
        session = SimpleNamespace(request=mock.Mock(return_value=response(NO_ALERT, url=APP + "listagens/Exemplo")))
        with self.assertRaises(base.ProjudiSessionExpired):
            tjba.ADAPTER.request(session, KEY, "GET", "listagens/Exemplo")
        redirect = response(
            "", status=302, url=APP + "listagens/Exemplo", headers={"Location": "/projudi/PaginaPrincipal.jsp"}
        )
        self.assertTrue(tjba.ADAPTER.is_login_redirect(redirect))

    def test_documents_stay_blocked(self):
        with self.assertRaises(legacy.DocumentsBlocked) as raised:
            tjba.ADAPTER.download(object(), KEY, "1", "abc")
        self.assertEqual(raised.exception.ciencia["estado"], "nenhuma")


if __name__ == "__main__":
    unittest.main()
