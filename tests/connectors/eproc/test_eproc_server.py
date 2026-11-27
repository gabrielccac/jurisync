import asyncio
import os
import subprocess
import sys
import unittest
from unittest import mock

from mcp.client.client import Client

from jurisync import portals
from jurisync.connectors.eproc import ciencia as eproc_ciencia
from jurisync.connectors.eproc import server
from jurisync.core import ciencia, errors


class EprocServerTest(unittest.TestCase):
    def call(self, srv, name, arguments):
        async def run():
            async with Client(srv) as client:
                return await client.call_tool(name, arguments)

        return asyncio.run(run())

    def test_portal_argument_lists_only_configured_portals(self):
        env = {"JFSC_USUARIO": "u", "JFSC_SENHA": "p", "JFSC_EXPORT_DATA": "JBSWY3DPEHPK3PXP"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(server.configured_portals(), ("trf4-1g-sc",))
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(server.configured_portals(), ())
        with mock.patch.dict(os.environ, {"JFSC_USUARIO": "u", "JFSC_EXPORT_DATA": "JBSWY3DPEHPK3PXP"}, clear=True):
            self.assertEqual(server.configured_portals(), ())  # no password
        shared = {"TRF6_USUARIO": "u", "TRF6_SENHA": "p", "TRF6_EXPORT_DATA": "JBSWY3DPEHPK3PXP"}
        with mock.patch.dict(os.environ, shared, clear=True):
            self.assertEqual(server.configured_portals(), ("trf6-1g", "trf6-2g"))

        async def schema():
            async with Client(server.create_server(["trf4-1g-sc"])) as client:
                tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            return tools["eproc_list_intimacoes"].input_schema["properties"]["portal"]

        portal = asyncio.run(schema())
        self.assertEqual(portal.get("enum", [portal.get("const")]), ["trf4-1g-sc"])

    def test_main_offers_and_logs_in_only_where_a_login_is_set(self):
        served = []
        env = {"JFSC_USUARIO": "u", "JFSC_SENHA": "p", "JFSC_EXPORT_DATA": "JBSWY3DPEHPK3PXP"}
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.object(server, "load_env_file"),
            mock.patch.object(server, "serve", side_effect=lambda srv, on_exit: served.append(srv)),
            mock.patch.object(server, "create_server", wraps=server.create_server) as create,
            mock.patch.object(server.sessions, "_only", None),
        ):
            server.main()
            self.assertEqual(create.call_args.args, (("trf4-1g-sc",),))
            self.assertEqual(server.sessions._only, ("trf4-1g-sc",))
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.object(server, "load_env_file"),
            mock.patch.object(server, "serve"),
            mock.patch.object(server, "create_server") as create,
            mock.patch.object(server.sessions, "_only", None),
        ):
            server.main()  # nothing configured: every wired portal, so the error names the missing login
            self.assertEqual(create.call_args.args, (portals.keys("eproc"),))

    def test_a_login_page_answer_logs_in_again_once_and_repeats(self):
        logins = []

        def login(portal):
            logins.append(portal)
            return mock.Mock(), "<html>panel</html>", "https://x"

        answers = iter(
            [
                {"success": False, "portal": "trf4-1g-sc", "items": [], "diagnostics": ["session_expired"]},
                {"success": True, "portal": "trf4-1g-sc", "items": [], "diagnostics": []},
            ]
        )
        srv = server.create_server(["trf4-1g-sc"])
        with (
            mock.patch.object(server.sessions, "_login", side_effect=login),
            mock.patch.object(server, "list_notices", side_effect=lambda queue, **context: next(answers)),
        ):
            result = self.call(srv, "eproc_list_intimacoes", {"portal": "trf4-1g-sc", "queue": "pending"})
        self.assertFalse(result.is_error)
        self.assertEqual(logins, ["trf4-1g-sc", "trf4-1g-sc"])
        server.sessions.close()

    def test_unconfigured_portal_is_rejected_before_login(self):
        with mock.patch.object(server, "authenticate") as login:
            result = self.call(
                server.create_server(["trf4-1g-sc"]),
                "eproc_list_intimacoes",
                {"portal": "trf4-1g-rs", "queue": "pending"},
            )
        self.assertTrue(result.is_error)
        login.assert_not_called()

    def test_each_portal_keeps_its_own_session(self):
        logins = []

        def login(portal):
            logins.append(portal)
            return mock.Mock(name=f"{portal}-session"), "<html>panel</html>", f"https://{portal}.example/panel"

        def notices(queue, *, session, panel_html, panel_url, portal):
            return {"success": True, "portal": portal, "queue": queue, "items": []}

        srv = server.create_server(["trf4-1g-sc", "trf4-1g-rs"])
        with (
            mock.patch.object(server.sessions, "_login", side_effect=login),
            mock.patch.object(server, "list_notices", side_effect=notices),
        ):
            for portal in ("trf4-1g-sc", "trf4-1g-rs", "trf4-1g-sc"):
                result = self.call(srv, "eproc_list_intimacoes", {"portal": portal, "queue": "pending"})
                self.assertEqual(result.structured_content["portal"], portal)
        self.assertEqual(logins, ["trf4-1g-sc", "trf4-1g-rs"])
        server.sessions.close()

    def test_get_process_is_not_read_only_and_tells_to_relay_the_ciencia_notice(self):
        async def tools():
            async with Client(server.create_server(["trf4-1g-sc"])) as client:
                return {tool.name: tool for tool in (await client.list_tools()).tools}

        listed = asyncio.run(tools())
        # It can store a file and, by opening a document, register a ciência.
        self.assertEqual(
            {name: tool.annotations.read_only_hint for name, tool in listed.items()},
            {"eproc_list_intimacoes": True, "eproc_list_processes": True, "eproc_get_process": False},
        )
        tool = listed["eproc_get_process"]
        self.assertTrue(tool.annotations.destructive_hint)  # a ciência starts a deadline: the same rule in every portal
        self.assertIn("acknowledgeCiencia", tool.input_schema["properties"])
        self.assertIn(ciencia.AUTHORIZATION, tool.description)

    def test_document_ids_and_save_reach_the_client(self):
        seen = []

        def fetch(number, scope, document_ids, **kwargs):
            seen.append((document_ids, kwargs["save"], kwargs["acknowledge_ciencia"]))
            return {"success": True, "ciencia": {"estado": "dada", "aviso": "informe o advogado"}}

        srv = server.create_server(["trf4-1g-sc"])
        arguments = {"portal": "trf4-1g-sc", "processNumber": "5000001-23.2026.4.04.7207", "documentIds": ["9"]}
        with (
            mock.patch.object(server.sessions, "_login", return_value=(mock.Mock(), "<html></html>", "https://x")),
            mock.patch.object(server, "fetch_process", side_effect=fetch),
        ):
            first = self.call(srv, "eproc_get_process", arguments)
            self.call(srv, "eproc_get_process", {**arguments, "save": True, "acknowledgeCiencia": True})
            with mock.patch.object(server, "ciencia_on_read", return_value="allow") as setting:
                self.call(srv, "eproc_get_process", arguments)
                self.call(srv, "eproc_get_process", {**arguments, "acknowledgeCiencia": False})
            setting.assert_called_with(system="eproc")
        self.assertEqual(
            seen, [(["9"], False, False), (["9"], True, True), (["9"], False, True), (["9"], False, False)]
        )
        self.assertFalse(first.is_error)
        self.assertEqual(first.structured_content["ciencia"]["aviso"], "informe o advogado")
        server.sessions.close()

    def test_a_ciencia_refusal_is_a_tool_error_that_says_how_to_go_on(self):
        srv = server.create_server(["trf4-1g-sc"])
        arguments = {"portal": "trf4-1g-sc", "processNumber": "5000001-23.2026.4.04.7207", "documentIds": ["9"]}
        refusal = eproc_ciencia.CienciaPending(f"ciência pending: nothing was opened. {ciencia.AUTHORIZATION}.")
        with (
            mock.patch.object(server.sessions, "_login", return_value=(mock.Mock(), "<html></html>", "https://x")),
            mock.patch.object(server, "fetch_process", side_effect=refusal),
        ):
            result = self.call(srv, "eproc_get_process", arguments)
        self.assertTrue(result.is_error)
        text = result.content[0].text
        self.assertIn("ciência pending", text)
        self.assertIn("acknowledgeCiencia=true", text)
        server.sessions.close()

    def test_refresh_reads_the_panel_of_every_portal_family(self):
        """TNU names its panel painel_advogado_listar: a refresh that raises would force a full login every call."""
        for portal, action in (
            ("trf4-1g-sc", "painel_adv_listar"),
            ("trf6-1g", "painel_adv_listar"),
            ("tjrs-1g", "painel_adv_listar"),
            ("tjsp-1g", "painel_adv_listar"),
            ("tjto-1g", "painel_adv_listar"),
            ("tnu", "painel_advogado_listar"),
        ):
            with self.subTest(portal=portal):
                panel_url = portals.eproc(portal).url(f"controlador.php?acao={action}&hash=h")
                body = f'<html><a href="controlador.php?acao={action}&hash=h">Painel</a></html>'
                session = mock.Mock()
                session.request.return_value = mock.Mock(status_code=200, content=body.encode(), headers={})
                self.assertEqual(server.refresh_panel(portal, (session, "old", panel_url)), (session, body, panel_url))
                self.assertEqual(session.request.call_args.args, ("GET", panel_url))
                # A session the portal sent back to its login page is reported as dead, not refreshed.
                session.request.return_value = mock.Mock(
                    status_code=200, content=b'<form><input name="username"></form>', headers={}
                )
                self.assertIsNone(server.refresh_panel(portal, (session, "old", panel_url)))

    def test_pool_keeps_the_tnu_session_after_idle_instead_of_logging_in_again(self):
        panel_url = portals.eproc("tnu").url("controlador.php?acao=painel_advogado_listar&hash=h")
        session = mock.Mock()
        session.request.return_value = mock.Mock(
            status_code=200, content=b'<a href="controlador.php?acao=painel_advogado_listar">P</a>', headers={}
        )
        logins = []

        def login(portal):
            logins.append(portal)
            return session, "<html>panel</html>", panel_url

        pool = server.SessionPool(
            login=login, refresh=server.refresh_panel, close=lambda value: value[0].close(), revalidate_after=-1
        )
        for _ in range(3):
            with pool.session("tnu") as value:
                self.assertIs(value[0], session)
        self.assertEqual(logins, ["tnu"])
        self.assertEqual(session.request.call_count, 2)

    def test_server_import_does_not_load_selenium(self):
        code = (
            "import sys; import jurisync.connectors.eproc.server as s; import jurisync.connectors.eproc.certificate; "
            "loaded = sorted(m for m in sys.modules if m.split('.')[0] in ('selenium', 'seleniumbase')); "
            "from jurisync.core import errors; assert not loaded, loaded; "
            "assert 'WebDriverException' not in [kind.__name__ for kind in errors._network_errors()]"
        )
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])

    def test_browser_failures_are_anticipated_once_selenium_is_loaded(self):
        from selenium.common.exceptions import WebDriverException

        self.assertIn(WebDriverException, errors._network_errors())
        srv = server.create_server(["trf4-1g-sc"])
        with mock.patch.object(server.sessions, "_login", side_effect=WebDriverException("chrome crashed")):
            result = self.call(srv, "eproc_list_intimacoes", {"portal": "trf4-1g-sc", "queue": "pending"})
        self.assertTrue(result.is_error)
        self.assertIn("chrome crashed", result.content[0].text)


if __name__ == "__main__":
    unittest.main()
