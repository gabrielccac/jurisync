import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mcp.server.mcpserver import MCPServer

from jurisync import serve as serve_module
from jurisync.core import mcp as core_mcp


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
        self.assertFalse(kwargs["transport_security"].enable_dns_rebinding_protection)
        closed.assert_called_once()


class CombinedAppTest(unittest.TestCase):
    def test_every_server_is_mounted_under_its_own_path(self):
        servers = {"a": MCPServer("one"), "b": MCPServer("two")}
        with mock.patch.object(serve_module, "load", side_effect=lambda name: (servers[name], None)):
            app, closers = serve_module.build_app(["a", "b"])
        self.assertEqual(sorted(route.path for route in app.routes), ["/a", "/b"])
        self.assertEqual(closers, [])

    def test_known_servers_resolve_and_close_callbacks_are_found(self):
        for name in serve_module.SERVERS:
            with self.subTest(name):
                server, close = serve_module.load(name)
                self.assertIsInstance(server, MCPServer)
                self.assertTrue(close is None or callable(close))


class EnvFileDefaultTest(unittest.TestCase):
    def test_user_env_file_is_used_when_no_path_is_given(self):
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory) / ".env"
            env.write_text("JURISYNC_SERVE_TEST=yes\n")
            clean = {
                key: value for key, value in os.environ.items() if key not in ("JURISYNC_ENV_FILE", "JURISYNC_CUSTOMER")
            }
            with mock.patch.dict(os.environ, clean, clear=True), mock.patch("jurisync.paths.USER_ENV_FILE", env):
                core_mcp.load_env_file()
                self.assertEqual(os.environ.get("JURISYNC_SERVE_TEST"), "yes")


if __name__ == "__main__":
    unittest.main()
