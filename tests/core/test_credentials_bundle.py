import io
import json
import os
import stat
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from jurisync.install import credentials_bundle as bundle
from jurisync.install import setup

ENV = {"TJRS_1G_USERNAME": "user-123", "TJRS_1G_PASSWORD": "s3cret-value", "TJRS_1G_2FA_DATA": "otpauth://x"}
PASSPHRASE = "correct horse battery"
URI = "otpauth://totp/Eproc:user?secret=JBSWY3DPEHPK3PXP&issuer=Eproc"


class SealTest(unittest.TestCase):
    def test_roundtrip_hides_values_and_rejects_wrong_passphrase_or_tampering(self):
        text = bundle.seal(ENV, PASSPHRASE, customer="elisandra")
        for value in ENV.values():
            self.assertNotIn(value, text)
        opened = bundle.unseal(text, PASSPHRASE)
        self.assertEqual((opened["customer"], opened["env"]), ("elisandra", ENV))
        with self.assertRaisesRegex(ValueError, "incorreta"):
            bundle.unseal(text, "another passphrase!")
        tampered = json.loads(text)
        tampered["data"] = tampered["data"][:-4] + "AAAA"
        with self.assertRaises(ValueError):
            bundle.unseal(json.dumps(tampered), PASSPHRASE)
        with self.assertRaisesRegex(ValueError, "não é um pacote"):
            bundle.unseal("{}", PASSPHRASE)

    def test_short_passphrases_are_refused(self):
        with self.assertRaises(ValueError):
            bundle.seal(ENV, "short")


class PageTest(unittest.TestCase):
    def test_page_returns_the_passphrase_once_and_checks_host_and_token(self):
        urls = []
        result = {}

        def run():
            result["value"] = bundle.passphrase_from_page(urls.append, timeout=20, open_browser=False)

        thread = threading.Thread(target=run)
        thread.start()
        for _ in range(100):
            if urls:
                break
            threading.Event().wait(0.05)
        url = urls[0].split(": ", 1)[1]
        self.assertEqual(urllib.request.urlopen(url, timeout=5).status, 200)
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(url.rsplit("/", 1)[0] + "/wrong-token", timeout=5)
        request = urllib.request.Request(url, data=b"passphrase=abc+def%21", method="POST")
        self.assertEqual(urllib.request.urlopen(request, timeout=5).status, 200)
        thread.join(10)
        self.assertEqual(result["value"], "abc def!")


class ExportImportTest(unittest.TestCase):
    def test_export_then_import_writes_the_same_variables(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            customer = root / "customers" / "demo"
            customer.mkdir(parents=True)
            (customer / "portals.toml").write_text(
                '[tjrs-1g]\nmethod = "browser_totp"\nsecrets = ["TJRS_1G_USERNAME"]\n', encoding="utf-8"
            )
            setup.write_env(customer / ".env", {**ENV, "UNRELATED_TOKEN": "keep-out"})
            messages = []
            out = root / "demo.jurisync"
            with mock.patch.dict(os.environ, {"JURISYNC_CUSTOMERS_DIR": str(root / "customers")}, clear=True):
                code = setup.main(["export", "--customer", "demo", "--out", str(out)], say=messages.append)
            self.assertEqual(code, 0)
            self.assertEqual(stat.S_IMODE(out.stat().st_mode), 0o600)
            passphrase = next(m for m in messages if m.startswith("Senha do pacote")).rsplit(": ", 1)[1]
            self.assertNotIn("keep-out", out.read_text(encoding="utf-8"))
            target = root / "home" / ".env"
            with mock.patch.dict(os.environ, {}, clear=True), mock.patch("sys.stdin", io.StringIO(passphrase + "\n")):
                code = setup.main(
                    [
                        "import",
                        str(out),
                        "--passphrase-stdin",
                        "--env-file",
                        str(target),
                        "--no-login",
                        "--register",
                        "none",
                    ],
                    say=lambda _m: None,
                )
            self.assertEqual(code, 0)
            self.assertEqual(setup.read_env(target), ENV)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            # a wrong passphrase fails cleanly and writes nothing new
            other = root / "other" / ".env"
            with (
                mock.patch.dict(os.environ, {}, clear=True),
                mock.patch("sys.stdin", io.StringIO("wrong passphrase 1\n")),
            ):
                code = setup.main(
                    [
                        "import",
                        str(out),
                        "--passphrase-stdin",
                        "--env-file",
                        str(other),
                        "--no-login",
                        "--register",
                        "none",
                    ],
                    say=lambda _m: None,
                )
            self.assertEqual(code, 2)
            self.assertFalse(other.exists())


class FilesAndChecksTest(unittest.TestCase):
    def test_files_checks_and_servers_survive_the_roundtrip(self):
        text = bundle.seal(
            ENV,
            PASSPHRASE,
            customer="demo",
            files={"cert.p12": b"\x00binary\xff"},
            file_vars={"PJE_A1_P12": "cert.p12"},
            checks=["pje:trf5-1g", "advbox"],
            servers=["pje", "advbox", "djen"],
        )
        self.assertNotIn("binary", text)
        opened = bundle.unseal(text, PASSPHRASE)
        self.assertEqual(opened["files"], {"cert.p12": b"\x00binary\xff"})
        self.assertEqual(opened["file_vars"], {"PJE_A1_P12": "cert.p12"})
        self.assertEqual((opened["checks"], opened["servers"]), (["pje:trf5-1g", "advbox"], ["pje", "advbox", "djen"]))


class CertificateCustomerTest(unittest.TestCase):
    """A Victor-like customer: A1 file, authenticator QR image, and non-portal variables from the shared .env."""

    def test_export_embeds_the_certificate_and_import_points_the_variable_at_the_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            customer = root / "customers" / "vic"
            customer.mkdir(parents=True)
            cert, qr = root / "a1.p12", root / "qr.jpeg"
            cert.write_bytes(b"CERTIFICATE-BYTES")
            qr.write_bytes(b"image")
            (customer / "portals.toml").write_text(
                '[trf5-1g]\nmethod = "certificate_totp"\n'
                'secrets = ["PJE_A1_P12", "PJE_A1_PIN", "PJE_TRF5_2FA_DATA|PJE_TRF5_2FA_QR"]\n'
                '[env]\nsecrets = ["ADVBOX_EMAIL", "ADVBOX_PASSWORD"]\noptional = ["OPENROUTER_API_KEY"]\n'
                'set = { ADVBOX_ALLOW_WRITES = "true" }\n',
                encoding="utf-8",
            )
            setup.write_env(
                customer / ".env", {"PJE_A1_P12": str(cert), "PJE_A1_PIN": "pin-1", "PJE_TRF5_2FA_QR": str(qr)}
            )
            shared = root / "shared.env"
            setup.write_env(shared, {"ADVBOX_EMAIL": "v@x", "ADVBOX_PASSWORD": "pw", "UNRELATED": "no"})
            out, messages = root / "vic.jurisync", []
            env = {"JURISYNC_CUSTOMERS_DIR": str(root / "customers"), "JURISYNC_ENV_FILE": str(shared)}
            command = ["export", "--customer", "vic", "--out", str(out)]
            allow = ["--allow-shared", "ADVBOX_EMAIL", "--allow-shared", "ADVBOX_PASSWORD"]
            with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(setup, "qr_text", return_value=URI):
                # A declared secret found only in the shared file is refused until the operator names it.
                with self.assertRaises(SystemExit) as refusal:
                    setup.main(command, say=messages.append)
                self.assertIn("--allow-shared ADVBOX_EMAIL --allow-shared ADVBOX_PASSWORD", str(refusal.exception))
                self.assertNotIn("v@x", str(refusal.exception))
                self.assertFalse(out.exists())
                with self.assertRaises(SystemExit):
                    setup.main([*command, "--allow-shared", "UNRELATED"], say=messages.append)
                self.assertEqual(setup.main([*command, *allow], say=messages.append), 0)
            joined = "\n".join(messages)
            self.assertIn("a1.p12", joined)
            self.assertIn("ADVBOX_EMAIL", joined)
            self.assertIn("vindas do .env da raiz", joined)
            self.assertNotIn("UNRELATED", joined)
            passphrase = next(m for m in messages if m.startswith("Senha do pacote")).rsplit(": ", 1)[1]
            self.assertNotIn("CERTIFICATE-BYTES", out.read_text(encoding="utf-8"))

            target = root / "home" / ".env"
            with (
                mock.patch.dict(os.environ, {}, clear=True),
                mock.patch("sys.stdin", io.StringIO(passphrase + "\n")),
            ):
                code = setup.main(
                    [
                        "import",
                        str(out),
                        "--passphrase-stdin",
                        "--env-file",
                        str(target),
                        "--no-login",
                        "--register",
                        "none",
                    ],
                    say=lambda _m: None,
                )
            self.assertEqual(code, 0)
            stored = setup.read_env(target)
            copy = Path(stored["PJE_A1_P12"])
            self.assertEqual(copy.read_bytes(), b"CERTIFICATE-BYTES")
            self.assertEqual(copy.parent, target.parent / "files")
            self.assertEqual(stat.S_IMODE(copy.stat().st_mode), 0o600)
            self.assertEqual(stored["PJE_TRF5_2FA_DATA"], URI)
            self.assertNotIn("PJE_TRF5_2FA_QR", stored)
            self.assertEqual(stored["ADVBOX_EMAIL"], "v@x")
            self.assertEqual(stored["ADVBOX_ALLOW_WRITES"], "true")
            self.assertNotIn("UNRELATED", stored)

    def test_shared_env_never_leaks_another_customers_portal_variables(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            customer = root / "customers" / "vic"
            customer.mkdir(parents=True)
            (root / "a1.p12").write_bytes(b"CERTIFICATE-BYTES")
            (customer / "portals.toml").write_text(
                '[trf1-1g]\nmethod = "certificate_totp"\n'
                'secrets = ["PJE_A1_P12", "PJE_A1_PIN", "PJE_TRF5_2FA_DATA"]\n'
                '[env]\nset = { PJE_1G_CERT = "true" }\n',
                encoding="utf-8",
            )
            setup.write_env(
                customer / ".env",
                {"PJE_A1_P12": str(root / "a1.p12"), "PJE_A1_PIN": "pin-1", "PJE_TRF5_2FA_DATA": URI},
            )
            shared = root / "shared.env"
            setup.write_env(
                shared, {"PJE_1G_USERNAME": "jean-cpf", "PJE_1G_PASSWORD": "jean-pw", "PJE_1G_2FA_DATA": "jean-totp"}
            )
            out, messages = root / "vic.jurisync", []
            env = {"JURISYNC_CUSTOMERS_DIR": str(root / "customers"), "JURISYNC_ENV_FILE": str(shared)}
            with mock.patch.dict(os.environ, env, clear=True):
                self.assertEqual(setup.main(["export", "--customer", "vic", "--out", str(out)], say=messages.append), 0)
            joined = "\n".join(messages)
            self.assertNotIn("PJE_1G_USERNAME", joined)
            self.assertNotIn("PJE_1G_PASSWORD", joined)
            self.assertIn("PJE_1G_CERT=true", joined)

    def test_portals_limits_the_bundle_to_those_portals(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            customer = root / "customers" / "vic"
            customer.mkdir(parents=True)
            (customer / "portals.toml").write_text(
                '[tjrs-1g]\nmethod = "browser_totp"\nsecrets = ["TJRS_1G_USERNAME", "TJRS_1G_2FA_DATA"]\n'
                '[tjsc-1g]\nmethod = "browser_totp"\nsecrets = ["TJSC_1G_USERNAME", "TJSC_1G_2FA_DATA"]\n',
                encoding="utf-8",
            )
            setup.write_env(
                customer / ".env",
                {"TJRS_1G_USERNAME": "rs", "TJRS_1G_2FA_DATA": URI, "TJSC_1G_USERNAME": "sc", "TJSC_1G_2FA_DATA": URI},
            )
            out, messages = root / "vic.jurisync", []
            env = {"JURISYNC_CUSTOMERS_DIR": str(root / "customers"), "JURISYNC_ENV_FILE": str(root / "none.env")}
            command = ["export", "--customer", "vic", "--out", str(out)]
            with mock.patch.dict(os.environ, env, clear=True):
                with self.assertRaises(SystemExit):
                    setup.main([*command, "--portals", "tjrs-1g,trf5-1g"], say=messages.append)
                self.assertEqual(setup.main([*command, "--portals", "tjrs-1g"], say=messages.append), 0)
            joined = "\n".join(messages)
            self.assertIn("variáveis (2): TJRS_1G_2FA_DATA, TJRS_1G_USERNAME", joined)
            self.assertIn("verificações: eproc:tjrs-1g |", joined)
            self.assertNotIn("TJSC", joined)

    def test_an_optional_name_in_the_shared_file_is_left_out_with_a_notice(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            customer = root / "customers" / "vic"
            customer.mkdir(parents=True)
            (customer / "portals.toml").write_text(
                '[tjrs-1g]\nmethod = "browser_totp"\nsecrets = ["TJRS_1G_USERNAME", "TJRS_1G_2FA_DATA|TJRS_1G_2FA_QR"]\n'
                '[env]\noptional = ["OPENROUTER_API_KEY"]\n',
                encoding="utf-8",
            )
            setup.write_env(customer / ".env", {"TJRS_1G_USERNAME": "user-1", "TJRS_1G_2FA_DATA": URI})
            shared = root / "shared.env"
            setup.write_env(shared, {"OPENROUTER_API_KEY": "someone-elses", "TJRS_1G_2FA_QR": "/other/qr.png"})
            out, messages = root / "vic.jurisync", []
            env = {"JURISYNC_CUSTOMERS_DIR": str(root / "customers"), "JURISYNC_ENV_FILE": str(shared)}
            with mock.patch.dict(os.environ, env, clear=True):
                self.assertEqual(setup.main(["export", "--customer", "vic", "--out", str(out)], say=messages.append), 0)
            joined = "\n".join(messages)
            self.assertIn("ignoradas do .env da raiz", joined)
            self.assertIn("variáveis (2): TJRS_1G_2FA_DATA, TJRS_1G_USERNAME", joined)
            self.assertNotIn("someone-elses", joined)


class ImportValidationTest(unittest.TestCase):
    def run_import(self, env, *, file_vars=None, files=None, customer=""):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        out, self.messages = self.root / "x.jurisync", []
        out.write_text(
            bundle.seal(env, "a long passphrase", customer=customer, files=files, file_vars=file_vars), encoding="utf-8"
        )
        self.target = self.root / "home" / ".env"
        command = ["import", str(out), "--passphrase-stdin", "--env-file", str(self.target)]
        with (
            mock.patch.dict(os.environ, {"JURISYNC_CUSTOMERS_DIR": str(self.root / "customers")}, clear=True),
            mock.patch("sys.stdin", io.StringIO("a long passphrase\n")),
        ):
            return setup.main([*command, "--no-login", "--register", "none"], say=self.messages.append)

    def test_only_portal_and_system_names_are_written(self):
        env = {
            "TJRS_1G_USERNAME": "user-1",
            "PJE_TRF5_2FA_DATA": URI,
            "ADVBOX_EMAIL": "v@x",
            "PROJUDI_A1_PIN": "1234",
            "JURISYNC_ENV_FILE": "/elsewhere/.env",
            "JURISYNC_CHROME_PATH": "/tmp/evil",
            "LIBREOFFICE_BIN": "/tmp/evil",
            "JURIDIQ_LOGIN_URL": "https://evil.example/login",
            "TJRS_1G_LOGIN_URL": "https://evil.example/login",
            "PATH": "/tmp",
            "LD_PRELOAD": "/tmp/x.so",
            "A=B": "x",
            "lower_case": "x",
        }
        self.assertEqual(self.run_import(env), 0)
        self.assertEqual(
            set(setup.read_env(self.target)),
            {"TJRS_1G_USERNAME", "PJE_TRF5_2FA_DATA", "ADVBOX_EMAIL", "PROJUDI_A1_PIN"},
        )
        notice = next(m for m in self.messages if m.startswith("Variáveis do pacote ignoradas"))
        for name in ("JURISYNC_ENV_FILE", "TJRS_1G_LOGIN_URL", "LD_PRELOAD", "PATH", "'A=B'"):
            self.assertIn(name, notice)
        self.assertNotIn("evil", notice)

    def test_names_the_customer_declares_are_accepted_when_its_setup_is_present(self):
        env = {"TJRS_1G_USERNAME": "user-1", "ACME_ATTORNEY_CPF": "000", "OTHER_CPF": "111"}
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (root / "acme").mkdir()
        (root / "acme" / "portals.toml").write_text(
            '[tjrs-1g]\nmethod = "browser_totp"\nsecrets = ["TJRS_1G_USERNAME"]\n'
            '[env]\nsecrets = ["ACME_ATTORNEY_CPF"]\n',
            encoding="utf-8",
        )
        with mock.patch.dict(os.environ, {"JURISYNC_CUSTOMERS_DIR": str(root)}):
            self.assertEqual(setup.bundle_names("acme"), {"TJRS_1G_USERNAME", "ACME_ATTORNEY_CPF"})
            accepted = setup.accepted_values(env, "acme", lambda _m: None)
        self.assertEqual(set(accepted), {"TJRS_1G_USERNAME", "ACME_ATTORNEY_CPF"})

    def test_a_value_with_a_line_break_or_control_character_fails_and_writes_nothing(self):
        for value in ("pw\nJURISYNC_ENV_FILE=/elsewhere", "pw\rX=1", "pw\x00", "pw\x1b[0m"):
            with self.subTest(value=value):
                self.assertEqual(self.run_import({"TJRS_1G_USERNAME": "user-1", "TJRS_1G_PASSWORD": value}), 2)
                self.assertFalse(self.target.exists())
                self.assertNotIn("pw", "\n".join(self.messages))

    def test_a_file_variable_must_be_a_certificate_name_with_its_file_in_the_bundle(self):
        self.assertEqual(self.run_import({}, file_vars={"TJRS_1G_USERNAME": "a1.p12"}, files={"a1.p12": b"x"}), 2)
        self.assertEqual(self.run_import({}, file_vars={"PJE_A1_P12": "missing.p12"}, files={"a1.p12": b"x"}), 2)
        code = self.run_import(
            {"PJE_A1_PIN": "1"},
            file_vars={"PROJUDI_A1_P12": "../../a1.p12"},
            files={"../../a1.p12": b"CERT", "stray.bin": b"x"},
        )
        self.assertEqual(code, 0)
        stored = setup.read_env(self.target)
        self.assertEqual(Path(stored["PROJUDI_A1_P12"]), self.target.parent / "files" / "a1.p12")
        self.assertEqual(sorted(p.name for p in (self.target.parent / "files").iterdir()), ["a1.p12"])


class FileVariableTest(unittest.TestCase):
    def test_the_local_and_hosted_imports_share_one_certificate_rule(self):
        from jurisync.hosted import cli

        for name in ("PJE_A1_P12", "PROJUDI_A1_P12", "TJSP_1G_A1_P12", "ESAJ_A1_PFX"):
            self.assertTrue(setup.is_file_variable(name), name)
        for name in ("PJE_A1_PIN", "PJE_1G_CERT", "PJE_TRF5_2FA_QR"):
            self.assertFalse(setup.is_file_variable(name), name)
        values, files = cli.normalize({"PROJUDI_A1_P12": "a1.p12", "PJE_A1_PIN": "1"}, {"PROJUDI_A1_P12": b"CERT"})
        self.assertEqual((values, files), ({"PJE_A1_PIN": "1"}, {"PROJUDI_A1_P12": b"CERT"}))


if __name__ == "__main__":
    unittest.main()
