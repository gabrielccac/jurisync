import base64
import unittest
from unittest import mock
from urllib.parse import quote

from jurisync.core.totp import (
    match_for_tribunal,
    next_code,
    parse_accounts,
    parse_index,
    portal_account,
    select_account,
    totp,
)

SECRET = "JBSWY3DPEHPK3PXP"


class TotpTest(unittest.TestCase):
    def test_migration_preserves_escaped_base64_plus(self):
        secret = b"\xfa" * 20
        account = b"\x0a" + bytes([len(secret)]) + secret
        data = base64.b64encode(b"\x0a" + bytes([len(account)]) + account).decode()
        self.assertIn("+", data)
        query = "data=" + quote(data, safe="")
        uri = "otpauth-migration://offline?" + query
        for value in (uri, quote(uri, safe=""), query):
            with self.subTest(value=value):
                self.assertEqual(parse_accounts(value)[0]["secret"], base64.b32encode(secret).decode())

    def test_parses_raw_secret_and_otpauth_uri(self):
        self.assertEqual(parse_accounts(SECRET.lower())[0]["secret"], SECRET)
        account = parse_accounts(
            f"otpauth://totp/JFSC:user?secret={SECRET}&issuer=JFSC&algorithm=SHA256&digits=8&period=45"
        )[0]
        self.assertEqual(
            (account["issuer"], account["algorithm"], account["digits"], account["period"]), ("JFSC", "sha256", 8, 45)
        )
        self.assertEqual(len(totp(account).now()), 8)

    def test_selection_by_match_index_or_single(self):
        accounts = [
            {"name": "first", "issuer": "PDPJ", "secret": SECRET, "type": "totp"},
            {"name": "second", "issuer": "PDPJ", "secret": SECRET, "type": "totp"},
        ]
        self.assertEqual(select_account(accounts, match="SECOND")["name"], "second")
        self.assertEqual(select_account(accounts, index=0)["name"], "first")
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            select_account(accounts, match="pdpj")
        with self.assertRaisesRegex(ValueError, "match or zero-based index"):
            select_account(accounts)
        with self.assertRaisesRegex(ValueError, "outside"):
            select_account(accounts, index=2)
        with self.assertRaisesRegex(ValueError, "not TOTP"):
            select_account([{**accounts[0], "type": "hotp"}])

    def test_match_for_tribunal_maps_the_issuer_once(self):
        accounts = [
            {"name": "lawyer", "issuer": f"Eproc/{court}", "secret": SECRET, "type": "totp"}
            for court in ("TRF4", "TRF6", "TRF2", "TRF1", "TRF10")
        ]
        self.assertEqual(match_for_tribunal(accounts, "TRF4"), "Eproc/TRF4")
        self.assertEqual(match_for_tribunal(accounts, "trf1"), "Eproc/TRF1")  # not TRF10
        self.assertEqual(match_for_tribunal(accounts, "TRF10"), "Eproc/TRF10")
        self.assertIsNone(match_for_tribunal(accounts, "TJRS"))
        self.assertIsNone(match_for_tribunal(accounts, ""))
        self.assertIsNone(match_for_tribunal(accounts + [dict(accounts[0])], "TRF4"))
        # The stored value selects the same account at login.
        self.assertEqual(select_account(accounts, match="Eproc/TRF1")["issuer"], "Eproc/TRF1")
        by_name = [
            {"name": f"user {court}", "issuer": "", "secret": SECRET, "type": "totp"} for court in ("TJRS", "TJSC")
        ]
        self.assertEqual(match_for_tribunal(by_name, "TJSC"), "user TJSC")

    def test_login_never_guesses_by_tribunal(self):
        accounts = [
            {"name": "lawyer", "issuer": f"Eproc/{c}", "secret": SECRET, "type": "totp"} for c in ("TRF4", "TRF6")
        ]
        portal = mock.Mock(tribunal="TRF4", env_prefix="TRF4_1G_RS")
        portal.env.return_value = ""
        portal.env_name.side_effect = lambda name: f"TRF4_1G_RS_{name}"
        with self.assertRaisesRegex(ValueError, "match or zero-based index"):
            portal_account(portal, accounts)
        portal.env.side_effect = lambda name: "Eproc/TRF6" if name == "OTP_PROFILE_MATCH" else ""
        self.assertEqual(portal_account(portal, accounts)["issuer"], "Eproc/TRF6")

    def test_parse_index(self):
        self.assertIsNone(parse_index("", "X"))
        self.assertEqual(parse_index("1", "X"), 1)
        with self.assertRaisesRegex(ValueError, "X must be an integer"):
            parse_index("one", "X")


class NextCodeTest(unittest.TestCase):
    def test_waits_until_just_after_the_window_boundary(self):
        waits = []
        account = parse_accounts(SECRET)[0]
        code = next_code(account, sleep=waits.append, now=1_000_000_000)  # 10 s into a 30 s window
        self.assertEqual(waits, [21])
        self.assertEqual(len(code), 6)


if __name__ == "__main__":
    unittest.main()
