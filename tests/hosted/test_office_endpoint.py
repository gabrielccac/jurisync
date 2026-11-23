"""The combined ``/mcp`` endpoint, the per-call audit log and the pooled login check."""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from typing import Literal
from unittest import mock

from mcp.server.mcpserver import MCPServer
from starlette.testclient import TestClient

from jurisync import portals
from jurisync.core import credentials
from jurisync.core.session_pool import LoginRejected
from jurisync.hosted import access, cli
from jurisync.hosted.app import REQUIRE_RESOURCE_FLAG, build_app
from jurisync.hosted.store import CALLS_KEPT, Store
from jurisync.hosted.vault import Vault, generate_key, load_key

from .test_signin_security import BASE, PASSWORD, SignInCase, ping


def servers():
    alpha, beta = MCPServer("alpha"), MCPServer("beta")

    @alpha.tool(name="alpha_whoami")
    def whoami() -> dict:
        return {"tenant": credentials.tenant(), "pin": credentials.env("PJE_A1_PIN")}

    @alpha.tool(name="alpha_fail")
    def fail(number: str) -> str:
        raise ValueError("portal fora do ar")

    @alpha.tool(name="alpha_draft_write")
    def write() -> str:
        return "written"

    @beta.tool(name="beta_whoami")
    def beta_whoami() -> dict:
        return {"tenant": credentials.tenant()}

    return {"alpha": alpha, "beta": beta}


class OfficeCase(SignInCase):
    """office-a has alpha only; office-b has alpha and beta."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.store = Store(root / "store.sqlite")
        self.vault = Vault(root / "vault", load_key(generate_key()))
        for tenant, connectors in (("office-a", ["alpha"]), ("office-b", ["alpha", "beta"])):
            self.store.add_tenant(tenant, tenant, connectors=connectors)
            self.store.add_user(f"user@{tenant}.test", tenant, PASSWORD)
            self.vault.put(tenant, {"PJE_A1_PIN": f"pin-{tenant}"})
        made = servers()
        with (
            mock.patch.dict(os.environ, {REQUIRE_RESOURCE_FLAG: "", **self.environment}),
            mock.patch("jurisync.hosted.app.TENANT_READY", ("alpha", "beta")),
            mock.patch.dict("jurisync.hosted.app.LOCAL_ONLY_TOOLS", {"alpha": {"alpha_draft_write"}}),
        ):
            self.app, _ = build_app(BASE, self.store, self.vault, list(made), load=lambda name: (made[name], None))

    def mcp(self, client, token, path="/mcp"):
        """An initialized MCP session; returns ``send(method, params)`` -> JSON-RPC body."""
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"}
        counter = iter(range(1, 100))

        def send(method, params=None, notify=False):
            payload = {"jsonrpc": "2.0", "method": method, **({"params": params} if params is not None else {})}
            if not notify:
                payload["id"] = next(counter)
            response = client.post(path, json=payload, headers=headers)
            self.assertLess(response.status_code, 300, response.text)
            if "mcp-session-id" in response.headers:
                headers["mcp-session-id"] = response.headers["mcp-session-id"]
            for line in response.text.splitlines():
                if line.startswith("data:"):
                    return json.loads(line[5:])
            return None

        send(
            "initialize",
            {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
        )
        send("notifications/initialized", notify=True)
        return send

    @staticmethod
    def names(send):
        return {tool["name"] for tool in send("tools/list")["result"]["tools"]}

    @staticmethod
    def call(send, name, arguments=None):
        return send("tools/call", {"name": name, "arguments": arguments or {}})["result"]

    @staticmethod
    def data(result):
        return result.get("structuredContent") or json.loads(result["content"][0]["text"])


class CombinedEndpointTest(OfficeCase):
    def test_each_office_sees_and_calls_only_its_connectors(self):
        with TestClient(self.app, base_url=BASE) as client:
            office_a = self.mcp(client, self.token(client, resource=f"{BASE}/mcp"))
            office_b = self.mcp(client, self.token(client, "user@office-b.test", resource=f"{BASE}/mcp"))
            self.assertEqual(self.names(office_a), {"alpha_whoami", "alpha_fail"})
            self.assertEqual(self.names(office_b), {"alpha_whoami", "alpha_fail", "beta_whoami"})
            refused = self.call(office_a, "beta_whoami")
            self.assertTrue(refused["isError"])
            self.assertIn("not enabled for this office", refused["content"][0]["text"])
            self.assertEqual(
                self.data(self.call(office_a, "alpha_whoami")), {"tenant": "office-a", "pin": "pin-office-a"}
            )
            self.assertEqual(self.data(self.call(office_b, "beta_whoami")), {"tenant": "office-b"})

    def test_write_tools_are_off_on_every_endpoint(self):
        with TestClient(self.app, base_url=BASE) as client:
            token = self.token(client, resource=f"{BASE}/mcp")
            send = self.mcp(client, token)
            self.assertNotIn("alpha_draft_write", self.names(send))
            self.assertTrue(self.call(send, "alpha_draft_write")["isError"])
            alpha = self.mcp(client, self.token(client, resource=f"{BASE}/alpha/mcp"), "/alpha/mcp")
            self.assertNotIn("alpha_draft_write", self.names(alpha))

    def test_the_combined_server_names_its_systems(self):
        with TestClient(self.app, base_url=BASE) as client:
            token = self.token(client, resource=f"{BASE}/mcp")
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"}
            body = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                               "clientInfo": {"name": "t", "version": "1"}}}  # fmt: skip
            response = client.post("/mcp", json=body, headers=headers)
        result = json.loads(next(line[5:] for line in response.text.splitlines() if line.startswith("data:")))
        self.assertIn("alpha, beta", result["result"]["instructions"])

    def test_a_tool_name_served_twice_stops_the_start(self):
        twice = {"alpha": MCPServer("alpha"), "beta": MCPServer("beta")}
        for server in twice.values():
            server.add_tool(lambda: "x", name="same_name")
        with mock.patch("jurisync.hosted.app.TENANT_READY", ("alpha", "beta")), self.assertRaises(ValueError):
            build_app(BASE, self.store, self.vault, list(twice), load=lambda name: (twice[name], None))


def portal_servers():
    """Stand-ins for Projudi (two portals, one tool for tjgo-1g only) and STJ (one portal, no ``portal`` field)."""
    projudi, stj = MCPServer("projudi"), MCPServer("stj")

    @projudi.tool(name="projudi_get_process")
    def get_process(number: str, portal: Literal["tjgo-1g", "tjam-1g"] = "tjgo-1g") -> dict:
        return {"portal": portal}

    @projudi.tool(name="projudi_list_intimations")
    def intimations(portal: Literal["tjgo-1g"] = "tjgo-1g") -> dict:
        return {"portal": portal}

    @stj.tool(name="stj_get_process")
    def stj_process(number: str) -> dict:
        return {"number": number}

    return {"projudi": projudi, "stj": stj}


def configured(system):
    """``configured_portals`` read from the office's credentials, as the connectors do."""
    return lambda: tuple(key for key in credentials.env("CONFIGURED").split(",") if key in portals.keys(system))


class PortalChoicesTest(OfficeCase):
    """office-a has a login for tjam-1g only; office-b for both Projudi portals and STJ."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.store = Store(root / "store.sqlite")
        self.vault = Vault(root / "vault", load_key(generate_key()))
        for tenant, logins in (("office-a", "tjam-1g"), ("office-b", "tjgo-1g,tjam-1g,stj")):
            self.store.add_tenant(tenant, tenant, connectors=["projudi", "stj"])
            self.store.add_user(f"user@{tenant}.test", tenant, PASSWORD)
            self.vault.put(tenant, {"CONFIGURED": logins})
        self.servers = portal_servers()
        for system in self.servers:
            patch = mock.patch(
                f"jurisync.connectors.{system}.server.configured_portals", configured(system), create=True
            )
            patch.start()
            self.addCleanup(patch.stop)
        with mock.patch.dict(os.environ, {REQUIRE_RESOURCE_FLAG: ""}):
            self.app, _ = build_app(
                BASE, self.store, self.vault, list(self.servers), load=lambda name: (self.servers[name], None)
            )

    def tools(self, send):
        return {tool["name"]: tool for tool in send("tools/list")["result"]["tools"]}

    def test_each_office_sees_only_the_portals_it_has_a_login_for(self):
        with TestClient(self.app, base_url=BASE) as client:
            office_a = self.tools(self.mcp(client, self.token(client, resource=f"{BASE}/mcp")))
            office_b = self.tools(self.mcp(client, self.token(client, "user@office-b.test", resource=f"{BASE}/mcp")))
        self.assertEqual(set(office_a), {"projudi_get_process"})  # STJ and the tjgo-1g-only tool are gone
        field = office_a["projudi_get_process"]["inputSchema"]["properties"]["portal"]
        self.assertEqual(field["enum"], ["tjam-1g"])
        self.assertNotIn("default", field)  # tjgo-1g: no login there
        self.assertIn("tjam-1g", field["description"])
        self.assertEqual(office_a["projudi_get_process"]["inputSchema"]["required"], ["number", "portal"])
        self.assertEqual(set(office_b), {"projudi_get_process", "projudi_list_intimations", "stj_get_process"})
        self.assertEqual(office_b["projudi_get_process"]["inputSchema"]["properties"]["portal"]["enum"],
                         ["tjgo-1g", "tjam-1g"])  # fmt: skip
        self.assertEqual(office_b["projudi_get_process"]["inputSchema"]["required"], ["number"])
        shared = self.servers["projudi"]._tool_manager.get_tool("projudi_get_process").parameters
        self.assertEqual(shared["properties"]["portal"]["enum"], ["tjgo-1g", "tjam-1g"])  # never changed in place

    def test_a_portal_without_login_is_refused_before_the_connector(self):
        with TestClient(self.app, base_url=BASE) as client:
            send = self.mcp(client, self.token(client, resource=f"{BASE}/mcp"))
            self.assertEqual(
                self.data(self.call(send, "projudi_get_process", {"number": "1", "portal": "tjam-1g"})),
                {"portal": "tjam-1g"},
            )
            refused = self.call(send, "projudi_get_process", {"number": "1", "portal": "tjgo-1g"})
            self.assertTrue(refused["isError"])
            self.assertIn("no login is configured for tjgo-1g", refused["content"][0]["text"])
            refused = self.call(send, "projudi_get_process", {"number": "1"})  # the default is not this office's
            self.assertTrue(refused["isError"])
            self.assertIn("give the portal", refused["content"][0]["text"])
            self.assertIn("tjam-1g", refused["content"][0]["text"])
            refused = self.call(send, "stj_get_process", {"number": "1"})
            self.assertTrue(refused["isError"])
            self.assertIn("no STJ login is configured", refused["content"][0]["text"])

    def test_the_default_portal_stands_when_the_office_has_it(self):
        with TestClient(self.app, base_url=BASE) as client:
            send = self.mcp(client, self.token(client, "user@office-b.test", resource=f"{BASE}/mcp"))
            self.assertEqual(self.data(self.call(send, "projudi_get_process", {"number": "1"})), {"portal": "tjgo-1g"})


class HealthTest(OfficeCase):
    def test_healthz_answers_without_a_token(self):
        with TestClient(self.app, base_url=BASE) as client:
            response = client.get("/healthz")
        self.assertEqual((response.status_code, response.json()), (200, {"ok": True}))


class CombinedAudienceTest(OfficeCase):
    def test_resource_metadata_and_token_audience_for_mcp(self):
        with TestClient(self.app, base_url=BASE) as client:
            challenge = client.post("/mcp", json={})
            self.assertEqual(challenge.status_code, 401)
            self.assertIn("oauth-protected-resource/mcp", challenge.headers["www-authenticate"])
            metadata = client.get("/.well-known/oauth-protected-resource/mcp").json()
            self.assertEqual(metadata["resource"].rstrip("/"), f"{BASE}/mcp")
            combined = self.token(client, resource=f"{BASE}/mcp")
            per_connector = self.token(client, resource=f"{BASE}/alpha/mcp")
            self.assertEqual(ping(client, combined, "/mcp").status_code, 200)
            self.assertEqual(ping(client, combined, "/alpha/mcp").status_code, 401)
            self.assertEqual(ping(client, per_connector, "/mcp").status_code, 401)
            self.assertEqual(ping(client, per_connector, "/alpha/mcp").status_code, 200)
            self.assertEqual(ping(client, self.token(client, resource=None), "/mcp").status_code, 200)


class StrictCombinedAudienceTest(OfficeCase):
    environment = {REQUIRE_RESOURCE_FLAG: "1"}

    def test_the_switch_refuses_a_token_without_resource_on_mcp(self):
        with TestClient(self.app, base_url=BASE) as client:
            self.assertEqual(ping(client, self.token(client, resource=None), "/mcp").status_code, 401)
            self.assertEqual(ping(client, self.token(client, resource=f"{BASE}/mcp"), "/mcp").status_code, 200)


class AuditTest(OfficeCase):
    def test_every_call_is_recorded_without_arguments(self):
        with TestClient(self.app, base_url=BASE) as client:
            send = self.mcp(client, self.token(client, resource=f"{BASE}/mcp"))
            self.call(send, "alpha_whoami")
            self.assertTrue(self.call(send, "alpha_fail", {"number": "SEGREDO-0001234"})["isError"])
            self.call(send, "beta_whoami")
            alpha = self.mcp(client, self.token(client, resource=f"{BASE}/alpha/mcp"), "/alpha/mcp")
            self.call(alpha, "alpha_whoami")
        rows = list(reversed(self.store.calls("office-a")))
        summary = [(row["user"], row["connector"], row["tool"], row["ok"], row["error"]) for row in rows]
        self.assertEqual(
            summary,
            [
                ("user@office-a.test", "alpha", "alpha_whoami", 1, ""),
                ("user@office-a.test", "alpha", "alpha_fail", 0, "ValueError"),
                ("user@office-a.test", "beta", "beta_whoami", 0, "NotEnabled"),
                ("user@office-a.test", "alpha", "alpha_whoami", 1, ""),
            ],
        )
        self.assertTrue(all(isinstance(row["duration_ms"], int) and row["at"] for row in rows))
        dump = json.dumps([dict(row) for row in self.store._run("select * from calls")])
        self.assertNotIn("SEGREDO", dump)
        self.assertNotIn("pin-office-a", dump)
        self.assertEqual(self.store.calls("office-b"), [])

    def test_old_calls_are_purged_and_go_with_the_office(self):
        self.store.log_call("office-a", "u", "alpha", "alpha_whoami", "", 3)
        self.store._run("update calls set at = ?", int(time.time()) - CALLS_KEPT - 10)
        self.store.log_call("office-a", "u", "alpha", "alpha_whoami", "", 3)
        self.store.purge()
        self.assertEqual(len(self.store.calls("office-a")), 1)
        self.store.remove_tenant("office-a")
        self.assertEqual(self.store.calls("office-a"), [])


class CallsCommandTest(unittest.TestCase):
    def test_calls_lists_the_offices_tool_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {"JURISYNC_HOSTED_DIR": directory, "JURISYNC_VAULT_KEY": generate_key()}
            said = []
            with mock.patch.dict(os.environ, env):
                cli.main(["tenant", "add", "office-a", "--name", "A"], say=said.append)
                store = cli.open_store()
                store.log_call("office-a", "u@a.test", "pje", "pje_get_process", "", 812)
                store.log_call("office-a", "u@a.test", "pje", "pje_list_inbox", "LoginRejected", 40)
                said.clear()
                cli.main(["calls", "office-a", "--limit", "1"], say=said.append)
        self.assertEqual(len(said), 1)
        self.assertIn("u@a.test  pje_list_inbox  erro LoginRejected  40 ms", said[0])


class PooledCheckTest(unittest.TestCase):
    VALUES = {
        "PJE_1G_USERNAME": "123",
        "PJE_1G_PASSWORD": "errada",
        "PJE_1G_2FA_DATA": "otpauth://totp/x?secret=JBSWY3DPEHPK3PXP",
    }

    def test_a_rejected_password_is_not_sent_again_within_the_cool_down(self):
        from jurisync.connectors.pje.auth import sso

        tenant = f"cooldown-{time.monotonic_ns()}"
        with (
            credentials.scope(tenant, self.VALUES),
            mock.patch.object(sso, "login_session", side_effect=LoginRejected("senha incorreta")) as login,
        ):
            self.addCleanup(lambda: self.forget(tenant))
            with self.assertRaises(LoginRejected):
                access.check_login("trf1-1g")
            with self.assertRaisesRegex(LoginRejected, "no new login was attempted"):
                access.check_login("trf1-1g")
            self.assertEqual(login.call_count, 1)
            self.forget(tenant)  # credentials changed: the cool-down ends
            with self.assertRaises(LoginRejected):
                access.check_login("trf1-1g")
            self.assertEqual(login.call_count, 2)

    def test_the_password_login_names_its_portal(self):
        from jurisync.connectors.pje.auth import sso

        values = {name.replace("PJE_1G", "TRT19_1G"): value for name, value in self.VALUES.items()}
        with credentials.scope("named-portal", values), mock.patch.object(sso, "login_session") as login:
            access.fresh_pje_login("trt19-1g")
        self.assertEqual(login.call_args.kwargs["portal"], "trt19-1g")

    def forget(self, tenant):
        from jurisync.connectors.pje.server import sessions

        with credentials.scope(tenant, self.VALUES):
            sessions.forget("trf1-1g")


if __name__ == "__main__":
    unittest.main()
