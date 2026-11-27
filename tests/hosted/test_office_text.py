"""What the server says to an office (MCP instructions, tool titles and descriptions) names only systems and portals the
office has: the real court connectors, each office with its own logins."""

import asyncio
import json
import os
import re
import tempfile
from pathlib import Path
from unittest import mock

from mcp.server.mcpserver import MCPServer
from starlette.testclient import TestClient

from jurisync import portals, registry
from jurisync.hosted.app import REQUIRE_RESOURCE_FLAG, SYSTEMS, Office, build_app
from jurisync.hosted.store import Store
from jurisync.hosted.vault import Vault, generate_key, load_key

from .test_office_endpoint import OfficeCase, configured
from .test_signin_security import BASE, PASSWORD

COURTS = registry.GROUPS[registry.COURTS]
# office-a: PJe (one TRF5 login) and DJEN. office-b: one login on each of four systems, STJ enabled without a login.
OFFICES = {
    "office-a": (["pje", "djen"], "trf5-1g"),
    "office-b": (["pje", "eproc", "esaj", "projudi", "stj"], "trf5-1g,tjrs-1g,tjsp-esaj,tjgo-1g"),
}


def court(key):
    """The court a portal key names: 'tjam-1g' -> 'TJAM', 'trf6-pje-1g' -> 'TRF6', 'tst' -> 'TST'."""
    return key.split("-")[0].upper()


class OfficeTextCase(OfficeCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.store = Store(root / "store.sqlite")
        self.addCleanup(self.store.close)
        self.vault = Vault(root / "vault", load_key(generate_key()))
        for tenant, (connectors, logins) in OFFICES.items():
            self.store.add_tenant(tenant, tenant, connectors=connectors)
            self.store.add_user(f"user@{tenant}.test", tenant, PASSWORD)
            self.vault.put(tenant, {"CONFIGURED": logins})
        for system in COURTS:
            if registry.get(system).portals():
                patch = mock.patch(f"{registry.get(system).module}.configured_portals", configured(system), create=True)
                patch.start()
                self.addCleanup(patch.stop)
        with mock.patch.dict(os.environ, {REQUIRE_RESOURCE_FLAG: ""}):
            self.app, closers = build_app(BASE, self.store, self.vault, COURTS)
        for close in closers:
            self.addCleanup(close)

    def initialize(self, client, email, path):
        token = self.token(client, email, resource=f"{BASE}{path}")
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "1"},
            },
        }
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"}
        response = client.post(path, json=payload, headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        data = next(line[5:] for line in response.text.splitlines() if line.startswith("data:"))
        return json.loads(data)["result"]["instructions"]


class InstructionsTest(OfficeTextCase):
    def test_an_office_with_pje_and_djen_reads_only_those(self):
        with TestClient(self.app, base_url=BASE) as client:
            for path in ("/tribunais/mcp", "/mcp"):
                text = self.initialize(client, "user@office-a.test", path)
                self.assertIn("PJe", text)
                self.assertIn("DJEN", text)
                for other in ("STJ", "Tucujuris", "JPe", "Eproc", "e-SAJ", "Projudi"):
                    self.assertNotIn(other, text, path)

    def test_a_system_enabled_without_a_login_is_not_named(self):
        with TestClient(self.app, base_url=BASE) as client:
            text = self.initialize(client, "user@office-b.test", "/tribunais/mcp")
        for system in ("PJe", "Eproc", "e-SAJ", "Projudi"):
            self.assertIn(system, text)
        for other in ("STJ", "Tucujuris", "JPe", "DJEN"):
            self.assertNotIn(other, text)

    def test_without_an_office_no_system_is_named(self):
        office = Office({name: MCPServer(name) for name in COURTS}, self.store, label="Jurisync Tribunais")
        text = asyncio.run(office.instructions_for_caller())  # no caller: not inside a signed-in request
        self.assertEqual(text, "Jurisync Tribunais. Each tool name starts with its system.")
        for label in SYSTEMS.values():
            self.assertNotIn(label, text)


class ToolTextTest(OfficeTextCase):
    def test_tool_titles_and_descriptions_name_only_the_offices_portals(self):
        """office-b has trf5-1g, tjrs-1g, tjsp-esaj and tjgo-1g: no other court (TJAM, TJPR, TRT, TST, TJSC, TRF6...)
        and no tool of a system it lacks (ADVBOX, DJEN) appears in what it is shown."""
        _, logins = OFFICES["office-b"]
        offered = set(logins.split(","))
        keys = {key for system in COURTS for key in portals.keys(system)} - offered
        named = {court(key) for key in offered}
        foreign = {court(key) for key in keys} - named
        foreign |= {re.sub(r"\d+$", "", name) for name in foreign} - {re.sub(r"\d+$", "", name) for name in named}
        pattern = re.compile(r"\b(" + "|".join(sorted(map(re.escape, foreign | keys), key=len, reverse=True)) + r")\b")
        with TestClient(self.app, base_url=BASE) as client:
            send = self.mcp(client, self.token(client, "user@office-b.test", resource=f"{BASE}/tribunais/mcp"),
                            "/tribunais/mcp")  # fmt: skip
            tools = send("tools/list")["result"]["tools"]
        self.assertTrue({"projudi_list_intimacoes", "projudi_list_processes", "esaj_list_processes"}
                        <= {tool["name"] for tool in tools})  # fmt: skip
        for tool in tools:
            text = json.dumps(tool, ensure_ascii=False)
            self.assertIsNone(pattern.search(text), f"{tool['name']}: {pattern.findall(text)}")
            for system in ("advbox", "juridiq", "djen", "tucujuris", "jpe"):
                self.assertNotIn(f"{system}_", text, tool["name"])
