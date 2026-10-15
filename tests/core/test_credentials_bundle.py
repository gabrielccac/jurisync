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
                    ["import", str(out), "--passphrase-stdin", "--env-file", str(target), "--no-login",
                     "--register", "none"],
                    say=lambda _m: None,
                )
            self.assertEqual(code, 0)
            self.assertEqual(setup.read_env(target), ENV)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            # a wrong passphrase fails cleanly and writes nothing new
            other = root / "other" / ".env"
            with mock.patch.dict(os.environ, {}, clear=True), mock.patch("sys.stdin", io.StringIO("wrong passphrase 1\n")):
                code = setup.main(
                    ["import", str(out), "--passphrase-stdin", "--env-file", str(other), "--no-login", "--register", "none"],
                    say=lambda _m: None,
                )
            self.assertEqual(code, 2)
            self.assertFalse(other.exists())


if __name__ == "__main__":
    unittest.main()
