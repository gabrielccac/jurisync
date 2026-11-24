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

    def test_an_instance_made_under_other_credentials_is_replaced(self):
        closed = []
        local = TenantLocal(object, close=closed.append)
        with credentials.scope("office-a", {"ADVBOX_PASSWORD": "old"}):
            old = local.get()
        with credentials.scope("office-a", {"ADVBOX_PASSWORD": "old"}):
            self.assertIs(local.get(), old)
        with credentials.scope("office-a", {"ADVBOX_PASSWORD": "new"}):
            self.assertIsNot(local.get(), old)
        self.assertEqual(closed, [old])


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

    def test_a_token_from_other_credentials_is_not_reused(self):
        from jurisync.connectors.juridiq.api import JuridiqApi

        logins = []

        def send(method, url, headers, body):
            logins.append(body)
            return mock.Mock(status_code=200, json=lambda: {"token": f"token-{len(logins)}"})

        values = {"JURIDIQ_EMAIL": "a@x.br", "JURIDIQ_PASSWORD": "old"}
        api = JuridiqApi(send=send)
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch("jurisync.connectors.juridiq.api._cache_dir", return_value=Path(directory)):
                with credentials.scope("office-a", values):
                    first = api.token()
                with credentials.scope("office-a", values):
                    self.assertEqual(api.token(), first)
                with credentials.scope("office-a", {**values, "JURIDIQ_PASSWORD": "new"}):
                    self.assertNotEqual(api.token(), first)
                with credentials.scope("office-a", {**values, "JURIDIQ_PASSWORD": "new"}):
                    self.assertEqual(JuridiqApi(send=send).token(), api.token())  # the file serves the new password
        self.assertEqual(len(logins), 2)


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


class PortalAccessTest(unittest.TestCase):
    def test_every_portal_is_checked_and_forgotten_through_its_pool(self):
        from jurisync import registry
        from jurisync.connectors.projudi.server import sessions

        targets = access.check_targets()
        for connector in registry.CONNECTORS.values():
            self.assertLessEqual(set(connector.portals()), set(targets))
        with mock.patch.object(sessions, "check") as check, mock.patch.object(sessions, "forget") as forget:
            access.check("tjam-1g")
            access.forget("tjam-1g")
        check.assert_called_once_with("tjam-1g")
        forget.assert_called_once_with("tjam-1g")


class PendingDraftsTest(unittest.TestCase):
    def test_the_trf5_draft_search_stays_local_and_the_smoke_test_knows_it(self):
        import ast

        from jurisync.hosted.app import LOCAL_ONLY_TOOLS

        self.assertIn("pje_search_pending_drafts", LOCAL_ONLY_TOOLS["pje"])
        source = (Path(__file__).parents[2] / "deploy" / "smoke_test.py").read_text(encoding="utf-8")
        listed = next(
            node.value
            for node in ast.parse(source).body
            if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "LOCAL_ONLY"
        )
        self.assertEqual(ast.literal_eval(listed), LOCAL_ONLY_TOOLS["pje"])


class ServerGuardsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.directory.name) / "s.sqlite")
        self.vault = Vault(Path(self.directory.name) / "v", load_key(generate_key()))

    def tearDown(self):
        self.directory.cleanup()

    def test_only_tenant_aware_connectors_mount(self):
        self.assertEqual(
            set(TENANT_READY),
            {"pje", "eproc", "esaj", "projudi", "stj", "tucujuris", "jpe", "djen", "advbox", "juridiq"},
        )
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
        gate = TenantGate(
            app,
            connectors=("advbox",),
            name="advbox",
            store=self.store,
            vault=self.vault,
            resource="https://x/advbox/mcp",
        )
        import anyio

        with mock.patch("jurisync.hosted.app.AuthenticatedUser", mock.Mock):
            anyio.run(gate, {"type": "http", "user": user}, None, None)
        self.assertEqual(seen, {"tenant": "victor", "writes": "true", "cert": "true", "customer": "victor"})

    def gate_values(self, tenant="victor", *names):
        seen = {}

        async def app(scope, receive, send):
            seen.update({name: credentials.env(name) for name in names}, signature=credentials.signature())

        user = mock.Mock()
        user.access_token = mock.Mock(claims={"tenant": tenant}, resource=None)
        gate = TenantGate(
            app,
            connectors=("advbox",),
            name="advbox",
            store=self.store,
            vault=self.vault,
            resource="https://x/advbox/mcp",
        )
        import anyio

        with mock.patch("jurisync.hosted.app.AuthenticatedUser", mock.Mock):
            anyio.run(gate, {"type": "http", "user": user}, None, None)
        return seen

    def test_the_profile_wins_over_the_vault_and_the_operator_checks_read_the_same(self):
        self.store.add_tenant("victor", "Victor", customer="victor", connectors=["advbox"])
        # A copy an older provision stored, and a later portals.toml change.
        self.vault.put("victor", {"ADVBOX_ALLOW_WRITES": "false", "PJE_1G_CERT": "", "JURISYNC_CUSTOMER": "x"})
        names = ("ADVBOX_ALLOW_WRITES", "PJE_1G_CERT", "JURISYNC_CUSTOMER")
        served = self.gate_values("victor", *names)
        self.assertEqual([served[name] for name in names], ["true", "true", "victor"])
        checked = {}

        def check(target):
            checked.update({name: credentials.env(name) for name in names}, signature=credentials.signature())

        with mock.patch.object(access, "check", side_effect=check):
            self.assertTrue(
                cli.run_check(None, self.store.tenant("victor"), "trf1-1g", *self.vault.get("victor"), print)
            )
        self.assertEqual(checked, served)  # the same configuration, so a token the check got is the server's too

    def test_a_vault_change_changes_the_signature_and_a_renewed_device_id_does_not(self):
        self.store.add_tenant("victor", "Victor", customer="victor", connectors=["advbox"])
        self.vault.put("victor", {"ADVBOX_PASSWORD": "old", "ADVBOX_DEVICE_ID": "d1"})
        first = self.gate_values()["signature"]
        self.assertEqual(self.gate_values()["signature"], first)
        self.vault.update("victor", "ADVBOX_DEVICE_ID", "d2")
        self.assertEqual(self.gate_values()["signature"], first)
        self.vault.update("victor", "ADVBOX_PASSWORD", "new")
        self.assertNotEqual(self.gate_values()["signature"], first)


class ServiceImportTest(unittest.TestCase):
    def test_bundle_filter_knows_advbox_and_juridiq(self):
        names = cli.wanted_names(["advbox", "trf1-1g"], "")
        self.assertTrue({"ADVBOX_EMAIL", "ADVBOX_PASSWORD", "ADVBOX_DEVICE_ID", "PJE_1G_USERNAME"} <= names)
        self.assertNotIn("JURIDIQ_PASSWORD", names)


if __name__ == "__main__":
    unittest.main()


class CredentialSaverTest(unittest.TestCase):
    def test_a_renewed_value_goes_to_that_office_vault_only(self):
        import secrets

        from jurisync.hosted.app import TenantGate
        from jurisync.hosted.store import Store
        from jurisync.hosted.vault import Vault

        with tempfile.TemporaryDirectory() as directory:
            store, vault = (
                Store(Path(directory) / "store.sqlite"),
                Vault(Path(directory) / "vault", secrets.token_bytes(32)),
            )
            vault.put("office-a", {"ADVBOX_DEVICE_ID": "old", "ADVBOX_EMAIL": "a@x.br"}, {"cert.pfx": b"x"})
            vault.put("office-b", {"ADVBOX_DEVICE_ID": "other"})
            gate = TenantGate(None, connectors=None, store=store, vault=vault, resource="https://mcp.test")
            with credentials.scope("office-a", {"ADVBOX_DEVICE_ID": "old"}, saver=gate._saver("office-a")):
                credentials.save("ADVBOX_DEVICE_ID", "new")
                self.assertEqual(credentials.env("ADVBOX_DEVICE_ID"), "new")
            self.assertEqual(
                vault.get("office-a"), ({"ADVBOX_DEVICE_ID": "new", "ADVBOX_EMAIL": "a@x.br"}, {"cert.pfx": b"x"})
            )
            self.assertEqual(vault.get("office-b")[0], {"ADVBOX_DEVICE_ID": "other"})

    def test_saves_from_two_endpoints_do_not_lose_each_other(self):
        import secrets
        import threading
        import time

        with tempfile.TemporaryDirectory() as directory:
            store, vault = (
                Store(Path(directory) / "store.sqlite"),
                Vault(Path(directory) / "vault", secrets.token_bytes(32)),
            )
            vault.put("office-a", {"ADVBOX_EMAIL": "a@x.br"})
            read = vault.get

            def slow_get(tenant):
                values = read(tenant)
                time.sleep(0.05)  # both reads would see the same old values without the vault's lock
                return values

            gates = [
                TenantGate(
                    None, connectors=name and (name,), store=store, vault=vault, resource=f"https://mcp.test/{name}"
                )
                for name in (None, "advbox")
            ]
            with mock.patch.object(vault, "get", side_effect=slow_get):
                threads = [
                    threading.Thread(target=gate._saver("office-a"), args=(f"NAME_{index}", "v"))
                    for index, gate in enumerate(gates)
                ]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join()
            self.assertEqual(set(vault.get("office-a")[0]), {"ADVBOX_EMAIL", "NAME_0", "NAME_1"})


class ProfilePortalsTest(unittest.TestCase):
    """A login that opens more portals than the office's ``portals.toml`` declares offers only the declared ones."""

    PROFILE = "\n".join(
        f'[{key}]\nmethod = "{method}"\nsecrets = []\n'
        for key, method in (
            ("trf1-1g", "password_totp"),
            ("trf4-1g-sc", "browser_totp"),
            ("tjms-esaj", "certificate_challenge"),
            ("tjam-1g", "password_totp"),
        )
    )
    VALUES = {
        **{f"PJE_{grade}_{name}": "x" for grade in ("1G", "2G") for name in ("USERNAME", "PASSWORD")},
        "PJE_1G_2FA_DATA": "JBSWY3DPEHPK3PXP",
        "PJE_2G_2FA_DATA": "JBSWY3DPEHPK3PXP",
        **{f"{prefix}_{name}": "x" for prefix in ("JFSC", "TRF6") for name in ("USUARIO", "SENHA")},
        "JFSC_EXPORT_DATA": "JBSWY3DPEHPK3PXP",
        "TRF6_EXPORT_DATA": "JBSWY3DPEHPK3PXP",
        "PJE_A1_P12": "/a1.p12",
        "PJE_A1_PIN": "pin",
        "TJAM_1G_USERNAME": "AM000",
        "TJAM_1G_PASSWORD": "x",
    }
    DECLARED = {"pje": ("trf1-1g",), "eproc": ("trf4-1g-sc",), "esaj": ("tjms-esaj",), "projudi": ("tjam-1g",)}

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        (Path(directory.name) / "office").mkdir()
        (Path(directory.name) / "office" / "portals.toml").write_text(self.PROFILE, encoding="utf-8")
        self.root = directory.name

    def configured(self):
        import importlib

        return {
            name: importlib.import_module(f"jurisync.connectors.{name}.server").configured_portals()
            for name in self.DECLARED
        }

    def test_a_profile_keeps_only_its_portals_locally_and_hosted(self):
        customer = {"JURISYNC_CUSTOMER": "office"}
        with mock.patch.dict(os.environ, {**self.VALUES, "JURISYNC_CUSTOMERS_DIR": self.root}, clear=True):
            everything = self.configured()
            for name, declared in self.DECLARED.items():
                self.assertLess(set(declared), set(everything[name]))
            with mock.patch.dict(os.environ, customer):
                self.assertEqual(self.configured(), self.DECLARED)
        with mock.patch.dict(os.environ, {"JURISYNC_CUSTOMERS_DIR": self.root}, clear=True):
            with credentials.scope("office", {**self.VALUES, **customer}):
                self.assertEqual(self.configured(), self.DECLARED)
            with credentials.scope("other", {**self.VALUES, "JURISYNC_CUSTOMER": "no-profile"}):
                self.assertEqual(self.configured(), everything)
