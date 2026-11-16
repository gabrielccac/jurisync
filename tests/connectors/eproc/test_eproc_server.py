import asyncio
import os
import subprocess
import sys
import unittest
from unittest import mock

from mcp.client.client import Client

from jurisync import portals
from jurisync.connectors.eproc import server


class EprocServerTest(unittest.TestCase):
    def call(self, srv, name, arguments):
        async def run():
            async with Client(srv) as client:
                return await client.call_tool(name, arguments)

        return asyncio.run(run())

    def test_portal_argument_lists_only_configured_portals(self):
        env = {"JFSC_USUARIO": "u", "JFSC_EXPORT_DATA": "JBSWY3DPEHPK3PXP"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(server.configured_portals(), ("trf4-1g-sc",))
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                server.configured_portals(),
                (
                    "tjmg-1g",
                    "tjrj-eproc-1g",
                    "tjpr-1g",
                    "trf4-1g-sc",
                    "trf4-1g-rs",
                    "trf6-1g",
                    "trf6-2g",
                    "trf4-1g-pr",
                    "trf4-2g",
                    "trf2-1g-rj",
                    "trf2-1g-es",
                    "trf2-2g",
                    "tjrs-1g",
                    "tjsc-1g",
                    "tjsc-2g",
                    "tjto-1g",
                    "tjto-2g",
                    "tjsp-1g",
                    "tjsp-2g",
                    "tnu",
                ),
            )
        shared = {"TRF6_USUARIO": "u", "TRF6_EXPORT_DATA": "JBSWY3DPEHPK3PXP"}
        with mock.patch.dict(os.environ, shared, clear=True):
            self.assertEqual(server.configured_portals(), ("trf6-1g", "trf6-2g"))

        async def schema():
            async with Client(server.create_server(["trf4-1g-sc"])) as client:
                tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            return tools["eproc_list_notices"].input_schema["properties"]["portal"]

        portal = asyncio.run(schema())
        self.assertEqual(portal.get("enum", [portal.get("const")]), ["trf4-1g-sc"])

    def test_unconfigured_portal_is_rejected_before_login(self):
        with mock.patch.object(server, "authenticate") as login:
            result = self.call(
                server.create_server(["trf4-1g-sc"]), "eproc_list_notices", {"portal": "trf4-1g-rs", "queue": "pending"}
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
                result = self.call(srv, "eproc_list_notices", {"portal": portal, "queue": "pending"})
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
            {"eproc_list_notices": True, "eproc_list_processes": True, "eproc_get_process": False},
        )
        tool = listed["eproc_get_process"]
        self.assertFalse(tool.annotations.destructive_hint)
        self.assertIn("relay ciencia.aviso to the user", tool.description)
        self.assertNotIn("acknowledgeCiencia", tool.input_schema["properties"])

    def test_document_ids_and_save_reach_the_client(self):
        seen = []

        def fetch(number, scope, document_ids, **kwargs):
            seen.append((document_ids, kwargs["save"]))
            self.assertNotIn("acknowledge_ciencia", kwargs)
            return {"success": True, "ciencia": {"pendente": True, "aviso": "informe o advogado"}}

        srv = server.create_server(["trf4-1g-sc"])
        arguments = {"portal": "trf4-1g-sc", "processNumber": "5000001-23.2026.4.04.7207", "documentIds": ["9"]}
        with (
            mock.patch.object(server.sessions, "_login", return_value=(mock.Mock(), "<html></html>", "https://x")),
            mock.patch.object(server, "fetch_process", side_effect=fetch),
        ):
            first = self.call(srv, "eproc_get_process", arguments)
            self.call(srv, "eproc_get_process", {**arguments, "save": True})
        self.assertEqual(seen, [(["9"], False), (["9"], True)])
        self.assertFalse(first.is_error)
        self.assertEqual(first.structured_content["ciencia"]["aviso"], "informe o advogado")
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
            "assert not loaded, loaded; assert s.anticipated_errors() == s.ERRORS"
        )
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])

    def test_browser_failures_are_anticipated_once_selenium_is_loaded(self):
        from selenium.common.exceptions import WebDriverException

        self.assertIn(WebDriverException, server.anticipated_errors())
        srv = server.create_server(["trf4-1g-sc"])
        with mock.patch.object(server.sessions, "_login", side_effect=WebDriverException("chrome crashed")):
            result = self.call(srv, "eproc_list_notices", {"portal": "trf4-1g-sc", "queue": "pending"})
        self.assertTrue(result.is_error)
        self.assertIn("chrome crashed", result.content[0].text)


if __name__ == "__main__":
    unittest.main()
