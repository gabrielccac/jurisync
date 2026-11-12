"""Offline checks for portal-isolated PJe login reuse."""

import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from curl_cffi import requests

from jurisync.connectors.pje import process as client
from jurisync.connectors.pje.auth import sessions as session_cache


class PjeSessionCacheChecks(unittest.TestCase):
    def test_cache_is_private_and_kept_per_portal(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict(
                os.environ,
                {
                    "XDG_CACHE_HOME": temporary,
                    "PJE_1G_USERNAME": "fixture",
                    "PJE_TRF5_1G_SESSION_ACCOUNT": "fixture-certificate",
                },
                clear=True,
            ),
        ):
            for portal, host in (("trf1-1g", "pje1g.trf1.jus.br"), ("trf5-1g", "pje1g.trf5.jus.br")):
                session = requests.Session()
                session.cookies.set("test-cookie", portal, domain=host, secure=True)
                session_cache.save_session(portal, session)
                path = session_cache.cache_path(portal)
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
                session.close()

            def valid_page(_session, url, **_kwargs):
                return SimpleNamespace(
                    status_code=200, url=url, text='<form id="fPP"><input name="javax.faces.ViewState"></form>'
                )

            with patch.object(requests.Session, "get", valid_page):
                one = session_cache.load_session("trf1-1g")
                five = session_cache.load_session("trf5-1g")
            self.assertEqual(one.cookies.get("test-cookie", domain="pje1g.trf1.jus.br"), "trf1-1g")
            self.assertEqual(five.cookies.get("test-cookie", domain="pje1g.trf5.jus.br"), "trf5-1g")
            one.close()
            five.close()

    def test_account_and_project_changes_cannot_reuse_another_cache(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict(
                os.environ,
                {
                    "XDG_CACHE_HOME": temporary,
                    "PJE_1G_USERNAME": "account-a",
                    "PJE_1G_SESSION_ACCOUNT": "same-project-label",
                },
                clear=True,
            ),
        ):
            session = requests.Session()
            session.cookies.set("fixture", "value", domain="pje1g.trf1.jus.br", secure=True)
            session_cache.save_session("trf1-1g", session)
            first = session_cache.cache_path("trf1-1g")
            with (
                patch.dict(os.environ, {"PJE_1G_USERNAME": "account-b"}),
                patch.object(requests.Session, "get") as network,
            ):
                self.assertNotEqual(first, session_cache.cache_path("trf1-1g"))
                self.assertIsNone(session_cache.load_session("trf1-1g"))
                network.assert_not_called()
            with patch.object(session_cache.Path, "cwd", return_value=session_cache.Path(temporary)):
                self.assertNotEqual(first, session_cache.cache_path("trf1-1g"))
            session.close()

    def test_cache_identity_is_checked_even_when_file_is_copied(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict(
                os.environ,
                {
                    "XDG_CACHE_HOME": temporary,
                    "PJE_1G_USERNAME": "account-a",
                },
                clear=True,
            ),
        ):
            path = session_cache.cache_path("trf1-1g")
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"portal": "trf1-1g", "identity": "another-account", "cookies": [{}]}))
            with patch.object(requests.Session, "get") as network:
                self.assertIsNone(session_cache.load_session("trf1-1g"))
                network.assert_not_called()

    def test_unidentified_certificate_account_uses_fresh_login(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(session_cache.cache_path("trf5-1g"))
            self.assertIsNone(session_cache.load_session("trf5-1g"))
            session_cache.save_session("trf5-1g", Mock())
            expected = Mock()
            with patch("jurisync.connectors.pje.auth.certificate.cert_login", return_value=expected) as login:
                self.assertIs(client.login_portal("trf5-1g")[0], expected)
                login.assert_called_once_with("trf5-1g")

    def test_valid_cache_skips_new_login_for_selected_portal(self):
        cached = Mock()
        with (
            patch.object(client, "load_session", return_value=cached) as load,
            patch.object(client, "login_session") as login,
            patch.object(client, "save_session") as save,
        ):
            session, response = client.login_portal("trf1-1g")
        self.assertIs(session, cached)
        self.assertIsNone(response)
        load.assert_called_once_with("trf1-1g")
        login.assert_not_called()
        save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
