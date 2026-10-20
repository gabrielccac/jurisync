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

from jurisync import credentials_bundle as bundle
from jurisync import setup

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
            with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(setup, "qr_text", return_value=URI):
                self.assertEqual(setup.main(["export", "--customer", "vic", "--out", str(out)], say=messages.append), 0)
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


if __name__ == "__main__":
    unittest.main()


def test_inss_customer_bundle_registers_server_and_keeps_chrome_profile_with_config(tmp_path, monkeypatch):
    from jurisync import credentials_bundle, setup

    customers = tmp_path / "customers" / "jeanx"
    customers.mkdir(parents=True)
    (customers / "portals.toml").write_text('[env]\nsecrets = ["INSS_GERID_TOTP_SECRET"]\n', encoding="utf-8")
    (customers / ".env").write_text("INSS_GERID_TOTP_SECRET=otpauth://totp/x?secret=ABC\n", encoding="utf-8")
    monkeypatch.setenv("JURISYNC_CUSTOMERS_DIR", str(tmp_path / "customers"))
    monkeypatch.setenv("JURISYNC_ENV_FILE", str(tmp_path / "none.env"))
    out = tmp_path / "b.jurisync"
    lines = []
    setup.export_main(["--customer", "jeanx", "--out", str(out)], ask_secret=None, say=lines.append)
    passphrase = lines[-1].rsplit(": ", 1)[1]
    opened = credentials_bundle.unseal(out.read_text(encoding="utf-8"), passphrase)
    assert "inss" in opened["servers"]
    assert opened["checks"] == []
