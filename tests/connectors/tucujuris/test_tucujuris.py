"""Tucujuris (TJAP) connector against SYNTHETIC API answers.

The fixtures follow the JSON contract read from the portal's public bundles (``js/ds.js``, ``servicos/*.js`` and the
Knockout templates that bind each field). No authenticated answer was captured: the live login of 2026-11-16 stopped
at "Essa conta de usuário não existe" (a CPF was configured; lawyers log in with OAB + seccional). Names and numbers
here are invented.
"""

import asyncio
import json
import os
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit

from jurisync import portals
from jurisync.connectors.tucujuris import client, process, server
from jurisync.core.session_pool import LoginRejected, SessionPool

FIXTURES = Path(__file__).parent / "fixtures"
HOST = "https://tucujuris.tjap.jus.br"
N1, N2, N3 = "0000101-11.2025.8.03.0001", "0000202-22.2024.8.03.0002", "0000303-33.2020.8.03.0001"
OTP_SECRET = "JBSWY3DPEHPK3PXP"  # the public example secret, not a real one
ENV = {
    "TJAP_TUCUJURIS_USERNAME": "OAB/AP 1234",
    "TJAP_TUCUJURIS_PASSWORD": "synthetic-password",
    "TJAP_TUCUJURIS_2FA_DATA": f"otpauth://totp/Tucujuris?secret={OTP_SECRET}",
}
PDF = b"%PDF-1.4\n% synthetic\n"


def fixture(name):
    return (FIXTURES / f"{name}.json").read_bytes()


class Response:
    def __init__(self, status=200, body=b"", content_type="application/json; charset=UTF-8", headers=None):
        self.status_code = status
        self.content = body
        self.headers = {"content-type": content_type, **(headers or {})}
        self.text = body.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.content)

    def iter_content(self, chunk_size=65536):
        for start in range(0, len(self.content), chunk_size):
            yield self.content[start : start + chunk_size]

    def close(self):
        pass


class FakeHttp:
    """Routes ``(METHOD, path)`` to answers; a list answers in turn. Records every request."""

    def __init__(self, routes):
        self.routes = {key: list(value) if isinstance(value, list) else value for key, value in routes.items()}
        self.requests = []
        self.closed = False

    def request(self, method, url, params=None, json=None, headers=None, **_):
        parts = urlsplit(url)
        self.requests.append(
            {"method": method, "path": parts.path, "params": list(params or []), "json": json, "headers": headers or {}}
        )
        answer = self.routes.get((method, parts.path))
        if answer is None:
            raise AssertionError(f"unexpected request {method} {parts.path}")
        if isinstance(answer, list):
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
        if callable(answer):
            answer = answer(params)
        return answer if isinstance(answer, Response) else Response(body=fixture(answer))

    def get(self, url, params=None, headers=None, **options):
        return self.request("GET", url, params=params, headers=headers, **options)

    def close(self):
        self.closed = True

    def calls(self, path):
        return [item for item in self.requests if item["path"] == path]


def api(path):
    return "/api/" + path


def comunicacoes_route(params):
    situacao = dict(params or []).get("situacao")
    return "comunicacoes_porprocesso" if situacao == "porprocesso" else "comunicacoes_naolidas"


def routes(overrides=None):
    base = {
        ("GET", "/pages/login/login.html"): Response(body=b"<html>login</html>", content_type="text/html"),
        ("POST", api("publico/login")): "login",
        ("POST", api("usuario/segundo-fator-verificar")): "code_ok",
        ("GET", api("usuario/buscar-varas-disponiveis")): "varas",
        ("GET", api("usuario/segundo-fator-situacao")): "situacao",
        ("GET", api("publico/buscar-passe-captcha")): "passe",
        ("GET", api("autos/buscar-autos-pesssoa-esta-envolvida")): lambda params: (
            "envolvido_1" if dict(params).get("situacao_processo") == "1" else "envolvido_0"
        ),
        ("GET", api("publico/buscar-detalhes-autos-consulta-publica")): "detalhes",
        ("GET", api("publico/buscar-anexos-processo-consulta-publica")): "anexos",
        ("GET", api("publico/buscar-anexos-movimento-consulta-publica")): "anexos_movimento",
        ("GET", api("publico/download-anexo-movimento-consulta-publica")): Response(
            body=PDF, content_type="application/pdf"
        ),
        ("GET", api("comunicacoes-processuais/buscar-comunicacoes")): comunicacoes_route,
        ("GET", api("comunicacoes-processuais/buscar-periodos-comunicacoes")): "periodos",
        ("GET", api("comunicacoes-processuais/buscar-tipos-comunicacao-periodo")): "tipos",
    }
    base.update(overrides or {})
    return base


class Base(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, ENV, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def login(self, overrides=None):
        self.http = FakeHttp(routes(overrides))
        # 10 s into a 30 s window: the code is used at once.
        return client.login(http_factory=lambda: self.http, clock=lambda: 1_800_000_010.0, sleep=lambda _: None)


class RegistryTest(unittest.TestCase):
    def test_portal_is_registered_but_never_guessed_from_a_number(self):
        portal = portals.tucujuris("tjap-tucujuris")
        self.assertEqual((portal.system, portal.tribunal, portal.env_prefix), ("tucujuris", "TJAP", "TJAP_TUCUJURIS"))
        self.assertEqual(portal.login_url, f"{HOST}/pages/login/login.html")
        self.assertNotIn(portal, portals.for_process(N1))
        self.assertIn(portals.PORTALS["tjap-1g"], portals.for_process(N1))
        self.assertIs(portals.for_host("tucujuris.tjap.jus.br"), portal)
        self.assertEqual(portals.keys("tucujuris"), ("tjap-tucujuris",))


class GuardTest(unittest.TestCase):
    def test_marking_a_comunicacao_read_is_refused_for_every_method(self):
        for method in ("POST", "GET", "PUT", "DELETE"):
            with self.subTest(method=method), self.assertRaisesRegex(client.UnsafeRequest, "ciência"):
                client.guard(method, "/comunicacoes-processuais/processar-comunicacao")

    def test_only_the_login_posts_and_the_read_gets_pass(self):
        self.assertEqual(client.guard("POST", "publico/login"), "publico/login")
        self.assertEqual(
            client.guard("GET", "comunicacoes-processuais/buscar-comunicacoes"),
            "comunicacoes-processuais/buscar-comunicacoes",
        )
        for method, path in (
            ("POST", "comunicacoes-processuais/buscar-comunicacoes"),
            ("GET", "publico/login"),
            ("POST", "usuario/segundo-fator-resetar"),
            ("POST", "usuario/definir-acesso-vara"),
            ("POST", "publico/seguir-processo"),
            ("POST", "peticao/protocolar"),
            ("PUT", "usuario/segundo-fator-situacao"),
        ):
            with self.subTest(method=method, path=path), self.assertRaises(client.UnsafeRequest):
                client.guard(method, path)

    def test_a_refused_request_is_never_sent(self):
        http = FakeHttp({})
        session = client.Session(http, "tok-synthetic-0123456789abcdef", client.config())
        with self.assertRaises(client.UnsafeRequest):
            client.call(
                http, session.portal, "POST", client.CIENCIA, body={"autos_pessoa_intimacao_id": 1}, token=session.token
            )
        self.assertEqual(http.requests, [])

    def test_login_names(self):
        for value, expected in (
            ("1234AP", "1234AP"), ("OAB/AP 1234", "1234AP"), ("1234/ap", "1234AP"), ("AP-01234", "1234AP"),
            ("529.982.247-25", "52998224725"), ("52998224725", "52998224725"), ("pessoa@example.com", "pessoa@example.com"),
        ):  # fmt: skip
            with self.subTest(value=value):
                self.assertEqual(client.login_name(value), expected)


class LoginTest(Base):
    def test_login_sends_the_credentials_once_then_the_code_with_the_token(self):
        session = self.login()
        self.assertEqual(session.token, "tok-synthetic-0123456789abcdef")
        (login,) = self.http.calls(api("publico/login"))
        self.assertEqual(
            login["json"], {"login": "1234AP", "senha": "synthetic-password", "permanente": False, "fonte": "tucujuris"}
        )
        self.assertNotIn("Auth-Token", login["headers"])
        (code,) = self.http.calls(api("usuario/segundo-fator-verificar"))
        self.assertRegex(code["json"]["codigo"], r"^\d{6}$")
        self.assertEqual(code["headers"]["Auth-Token"], session.token)
        self.assertTrue(code["headers"]["Tucujuris-Front-Url"].startswith(f"{HOST}/pages/"))
        self.assertEqual([r["method"] for r in self.http.requests].count("POST"), 2)

    def test_refused_credentials_are_login_rejected_and_close_the_session(self):
        with self.assertRaisesRegex(LoginRejected, "não existe"):
            self.login({("POST", api("publico/login")): "login_refused"})
        self.assertEqual(len(self.http.calls(api("publico/login"))), 1)
        self.assertEqual(self.http.calls(api("usuario/segundo-fator-verificar")), [])
        self.assertTrue(self.http.closed)

    def test_refused_code_is_login_rejected(self):
        with self.assertRaisesRegex(LoginRejected, "authenticator code"):
            self.login({("POST", api("usuario/segundo-fator-verificar")): "code_refused"})
        self.assertEqual(len(self.http.calls(api("usuario/segundo-fator-verificar"))), 1)

    def test_cloudflare_challenge_stops_before_the_credentials(self):
        challenge = Response(403, b"<html>Just a moment...</html>", "text/html", {"cf-mitigated": "challenge"})
        with self.assertRaises(client.HumanVerificationRequired):
            self.login({("GET", "/pages/login/login.html"): challenge})
        self.assertEqual(self.http.calls(api("publico/login")), [])

    def test_missing_authenticator_fails_before_any_request(self):
        self.http = FakeHttp({})
        with mock.patch.dict(os.environ, {"TJAP_TUCUJURIS_2FA_DATA": ""}), self.assertRaises(client.MissingCredentials):
            client.login(http_factory=lambda: self.http)
        self.assertEqual(self.http.requests, [])

    def test_enrolment_or_password_change_is_never_done(self):
        for flag, message in (
            ({"situcao_segundo_fator": "configuracao_pendente"}, "enroll"),
            ({"requer_trocar_senha": "x"}, "password"),
        ):
            data = json.loads(fixture("login"))
            data["dados"].update(flag)
            with self.subTest(flag=flag), self.assertRaisesRegex(LoginRejected, message):
                self.login({("POST", api("publico/login")): Response(body=json.dumps(data).encode())})
            self.assertEqual(self.http.calls(api("usuario/segundo-fator-verificar")), [])

    def test_code_near_the_end_of_its_window_waits_for_the_next(self):
        account = client.otp_account(client.config())
        waits = []
        client.fresh_code(account, clock=lambda: 1_800_000_027.0, sleep=waits.append)
        self.assertEqual(waits, [4.0])

    def test_refresh_drops_an_expired_token(self):
        session = self.login()
        self.assertIs(client.refresh(client.PORTAL, session), session)
        self.http.routes[("GET", api("usuario/segundo-fator-situacao"))] = Response(
            401, b'{"status":"ERRO","mensagem":"x"}'
        )
        self.assertIsNone(client.refresh(client.PORTAL, session))


class ReadTest(Base):
    def setUp(self):
        super().setUp()
        self.session = self.login()

    def assert_read_only(self):
        posts = [item["path"] for item in self.http.requests if item["method"] != "GET"]
        self.assertEqual(posts, [api("publico/login"), api("usuario/segundo-fator-verificar")])

    def test_list_processes_keeps_active_and_archived_apart(self):
        active = server.list_processes(self.session, False, None)
        self.assertEqual([item["number"] for item in active["processes"]], [N1, N2])
        first = active["processes"][0]
        self.assertEqual(first["court"], "1ª VARA CÍVEL DE MACAPÁ")
        self.assertEqual(first["nextDeadline"]["purpose"], "MANIFESTAÇÃO")
        self.assertEqual(
            first["parties"][0], {"name": "PARTE AUTORA SINTÉTICA", "role": "REQUERENTE", "pole": "ativo", "main": True}
        )
        self.assertNotIn("000.000.000-00", json.dumps(active, ensure_ascii=False))
        archived = server.list_processes(self.session, True, None)
        self.assertEqual([item["number"] for item in archived["processes"]], [N3])
        self.assertEqual(server.list_processes(self.session, False, "empresa ré")["count"], 2)
        self.assertEqual(server.list_processes(self.session, False, "00002022220248030002")["count"], 1)
        self.assert_read_only()

    def test_get_process_flags_the_movement_of_an_unread_comunicacao(self):
        data, files, pending = process.load_process(self.session, N1)
        result = server._public_process((data, files, pending), True)
        self.assertEqual(result["number"], N1)
        self.assertEqual([m["movementId"] for m in result["movements"]], ["7003", "7002", "7001"])
        self.assertTrue(result["movements"][0]["pendingCiencia"])
        self.assertNotIn("pendingCiencia", result["movements"][1])  # its comunicação was read
        self.assertEqual(result["pendingCienciaMovements"], ["7003"])
        self.assertEqual(result["parties"][0]["lawyers"], "ADVOGADO SINTÉTICO (1234AP)")
        documents = {item["documentId"]: item for item in result["documents"]}
        self.assertTrue(documents["9004"]["pendingCiencia"])
        self.assertTrue(documents["9005"]["outsideTucujuris"])
        self.assertNotIn("_autos", result)
        dumped = json.dumps(result)
        for internal in ("tj_autos_anexo", "nome_campo", "chave_consumo", "tok-synthetic"):
            self.assertNotIn(internal, dumped)
        (lookup,) = self.http.calls(api("comunicacoes-processuais/buscar-comunicacoes"))
        self.assertEqual(dict(lookup["params"]), {"situacao": "porprocesso", "numero_unico": N1})
        self.assert_read_only()

    def test_documents_of_a_pending_movement_are_never_downloaded(self):
        with self.assertRaises(process.CienciaPending):
            process.read_documents(self.session, N1, ["9003", "9004"])
        with self.assertRaises(process.CienciaPending):
            process.download_document(self.session, N1, "9004")
        self.assertEqual(self.http.calls(api("publico/download-anexo-movimento-consulta-publica")), [])
        self.assert_read_only()

    def test_read_documents_downloads_by_the_index_never_by_caller_input(self):
        with mock.patch.object(process, "extract_document_text", return_value=("extracted", "Decisão sintética")):
            number, documents = process.read_documents(self.session, N1, ["9003"])
        self.assertEqual(number, N1)
        self.assertEqual(documents[0]["extractionStatus"], "extracted")
        self.assertEqual(documents[0]["format"], "pdf")
        (download,) = self.http.calls(api("publico/download-anexo-movimento-consulta-publica"))
        self.assertEqual(
            dict(download["params"]), {"nome_tabela": "tj_autos_anexo", "id_tabela": "9003", "nome_campo": "arquivo"}
        )
        self.assertEqual(download["headers"]["Auth-Token"], self.session.token)
        with self.assertRaisesRegex(ValueError, "not in this process"):
            process.read_documents(self.session, N1, ["123456"])
        self.assert_read_only()

    def test_media_outside_tucujuris_is_not_fetched(self):
        number, documents = process.read_documents(self.session, N1, ["9005"])
        self.assertEqual(documents[0]["extractionStatus"], "outside_tucujuris")
        self.assertEqual(self.http.calls(api("publico/download-anexo-movimento-consulta-publica")), [])

    def test_movement_files(self):
        result = server.list_documents(self.session, N1, "7002")
        self.assertEqual([item["documentId"] for item in result["documents"]], ["9003"])
        with self.assertRaisesRegex(ValueError, "not a movement"):
            server.list_documents(self.session, N1, "1")

    def test_unknown_number_needs_the_public_search_which_asks_a_captcha(self):
        with self.assertRaisesRegex(process.ProcessNotFound, "CAPTCHA"):
            process.load_process(self.session, "0000999-99.2026.8.03.0001")
        self.assertEqual(self.http.calls(api("publico/buscar-autos-consulta-publica")), [])

    def test_captcha_protected_detail_is_reported_not_solved(self):
        self.http.routes[("GET", api("publico/buscar-detalhes-autos-consulta-publica"))] = "detalhes_captcha"
        with self.assertRaisesRegex(client.TucujurisError, "CAPTCHA"):
            process.load_process(self.session, N1)

    def test_other_tribunals_are_refused(self):
        with self.assertRaisesRegex(ValueError, "TJAP"):
            process.cnj("0000101-11.2025.8.04.0001")

    def test_list_intimacoes_lists_without_marking_read(self):
        result = process.comunicacoes(self.session, "naolidas", "ultimos10dias")
        self.assertEqual(result["counts"], {"hoje": 0, "ontem": 1, "ultimos10dias": 2})
        self.assertEqual([(c["number"], c["read"]) for c in result["comunicacoes"]], [(N1, False), (N2, False)])
        self.assertEqual(result["comunicacoes"][0]["movementId"], "7003")
        listing = self.http.calls(api("comunicacoes-processuais/buscar-comunicacoes"))[-1]
        self.assertEqual(
            listing["params"],
            [("situacao", "naolidas"), ("periodo", "ultimos10dias"), ("tipos[]", "1"), ("tipos[]", "4")],
        )
        process.comunicacoes(self.session, "lidas", "2026-10")
        self.assertIn(
            ("periodo", "2026-10-01"),
            self.http.calls(api("comunicacoes-processuais/buscar-tipos-comunicacao-periodo"))[-1]["params"],
        )
        by_process = process.comunicacoes(self.session, process_number=N1)
        self.assertEqual([c["read"] for c in by_process["comunicacoes"]], [False, True])
        self.assertEqual(by_process["comunicacoes"][1]["cienciaAt"], "2026-11-12 08:00:00")
        self.assertEqual(self.http.calls(api(client.CIENCIA)), [])
        self.assert_read_only()

    def test_no_types_means_no_listing(self):
        self.http.routes[("GET", api("comunicacoes-processuais/buscar-tipos-comunicacao-periodo"))] = "tipos_vazio"
        result = process.comunicacoes(self.session, "naolidas", "hoje")
        self.assertEqual(result["comunicacoes"], [])
        self.assertEqual(self.http.calls(api("comunicacoes-processuais/buscar-comunicacoes")), [])

    def test_bad_period_is_refused(self):
        with self.assertRaises(ValueError):
            process.comunicacoes(self.session, "naolidas", "2026-13")


class ServerTest(Base):
    def test_tools_and_annotations(self):
        tools = {tool.name: tool for tool in asyncio.run(server.create_server().list_tools())}
        self.assertEqual(
            set(tools),
            {
                "tucujuris_list_processes", "tucujuris_get_process", "tucujuris_list_documents",
                "tucujuris_read_documents", "tucujuris_download_document", "tucujuris_list_intimacoes",
            },
        )  # fmt: skip
        for name, tool in tools.items():
            with self.subTest(name=name):
                self.assertFalse(tool.annotations.destructive_hint)
                self.assertEqual(tool.annotations.read_only_hint, name != "tucujuris_download_document")

    def test_expired_token_logs_in_once_more_and_repeats(self):
        http = FakeHttp(routes())
        pool = SessionPool(
            login=lambda key: client.login(key, http_factory=lambda: http, clock=lambda: 1_800_000_010.0),
            refresh=client.refresh,
            close=lambda session: session.close(),
        )
        expired = Response(401, b'{"ambiente":"producao","status":"ERRO","mensagem":"sessao"}')
        http.routes[("GET", api("autos/buscar-autos-pesssoa-esta-envolvida"))] = [expired, "envolvido_0"]
        with mock.patch.object(server, "sessions", pool):
            result = server.call(lambda session: server.list_processes(session, False, None))
        self.assertEqual(result["count"], 2)
        self.assertEqual(len(http.calls(api("publico/login"))), 2)

    def test_rejected_login_is_not_retried(self):
        http = FakeHttp(routes({("POST", api("publico/login")): "login_refused"}))
        pool = SessionPool(
            login=lambda key: client.login(key, http_factory=lambda: http),
            refresh=client.refresh,
            close=lambda s: s.close(),
        )
        with mock.patch.object(server, "sessions", pool):
            for _ in range(2):
                with self.assertRaises(Exception):
                    server.call(lambda session: server.list_processes(session, False, None))
        self.assertEqual(len(http.calls(api("publico/login"))), 1)


if __name__ == "__main__":
    unittest.main()
