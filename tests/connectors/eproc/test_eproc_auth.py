import base64
import unittest
from unittest.mock import Mock, patch

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
            "TRF4_1G_SC_USERNAME": "sc-user",
            "TRF4_1G_SC_PASSWORD": "sc-pass",
            "TRF4_1G_RS_USERNAME": "rs-user",
            "TRF4_1G_RS_PASSWORD": "rs-pass",
        }
        with patch.dict("os.environ", environment, clear=True):
            self.assertEqual(client._credentials("jfsc"), ("sc-user", "sc-pass"))
            self.assertEqual(client._credentials("jfrs"), ("rs-user", "rs-pass"))

        # Legacy prefixes (JFSC_*, JFRS_*, JRFS_*) still resolve when the canonical names are absent.
        with patch.dict(
            "os.environ",
            {
                "JFSC_USUARIO": "jfsc-user",
                "JFSC_SENHA": "jfsc-pass",
                "JRFS_USUARIO": "jrfs-user",
                "JRFS_SENHA": "jrfs-pass",
            },
            clear=True,
        ):
            self.assertEqual(client._credentials("jfsc"), ("jfsc-user", "jfsc-pass"))
            self.assertEqual(client._credentials("jfrs"), ("jrfs-user", "jrfs-pass"))

        with (
            patch.dict(
                "os.environ",
                {
                    "TRF4_1G_SC_USERNAME": "sc-user",
                    "TRF4_1G_SC_PASSWORD": "sc-pass",
                },
                clear=True,
            ),
            self.assertRaisesRegex(RuntimeError, "TRF4_1G_RS_USERNAME"),
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


class FillFieldTests(unittest.TestCase):
    def test_field_is_found_again_when_the_page_re_renders(self):
        from selenium.common.exceptions import StaleElementReferenceException

        from jurisync.connectors.eproc import auth

        class Field:
            def __init__(self, stale):
                self.stale, self.typed = stale, []

            def clear(self):
                if self.stale:
                    raise StaleElementReferenceException("stale")

            def send_keys(self, value):
                self.typed.append(value)

        fields = [Field(True), Field(False)]
        driver = Mock()
        driver.wait_for_element.side_effect = lambda *_a, **_k: fields[min(driver.wait_for_element.call_count - 1, 1)]
        with patch.object(auth.time, "sleep"):
            field = auth._fill_field(driver, ("#otp",), "123456")
        self.assertIs(field, fields[1])
        self.assertEqual(fields[1].typed, ["123456"])


if __name__ == "__main__":
    unittest.main()
