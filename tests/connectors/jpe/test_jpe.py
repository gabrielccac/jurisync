"""TJMG JPe (Themis) connector, offline.

Two fixtures are real public answers captured on 19/11/2026 with GET only: ``portal_principal.html`` (the login page
with its three logins) and ``sso_redirect_uri.html`` (what the PDPJ SSO answers when the JPe button sends a browser
to it: HTTP 400 "Parâmetro inválido: redirect_uri"). Every ``synthetic_*`` fixture is built from the public manual's
screenshots, because no authenticated page has been seen; their markup, names and numbers are invented.
"""

import asyncio
import os
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from jurisync import portals
from jurisync.connectors.jpe import client, parsers, process, server
from jurisync.core.session_pool import LoginRejected, SessionPool

FIXTURES = Path(__file__).parent / "fixtures"
HOST = "https://jpe.tjmg.jus.br"
ENTRY = "/rupe/portaljus/intranet/principal.rupe"
SSO = "https://sso.cloud.pje.jus.br"
SSO_AUTH = (
    f"{SSO}/auth/realms/pje/protocol/openid-connect/auth?response_type=code&client_id=jpe-tjmg-2g"
    "&redirect_uri=https://jpe.tjmg.jus.br/rupe/loginSsoKeycloakServlet/callback&scope=openid%20profile%20email"
)
OTP_SECRET = "JBSWY3DPEHPK3PXP"  # the public example secret, not a real one
OWN_ENV = {"TJMG_JPE_2G_LOGIN": "advogada.exemplo", "TJMG_JPE_2G_LOGIN_PASSWORD": "synthetic-password"}
SSO_ENV = {
    "TJMG_THEMIS_2G_USERNAME": "000.000.001-91",
    "TJMG_THEMIS_2G_PASSWORD": "synthetic-password",
    "TJMG_THEMIS_2G_2FA_DATA": f"otpauth://totp/PDPJ?secret={OTP_SECRET}",
}
PDF = b"%PDF-1.4\n% synthetic\n"
N_PENDING, N_FREE = "0000101-11.2014.8.13.0000", "0000202-22.2015.8.13.0000"


def fixture(name):
    return (FIXTURES / name).read_bytes()


class Headers(dict):
    """Case-insensitive, like curl_cffi's."""

    def __init__(self, values):
        super().__init__({key.lower(): value for key, value in values.items()})

    def get(self, key, default=None):
        return super().get(key.lower(), default)


class Response:
    def __init__(self, status=200, body=b"", content_type="text/html; charset=UTF-8", headers=None, url=""):
        self.status_code = status
        self.content = body
        self.headers = Headers({"content-type": content_type, **(headers or {})})
        self.text = body.decode("utf-8", "replace")
        self.url = url

    def iter_content(self, chunk_size=65536):
        for start in range(0, len(self.content), chunk_size):
            yield self.content[start : start + chunk_size]

    def close(self):
        pass


def redirect(location):
    return Response(302, b"", headers={"Location": location})


class FakeHttp:
    """Routes ``(METHOD, url without query or full url)`` to answers; a list answers in turn. Records requests."""

    def __init__(self, routes):
        self.routes = {key: list(value) if isinstance(value, list) else value for key, value in routes.items()}
        self.requests = []

    def request(self, method, url, data=None, headers=None, **_):
        self.requests.append(
            {"method": method, "url": url, "data": parse_qs(data, keep_blank_values=True) if data is not None else None}
        )
        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}{parts.path}"
        for key in ((method, url), (method, base)):
            if key in self.routes:
                answer = self.routes[key]
                answer = answer.pop(0) if isinstance(answer, list) else answer
                answer.url = url
                return answer
        raise AssertionError(f"unexpected request {method} {url}")

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def close(self):
        pass

    def posted(self):
        return [item for item in self.requests if item["method"] == "POST"]


def home_routes():
    return {
        ("GET", HOST + ENTRY): Response(body=fixture("synthetic_home.html")),
        ("GET", HOST + "/rupe/portaljus/jpe/comunicacoes.rupe?situacao=PL"): Response(
            body=fixture("synthetic_pendentes_leitura.html")
        ),
        ("GET", HOST + "/rupe/portaljus/jpe/comunicacoes.rupe?situacao=L"): Response(
            body=fixture("synthetic_lidas.html")
        ),
        ("GET", HOST + "/rupe/portaljus/jpe/consulta.rupe"): Response(body=fixture("synthetic_consulta.html")),
        ("POST", HOST + "/rupe/portaljus/jpe/consulta.rupe"): Response(body=fixture("synthetic_processo.html")),
        ("GET", HOST + "/rupe/portaljus/jpe/documento.rupe"): Response(body=PDF, content_type="application/pdf"),
    }


def logged_in(routes=None):
    http = FakeHttp(routes or home_routes())
    home = client.Page(HOST + ENTRY, fixture("synthetic_home.html").decode())
    return client.Session(http, portals.jpe(client.PORTAL), "jpe", home, {"home_url": HOST + ENTRY}), http


class RegistryTests(unittest.TestCase):
    def test_portal(self):
        portal = portals.jpe("tjmg-jpe-2g")
        self.assertEqual((portal.system, portal.tribunal, portal.instance), ("jpe", "TJMG", "2g"))
        self.assertEqual((portal.env_prefix, portal.legacy_env_prefixes), ("TJMG_JPE_2G", ("TJMG_THEMIS_2G",)))
        self.assertEqual(portal.login_url, HOST + ENTRY + "?contexto=jpe")
        self.assertIs(portals.for_host("jpe.tjmg.jus.br"), portal)
        self.assertNotIn(portal, portals.for_process(N_FREE))  # a number alone never points here

    def test_route_is_configuration(self):
        portal = portals.jpe(client.PORTAL)
        with mock.patch.dict(os.environ, OWN_ENV, clear=True):
            self.assertEqual(client.auth_route(portal), "jpe")
        with mock.patch.dict(os.environ, SSO_ENV, clear=True):  # legacy TJMG_THEMIS_2G names
            self.assertEqual(client.auth_route(portal), "sso")
        with mock.patch.dict(os.environ, {**SSO_ENV, **OWN_ENV}, clear=True):
            self.assertEqual(client.auth_route(portal), "jpe")
        with mock.patch.dict(os.environ, {**SSO_ENV, **OWN_ENV, "TJMG_JPE_2G_AUTH": "pdpj"}, clear=True):
            self.assertEqual(client.auth_route(portal), "sso")
        with mock.patch.dict(os.environ, {"TJMG_JPE_2G_USERNAME": "advogada.exemplo"}, clear=True):
            with self.assertRaisesRegex(client.MissingCredentials, "not a CPF"):
                client.auth_route(portal)
        with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(client.MissingCredentials):
            client.auth_route(portal)


class PublicPageTests(unittest.TestCase):
    def test_real_login_page(self):
        html = fixture("portal_principal.html").decode("iso-8859-1")
        self.assertTrue(parsers.is_login_page(html))
        self.assertFalse(parsers.is_authenticated(html))
        self.assertEqual(parsers.login_form(html)["action"], "/rupe/j_security_check")
        self.assertEqual(parsers.sso_form(html)["action"], "/rupe/loginSsoKeycloakServlet")
        names = {element.get("name") for element in parsers.login_form(html).find_all("input")}
        self.assertTrue({"j_username", "j_password"} <= names)

    def test_real_sso_refusal(self):
        self.assertEqual(
            parsers.sso_refusal(fixture("sso_redirect_uri.html").decode()), "Parâmentro inválido: redirect_uri"
        )
        self.assertEqual(parsers.sso_refusal(fixture("portal_principal.html").decode("iso-8859-1")), "")


class LoginTests(unittest.TestCase):
    def test_sso_redirect_uri_refusal_sends_no_credential(self):
        http = FakeHttp(
            {
                ("GET", HOST + ENTRY): Response(body=fixture("portal_principal.html"), content_type="text/html"),
                ("POST", HOST + "/rupe/loginSsoKeycloakServlet"): redirect(SSO_AUTH),
                ("GET", SSO + "/auth/realms/pje/protocol/openid-connect/auth"): Response(
                    400, fixture("sso_redirect_uri.html")
                ),
            }
        )
        with mock.patch.dict(os.environ, SSO_ENV, clear=True):
            with self.assertRaises(client.SsoRefused) as raised:
                client.login(http_factory=lambda: http)
        self.assertIn("o JPe recusou o login pelo PDPJ do lado do tribunal", str(raised.exception))
        self.assertIn("jpe-tjmg-2g", str(raised.exception))
        self.assertIsInstance(raised.exception, LoginRejected)  # the pool never retries it
        self.assertEqual([item["data"] for item in http.posted()], [{"origemURL": [""]}])  # no username or password

    def test_sso_login_sends_cpf_digits_on_pdpj_theme(self):
        login_page = (
            b'<form id="kc-form-login" action="https://sso.cloud.pje.jus.br/auth/realms/pje/login-actions/authenticate'
            b'?session_code=x"><input name="username"/><input name="password"/></form>'
        )
        otp_page = (
            b'<form id="kc-otp-login-form" action="https://sso.cloud.pje.jus.br/auth/realms/pje/login-actions/'
            b'authenticate?step=otp"><input name="otp"/></form>'
        )
        authenticate = SSO + "/auth/realms/pje/login-actions/authenticate"
        callback = HOST + "/rupe/loginSsoKeycloakServlet/callback?code=x"
        http = FakeHttp(
            {
                ("GET", HOST + ENTRY): [
                    Response(body=fixture("portal_principal.html"), content_type="text/html"),
                ],
                ("POST", HOST + "/rupe/loginSsoKeycloakServlet"): redirect(SSO_AUTH),
                ("GET", SSO + "/auth/realms/pje/protocol/openid-connect/auth"): Response(body=login_page),
                ("POST", authenticate): [Response(body=otp_page), redirect(callback)],
                ("GET", HOST + "/rupe/loginSsoKeycloakServlet/callback"): redirect(
                    HOST + "/rupe/portaljus/jpe/inicio.rupe"
                ),
                ("GET", HOST + "/rupe/portaljus/jpe/inicio.rupe"): Response(body=fixture("synthetic_home.html")),
            }
        )
        with mock.patch.dict(os.environ, SSO_ENV, clear=True):
            session = client.login(http_factory=lambda: http, clock=lambda: 1_000_000_005.0, sleep=lambda _: None)
        self.assertEqual(session.route, "sso")
        credentials, code = http.posted()[1]["data"], http.posted()[2]["data"]
        self.assertEqual(credentials["username"], ["00000000191"])
        self.assertTrue(code["otp"][0].isdigit())
        self.assertEqual(session.cache["home_url"], HOST + "/rupe/portaljus/jpe/inicio.rupe")

    def test_own_login(self):
        http = FakeHttp(
            {
                ("GET", HOST + ENTRY): Response(body=fixture("portal_principal.html"), content_type="text/html"),
                ("POST", HOST + "/rupe/j_security_check"): redirect(HOST + "/rupe/portaljus/jpe/inicio.rupe"),
                ("GET", HOST + "/rupe/portaljus/jpe/inicio.rupe"): Response(body=fixture("synthetic_home.html")),
            }
        )
        with mock.patch.dict(os.environ, OWN_ENV, clear=True):
            session = client.login(http_factory=lambda: http)
        self.assertEqual(session.route, "jpe")
        self.assertEqual(
            http.posted()[0]["data"], {"j_username": ["advogada.exemplo"], "j_password": ["synthetic-password"]}
        )

    def test_own_login_refused(self):
        http = FakeHttp(
            {
                ("GET", HOST + ENTRY): [
                    Response(body=fixture("portal_principal.html"), content_type="text/html"),
                    Response(body=fixture("portal_principal.html"), content_type="text/html"),
                ],
                ("POST", HOST + "/rupe/j_security_check"): redirect(HOST + ENTRY + "?contexto=jpe"),
            }
        )
        with mock.patch.dict(os.environ, OWN_ENV, clear=True), self.assertRaises(LoginRejected):
            client.login(http_factory=lambda: http)
        self.assertEqual(len(http.posted()), 1)

    def test_pool_does_not_retry_a_refusal(self):
        pool = SessionPool(login=mock.Mock(side_effect=client.SsoRefused("no")), refresh=None, close=None)
        for _ in range(2):
            with self.assertRaises(LoginRejected):
                pool.run(client.PORTAL, lambda session: session)
        self.assertEqual(pool._login.call_count, 1)


class ReadTests(unittest.TestCase):
    def test_counters(self):
        session, _ = logged_in()
        result = process.counters(session)
        self.assertEqual(result["communications"], {"pendingReading": 1, "pendingManifestation": 2})
        self.assertEqual(result["processes"]["active"], {"activePole": 3, "passivePole": 1})
        self.assertEqual(result["processes"]["notFiled"], {"activePole": 0, "passivePole": None})

    def test_communications_are_listed_never_opened(self):
        session, http = logged_in()
        result = process.communications(session, "pendentes_leitura")
        self.assertEqual((result["count"], result["total"], result["complete"]), (1, 1, True))
        item = result["communications"][0]
        self.assertEqual(
            (item["number"], item["tjNumber"], item["urgent"]), (N_PENDING, "1.0000.14.000101-1/000", False)
        )
        self.assertEqual(item["court"], "1ª CÂMARA CÍVEL")
        self.assertEqual(http.posted(), [])
        self.assertEqual(process.communications(session, "lidas")["count"], 0)

    def test_list_processes(self):
        session, http = logged_in()
        result = process.list_processes(session)
        self.assertEqual([item["number"] for item in result["processes"]], [N_PENDING, N_FREE])
        self.assertTrue(result["complete"])
        self.assertEqual(process.list_processes(session, "000202")["count"], 1)
        self.assertEqual(http.posted(), [])

    def test_get_process_clicks_only_the_view_control(self):
        session, http = logged_in()
        result = process.get_process(session, N_FREE)
        (posted,) = http.posted()
        self.assertEqual(posted["url"], HOST + "/rupe/portaljus/jpe/consulta.rupe")  # jsessionid left to the cookie
        self.assertIn("formConsulta:tabela:1:visualizar", posted["data"])
        self.assertEqual(posted["data"]["AJAXREQUEST"], ["_viewRoot"])
        self.assertEqual(posted["data"]["javax.faces.ViewState"], ["j_id4"])
        self.assertNotIn("formConsulta:tabela:0:peticionar", posted["data"])
        self.assertEqual(result["details"]["Comarca"], "BELO HORIZONTE")
        self.assertEqual(result["details"]["Relator"], "DES. RELATOR EXEMPLO")
        self.assertEqual(result["movements"][0], {"date": "03/10/2026 10:00", "description": "Conclusos ao relator"})
        self.assertEqual([item["documentId"] for item in result["documents"]], ["123401", "123402"])
        self.assertEqual(result["parties"][0], {"name": "PARTE AUTORA EXEMPLO", "type": "Apelante"})
        self.assertNotIn("acesso de terceiros", result["tabs"])

    def test_unknown_process_is_not_opened(self):
        session, http = logged_in()
        with self.assertRaises(process.ProcessNotFound):
            process.get_process(session, "0000303-33.2016.8.13.0000")
        self.assertEqual(http.posted(), [])

    def test_third_party_prompt_stops(self):
        routes = home_routes()
        routes[("POST", HOST + "/rupe/portaljus/jpe/consulta.rupe")] = Response(
            body=fixture("synthetic_justificativa.html")
        )
        session, http = logged_in(routes)
        with self.assertRaises(client.ThirdPartyAccess):
            process.get_process(session, N_FREE)
        self.assertEqual(len(http.posted()), 1)

    def test_read_document_of_a_free_process(self):
        session, http = logged_in()
        number, documents, ciencia = process.read_documents(session, N_FREE, ["123401"])
        self.assertEqual(number, N_FREE)
        self.assertEqual(ciencia, {"estado": "nenhuma"})
        self.assertEqual(documents[0]["format"], "pdf")
        self.assertEqual(http.requests[-1]["url"], HOST + "/rupe/portaljus/jpe/documento.rupe?idDocumento=123401")

    def test_pending_reading_blocks_the_read(self):
        session, http = logged_in()
        with self.assertRaises(process.CienciaPending):
            process.read_documents(session, N_PENDING, ["123401"])
        self.assertEqual(http.posted(), [])  # the process was not even opened
        self.assertFalse(any("documento.rupe" in item["url"] for item in http.requests))

    def test_acknowledged_read_reports_the_ciencia(self):
        routes = home_routes()
        session, _ = logged_in(routes)
        _, _, ciencia = process.read_documents(session, N_PENDING, ["123401"], acknowledge_ciencia=True)
        self.assertEqual(ciencia["estado"], "dada")
        self.assertEqual(ciencia["intimacoes"][0]["number"], N_PENDING)
        self.assertIn("ciência", ciencia["aviso"])

    def test_acknowledged_read_of_an_unchecked_list_is_desconhecida(self):
        routes = home_routes()
        routes[("GET", HOST + "/rupe/portaljus/jpe/comunicacoes.rupe?situacao=PL")] = Response(500, b"erro")
        session, _ = logged_in(routes)
        _, _, ciencia = process.read_documents(session, N_FREE, ["123401"], acknowledge_ciencia=True)
        self.assertEqual(ciencia["estado"], "desconhecida")
        self.assertNotIn("intimacoes", ciencia)

    def test_download_reports_the_ciencia(self):
        session, _ = logged_in()
        with mock.patch.object(process, "save_document", return_value="saved.pdf"):
            result = process.download_document(session, N_FREE, "123401")
        self.assertEqual(result["ciencia"], {"estado": "nenhuma"})
        self.assertTrue(result["saved"])

    def test_unreadable_pending_list_blocks_the_read(self):
        routes = home_routes()
        routes[("GET", HOST + "/rupe/portaljus/jpe/comunicacoes.rupe?situacao=PL")] = Response(500, b"erro")
        session, _ = logged_in(routes)
        with self.assertRaisesRegex(process.CienciaUnchecked, "ciência not checked"):
            process.read_documents(session, N_FREE, ["123401"])

    def test_forbidden_controls_are_refused(self):
        session, http = logged_in()
        page = client.Page(HOST + "/rupe/portaljus/jpe/consulta.rupe", fixture("synthetic_consulta.html").decode())
        soup = parsers.soup_of(page.html)
        for title in ("Peticionamento recursal, incidental e intermediário", "Cadastrar advogado"):
            control = soup.find("img", title=title).find_parent("a")
            with self.assertRaises(client.UnsafeAction):
                client.click(session, page, control, kind="view_process")
        comunicacoes = parsers.soup_of(fixture("synthetic_pendentes_leitura.html").decode())
        for title in ("Dados da comunicação", "Documentos da comunicação"):
            with self.assertRaises(client.UnsafeAction):
                client.click(session, page, comunicacoes.find("img", title=title).find_parent("a"), kind="download")
        with self.assertRaises(client.UnsafeAction):
            client.open_menu(session, "peticionamento")
        self.assertEqual(http.requests, [])

    def test_expired_session(self):
        routes = home_routes()
        routes[("GET", HOST + "/rupe/portaljus/jpe/consulta.rupe")] = Response(
            body=fixture("portal_principal.html"), content_type="text/html"
        )
        session, _ = logged_in(routes)
        with self.assertRaises(client.JpeSessionExpired):
            process.list_processes(session)


class ServerTests(unittest.TestCase):
    def test_configured_portals(self):
        for env, expected in (
            (OWN_ENV, ("tjmg-jpe-2g",)),
            (SSO_ENV, ("tjmg-jpe-2g",)),
            ({"TJMG_JPE_2G_USERNAME": "000.000.001-91", "TJMG_JPE_2G_PASSWORD": "x"}, ()),  # no authenticator
            ({}, ()),
        ):
            with self.subTest(env=sorted(env)), mock.patch.dict(os.environ, env, clear=True):
                self.assertEqual(server.configured_portals(), expected)

    def test_tools(self):
        tools = {tool.name: tool for tool in asyncio.run(server.create_server().list_tools())}
        self.assertEqual(
            set(tools),
            {
                "jpe_get_counters", "jpe_list_communications", "jpe_list_processes", "jpe_get_process",
                "jpe_read_documents", "jpe_download_document",
            },
        )  # fmt: skip
        for name in ("jpe_get_counters", "jpe_list_communications", "jpe_list_processes", "jpe_get_process"):
            self.assertTrue(tools[name].annotations.read_only_hint)
        for name in ("jpe_read_documents", "jpe_download_document"):
            self.assertFalse(tools[name].annotations.read_only_hint)
            self.assertIn("acknowledgeCiencia", tools[name].input_schema["properties"])


if __name__ == "__main__":
    unittest.main()
