import unittest

from jurisync.core.totp import next_code, parse_accounts, parse_index, select_account, totp

SECRET = "JBSWY3DPEHPK3PXP"


class TotpTest(unittest.TestCase):
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
