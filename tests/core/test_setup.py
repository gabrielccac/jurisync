import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jurisync import setup

URI = "otpauth://totp/Eproc:user?secret=JBSWY3DPEHPK3PXP&issuer=Eproc"


class EnvFileTest(unittest.TestCase):
    def test_write_merges_keeps_other_lines_and_is_owner_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sub" / ".env"
            path.parent.mkdir()
            path.write_text("# comment\nOTHER=1\nTJRS_1G_USERNAME=old\n", encoding="utf-8")
            setup.write_env(path, {"TJRS_1G_USERNAME": "new", "TJRS_1G_PASSWORD": "pw"})
            self.assertEqual(
                path.read_text(encoding="utf-8").splitlines(),
                ["# comment", "OTHER=1", "TJRS_1G_USERNAME=new", "TJRS_1G_PASSWORD=pw"],
            )
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(setup.read_env(path)["OTHER"], "1")


class CollectTest(unittest.TestCase):
    def run_collect(self, keys, current, answers, secrets):
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.object(setup, "qr_text", return_value=URI),
        ):
            return setup.collect(
                keys,
                current,
                qr="qr.png",
                ask=lambda _prompt: answers.pop(0),
                ask_secret=lambda _prompt: secrets.pop(0),
                say=lambda _text: None,
            )

    def test_asks_only_for_missing_values_and_stores_canonical_names(self):
        current = {"TJRS_1G_USERNAME": "already"}
        updates = self.run_collect(["tjrs-1g"], current, [], ["secret-pass"])
        self.assertEqual(
            updates,
            {"TJRS_1G_PASSWORD": "secret-pass", "TJRS_1G_2FA_DATA": URI},
        )

    def test_shared_authenticator_is_stored_for_each_portal(self):
        updates = self.run_collect(["trf4-1g-rs", "trf4-1g-sc"], {}, ["u1", "u2"], ["p1", "p2"])
        self.assertEqual(updates["TRF4_1G_RS_USERNAME"], "u1")
        self.assertEqual(updates["TRF4_1G_SC_PASSWORD"], "p2")
        self.assertEqual(updates["TRF4_1G_RS_2FA_DATA"], updates["TRF4_1G_SC_2FA_DATA"])


class LegacyNamesTest(unittest.TestCase):
    def test_earlier_variable_names_in_the_file_are_not_asked_again(self):
        current = {"TJRS_1G_USUARIO": "u", "TJRS_1G_SENHA": "p", "TJRS_1G_EXPORT_DATA": URI}

        def refuse(_prompt):
            raise AssertionError("não deveria perguntar")

        with mock.patch.dict(os.environ, {}, clear=True):
            updates = setup.collect(["tjrs-1g"], current, qr=None, ask=refuse, ask_secret=refuse, say=lambda _t: None)
        self.assertEqual(updates, {})


class LoginRetryTest(unittest.TestCase):
    def run_verify(self, results):
        args = mock.Mock(no_login=False, register="none", servers="eproc")
        calls = []

        def fake_login(key):
            calls.append(key)
            return results.pop(0)

        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.object(setup, "check_login", side_effect=fake_login),
            mock.patch.object(setup, "wait_for_fresh_code"),
        ):
            failures = setup.verify_and_register(["eproc:tjrs-1g"], args, lambda _t: None)
        return failures, calls

    def test_a_timeout_is_retried_once_but_a_rejection_is_not(self):
        failures, calls = self.run_verify([(False, "RuntimeError: Timed out waiting"), (True, "login ok")])
        self.assertEqual((failures, len(calls)), (0, 2))
        failures, calls = self.run_verify([(False, "RuntimeError: eproc rejected the username/password")])
        self.assertEqual((failures, len(calls)), (1, 1))


class RegistrationTest(unittest.TestCase):
    def test_desktop_registration_merges_and_backs_up(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Claude" / "claude_desktop_config.json"
            path.parent.mkdir()
            path.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}), encoding="utf-8")
            with mock.patch.object(setup, "desktop_config_path", return_value=path):
                self.assertTrue(setup.register_desktop({"eproc": "/bin/jurisync-eproc"}, lambda _t: None))
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["mcpServers"]["other"], {"command": "x"})
            self.assertEqual(data["mcpServers"]["eproc"], {"command": "/bin/jurisync-eproc"})
            self.assertTrue(path.with_name(path.name + ".bak").exists())


class MainTest(unittest.TestCase):
    def test_end_to_end_without_login_or_registration(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            answers, secrets = ["login-user"], ["login-pass"]
            with (
                mock.patch.dict(os.environ, {}, clear=True),
                mock.patch.object(setup, "qr_text", return_value=URI),
            ):
                code = setup.main(
                    [
                        "--portals",
                        "tjrs-1g",
                        "--qr",
                        "qr.png",
                        "--env-file",
                        str(env_file),
                        "--no-login",
                        "--register",
                        "none",
                    ],
                    ask=lambda _p: answers.pop(0),
                    ask_secret=lambda _p: secrets.pop(0),
                    say=lambda _t: None,
                )
            self.assertEqual(code, 0)
            stored = setup.read_env(env_file)
            self.assertEqual(stored["TJRS_1G_USERNAME"], "login-user")
            self.assertEqual(stored["TJRS_1G_2FA_DATA"], URI)
            self.assertEqual(stat.S_IMODE(env_file.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
