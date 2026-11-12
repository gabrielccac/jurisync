"""Offline checks for registering an office's access: certificate, per-portal login, test-before-save, history."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pyotp

from jurisync import portals
from jurisync.core import credentials
from jurisync.core.session_pool import SessionPool
from jurisync.hosted import access, cli
from jurisync.hosted.throttle import Throttle
from jurisync.hosted.vault import generate_key

from .test_hosted import make_p12_bytes

SECRET = "JBSWY3DPEHPK3PXP"


class AccessRulesTest(unittest.TestCase):
    def test_password_login_sets_only_this_portal(self):
        trf1 = portals.pje("trf1-1g")
        values = {"PJE_2G_USERNAME": "outro", "PJE_1G_CERT": "true", "PJE_A1_PIN": "1"}
        uri = access.single_account_uri(access.authenticator_accounts(text=SECRET))
        updated = access.with_portal(values, trf1, access.PASSWORD, username="123", password="s", authenticator=uri)
        self.assertEqual(updated["PJE_1G_USERNAME"], "123")
        self.assertNotIn("PJE_1G_CERT", updated)
        self.assertEqual(updated["PJE_2G_USERNAME"], "outro")
        self.assertEqual(access.portal_state(trf1, updated, {}).method, access.PASSWORD)
        with self.assertRaises(ValueError):
            access.with_portal(values, trf1, access.PASSWORD, username="123", password="s")

    def test_certificate_opts_in_password_portals_and_drops_their_password(self):
        trf1, trf5 = portals.pje("trf1-1g"), portals.pje("trf5-1g")
        values = {"PJE_1G_USERNAME": "123", "PJE_1G_PASSWORD": "s"}
        updated = access.with_portal(values, trf1, access.CERTIFICATE)
        self.assertEqual(updated, {"PJE_1G_CERT": "true"})
        files = {access.A1_FILE: b"x"}
        self.assertEqual(access.portal_state(trf1, updated, files).method, access.CERTIFICATE)
        self.assertEqual(access.portal_state(trf5, {}, files).method, access.CERTIFICATE)
        self.assertNotIn("PJE_TRF5_1G_CERT", access.with_portal({}, trf5, access.CERTIFICATE))
        self.assertIsNone(access.portal_state(trf1, {}, {}).method)

    def test_authenticator_exports_become_one_totp_uri(self):
        many = [
            {"secret": SECRET, "name": "a", "issuer": "PJe", "type": "totp"},
            {"secret": "KRSXG5CTMVRXEZLU", "name": "b", "issuer": "Gov", "type": "totp"},
        ]
        uri = access.single_account_uri(many, 1)
        self.assertEqual(pyotp.parse_uri(uri).secret, "KRSXG5CTMVRXEZLU")
        with self.assertRaises(ValueError):
            access.single_account_uri(many)
        with self.assertRaises(ValueError):
            access.authenticator_accounts(text="não é um código")

    def test_certificate_is_described_without_exposing_it(self):
        data, pin = make_p12_bytes("Advogada Teste", "pin-1")
        info = access.read_certificate(data, pin)
        self.assertEqual(info.holder, "Advogada Teste")
        self.assertFalse(info.expired)
        with self.assertRaises(ValueError):
            access.read_certificate(data, "errado")


class ForgetAndThrottleTest(unittest.TestCase):
    def test_forget_drops_only_the_current_tenants_login(self):
        pool = SessionPool(login=lambda portal: object(), refresh=lambda portal, s: s, close=lambda s: None)
        with credentials.scope("a"), pool.session("trf1-1g") as first:
            pass
        with credentials.scope("b"), pool.session("trf1-1g") as other:
            pass
        with credentials.scope("a"):
            pool.forget("trf1-1g")
            with pool.session("trf1-1g") as again:
                pass
        with credentials.scope("b"), pool.session("trf1-1g") as same:
            pass
        self.assertIsNot(first, again)
        self.assertIs(other, same)

    def test_throttle_blocks_after_repeated_failures(self):
        now = [0.0]
        throttle = Throttle(max_failures=3, window=60, clock=lambda: now[0])
        for _ in range(3):
            throttle.fail("x@y")
        self.assertTrue(throttle.blocked("x@y"))
        self.assertFalse(throttle.blocked("z@y"))
        now[0] = 61
        self.assertFalse(throttle.blocked("x@y"))


class OperatorCommandsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(
            os.environ, {"JURISYNC_HOSTED_DIR": self.directory.name, "JURISYNC_VAULT_KEY": generate_key()}
        )
        self.env.start()
        self.said = []
        self.cli(["tenant", "add", "office-a", "--name", "A"])

    def tearDown(self):
        self.env.stop()
        self.directory.cleanup()

    def cli(self, argv):
        return cli.main(argv, say=self.said.append)

    def test_stored_access_can_be_listed_tested_and_removed(self):
        data, pin = make_p12_bytes("Advogado Teste", "pin-9")
        trf1 = portals.pje("trf1-1g")
        uri = access.single_account_uri(access.authenticator_accounts(text=SECRET))
        values = access.with_portal(
            {access.A1_PIN: pin}, trf1, access.PASSWORD, username="123", password="certa", authenticator=uri
        )
        cli.open_vault().put("office-a", values, {access.A1_FILE: data})

        def login(portal):
            self.assertEqual((credentials.tenant(), credentials.env("PJE_1G_PASSWORD")), ("office-a", "certa"))

        with mock.patch.object(access, "check_login", side_effect=login):
            self.cli(["portal", "check", "office-a", "trf1-1g"])
        with mock.patch.object(access, "check_login", side_effect=RuntimeError("Invalid password")):
            with self.assertRaises(SystemExit):
                self.cli(["portal", "check", "office-a", "trf1-2g"])
        self.said.clear()
        self.cli(["portal", "list", "office-a"])
        self.assertIn("Certificado A1: Advogado Teste", " ".join(self.said))
        self.assertTrue(
            any(line.startswith("trf1-1g") and "login com senha" in line and "OK" in line for line in self.said)
        )
        self.assertTrue(any(line.startswith("trf1-2g") and "falhou" in line for line in self.said))

        self.cli(["portal", "remove", "office-a", "trf1-1g"])
        self.cli(["certificate", "remove", "office-a"])
        self.assertEqual(cli.open_vault().get("office-a"), ({}, {}))
        self.said.clear()
        self.cli(["history", "office-a"])
        joined = " ".join(self.said)
        for action in (
            "teste de login OK",
            "teste de login falhou",
            "acesso ao portal removido",
            "certificado A1 removido",
        ):
            self.assertIn(action, joined)
        self.assertNotIn("certa", joined)

    def test_bundle_import_can_require_a_login_check(self):
        from jurisync.install import credentials_bundle

        bundle = Path(self.directory.name) / "office.jurisync"
        env = {"PJE_1G_USERNAME": "1", "PJE_1G_PASSWORD": "2", "PJE_1G_2FA_DATA": SECRET, "ADVBOX_PASSWORD": "x"}
        bundle.write_text(credentials_bundle.seal(env, "senha-do-pacote-123"))
        argv = ["secrets", "import-bundle", "office-a", str(bundle), "--portal", "trf1-1g", "--check"]
        with mock.patch("getpass.getpass", return_value="senha-do-pacote-123"):
            with (
                mock.patch.object(access, "check_login", side_effect=RuntimeError("bad")),
                self.assertRaises(SystemExit),
            ):
                self.cli(argv)
            self.assertEqual(cli.open_vault().get("office-a"), ({}, {}))
            with mock.patch.object(access, "check_login"):
                self.cli(argv)
        values, _ = cli.open_vault().get("office-a")
        self.assertEqual(values["PJE_1G_PASSWORD"], "2")
        self.assertNotIn("ADVBOX_PASSWORD", values)


if __name__ == "__main__":
    unittest.main()
