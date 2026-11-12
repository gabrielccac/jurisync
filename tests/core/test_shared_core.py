import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jurisync.core import certificate, credentials, documents
from jurisync.core.http import REDIRECTS, UnsafeUrlError, follow_redirects
from jurisync.core.session_pool import SessionExpired, SessionPool


class DocumentLimitsTest(unittest.TestCase):
    def test_ids_are_one_to_five_unique_and_numeric(self):
        documents.validate_document_ids(["1", "22", "333", "4", "5"])
        for bad in ([], ["1", "1"], [str(n) for n in range(6)], ["x"], [1], "1"):
            with self.subTest(ids=bad), self.assertRaises(ValueError):
                documents.validate_document_ids(bad)

    def test_a_body_past_the_limit_is_dropped(self):
        response = mock.Mock()
        response.iter_content.return_value = [b"abc", b"", b"def"]
        self.assertEqual(documents.read_limited(response, limit=6), b"abcdef")
        self.assertIsNone(documents.read_limited(response, limit=5))


class RedirectTest(unittest.TestCase):
    def reply(self, status, location="", url="https://portal.example/a"):
        return mock.Mock(status_code=status, headers={"Location": location} if location else {}, url=url)

    def test_every_hop_is_checked_before_it_is_requested(self):
        session = mock.Mock()
        session.get.return_value = self.reply(200, url="https://portal.example/b")
        seen = []
        final = follow_redirects(session, self.reply(302, "/b"), check=seen.append)
        self.assertEqual(seen, ["https://portal.example/b"])
        self.assertEqual(final.status_code, 200)

        def refuse(url):
            raise UnsafeUrlError(url)

        with self.assertRaises(UnsafeUrlError):
            follow_redirects(session, self.reply(302, "https://evil.example/x"), check=refuse)
        self.assertEqual(session.get.call_count, 1)

    def test_rewrite_runs_first_and_hops_are_bounded(self):
        session = mock.Mock()
        session.get.return_value = self.reply(302, "/loop")
        seen = []
        follow_redirects(
            session,
            self.reply(302, "http://portal.example/x"),
            check=seen.append,
            hops=3,
            rewrite=lambda url: url.replace("http://", "https://"),
        )
        self.assertEqual(seen[0], "https://portal.example/x")
        self.assertEqual(session.get.call_count, 3)
        self.assertIn(302, REDIRECTS)


class CertificateTest(unittest.TestCase):
    def test_a_missing_or_wrong_file_fails_clearly(self):
        with mock.patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(RuntimeError, "Set PJE_A1_P12"):
            certificate.load_a1(None, "pin")
        with credentials.scope("office", {}, {}), self.assertRaisesRegex(RuntimeError, "not set for this account"):
            certificate.load_a1(None, "pin")
        with tempfile.NamedTemporaryFile() as empty, self.assertRaises(ValueError):
            certificate.load_a1(empty.name, "pin")

    def test_pem_is_private_and_removed_after_the_block(self):
        import datetime

        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
        now = datetime.datetime.now(datetime.UTC)
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(1)
            .not_valid_before(now)
            .not_valid_after(now + datetime.timedelta(days=1))
            .sign(key, hashes.SHA256())
        )
        with certificate.client_certificate(key, cert, []) as (cert_path, key_path):
            self.assertEqual(oct(os.stat(key_path).st_mode & 0o777), "0o600")
            self.assertIn(b"BEGIN CERTIFICATE", Path(cert_path).read_bytes())
            folder = os.path.dirname(cert_path)
            self.assertEqual(oct(os.stat(folder).st_mode & 0o777), "0o700")
        self.assertFalse(os.path.exists(folder))


class SessionRunTest(unittest.TestCase):
    def pool(self):
        self.logins = []
        return SessionPool(
            login=lambda portal: self.logins.append(portal) or object(), refresh=lambda p, s: s, close=lambda s: None
        )

    def test_an_expired_session_logs_in_again_once(self):
        pool, calls = self.pool(), []

        def read(session):
            calls.append(session)
            if len(calls) == 1:
                raise SessionExpired("expired")
            return "ok"

        self.assertEqual(pool.run("portal", read), "ok")
        self.assertEqual(self.logins, ["portal", "portal"])
        self.assertIsNot(calls[0], calls[1])

    def test_a_second_expiry_and_other_errors_are_not_retried(self):
        pool = self.pool()
        always = mock.Mock(side_effect=SessionExpired("expired"))
        with self.assertRaises(SessionExpired):
            pool.run("portal", always)
        self.assertEqual(always.call_count, 2)
        other = mock.Mock(side_effect=ValueError("bad input"))
        with self.assertRaises(ValueError):
            pool.run("portal", other)
        self.assertEqual(other.call_count, 1)


class PathsTest(unittest.TestCase):
    def test_state_locations_keep_their_defaults_and_honor_overrides(self):
        from pathlib import Path

        from jurisync import paths

        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(paths.user_cache("pje"), Path.home() / ".cache" / "jurisync" / "pje")
            self.assertEqual(paths.user_data("filings"), Path.home() / ".local/share/jurisync/filings")
        with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": "/xdg", "PJE_SESSION_DIR": "/mine"}, clear=True):
            self.assertEqual(paths.user_cache("juridiq"), Path("/xdg/jurisync/juridiq"))
            self.assertEqual(paths.user_cache("pje", "PJE_SESSION_DIR"), Path("/mine"))
        with mock.patch.dict(os.environ, {"JURISYNC_FILING_DIR": "  "}, clear=True):
            self.assertEqual(
                paths.user_data("filings", "JURISYNC_FILING_DIR"), Path.home() / ".local/share/jurisync/filings"
            )


if __name__ == "__main__":
    unittest.main()
