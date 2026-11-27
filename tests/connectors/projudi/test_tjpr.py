"""TJPR: the Keycloak login in front of TJAM's desk (shapes observed live on 2026-11-24, synthetic content)."""

import contextlib
import dataclasses
import os
import unittest
from types import SimpleNamespace
from unittest import mock

from jurisync import portals
from jurisync.connectors.projudi import base, keycloak, parsers, tjam, tjpr

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
    """The desk's agenda shows 20 entries only: the "Novas" lists of both instances decide, with the desk's
    "Aguardando Ciência" intimações (to the lawyer and to the Sociedade de advogados) and online citações."""

    def read(self, complete=True, desk_complete=True):
        def read_list(_session, _portal, _section, _label, *, instance, max_pages):
            return {"rows": [{"processNumber": f"000000{instance}-00.2026.8.16.0001"}], "complete": complete}

        def read_desk(_session, _portal, key, *, max_pages):
            numbers = {
                "sociedadeAguardandoCiencia": {"0000003-00.2026.8.16.0001"},
                "citacoesAguardandoCiencia": {"0000004-00.2026.8.16.0001", "0000005-00.2026.8.16.0000"},
            }.get(key, set())
            return {"rows": [{"_numbers": numbers}], "complete": desk_complete}

        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(tjpr.ADAPTER, "_read_list", side_effect=read_list))
        self.desk = stack.enter_context(mock.patch.object(tjpr.ADAPTER, "_read_desk", side_effect=read_desk))
        return stack

    def test_the_desk_lists_to_the_sociedade_and_online_citacoes_count_too(self):
        for number in ("0000003-00.2026.8.16.0001", "0000004-00.2026.8.16.0001", "0000005-00.2026.8.16.0000"):
            with self.subTest(number=number), self.read():
                with self.assertRaises(base.CienciaPending):
                    base.check_ciencia(tjpr.ADAPTER, object(), KEY, number)
                self.assertEqual({call.args[2] for call in self.desk.call_args_list}, set(tjpr.DESK_AWAITING))
        with self.read(desk_complete=False):
            self.assertIn("in full", self.pending("0000009-00.2026.8.16.0001")["unchecked"])
        with (
            self.read(),
            mock.patch.object(tjpr.ADAPTER, "_read_desk", side_effect=base.ProjudiParseError("other filter")),
        ):
            self.assertIn("could not be read", self.pending("0000009-00.2026.8.16.0001")["unchecked"])

    def pending(self, number):
        return tjpr.ADAPTER.pending_ciencia(object(), KEY, number)

    def test_a_process_awaiting_ciencia_in_either_instance_is_reported(self):
        for number in ("0000001-00.2026.8.16.0001", "0000002-00.2026.8.16.0001"):
            with self.subTest(number=number), self.read():
                state = self.pending(number)
                self.assertEqual((state["items"][0]["process"], state["unchecked"]), (number, None))
                with self.assertRaises(base.CienciaPending):
                    base.check_ciencia(tjpr.ADAPTER, object(), KEY, number)
                block = base.check_ciencia(tjpr.ADAPTER, object(), KEY, number, acknowledge=True)
                self.assertEqual(block["estado"], "dada")

    def test_another_process_is_clear_and_an_incomplete_or_failed_list_is_unchecked(self):
        other = "0000009-00.2026.8.16.0001"
        with self.read():
            self.assertEqual(self.pending(other), {"items": [], "unchecked": None})
        with self.read(complete=False):
            self.assertIn("in full", self.pending(other)["unchecked"])
        for error in (RuntimeError("HTTP 500"), ValueError("no instance choice")):
            with mock.patch.object(tjpr.ADAPTER, "_read_list", side_effect=error):
                self.assertIn("could not be read", self.pending(other)["unchecked"])
        with mock.patch.object(tjpr.ADAPTER, "_read_list", side_effect=base.ProjudiSessionExpired("expired")):
            with self.assertRaises(base.ProjudiSessionExpired):
                self.pending(other)


FIRST = "0000001-00.2026.8.16.0001"
APPEAL = "0000002-00.2026.8.16.0000"


def desk_page(rows, total, *, lidas="false", destino="1", citations=False):
    """The Mesa do advogado tab as observed on 2026-11-27 (markup kept, content synthetic)."""
    radios = (
        f'<input type="radio" name="lidasCitacoes" value="false" {"checked" if lidas == "false" else ""}>'
        f'<input type="radio" name="lidasCitacoes" value="true" {"checked" if lidas == "true" else ""}>'
        if citations
        else f'<input type="radio" name="lidas" value="false" {"checked" if lidas == "false" else ""}>'
        f'<input type="radio" name="lidas" value="true" {"checked" if lidas == "true" else ""}>'
        f'<input type="radio" name="destinoIntimacao" value="1" {"checked" if destino == "1" else ""}>'
        f'<input type="radio" name="destinoIntimacao" value="2" {"checked" if destino == "2" else ""}>'
    )
    header = (
        "<th>Processo/Recurso<br>Parte</th><th>Tipo<br>Data Envio</th><th>Prazo Leitura Autom.</th>"
        if citations
        else "<th>Processo/Recurso<br>Parte</th><th>Evento<br>Prazo</th><th>Data Envio<br>Último Dia</th>"
    )
    count = (
        f"{total} registro(s) encontrado(s), exibindo de 1 at&eacute; {len(rows)}"
        if total
        else ""
        if not citations
        else "0 registro(s) encontrado(s)"
    )
    body = "".join(rows) or '<tr><td colspan="5">Nenhum registro encontrado</td></tr>'
    return f"""<html><a href="/projudi/usuario/logon.do?actionType=logout">Sair</a>
<form name="mesaAdvogadoForm" id="mesaAdvogadoForm" method="post" action="/projudi/usuario/mesaAdvogado.do?actionType=listaInicio">
<input type="hidden" name="selectedIcon" value="tabIntimacoes">{radios}
<table class="resultTable"><thead><tr>{header}<th>Inicio Prazo<br>Final Prazo</th><th></th></tr></thead>
<tbody>{body}</tbody></table><div class="navLeft">{count}</div></form></html>"""


def intimation_row(number, event, sent, last, start="", end="", appeal=False):
    target = "processo/recursal/recurso.do" if appeal else "processo.do"
    return f"""<tr class="even"><td><a class="link" href="/projudi/{target}?_tj=0a1b2c3d4e5f6a7b"> {number} </a><br>
<strong>PARTE EXEMPLO</strong><br> Vara Cível de Exemplo </td><td align="center"> DECISÃO<br> {event} </td>
<td align="center"> {sent}<br> <font color="#FF0000"> {last} </font></td><td align="center"> {start}<br>{end} </td>
<td><a class="link" href="/projudi/processo/intimacao.do?_tj=9f8e7d6c5b4a3f2e">Ver</a></td></tr>"""


class DeskTest(unittest.TestCase):
    AWAITING = desk_page(
        [
            intimation_row(FIRST, "(15 dias úteis)", "23/11/2026", "03/12/2026"),
            intimation_row(
                APPEAL,
                f"Da instância superior. Apelação Cível {FIRST} Ap. (Mera ciência, sem prazo)",
                "24/11/2026",
                "04/12/2026",
                appeal=True,
            ),
        ],
        2,
    )
    RUNNING = desk_page(
        [
            intimation_row(
                FIRST,
                "A AÇÃO. Veiculado no DJEN em 27/11/2026. (15 dias úteis)",
                "18/11/2026",
                "30/11/2026",
                "01/12/2026",
                "25/01/2027",
            ),
            intimation_row(FIRST, "(5 dias úteis)", "01/11/2026", "11/11/2026", "12/11/2026", "20/11/2026"),
        ],
        2,
        lidas="true",
    )

    def routes(self, pages):
        def answer(method, url, **kwargs):
            data = kwargs.get("data") or {}
            key = (data.get("lidas") or data.get("lidasCitacoes"), data.get("destinoIntimacao"), "actionType" in data)
            return SimpleNamespace(status_code=200, headers={}, content=pages[key].encode("latin-1"), url=url)

        session = mock.Mock()
        session.request.side_effect = answer
        return session

    def pages(self):
        return {
            ("false", "1", False): self.AWAITING,
            ("true", "1", False): self.RUNNING,
            ("false", "2", False): desk_page([], 0, destino="2"),
            ("true", "2", False): desk_page([], 0, lidas="true", destino="2"),
            ("false", None, True): desk_page([], 0, citations=True),
            ("true", None, True): desk_page([], 0, lidas="true", citations=True),
        }

    def test_rows_carry_instance_event_and_the_real_end_date(self):
        page = tjpr.parse_desk_page(self.RUNNING, "intimação")
        self.assertEqual((page["total"], page["checked"]), (2, {"lidas": "true", "destinoIntimacao": "1"}))
        row = page["rows"][0]
        self.assertEqual(
            {k: v for k, v in row.items() if k != "_numbers"},
            {
                "processNumber": FIRST,
                "instance": "1",
                "parte": "PARTE EXEMPLO",
                "orgao": "Vara Cível de Exemplo",
                "evento": "DECISÃO A AÇÃO. Veiculado no DJEN em 27/11/2026.",
                "prazo": "15 dias úteis",
                "dataEnvio": "18/11/2026",
                "ultimoDia": "30/11/2026",
                "inicioPrazo": "01/12/2026",
                "finalPrazo": "25/01/2027",
                "djen": "27/11/2026",
            },
        )
        appeal = tjpr.parse_desk_page(self.AWAITING, "intimação")["rows"][1]
        self.assertEqual((appeal["instance"], appeal["_numbers"]), ("2", {FIRST, APPEAL}))
        self.assertEqual(tjpr.parse_desk_page(desk_page([], 0, destino="2"), "intimação")["total"], 0)
        with self.assertRaises(base.ProjudiParseError):
            tjpr.parse_desk_page("<html>Erro geral</html>", "intimação")

    def test_the_desk_situation_follows_the_final_prazo(self):
        today = tjpr.tjgo_desk.parse_date("27/11/2026")
        self.assertEqual(tjpr.desk_situation({}, True, today)["situacao"], "aguardando_ciencia")
        running = tjpr.desk_situation({"finalPrazo": "25/01/2027"}, False, today)
        self.assertEqual(
            running, {"situacao": "prazo_correndo", "prazoFinal": "2027-01-25", "prazoFinalTipo": "portal"}
        )
        self.assertEqual(tjpr.desk_situation({"finalPrazo": "20/11/2026"}, False, today)["situacao"], "prazo_vencido")
        self.assertEqual(
            tjpr.desk_situation({"prazo": "Mera ciência, sem prazo"}, False, today)["situacao"], "sem_prazo"
        )

    def test_the_default_list_reads_every_desk_list_and_never_opens_one(self):
        session = self.routes(self.pages())
        with mock.patch.object(tjpr.tjgo_desk, "today", return_value=tjpr.tjgo_desk.parse_date("27/11/2026")):
            result = tjpr.ADAPTER.list_intimations(session, KEY)
        self.assertEqual(len(session.request.call_args_list), len(tjpr.DESK_LISTS))
        self.assertTrue(all("intimacao.do" not in call.args[1] for call in session.request.call_args_list))
        self.assertEqual(
            [(i["situacao"], i["instance"], i["cienciaPendente"]) for i in result["items"]],
            [("aguardando_ciencia", "1", True), ("aguardando_ciencia", "2", True), ("prazo_correndo", "1", False)],
        )
        self.assertEqual(result["items"][2]["prazoFinal"], "2027-01-25")
        self.assertEqual(result["omitted"], {"prazo_vencido": 1})
        self.assertEqual(result["awaitingCiencia"], {"processNumbers": [FIRST, APPEAL], "complete": True})
        self.assertEqual([summary["total"] for summary in result["desk"]], [2, 2, 0, 0, 0, 0])
        self.assertTrue(result["complete"])
        self.assertNotIn("_tj", str(result))
        self.assertNotIn("_numbers", str(result))
        sent = dict(session.request.call_args_list[0].kwargs["data"])
        self.assertEqual((sent["lidas"], sent["destinoIntimacao"], sent["pageSize"]), ("false", "1", "100"))

    def test_a_desk_page_with_another_filter_is_an_error(self):
        pages = self.pages()
        pages[("true", "1", False)] = self.AWAITING
        with self.assertRaisesRegex(base.ProjudiParseError, "another filter"):
            tjpr.ADAPTER.list_intimations(self.routes(pages), KEY)

    def test_menu_lists_read_both_instances_by_default(self):
        with mock.patch.object(tjam.TjamAdapter, "list_intimations", return_value={}) as menu:
            tjpr.ADAPTER.list_intimations(object(), KEY, lists=["novas"])
        self.assertEqual(menu.call_args.kwargs["instances"], ("1", "2"))


class DecodeTest(unittest.TestCase):
    def test_a_page_may_mix_utf8_and_latin1(self):
        self.assertEqual(parsers.decode("Intimações ".encode() + "Últimos".encode("latin-1")), "Intimações Últimos")
        self.assertEqual(parsers.decode("AÇÃO º é".encode("latin-1")), "AÇÃO º é")


if __name__ == "__main__":
    unittest.main()
