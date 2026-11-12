"""Offline checks that ADVBOX, Juridiq and Eproc keep each office apart on the hosted server."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jurisync.core import credentials
from jurisync.core.tenant_local import TenantLocal
from jurisync.hosted import access, cli
from jurisync.hosted.app import TENANT_READY, TenantGate, build_app
from jurisync.hosted.store import Store
from jurisync.hosted.vault import Vault, generate_key, load_key


class TenantLocalTest(unittest.TestCase):
    def test_each_office_gets_its_own_instance_and_close_reaches_all(self):
        closed = []
        local = TenantLocal(object, close=closed.append)
        with credentials.scope("office-a"):
            a = local.get()
            self.assertIs(local.get(), a)
        with credentials.scope("office-b"):
            b = local.get()
        self.assertIsNot(a, b)
        with credentials.scope("office-a"):
            local.forget()
            self.assertIsNot(local.get(), a)
        local.close()
        self.assertEqual(len(closed), 3)


class AdvboxTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        state = mock.patch("jurisync.connectors.advbox.session.STATE_DIR", Path(self.directory.name))
        state.start()
        self.addCleanup(state.stop)
        self.addCleanup(self.directory.cleanup)

    def test_server_client_session_and_writes_follow_the_office(self):
        from jurisync.connectors.advbox import server
        from jurisync.connectors.advbox.session import require_writes, session_file

        self.assertIsInstance(server.client, TenantLocal)
        with credentials.scope("office-a", {"ADVBOX_EMAIL": "a@x.br"}):
            client_a, path_a = server.client.get(), session_file()
            with self.assertRaises(PermissionError):
                require_writes()
        with credentials.scope("office-b", {"ADVBOX_EMAIL": "b@x.br", "ADVBOX_ALLOW_WRITES": "true"}):
            client_b, path_b = server.client.get(), session_file()
            require_writes()
        self.assertIsNot(client_a, client_b)
        self.assertIn("tenants/office-a", str(path_a))
        self.assertIn("tenants/office-b", str(path_b))
        with mock.patch.dict(os.environ, {"ADVBOX_ALLOW_WRITES": "true", "ADVBOX_PASSWORD": "server"}):
            with credentials.scope("office-c"), self.assertRaises(PermissionError):
                require_writes()

    def test_login_reads_the_office_password(self):
        from jurisync.connectors.advbox.session import _required

        with credentials.scope("office-a", {"ADVBOX_PASSWORD": "senha-a"}):
            self.assertEqual(_required("ADVBOX_PASSWORD"), "senha-a")
        with credentials.scope("office-b"), self.assertRaises(RuntimeError):
            _required("ADVBOX_PASSWORD")


class JuridiqTest(unittest.TestCase):
    def test_account_token_cache_and_writes_follow_the_office(self):
        from jurisync.connectors.juridiq.api import config

        with credentials.scope("office-a", {"JURIDIQ_EMAIL": "A@x.br", "JURIDIQ_PASSWORD": "p"}):
            settings = config()
        self.assertEqual((settings["email"], settings["password"]), ("a@x.br", "p"))
        self.assertIn("tenants/office-a", str(settings["sessionFile"]))
        with mock.patch.dict(os.environ, {"JURIDIQ_PASSWORD": "server"}), credentials.scope("office-b"):
            self.assertIsNone(config()["password"] or None)


class AccessChecksTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        state = mock.patch("jurisync.connectors.advbox.session.STATE_DIR", Path(self.directory.name))
        state.start()
        self.addCleanup(state.stop)
        self.addCleanup(self.directory.cleanup)

    def test_every_system_has_a_fresh_login_check(self):
        targets = access.check_targets()
        for expected in ("trf1-1g", "advbox", "juridiq"):
            self.assertIn(expected, targets)
        with mock.patch.object(access, "check_login") as pje:
            access.check("trf1-1g")
        pje.assert_called_once_with("trf1-1g")
        with credentials.scope("office-a", {"ADVBOX_EMAIL": "a@x.br"}):
            from jurisync.connectors.advbox.session import session_file

            path = session_file()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}")
            with mock.patch("jurisync.connectors.advbox.client.AdvboxClient") as client:
                access.check("advbox")
            self.assertFalse(path.exists())  # the cached cookie cannot pass for a new password
            client.return_value.ensure_session.assert_called_once()
            client.return_value.close.assert_called_once()
        with mock.patch("jurisync.connectors.juridiq.api.JuridiqApi") as api:
            access.check("juridiq")
        api.return_value.token.assert_called_once()
        with self.assertRaises(ValueError):
            access.check("nada")


class ServerGuardsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.directory.name) / "s.sqlite")
        self.vault = Vault(Path(self.directory.name) / "v", load_key(generate_key()))

    def tearDown(self):
        self.directory.cleanup()

    def test_only_tenant_aware_connectors_mount(self):
        self.assertEqual(set(TENANT_READY), {"pje", "eproc", "djen", "advbox", "juridiq"})
        with self.assertRaises(ValueError):
            build_app("https://mcp.test", self.store, self.vault, ["filing"])
        app, closers = build_app("https://mcp.test", self.store, self.vault, list(TENANT_READY))
        self.assertTrue(any(getattr(route, "path", "") == "/advbox" for route in app.routes))

    def test_customer_profile_pins_apply_to_the_office(self):
        self.store.add_tenant("victor", "Victor", customer="victor", connectors=["advbox"])
        seen = {}

        async def app(scope, receive, send):
            seen.update(
                tenant=credentials.tenant(),
                writes=credentials.env("ADVBOX_ALLOW_WRITES"),
                cert=credentials.env("PJE_1G_CERT"),
                customer=credentials.env("JURISYNC_CUSTOMER"),
            )

        user = mock.Mock()
        user.access_token = mock.Mock(claims={"tenant": "victor"}, resource=None)
        gate = TenantGate(app, connector="advbox", store=self.store, vault=self.vault, resource="https://x/advbox/mcp")
        import anyio

        with mock.patch("jurisync.hosted.app.AuthenticatedUser", mock.Mock):
            anyio.run(gate, {"type": "http", "user": user}, None, None)
        self.assertEqual(seen, {"tenant": "victor", "writes": "true", "cert": "true", "customer": "victor"})


class ServiceImportTest(unittest.TestCase):
    def test_bundle_filter_knows_advbox_and_juridiq(self):
        names = cli.wanted_names(["advbox", "trf1-1g"], "")
        self.assertTrue({"ADVBOX_EMAIL", "ADVBOX_PASSWORD", "ADVBOX_DEVICE_ID", "PJE_1G_USERNAME"} <= names)
        self.assertNotIn("JURIDIQ_PASSWORD", names)


if __name__ == "__main__":
    unittest.main()
