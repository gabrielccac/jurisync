import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mcp.server.mcpserver import MCPServer
from starlette.testclient import TestClient

from jurisync import customers, registry
from jurisync.core import mcp as core_mcp
from jurisync.install import serve as serve_module


class ServeArgumentsTest(unittest.TestCase):
    def test_stdio_is_the_default_and_http_is_opt_in(self):
        server = mock.Mock()
        with mock.patch.object(core_mcp, "run_stdio") as stdio:
            core_mcp.serve(server, argv=[])
        stdio.assert_called_once()
        server.run.assert_not_called()

        closed = mock.Mock()
        core_mcp.serve(server, on_exit=closed, argv=["--http", "--port", "9100"])
        args, kwargs = server.run.call_args
        self.assertEqual((args, kwargs["host"], kwargs["port"]), (("streamable-http",), "127.0.0.1", 9100))
        self.assertTrue(kwargs["transport_security"].enable_dns_rebinding_protection)
        self.assertEqual(kwargs["streamable_http_path"], "/mcp")
        closed.assert_called_once()

    def test_a_public_host_puts_the_endpoint_under_a_secret_path(self):
        server = mock.Mock()
        with mock.patch.dict(os.environ, {core_mcp.HTTP_SECRET_VAR: "s" * 32}):
            core_mcp.serve(server, argv=["--http", "--public-host", "abc.trycloudflare.com"])
        kwargs = server.run.call_args.kwargs
        self.assertEqual(kwargs["streamable_http_path"], f"/{'s' * 32}/mcp")
        self.assertIn("abc.trycloudflare.com", kwargs["transport_security"].allowed_hosts)


PING = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
}
ACCEPT = {"Accept": "application/json, text/event-stream"}


class HttpAccessTest(unittest.TestCase):
    def app(self, public_host=None, host="127.0.0.1"):
        prefix = core_mcp.http_prefix(host, public_host)
        with mock.patch.object(serve_module, "load", side_effect=lambda name: (MCPServer(name), None)):
            app, _ = serve_module.build_app(["a"], host, public_host, prefix)
        return app, prefix

    def post(self, client, path, host, **headers):
        return client.post(path, json=PING, headers={**ACCEPT, "Host": host, **headers})

    def test_on_this_machine_only_loopback_hosts_and_origins_pass(self):
        app, prefix = self.app()
        self.assertEqual(prefix, "")
        with TestClient(app) as client:
            self.assertEqual(self.post(client, "/a/mcp", "127.0.0.1:8000").status_code, 200)
            self.assertEqual(self.post(client, "/a/mcp", "rebind.evil.test").status_code, 421)
            refused = self.post(client, "/a/mcp", "127.0.0.1:8000", Origin="https://evil.test")
            self.assertEqual(refused.status_code, 403)

    def test_through_a_tunnel_the_secret_path_is_required(self):
        with mock.patch.dict(os.environ, {core_mcp.HTTP_SECRET_VAR: ""}):
            app, prefix = self.app("abc.trycloudflare.com")
        self.assertGreaterEqual(len(prefix), 25)
        with TestClient(app) as client:
            self.assertEqual(self.post(client, "/a/mcp", "abc.trycloudflare.com").status_code, 404)
            self.assertEqual(self.post(client, f"{prefix}/a/mcp", "abc.trycloudflare.com").status_code, 200)
            self.assertEqual(self.post(client, f"{prefix}/a/mcp", "other.trycloudflare.com").status_code, 421)

    def test_a_lan_bind_answers_to_its_own_address_under_the_secret_path(self):
        app, prefix = self.app(host="192.168.1.10")
        self.assertNotEqual(prefix, "")
        for wildcard in ("0.0.0.0", "::"):
            with self.assertRaises(SystemExit):
                core_mcp.http_prefix(wildcard)
        self.assertNotEqual(core_mcp.http_prefix("0.0.0.0", "abc.trycloudflare.com"), "")
        with TestClient(app) as client:
            self.assertEqual(self.post(client, "/a/mcp", "192.168.1.10:8000").status_code, 404)
            self.assertEqual(self.post(client, f"{prefix}/a/mcp", "192.168.1.10:8000").status_code, 200)
            self.assertEqual(self.post(client, f"{prefix}/a/mcp", "rebind.evil.test").status_code, 421)
        self.assertIn("[fd00::1]:*", core_mcp.http_settings("fd00::1").allowed_hosts)
        self.assertEqual(core_mcp.http_settings("0.0.0.0").allowed_hosts, core_mcp.http_settings().allowed_hosts)

    def test_the_secret_comes_from_the_environment_when_valid(self):
        with mock.patch.dict(os.environ, {core_mcp.HTTP_SECRET_VAR: "a-B_c" * 6}):
            self.assertEqual(core_mcp.http_prefix("127.0.0.1", "x.test"), "/" + "a-B_c" * 6)
        for weak in ("short", "has/slash" * 4):
            with mock.patch.dict(os.environ, {core_mcp.HTTP_SECRET_VAR: weak}), self.assertRaises(SystemExit):
                core_mcp.http_prefix("127.0.0.1", "x.test")


class CombinedAppTest(unittest.TestCase):
    def test_every_server_is_mounted_under_its_own_path(self):
        servers = {"a": MCPServer("one"), "b": MCPServer("two")}
        with mock.patch.object(serve_module, "load", side_effect=lambda name: (servers[name], None)):
            app, closers = serve_module.build_app(["a", "b"])
        self.assertEqual(sorted(route.path for route in app.routes), ["/a", "/b"])
        self.assertEqual(closers, [])

    def test_known_servers_resolve_and_close_callbacks_are_found(self):
        for name in registry.CONNECTORS:
            with self.subTest(name):
                server, close = serve_module.load(name)
                self.assertIsInstance(server, MCPServer)
                self.assertTrue(close is None or callable(close))


class EnvFileDefaultTest(unittest.TestCase):
    def test_user_env_file_is_used_when_no_path_is_given(self):
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory) / ".env"
            env.write_text("JURISYNC_SERVE_TEST=yes\n", encoding="utf-8")
            clean = {
                key: value for key, value in os.environ.items() if key not in ("JURISYNC_ENV_FILE", "JURISYNC_CUSTOMER")
            }
            with mock.patch.dict(os.environ, clean, clear=True), mock.patch("jurisync.paths.USER_ENV_FILE", env):
                customers.load_env_file()
                self.assertEqual(os.environ.get("JURISYNC_SERVE_TEST"), "yes")


if __name__ == "__main__":
    unittest.main()
