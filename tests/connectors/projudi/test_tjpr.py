"""TJPR: the Keycloak login in front of TJAM's desk (shapes observed live on 2026-11-24, synthetic content)."""

import dataclasses
import os
import unittest
from types import SimpleNamespace
from unittest import mock

from jurisync import portals
from jurisync.connectors.projudi import base, keycloak, legacy, parsers, tjam, tjpr

KEY = "tjpr-projudi-1g"
APP = "https://projudi.tjpr.jus.br/projudi/"
SSO = "https://sso.projudi.tjpr.jus.br/auth/realms/projudi/"
ENTRY = APP + "autenticacao.do?_tj=abc123"
HOME = """<ul class="acessos"><li class="interno" onclick="document.location.href='/projudi/tjpr/autenticacao.do?_tj=999';">
</li><li class="externo" onclick="document.location.href='/projudi/autenticacao.do?_tj=abc123';"></li></ul>"""
MENU = """<nav><ul id="main-menu"><li><a href="/projudi/usuario/mesaAdvogado.do?_tj=1">Início</a></li>
<li><a href="#">Ações 1º Grau</a><ul><li><a href="/projudi/processosAdvogado.do?_tj=2">Processos Ativos</a></li>
<li><a href="https://example.com/x">Fora</a></li></ul></li>
<li><a href="#">Intimações</a><ul><li><a href="/projudi/processo/intimacaoAdvogado.do?_tj=3">Novas (Aguardando Leitura)</a>
</li></ul></li></ul></nav>"""
LOGIN = f'<form id="kc-form-login" action="{SSO}login-actions/authenticate?x=1"><input name="username"></form>'
ENV = {
    "TJPR_PROJUDI_1G_USERNAME": "00000000000",
    "TJPR_PROJUDI_1G_PASSWORD": "x",
    "TJPR_PROJUDI_1G_2FA_DATA": "JBSWY3DPEHPK3PXP",
}


def response(status=200, text="", location=""):
    return SimpleNamespace(
        status_code=status, headers={"Location": location} if location else {}, text=text, content=text.encode(), url=""
    )


class Session:
    def __init__(self, home=HOME, back=MENU):
        self.home, self.back, self.calls = home, back, []

    def _answer(self, method, url):
        self.calls.append((method, url))
        if method == "POST":
            result = response(302, location=APP + "autenticacao.do?_tj=abc123&code=C")
        elif url == ENTRY:
            result = response(302, location=SSO + "protocol/openid-connect/auth")
        elif url.startswith(SSO):
            result = response(200, LOGIN)
        elif url.startswith(ENTRY + "&code="):
            result = response(200, self.back)
        elif url == APP + tjam.DESK:
            result = response(200, '<a href="usuario/logon.do?actionType=logout">Sair</a>')
        elif url in (APP, APP + "home.do"):
            result = response(200, self.home)
        else:
            raise AssertionError(f"unexpected {method} {url}")
        result.url = url
        return result

    def get(self, url, **_kwargs):
        return self._answer("GET", url)

    def post(self, url, **_kwargs):
        return self._answer("POST", url)


class LoginTest(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, ENV)
        env.start()
        self.addCleanup(env.stop)

    def test_login_enters_by_the_home_page_link_and_keeps_the_menu(self):
        session = Session()
        tjpr.ADAPTER.login(KEY, session=session)
        self.assertIn(("GET", ENTRY), session.calls)
        self.assertNotIn(("GET", APP + "autenticacao.do"), session.calls)
        self.assertEqual(len([call for call in session.calls if call[0] == "POST"]), 1)
        self.assertEqual(
            tjpr.ADAPTER._entries(session, KEY),
            [
                ("", "Início", "/usuario/mesaAdvogado.do?_tj=1"),
                ("Ações 1º Grau", "Processos Ativos", "/processosAdvogado.do?_tj=2"),
                ("Intimações", "Novas (Aguardando Leitura)", "/processo/intimacaoAdvogado.do?_tj=3"),
            ],
        )

    def test_home_without_the_lawyer_link_sends_no_credential(self):
        session = Session(home="<p>manutenção</p>")
        with self.assertRaises(base.ProjudiParseError):
            tjpr.ADAPTER.login(KEY, session=session)
        self.assertFalse([call for call in session.calls if call[0] == "POST"])

    def test_the_generic_error_after_the_sso_is_not_a_login(self):
        with self.assertRaisesRegex(RuntimeError, "lawyer menu"):
            tjpr.ADAPTER.login(KEY, session=Session(back="<li>Erro geral</li>"))

    def test_a_session_without_menu_logs_in_again(self):
        with self.assertRaises(base.ProjudiSessionExpired):
            tjpr.ADAPTER._menu(Session(), KEY)

    def test_tjrr_keeps_its_own_entry(self):
        config = dataclasses.replace(portals.PORTALS["tjrr-1g"], wired=True)
        self.assertTrue(keycloak.ADAPTER._entry_url(None, config).endswith("/tjpr/autenticacao.do"))


class CienciaTest(unittest.TestCase):
    """The desk's agenda shows 20 entries only: the "Novas" lists of both instances decide."""

    def read(self, complete=True):
        def read_list(_session, _portal, _section, _label, *, instance, max_pages):
            return {"rows": [{"processNumber": f"000000{instance}-00.2026.8.16.0001"}], "complete": complete}

        return mock.patch.object(tjpr.ADAPTER, "_read_list", side_effect=read_list)

    def download(self, number):
        with (
            mock.patch.object(tjpr.ADAPTER, "_process_files", return_value={"number": number}),
            mock.patch.object(base.Adapter, "download", return_value=(b"%PDF-", "application/pdf")) as opened,
        ):
            try:
                return tjpr.ADAPTER.download(object(), KEY, "1001", "a" * 16), opened
            except legacy.DocumentsBlocked as blocked:
                self.assertFalse(opened.called)
                return blocked, opened

    def test_a_process_awaiting_ciencia_in_either_instance_is_refused(self):
        for number in ("0000001-00.2026.8.16.0001", "0000002-00.2026.8.16.0001"):
            with self.subTest(number=number), self.read():
                blocked, _ = self.download(number)
                self.assertEqual(blocked.ciencia["estado"], "pendente")

    def test_another_process_opens_and_an_incomplete_list_refuses(self):
        with self.read():
            result, opened = self.download("0000009-00.2026.8.16.0001")
            self.assertTrue(opened.called)
            self.assertEqual(result[1], "application/pdf")
        with self.read(complete=False):
            self.assertIsInstance(self.download("0000009-00.2026.8.16.0001")[0], legacy.DocumentsBlocked)


class DecodeTest(unittest.TestCase):
    def test_a_page_may_mix_utf8_and_latin1(self):
        self.assertEqual(parsers.decode("Intimações ".encode() + "Últimos".encode("latin-1")), "Intimações Últimos")
        self.assertEqual(parsers.decode("AÇÃO º é".encode("latin-1")), "AÇÃO º é")


if __name__ == "__main__":
    unittest.main()
