"""Provisioning offices from customers/<name>/: the plan, the sealed bundle per office and the import on the server."""

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mcp.server.mcpserver import MCPServer
from starlette.testclient import TestClient

from jurisync.core import credentials
from jurisync.hosted import cli, provision
from jurisync.hosted.app import REQUIRE_RESOURCE_FLAG, build_app
from jurisync.hosted.store import Store
from jurisync.hosted.vault import Vault, generate_key, load_key

from .test_hosted import make_p12_bytes
from .test_office_endpoint import OfficeCase
from .test_signin_security import BASE, PASSWORD, ping

SECRET = "JBSWY3DPEHPK3PXP"

# An A1 office with ADVBOX writes, a password office with legacy names, and an A3 office with one password portal.
PROFILES = {
    "alfa": (
        """
[trf5-1g]
method = "certificate_totp"
secrets = ["PJE_A1_P12", "PJE_A1_PIN", "PJE_TRF5_2FA_DATA|PJE_TRF5_2FA_QR"]

[trf1-1g]
method = "certificate_totp"
secrets = ["PJE_A1_P12", "PJE_A1_PIN", "PJE_1G_2FA_DATA"]

[env]
secrets = ["ADVBOX_EMAIL", "ADVBOX_PASSWORD"]
set = { PJE_1G_CERT = "true" }
""",
        {
            "PJE_A1_P12": "cert.pfx",
            "PJE_A1_PIN": "pin-alfa",
            "PJE_TRF5_2FA_DATA": SECRET,
            "PJE_1G_2FA_DATA": SECRET,
            "ADVBOX_EMAIL": "alfa@advbox.test",
            "ADVBOX_PASSWORD": "advbox-secret",
            "ADVBOX_ALLOW_WRITES": "true",
            "PJE_ALLOW_DRAFT_WRITES": "true",
            "MINDCHAT_API_KEY": "mind-secret",
        },
    ),
    "beta": (
        """
[trf4-1g-sc]
method = "browser_totp"
secrets = ["TRF4_1G_SC_USERNAME|TRF4_1G_SC_USUARIO", "TRF4_1G_SC_PASSWORD|TRF4_1G_SC_SENHA", "TRF4_1G_SC_2FA_DATA|TRF4_1G_SC_EXPORT_DATA"]

[trf4-1g-rs]
method = "browser_totp"
secrets = ["TRF4_1G_RS_USERNAME", "TRF4_1G_RS_PASSWORD", "TRF4_1G_RS_2FA_DATA"]
""",
        {
            "TRF4_1G_SC_USUARIO": "user-beta",
            "TRF4_1G_SC_SENHA": "senha-beta",
            "TRF4_1G_SC_EXPORT_DATA": SECRET,
            "TRF4_1G_SC_OTP_PROFILE_INDEX": "0",
        },
    ),
    "gama": (
        """
[trf1-1g]
method = "password_totp"
secrets = ["PJE_1G_USERNAME", "PJE_1G_PASSWORD", "PJE_1G_2FA_DATA"]

[tjpe-1g]
method = "certificate_totp"
secrets = ["PJE_A3_PIN", "TJPE_1G_2FA_DATA"]

[env]
secrets = ["PJE_AUTH", "PJE_A3_PIN"]
""",
        {
            "PJE_1G_USERNAME": "123",
            "PJE_1G_PASSWORD": "senha-gama",
            "PJE_1G_2FA_DATA": SECRET,
            "TJPE_1G_2FA_DATA": SECRET,
            "PJE_AUTH": "a3",
            "PJE_A3_PIN": "pin-token",
        },
    ),
    "delta": (
        """
[trf1-1g]
method = "password_totp"
secrets = ["PJE_1G_USERNAME", "PJE_1G_PASSWORD", "PJE_1G_2FA_DATA"]

[tjsp-1g]
method = "certificate_totp"
secrets = ["PJE_A3_PIN", "TJSP_1G_2FA_DATA"]
""",
        {
            "PJE_1G_USERNAME": "456",
            "PJE_1G_PASSWORD": "senha-delta",
            "PJE_1G_2FA_DATA": SECRET,
            "PJE_A3_PIN": "delta-pin",
        },
    ),
}
# delta keeps its local install (settings.toml [hosted] mode = "local").
LOCAL_SETTINGS = '[hosted]\nmode = "local"\nreason = "A3 — instalação local"\n'
VALUES = [value for _, env in PROFILES.values() for value in env.values() if len(value) > 4 and value != "true"]


class ProvisionCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.ours = self.root / "ours" / "customers"
        for name, (profile, env) in PROFILES.items():
            folder = self.ours / name
            folder.mkdir(parents=True)
            (folder / "portals.toml").write_text(profile, encoding="utf-8")
            (folder / ".env").write_text("".join(f"{key}={value}\n" for key, value in env.items()), encoding="utf-8")
        (self.ours / "delta" / "settings.toml").write_text(LOCAL_SETTINGS, encoding="utf-8")
        self.p12, self.pin = make_p12_bytes("Advogada Alfa", "pin-alfa")
        (self.ours / "alfa" / "cert.pfx").write_bytes(self.p12)
        self.out = self.root / "rollout"
        self.passphrase = self.root / "secret" / "rollout.passphrase"
        self.said = []

    def export(self, *only):
        with mock.patch.dict(os.environ, {"JURISYNC_CUSTOMERS_DIR": str(self.ours)}):
            return provision.export(self.out, self.passphrase, only=list(only) or None, say=self.said.append)

    def server(self):
        """Environment of the server: its own data dir, vault key and customers dir."""
        return {
            "JURISYNC_HOSTED_DIR": str(self.root / "server" / "hosted"),
            "JURISYNC_VAULT_KEY": self.key,
            "JURISYNC_CUSTOMERS_DIR": str(self.root / "server" / "customers"),
        }

    key = generate_key()


class PlanTest(ProvisionCase):
    def test_each_office_gets_only_the_portals_the_server_can_log_in_to(self):
        offices = {plan.tenant: plan for plan in self.export()}
        alfa, beta, gama = offices["alfa"], offices["beta"], offices["gama"]
        self.assertEqual(alfa.portals, ["trf5-1g", "trf1-1g", "advbox"])
        self.assertEqual(alfa.connectors, ["pje", "djen", "advbox"])
        self.assertEqual(alfa.endpoints, ["/tribunais/mcp", "/advbox/mcp"])
        # Every served portal gets its first login; ADVBOX has no device id yet, so its login waits for the code.
        self.assertEqual(alfa.checks, ["trf5-1g", "trf1-1g"])
        self.assertIn(f"advbox: {provision.NO_DEVICE}", alfa.notes)
        self.assertTrue(any("MINDCHAT_API_KEY" in note for note in alfa.notes))
        # Legacy names count once renamed; the portal without its password is left out with the reason.
        self.assertEqual(beta.portals, ["trf4-1g-sc"])
        self.assertIn("TRF4_1G_RS_USERNAME", beta.skipped["trf4-1g-rs"])
        self.assertEqual(beta.renamed["TRF4_1G_SC_USUARIO"], "TRF4_1G_SC_USERNAME")
        self.assertEqual(beta.connectors, ["eproc", "djen"])
        self.assertEqual(beta.endpoints, ["/tribunais/mcp"])
        # The A3 portal stays out; the password portal goes in, flagged because the local install used the token.
        self.assertEqual(gama.portals, ["trf1-1g"])
        self.assertIn("A3", gama.skipped["tjpe-1g"])
        self.assertTrue(any("PJE_AUTH=a3" in note for note in gama.notes))

    def test_output_and_plan_never_carry_a_value(self):
        self.export()
        text = "\n".join(self.said) + (self.out / provision.PLAN).read_text(encoding="utf-8")
        for value in VALUES:
            self.assertNotIn(value, text)
        for path in [self.out / provision.PLAN, self.passphrase, *self.out.glob("*.jurisync")]:
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600, path)

    def test_the_passphrase_file_travels_apart_from_the_bundles(self):
        with self.assertRaises(SystemExit):
            provision.export(self.out, self.out / "passphrase", say=self.said.append)

    def test_users_written_in_the_plan_survive_a_new_export(self):
        self.export()
        plan = json.loads((self.out / provision.PLAN).read_text(encoding="utf-8"))
        plan["offices"][0]["users"] = ["pessoa@alfa.test"]
        (self.out / provision.PLAN).write_text(json.dumps(plan), encoding="utf-8")
        self.export("alfa")
        offices = {
            office["tenant"]: office
            for office in json.loads((self.out / provision.PLAN).read_text(encoding="utf-8"))["offices"]
        }
        self.assertEqual(offices["alfa"]["users"], ["pessoa@alfa.test"])
        self.assertEqual(set(offices), {"alfa", "beta", "gama", "delta"})


class LocalOfficeTest(ProvisionCase):
    def test_a_local_office_is_planned_with_nothing_for_the_server(self):
        offices = {plan.tenant: plan for plan in self.export()}
        delta = offices["delta"]
        self.assertEqual((delta.mode, delta.reason), ("local", "A3 — instalação local"))
        self.assertEqual((delta.portals, delta.connectors, delta.checks), ([], [], []))
        self.assertEqual(set(delta.skipped), {"trf1-1g", "tjsp-1g"})
        self.assertFalse((self.out / "delta.jurisync").exists())
        self.assertTrue((self.out / "profiles" / "delta" / "settings.toml").is_file())
        self.assertTrue(any("só local" in line for line in self.said))

    def test_apply_keeps_only_a_record_and_drops_what_an_earlier_hosted_setup_left(self):
        self.export()
        environment = {**ProvisionCase.server(self), "JURISYNC_CUSTOMERS_DIR": str(self.ours)}
        with mock.patch.dict(os.environ, environment):
            store, vault = cli.open_store(), cli.open_vault()
            # delta was hosted before the switch: connectors, vault and a user.
            store.add_tenant("delta", "Delta", customer="delta", connectors=["pje", "djen"])
            store.add_user("antiga@delta.test", "delta", PASSWORD)
            vault.put("delta", {"PJE_1G_PASSWORD": "senha-delta"})
            store.save_check("delta", "trf1-1g", True, "login OK")
            plan = json.loads((self.out / provision.PLAN).read_text(encoding="utf-8"))
            for office in plan["offices"]:
                office["users"] = [f"advogada@{office['tenant']}.test"]
            (self.out / provision.PLAN).write_text(json.dumps(plan), encoding="utf-8")
            provision.apply(self.out, self.passphrase, say=self.said.append)
            office = store.tenant("delta")
            self.assertEqual((office["mode"], office["connectors"]), ("local", []))
            self.assertEqual(vault.names("delta"), ([], []))
            self.assertEqual(store.users("delta"), [])
            self.assertEqual(store.checks("delta"), {})
            with self.assertRaises(ValueError):
                store.add_user("nova@delta.test", "delta", PASSWORD)
            said = []
            cli.main(["tenant", "list"], say=said.append)
            self.assertIn("delta\tDelta\tperfil=delta\tsó local: A3 — instalação local", said)
            said.clear()
            cli.main(["portal", "list", "delta"], say=said.append)
            self.assertEqual(said, ["delta: só local (A3 — instalação local); nenhum portal no servidor"])
            with self.assertRaises(SystemExit):
                cli.main(["secrets", "import-bundle", "delta", str(self.out / "alfa.jurisync")], say=said.append)
            with self.assertRaises(SystemExit):
                cli.main(["tenant", "connectors", "delta", "pje"], say=said.append)
        for value in VALUES:
            self.assertNotIn(value, "\n".join(self.said))


class ApplyTest(ProvisionCase):
    def apply(self):
        plan = json.loads((self.out / provision.PLAN).read_text(encoding="utf-8"))
        for office in plan["offices"]:
            office["users"] = [f"advogada@{office['tenant']}.test"]
        (self.out / provision.PLAN).write_text(json.dumps(plan), encoding="utf-8")
        with mock.patch.dict(os.environ, self.server()):
            provision.apply(self.out, self.passphrase, say=self.said.append)
            return cli.open_store(), cli.open_vault()

    def test_the_server_gets_tenants_vault_profiles_and_users(self):
        self.export()
        store, vault = self.apply()
        self.assertEqual(store.tenant("alfa")["connectors"], ["pje", "djen", "advbox"])
        self.assertEqual(store.tenant("gama")["customer"], "gama")
        values, files = vault.get("alfa")
        self.assertEqual(files["PJE_A1_P12"], self.p12)
        self.assertEqual(values["ADVBOX_ALLOW_WRITES"], "true")
        self.assertNotIn("PJE_1G_CERT", values)  # pinned in the profile: the server reads it there, never a copy
        for never in ("PJE_ALLOW_DRAFT_WRITES", "MINDCHAT_API_KEY"):
            self.assertNotIn(never, values)
        beta, _ = vault.get("beta")
        self.assertEqual(beta["TRF4_1G_SC_USERNAME"], "user-beta")
        self.assertNotIn("TRF4_1G_SC_USUARIO", beta)
        self.assertEqual(beta["TRF4_1G_SC_OTP_PROFILE_INDEX"], "0")
        gama, _ = vault.get("gama")
        self.assertEqual(set(gama), {"PJE_1G_USERNAME", "PJE_1G_PASSWORD", "PJE_1G_2FA_DATA"})
        self.assertTrue((self.root / "server" / "customers" / "beta" / "portals.toml").is_file())
        self.assertEqual(store.authenticate("advogada@alfa.test", "wrong-password-x"), None)
        [passwords] = Path(self.server()["JURISYNC_HOSTED_DIR"]).glob("new-user-passwords-*.txt")
        email, tenant, password = passwords.read_text(encoding="utf-8").splitlines()[0].split("\t")
        self.assertEqual(store.authenticate(email, password), tenant)
        self.assertTrue(store.must_change(email))
        self.assertEqual(stat.S_IMODE(passwords.stat().st_mode), 0o600)
        text = "\n".join(self.said)
        for value in [*VALUES, password]:
            self.assertNotIn(value, text)

    def test_apply_again_updates_without_duplicating_users(self):
        self.export()
        self.apply()
        store, _ = self.apply()
        [passwords] = Path(self.server()["JURISYNC_HOSTED_DIR"]).glob("new-user-passwords-*.txt")
        self.assertEqual(len(passwords.read_text(encoding="utf-8").splitlines()), 3)
        self.assertEqual(len(store.users()), 3)

    def test_apply_never_appends_to_an_existing_password_file(self):
        self.export()
        existing = self.root / "passwords.txt"
        existing.write_text("old\n", encoding="utf-8")
        with mock.patch.dict(os.environ, self.server()), self.assertRaises(SystemExit):
            provision.apply(self.out, self.passphrase, user_passwords=existing, say=self.said.append)
        self.assertEqual(existing.read_text(encoding="utf-8"), "old\n")

    def test_apply_again_replaces_the_vault_and_keeps_what_the_server_renewed(self):
        self.export()
        store, vault = self.apply()
        values, files = vault.get("alfa")
        values.update(ADVBOX_DEVICE_ID="renewed-device", PJE_2G_PASSWORD="dropped-portal")
        vault.put("alfa", values, {**files, "OLD_P12": b"x"})
        store.save_check("alfa", "trf1-2g", True, "login OK")
        store.save_check("alfa", "trf5-1g", True, "login OK")
        self.said.clear()
        store, vault = self.apply()
        values, files = vault.get("alfa")
        self.assertEqual(values["ADVBOX_DEVICE_ID"], "renewed-device")
        self.assertNotIn("PJE_2G_PASSWORD", values)
        self.assertEqual(set(files), {"PJE_A1_P12"})
        self.assertEqual(set(store.checks("alfa")), {"trf5-1g"})
        self.assertIn("alfa: removidos do cofre: OLD_P12, PJE_2G_PASSWORD", self.said)
        self.assertNotIn("dropped-portal", "\n".join(self.said))

    def test_the_device_id_the_server_renewed_beats_the_bundle_unless_replaced(self):
        env = self.ours / "alfa" / ".env"
        env.write_text(env.read_text(encoding="utf-8") + "ADVBOX_DEVICE_ID=office-machine-device\n", encoding="utf-8")
        self.export("alfa")
        _, vault = self.apply()
        self.assertEqual(vault.get("alfa")[0]["ADVBOX_DEVICE_ID"], "office-machine-device")
        vault.update("alfa", "ADVBOX_DEVICE_ID", "renewed-device")
        _, vault = self.apply()
        self.assertEqual(vault.get("alfa")[0]["ADVBOX_DEVICE_ID"], "renewed-device")
        with mock.patch.dict(os.environ, self.server()):
            provision.apply(self.out, self.passphrase, replace_renewed=True, say=self.said.append)
        self.assertEqual(vault.get("alfa")[0]["ADVBOX_DEVICE_ID"], "office-machine-device")

    def test_an_import_keeps_the_renewed_device_id(self):
        with mock.patch.dict(os.environ, self.server()):
            store, vault = cli.open_store(), cli.open_vault()
            store.add_tenant("alfa", "Alfa", customer="", connectors=["advbox"])
            vault.put("alfa", {"ADVBOX_DEVICE_ID": "renewed-device"})
            office = store.tenant("alfa")
            cli.merge(vault, office, {"ADVBOX_DEVICE_ID": "old", "ADVBOX_EMAIL": "a@x.br"}, {}, self.said.append)
            self.assertEqual(vault.get("alfa")[0], {"ADVBOX_DEVICE_ID": "renewed-device", "ADVBOX_EMAIL": "a@x.br"})
            cli.merge(vault, office, {"ADVBOX_DEVICE_ID": "old"}, {}, self.said.append, replace_renewed=True)
            self.assertEqual(vault.get("alfa")[0]["ADVBOX_DEVICE_ID"], "old")

    def test_serve_refuses_an_office_whose_profile_is_not_installed(self):
        self.export()
        store, _ = self.apply()
        with mock.patch.dict(os.environ, self.server()):
            self.assertEqual(cli.missing_profiles(store), [])
        with mock.patch.dict(os.environ, {"JURISYNC_CUSTOMERS_DIR": str(self.root / "empty")}):
            self.assertEqual(cli.missing_profiles(store), ["alfa", "beta", "gama"])

    def test_a_wrong_passphrase_stores_nothing(self):
        self.export()
        self.passphrase.write_text("outra-senha-qualquer", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.apply()


class NormalizeTest(unittest.TestCase):
    def test_a3_switches_and_pje_writes_never_reach_the_vault(self):
        values, _ = cli.normalize(
            {"PJE_AUTH": "a3", "EPROC_AUTH": "a3", "PJE_A3_PIN": "1", "PJE_ALLOW_DRAFT_WRITES": "true", "X_PIN": "2"},
            {},
        )
        self.assertEqual(values, {"X_PIN": "2"})


class MachineSettingTest(unittest.TestCase):
    def test_systemd_credentials_serve_the_vault_key_and_machine_keys(self):
        key = generate_key()
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "JURISYNC_VAULT_KEY").write_text(key + "\n", encoding="utf-8")
            (Path(directory) / "JURISYNC_CAPTCHA_API_KEY").write_text("pago-pelo-servidor\n", encoding="utf-8")
            environment = {"CREDENTIALS_DIRECTORY": directory, "JURISYNC_VAULT_KEY": "", "JURISYNC_CAPTCHA_API_KEY": ""}
            with mock.patch.dict(os.environ, environment), credentials.scope("office-a"):
                self.assertEqual(load_key(), load_key(key))
                self.assertEqual(credentials.machine("JURISYNC_CAPTCHA_API_KEY"), "pago-pelo-servidor")
                self.assertEqual(credentials.service_credential("../JURISYNC_VAULT_KEY"), "")

    def test_a_machine_setting_ignores_the_office_scope(self):
        with mock.patch.dict(os.environ, {"JURISYNC_CAPTCHA_MODEL": "server-model"}):
            with credentials.scope("office-a", {"JURISYNC_CAPTCHA_MODEL": "office-model"}):
                self.assertEqual(credentials.machine("JURISYNC_CAPTCHA_MODEL"), "server-model")
        for name in ("ADVBOX_ALLOW_WRITES", "PJE_A1_PIN", "JURISYNC_VAULT_KEY"):
            with self.assertRaises(ValueError):
                credentials.machine(name)


def advbox_servers():
    advbox = MCPServer("advbox")

    @advbox.tool(name="advbox_list_tasks")
    def tasks() -> dict:
        return {"tenant": credentials.tenant()}

    @advbox.tool(name="advbox_create_task")
    def create() -> dict:
        return {"created": credentials.tenant()}

    return {"advbox": advbox}


class WriteGrantTest(OfficeCase):
    """office-a has no ADVBOX grant; office-b has ADVBOX_ALLOW_WRITES=true in its vault."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.store = Store(root / "store.sqlite")
        self.vault = Vault(root / "vault", load_key(generate_key()))
        for tenant, grant in (("office-a", ""), ("office-b", "true")):
            self.store.add_tenant(tenant, tenant, connectors=["advbox"])
            self.store.add_user(f"user@{tenant}.test", tenant, PASSWORD)
            self.vault.put(tenant, {"ADVBOX_ALLOW_WRITES": grant} if grant else {})
        made = advbox_servers()
        with (
            mock.patch.dict(os.environ, {REQUIRE_RESOURCE_FLAG: "", "ADVBOX_ALLOW_WRITES": "true"}),
            mock.patch("jurisync.hosted.app.TENANT_READY", ("advbox",)),
        ):
            self.app, _ = build_app(BASE, self.store, self.vault, ["advbox"], load=lambda name: (made[name], None))

    def test_only_the_granted_office_sees_and_calls_write_tools(self):
        with TestClient(self.app, base_url=BASE) as client:
            office_a = self.mcp(client, self.token(client, resource=f"{BASE}/mcp"))
            office_b = self.mcp(client, self.token(client, "user@office-b.test", resource=f"{BASE}/mcp"))
            self.assertEqual(self.names(office_a), {"advbox_list_tasks"})
            self.assertEqual(self.names(office_b), {"advbox_list_tasks", "advbox_create_task"})
            refused = self.call(office_a, "advbox_create_task")
            self.assertTrue(refused["isError"])
            self.assertIn("writes are not enabled", refused["content"][0]["text"])
            self.assertEqual(self.data(self.call(office_b, "advbox_create_task")), {"created": "office-b"})
            per_connector = self.mcp(client, self.token(client, resource=f"{BASE}/advbox/mcp"), "/advbox/mcp")
            self.assertEqual(self.names(per_connector), {"advbox_list_tasks"})


class LocalOfficeEndpointTest(OfficeCase):
    def test_a_local_office_gets_no_tools_and_every_call_says_why(self):
        with TestClient(self.app, base_url=BASE) as client:
            token = self.token(client, resource=f"{BASE}/mcp")
            self.store.update_tenant("office-a", mode="local", reason="A3 — instalação local")
            office_a = self.mcp(client, token)
            self.assertEqual(self.names(office_a), set())
            refused = self.call(office_a, "alpha_whoami")
            self.assertTrue(refused["isError"])
            self.assertIn("uses its local Jurisync install (A3 — instalação local)", refused["content"][0]["text"])
            self.assertEqual(ping(client, token, "/alpha/mcp").status_code, 401)  # token was for /mcp only
            office_b = self.mcp(client, self.token(client, "user@office-b.test", resource=f"{BASE}/mcp"))
            self.assertEqual(self.names(office_b), {"alpha_whoami", "alpha_fail", "beta_whoami"})


class JuridiqGrantTest(OfficeCase):
    """The real Juridiq server as hosted builds it (writes registered); only office-b has JURIDIQ_ALLOW_WRITES."""

    TASK = {"title": "Réplica", "priority": "Alta", "columnId": "00000000-0000-4000-8000-000000000001",
            "initialDate": "2026-10-07"}  # fmt: skip

    def setUp(self):
        import base64
        import time

        from jurisync.connectors.juridiq.api import JuridiqApi
        from jurisync.connectors.juridiq.server import create_server

        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.sent = []

        def send(method, url, headers, body):
            expiry = base64.urlsafe_b64encode(json.dumps({"exp": int(time.time()) + 3600}).encode()).decode()
            if url.endswith("/auth/login"):
                email = json.loads(body)["email"]
                payload = {"token": f"x.{expiry.rstrip('=')}.{email}"}
            else:
                self.sent.append((method, url, headers.get("authorization")))
                payload = {"id": "task-1"}
            return mock.Mock(status_code=200, text=json.dumps(payload), json=lambda: payload)

        self.store = Store(root / "store.sqlite")
        self.vault = Vault(root / "vault", load_key(generate_key()))
        for tenant, grant in (("office-a", ""), ("office-b", "true")):
            self.store.add_tenant(tenant, tenant, connectors=["juridiq"])
            self.store.add_user(f"user@{tenant}.test", tenant, PASSWORD)
            entry = {
                "JURIDIQ_EMAIL": f"{tenant}@example.invalid",
                "JURIDIQ_PASSWORD": "offline-only",
                "JURIDIQ_API_BASE_URL": "https://api.example.invalid",
                "JURIDIQ_LOGIN_URL": "https://api.example.invalid/auth/login",
            }
            self.vault.put(tenant, {**entry, "JURIDIQ_ALLOW_WRITES": grant} if grant else entry)
        juridiq = create_server(allow_writes=True, api=JuridiqApi(send=send))
        environment = {REQUIRE_RESOURCE_FLAG: "", "JURIDIQ_ALLOW_WRITES": "true", "LOCALAPPDATA": str(root / "cache")}
        patcher = mock.patch.dict(os.environ, environment)
        patcher.start()
        self.addCleanup(patcher.stop)
        with mock.patch("jurisync.hosted.app.TENANT_READY", ("juridiq",)):
            self.app, _ = build_app(BASE, self.store, self.vault, ["juridiq"], load=lambda name: (juridiq, None))

    def test_an_office_without_the_grant_neither_sees_nor_calls_juridiq_writes(self):
        from jurisync import registry

        writes = registry.CONNECTORS["juridiq"].write_tools
        with TestClient(self.app, base_url=BASE) as client:
            office_a = self.mcp(client, self.token(client, resource=f"{BASE}/mcp"))
            office_b = self.mcp(client, self.token(client, "user@office-b.test", resource=f"{BASE}/mcp"))
            self.assertFalse(self.names(office_a) & writes)
            self.assertLessEqual(writes, self.names(office_b))
            for name in sorted(writes):
                with self.subTest(tool=name):
                    refused = self.call(office_a, name, {})
                    self.assertTrue(refused["isError"])
                    self.assertIn("writes are not enabled", refused["content"][0]["text"])
            self.assertEqual(self.sent, [])
            created = self.call(office_b, "juridiq_create_task", {"payload": self.TASK})
            self.assertFalse(created.get("isError"), created)
        self.assertEqual(len(self.sent), 1)
        method, url, token = self.sent[0]
        self.assertEqual((method, url), ("POST", "https://api.example.invalid/task/"))
        self.assertTrue(token.endswith("office-b@example.invalid"))


class JuridiqWritesTest(unittest.TestCase):
    def test_hosted_juridiq_registers_writes_and_marks_them_granted(self):
        from jurisync import registry
        from jurisync.hosted import app

        server, _ = app.hosted_load("juridiq")
        grant, tools = app.GRANTED_WRITES["juridiq"]
        self.assertEqual(grant, "JURIDIQ_ALLOW_WRITES")
        connector = registry.get("juridiq")
        built = {
            allowed: {tool.name for tool in connector.load(allow_writes=allowed)[0]._tool_manager.list_tools()}
            for allowed in (True, False)
        }
        self.assertEqual(tools, built[True] - built[False])  # a new write tool must be listed in the registry
        self.assertEqual({tool.name for tool in server._tool_manager.list_tools()}, built[True])
        with credentials.scope("office-a"):
            self.assertFalse(app.granted("juridiq", "juridiq_create_task"))
            self.assertTrue(app.granted("juridiq", "juridiq_list_tasks"))
        with credentials.scope("office-b", {"JURIDIQ_ALLOW_WRITES": "true"}):
            self.assertTrue(app.granted("juridiq", "juridiq_create_task"))


class RemotePluginTest(unittest.TestCase):
    def test_the_plugin_points_at_the_server_and_flags_local_only_skills(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skills = root / "customers" / "x" / "skills"
            for name, text in (
                ("triagem", "Use pje_list_inbox."),
                ("protocolo", "Use filing_prepare e pje_draft_write."),
            ):
                (skills / name).mkdir(parents=True)
                (skills / name / "SKILL.md").write_text(text, encoding="utf-8")
            plugin = root / "plugins" / "x"
            (plugin / ".claude-plugin").mkdir(parents=True)
            (plugin / "skills").mkdir()
            for name in ("triagem", "protocolo"):
                (plugin / "skills" / name).symlink_to(skills / name)
            local = {name: {"command": f"jurisync-{name}"} for name in ("pje", "djen", "advbox", "filing")}
            manifest = {"name": "x", "skills": ["./skills/triagem", "./skills/protocolo"], "mcpServers": local}
            (plugin / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
            out = root / "remote"
            local_only = provision.remote_plugin(plugin, "https://mcp.exemplo.com.br/mcp", out, say=lambda _: None)
            written = json.loads((out / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
            self.assertEqual(
                written["mcpServers"],
                {
                    "tribunais": {"type": "http", "url": "https://mcp.exemplo.com.br/tribunais/mcp"},
                    "advbox": {"type": "http", "url": "https://mcp.exemplo.com.br/advbox/mcp"},
                },
            )
            provision.remote_plugin(plugin, "https://mcp.exemplo.com.br", out, say=lambda _: None)  # base address
            self.assertEqual(
                json.loads((out / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["mcpServers"],
                written["mcpServers"],
            )
            self.assertFalse((out / "skills" / "triagem").is_symlink())
            self.assertEqual(local_only, {"protocolo": ["filing_prepare", "pje_draft_write"]})
            with self.assertRaises(SystemExit):
                provision.remote_plugin(plugin, "http://127.0.0.1:8080/mcp", out, say=lambda _: None)
