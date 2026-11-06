"""Offline checks for routing DJEN requests through the Brazilian bridge."""

import base64
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from jurisync.connectors.djen import bridge

BRIDGE = "https://djen-bridge.example.run.app"
SEARCH = "https://comunicaapi.pje.jus.br/api/v1/comunicacao?numeroOab=42543&ufOab=PE"


def unb64(part):
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class BridgeTest(unittest.TestCase):
    def setUp(self):
        bridge._cache.update(token=None, expires=0.0, audience=None)
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = self.key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ).decode()
        self.directory = tempfile.TemporaryDirectory()
        self.sa = os.path.join(self.directory.name, "sa.json")
        with open(self.sa, "w") as handle:
            json.dump(
                {"client_email": "bridge@p.iam.gserviceaccount.com", "private_key": pem, "private_key_id": "k1"}, handle
            )

    def tearDown(self):
        self.directory.cleanup()

    def test_without_a_bridge_requests_go_straight_to_the_cnj(self):
        with mock.patch.dict(os.environ, {"DJEN_BRIDGE_URL": ""}):
            request = bridge.request(SEARCH, {"Accept": "application/json"})
        self.assertEqual(request.full_url, SEARCH)
        self.assertIsNone(request.get_header("Authorization"))

    def test_with_a_bridge_the_cnj_host_is_replaced_and_both_secrets_are_sent(self):
        env = {"DJEN_BRIDGE_URL": BRIDGE + "/", "DJEN_BRIDGE_TOKEN": "segredo", "DJEN_BRIDGE_SA_FILE": self.sa}
        with mock.patch.dict(os.environ, env), mock.patch.object(bridge, "identity_token", return_value="id-1"):
            request = bridge.request(SEARCH, {"Accept": "application/json"})
            other = bridge.request("https://example.com/x", {})
        self.assertEqual(request.full_url, BRIDGE + "/api/v1/comunicacao?numeroOab=42543&ufOab=PE")
        self.assertEqual(request.get_header("Authorization"), "Bearer id-1")
        self.assertEqual(request.get_header("X-jurisync-token"), "segredo")
        self.assertEqual(other.full_url, "https://example.com/x")  # only the CNJ API goes through the bridge

    def test_identity_token_is_signed_by_the_service_account_and_cached(self):
        sent = []

        def send(request):
            sent.append(request)
            return Response(json.dumps({"id_token": f"id-{len(sent)}"}).encode())

        now = [1_000_000.0]
        with mock.patch.dict(os.environ, {"DJEN_BRIDGE_SA_FILE": self.sa}):
            first = bridge.identity_token(BRIDGE, clock=lambda: now[0], send=send)
            again = bridge.identity_token(BRIDGE, clock=lambda: now[0], send=send)
            now[0] += 3600
            later = bridge.identity_token(BRIDGE, clock=lambda: now[0], send=send)
        self.assertEqual((first, again, later), ("id-1", "id-1", "id-2"))
        assertion = dict(part.split("=", 1) for part in sent[0].data.decode().split("&"))["assertion"]
        header, claims, signature = assertion.split(".")
        self.key.public_key().verify(
            unb64(signature), f"{header}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256()
        )
        decoded = json.loads(unb64(claims))
        self.assertEqual(decoded["target_audience"], BRIDGE)
        self.assertEqual(decoded["iss"], "bridge@p.iam.gserviceaccount.com")

    def test_a_bridge_without_a_service_account_fails_clearly(self):
        with mock.patch.dict(os.environ, {"DJEN_BRIDGE_SA_FILE": ""}), self.assertRaises(RuntimeError):
            bridge.identity_token(BRIDGE)


if __name__ == "__main__":
    unittest.main()
