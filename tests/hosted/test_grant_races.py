"""One authorization code and one refresh token are redeemed once, even by two requests that read them together."""

import tempfile
import threading
import time
import unittest
from pathlib import Path

from mcp.server.auth.provider import AuthorizationCode, RefreshToken, TokenError

from jurisync.hosted.oauth import Provider
from jurisync.hosted.store import Store


class GrantRaceTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(directory.cleanup)
        self.store = Store(Path(directory.name) / "store.sqlite")
        self.store.add_tenant("office-a", "Escritório A")
        self.provider = Provider(self.store, "https://jurisync.example.test")

    def race(self, method, kind, redeem):
        """Both threads read the grant before either consumes it; returns (issued tokens, refusals)."""
        both_read = threading.Barrier(2, timeout=5)
        read = getattr(self.store, method)

        def reading(*args):
            row = read(*args)
            if method != "token" or args[1] == kind:
                both_read.wait()
            return row

        self.store.__dict__[method] = reading
        issued, refused = [], []

        def run():
            try:
                issued.append(redeem())
            except TokenError as error:
                refused.append(error.error)

        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        del self.store.__dict__[method]
        return issued, refused

    def test_an_authorization_code_is_redeemed_once(self):
        record = AuthorizationCode(
            code="codigo-ficticio",
            scopes=["jurisync"],
            expires_at=time.time() + 300,
            client_id="cliente",
            code_challenge="desafio",
            redirect_uri="https://claude.ai/api/mcp/auth_callback",
            redirect_uri_provided_explicitly=True,
            resource=None,
            subject="user@office-a.test",
        )
        self.store.save_code(record.code, "office-a", record.model_dump(mode="json"), record.expires_at)
        issued, refused = self.race("code", None, lambda: self.provider._exchange_code("cliente", record))
        self.assertEqual(len(issued), 1)
        self.assertEqual(refused, ["invalid_grant"])

    def test_a_refresh_token_rotates_once(self):
        first = self.provider._issue("cliente", "office-a", "user@office-a.test", ["jurisync"], None)
        token = RefreshToken(token=first.refresh_token, client_id="cliente", scopes=["jurisync"])
        issued, refused = self.race("token", "refresh", lambda: self.provider._exchange_refresh("cliente", token, []))
        self.assertEqual(len(issued), 1)
        self.assertEqual(refused, ["invalid_grant"])
        self.assertIsNotNone(self.store.token(issued[0].access_token, "access"))
