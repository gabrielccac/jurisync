import base64
import unittest
from unittest.mock import patch

from jurisync.connectors.eproc import client
from jurisync.connectors.eproc.auth import portal_totp_account, session_from_cookies


def field(number, payload):
    return bytes([number << 3 | 2, len(payload)]) + payload


class EprocAuthTests(unittest.TestCase):
    def test_otpauth_uri_preserves_digest_identity_and_period(self):
        uri = "otpauth://totp/JFSC:configured?secret=JBSWY3DPEHPK3PXP&issuer=JFSC&algorithm=SHA256&digits=8&period=45"
        with patch.dict(
            "os.environ",
            {
                "JFSC_EXPORT_DATA": uri,
                "JFSC_OTP_PROFILE_MATCH": "configured",
            },
            clear=True,
        ):
            selected = portal_totp_account("jfsc")
        self.assertEqual(selected["name"], "configured")
        self.assertEqual(selected["issuer"], "JFSC")
        self.assertEqual(selected["algorithm"], "sha256")
        self.assertEqual((selected["digits"], selected["period"]), (8, 45))

    def test_selects_configured_account_from_migration_payload(self):
        first = field(1, b"first-secret") + field(2, b"first") + field(3, b"issuer") + bytes([32, 1, 40, 1, 48, 2])
        second = field(1, b"second-secret") + field(2, b"second") + field(3, b"issuer") + bytes([32, 1, 40, 1, 48, 2])
        encoded = base64.urlsafe_b64encode(field(1, first) + field(1, second)).decode().rstrip("=")
        with patch.dict(
            "os.environ",
            {
                "JRFS_EXPORT_DATA": encoded,
                "JRFS_OTP_PROFILE_INDEX": "1",
            },
            clear=True,
        ):
            selected = portal_totp_account("jfrs")
        self.assertEqual(selected["name"], "second")
        self.assertEqual(selected["secret"], base64.b32encode(b"second-secret").decode())

    def test_portal_credentials_are_isolated(self):
        environment = {
            "JFSC_USUARIO": "jfsc-user",
            "JFSC_SENHA": "jfsc-pass",
            "JRFS_USUARIO": "jrfs-user",
            "JRFS_SENHA": "jrfs-pass",
        }
        with patch.dict("os.environ", environment, clear=True):
            self.assertEqual(client._credentials("jfsc"), ("jfsc-user", "jfsc-pass"))
            # Legacy JRFS_* variables still work for the jfrs portal.
            self.assertEqual(client._credentials("jfrs"), ("jrfs-user", "jrfs-pass"))

        with (
            patch.dict(
                "os.environ",
                {
                    "JFSC_USUARIO": "jfsc-user",
                    "JFSC_SENHA": "jfsc-pass",
                },
                clear=True,
            ),
            self.assertRaisesRegex(RuntimeError, "JFRS_USUARIO"),
        ):
            client._credentials("jfrs")

    def test_transfers_complete_cookie_attributes(self):
        session = session_from_cookies(
            [
                {"name": "PHPSESSID", "value": "opaque", "domain": "eproc.jfsc.jus.br", "path": "/", "secure": True},
                {"name": "aux", "value": "value", "domain": "eproc.jfsc.jus.br", "path": "/eprocV2", "expires": 123},
            ]
        )
        try:
            cookies = {cookie.name: cookie for cookie in session.cookies.jar}
            self.assertEqual(cookies["PHPSESSID"].value, "opaque")
            self.assertTrue(cookies["PHPSESSID"].secure)
            self.assertEqual(
                (cookies["aux"].domain, cookies["aux"].path, cookies["aux"].expires),
                ("eproc.jfsc.jus.br", "/eprocV2", 123),
            )
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main()
