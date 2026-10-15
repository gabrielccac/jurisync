import asyncio
import os
import unittest
from unittest import mock

from mcp.client.client import Client

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
            self.assertEqual(server.configured_portals(), ("trf4-1g-sc", "trf4-1g-rs", "trf6-1g", "trf6-2g", "tjrs-1g"))
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


if __name__ == "__main__":
    unittest.main()
