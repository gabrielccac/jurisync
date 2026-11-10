"""Offline checks for the hosted server: credential scopes, vault, OAuth sign-in and per-tenant tool calls."""

import base64
import hashlib
import json
import os
import secrets
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from mcp.server.mcpserver import MCPServer
from starlette.testclient import TestClient

from jurisync import portals
from jurisync.core import credentials
from jurisync.core.session_pool import SessionPool
from jurisync.hosted import cli
from jurisync.hosted.app import build_app
from jurisync.hosted.store import Store
from jurisync.hosted.vault import Vault, generate_key, load_key

BASE = "https://mcp.test"


class CredentialScopeTest(unittest.TestCase):
    def test_local_install_reads_the_environment(self):
        with mock.patch.dict(os.environ, {"PJE_A1_PIN": "local"}):
            self.assertEqual(credentials.env("PJE_A1_PIN"), "local")
            self.assertEqual(credentials.tenant(), "")

    def test_a_scope_never_falls_back_to_the_server_environment(self):
        with mock.patch.dict(os.environ, {"PJE_A1_PIN": "operator", "PJE_A1_P12": "/operator.p12"}):
            with credentials.scope("office-a", {"PJE_TRF5_2FA_DATA": "x"}):
                self.assertEqual(credentials.env("PJE_A1_PIN"), "")
                self.assertFalse(credentials.has("PJE_A1_P12"))
                with self.assertRaises(RuntimeError):
                    credentials.read_file("PJE_A1_P12")
                self.assertEqual(portals.pje("trf5-1g").env("2FA_DATA"), "x")
            with credentials.scope("office-b", files={"PJE_A1_P12": b"cert"}):
                self.assertTrue(credentials.has("PJE_A1_P12"))
                self.assertEqual(credentials.read_file("PJE_A1_P12"), b"cert")

    def test_threads_started_inside_a_scope_keep_it(self):
        seen = []
        with credentials.scope("office-a", {"PJE_A1_PIN": "a"}):
            import anyio

            anyio.run(lambda: anyio.to_thread.run_sync(lambda: seen.append(credentials.env("PJE_A1_PIN"))))
        self.assertEqual(seen, ["a"])


class SessionPoolTenantTest(unittest.TestCase):
    def test_each_tenant_gets_its_own_login(self):
        logins = []
        pool = SessionPool(
            login=lambda portal: logins.append((credentials.tenant(), portal)) or object(),
            refresh=lambda portal, session: session,
            close=lambda session: None,
        )
        with credentials.scope("office-a"), pool.session("trf5-1g") as first:
            pass
        with credentials.scope("office-b"), pool.session("trf5-1g") as second:
            pass
        with credentials.scope("office-a"), pool.session("trf5-1g") as again:
            pass
        self.assertIsNot(first, second)
        self.assertIs(first, again)
        self.assertEqual(logins, [("office-a", "trf5-1g"), ("office-b", "trf5-1g")])


class VaultTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.vault = Vault(self.directory.name, load_key(generate_key()))

    def tearDown(self):
        self.directory.cleanup()

    def test_round_trip_and_no_plain_text_on_disk(self):
        self.vault.put("office-a", {"PJE_A1_PIN": "segredo-123"}, {"PJE_A1_P12": b"\x00cert"})
        self.assertEqual(self.vault.get("office-a"), ({"PJE_A1_PIN": "segredo-123"}, {"PJE_A1_P12": b"\x00cert"}))
        raw = (Path(self.directory.name) / "office-a.vault").read_text()
        self.assertNotIn("segredo-123", raw)
        self.assertEqual(oct((Path(self.directory.name) / "office-a.vault").stat().st_mode & 0o777), "0o600")

    def test_an_entry_does_not_open_under_another_tenant_or_key(self):
        self.vault.put("office-a", {"PJE_A1_PIN": "a"})
        path = Path(self.directory.name)
        record = json.loads((path / "office-a.vault").read_text())
        record["tenant"] = "office-b"
        (path / "office-b.vault").write_text(json.dumps(record))
        with self.assertRaises(RuntimeError):
            self.vault.get("office-b")
        with self.assertRaises(RuntimeError):
            Vault(path, load_key(generate_key())).get("office-a")

    def test_bad_keys_and_tenant_ids_are_refused(self):
        with self.assertRaises(RuntimeError):
            load_key("curta")
        with self.assertRaises(ValueError):
            self.vault.put("../escape", {})


def pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def rpc(client, token, payload, session=None):
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"}
    if session:
        headers["mcp-session-id"] = session
    response = client.post("/fake/mcp", json=payload, headers=headers)
    body = None
    for line in response.text.splitlines():
        if line.startswith("data:"):
            body = json.loads(line[5:])
    if body is None and response.text and response.headers.get("content-type", "").startswith("application/json"):
        body = response.json()
    return response, body


class HostedAppTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.store = Store(root / "store.sqlite")
        self.vault = Vault(root / "vault", load_key(generate_key()))
        for tenant, pin in (("office-a", "pin-a"), ("office-b", "pin-b")):
            self.store.add_tenant(tenant, tenant, connectors=["fake"])
            self.store.add_user(f"user@{tenant}.test", tenant, "senha-bem-longa")
            self.vault.put(tenant, {"PJE_A1_PIN": pin})
        self.store.add_tenant("office-c", "office-c", connectors=["other"])
        self.store.add_user("user@office-c.test", "office-c", "senha-bem-longa")

        fake = MCPServer("fake")

        @fake.tool(name="whoami")
        def whoami() -> dict:
            return {"tenant": credentials.tenant(), "pin": credentials.env("PJE_A1_PIN")}

        @fake.tool(name="pje_draft_write")
        def write() -> str:
            return "written"

        with (
            mock.patch.dict("jurisync.hosted.app.WRITE_TOOLS", {"fake": {"pje_draft_write"}}),
            mock.patch("jurisync.hosted.app.TENANT_READY", ("fake",)),
        ):
            self.app, _ = build_app(BASE, self.store, self.vault, ["fake"], load=lambda name: (fake, None))

    def tearDown(self):
        self.directory.cleanup()

    def sign_in(self, client, email, password="senha-bem-longa"):
        registration = client.post(
            "/register",
            json={
                "redirect_uris": ["https://claude.test/callback"],
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
            },
        ).json()
        verifier, challenge = pkce()
        authorize = client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": registration["client_id"],
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "redirect_uri": "https://claude.test/callback",
                "state": "s1",
                "resource": f"{BASE}/fake/mcp",
            },
            follow_redirects=False,
        )
        login_url = urlsplit(authorize.headers["location"])
        request_id = parse_qs(login_url.query)["request"][0]
        self.assertIn("Entrar no Jurisync", client.get(f"/login?request={request_id}").text)
        login = client.post(
            "/login", data={"request": request_id, "email": email, "password": password}, follow_redirects=False
        )
        if login.status_code != 302:
            return None, login
        query = parse_qs(urlsplit(login.headers["location"]).query)
        self.assertEqual(query["state"], ["s1"])
        token = client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": query["code"][0],
                "client_id": registration["client_id"],
                "code_verifier": verifier,
                "redirect_uri": "https://claude.test/callback",
                "resource": f"{BASE}/fake/mcp",
            },
        )
        return token.json(), token

    def call_whoami(self, client, access):
        initialize = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        }
        response, _ = rpc(client, access, initialize)
        self.assertEqual(response.status_code, 200, response.text)
        session = response.headers.get("mcp-session-id")
        rpc(client, access, {"jsonrpc": "2.0", "method": "notifications/initialized"}, session)
        _, listed = rpc(client, access, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, session)
        names = [tool["name"] for tool in listed["result"]["tools"]]
        _, called = rpc(
            client,
            access,
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "whoami", "arguments": {}}},
            session,
        )
        result = called["result"]
        return names, result.get("structuredContent") or json.loads(result["content"][0]["text"])

    def test_unauthenticated_calls_get_the_resource_metadata_challenge(self):
        with TestClient(self.app, base_url=BASE) as client:
            response = client.post("/fake/mcp", json={})
            self.assertEqual(response.status_code, 401)
            self.assertIn("oauth-protected-resource/fake/mcp", response.headers["www-authenticate"])
            metadata = client.get("/.well-known/oauth-protected-resource/fake/mcp").json()
            self.assertEqual(metadata["authorization_servers"], [f"{BASE}"])
            self.assertTrue(client.get("/.well-known/oauth-authorization-server").json()["registration_endpoint"])

    def test_each_office_calls_tools_with_its_own_credentials(self):
        with TestClient(self.app, base_url=BASE) as client:
            token_a, _ = self.sign_in(client, "user@office-a.test")
            token_b, _ = self.sign_in(client, "USER@office-b.test")
            names, result_a = self.call_whoami(client, token_a["access_token"])
            _, result_b = self.call_whoami(client, token_b["access_token"])
        self.assertEqual(result_a, {"tenant": "office-a", "pin": "pin-a"})
        self.assertEqual(result_b, {"tenant": "office-b", "pin": "pin-b"})
        self.assertNotIn("pje_draft_write", names)

    def test_wrong_password_and_disabled_connector_are_refused(self):
        with TestClient(self.app, base_url=BASE) as client:
            token, response = self.sign_in(client, "user@office-a.test", "errada-errada")
            self.assertIsNone(token)
            self.assertIn("incorretos", response.text)
            token_c, _ = self.sign_in(client, "user@office-c.test")
            response, _ = rpc(client, token_c["access_token"], {"jsonrpc": "2.0", "id": 1, "method": "ping"})
            self.assertEqual(response.status_code, 403)

    def test_refresh_rotates_and_revoked_tokens_stop_working(self):
        with TestClient(self.app, base_url=BASE) as client:
            token, _ = self.sign_in(client, "user@office-a.test")
            client_id = self.store._run("select client_id from clients")[0]["client_id"]
            refreshed = client.post(
                "/token",
                data={"grant_type": "refresh_token", "client_id": client_id, "refresh_token": token["refresh_token"]},
            ).json()
            self.assertNotEqual(refreshed["access_token"], token["access_token"])
            old, _ = rpc(client, token["access_token"], {"jsonrpc": "2.0", "id": 1, "method": "ping"})
            self.assertEqual(old.status_code, 401)
            self.store.remove_user("user@office-a.test")
            gone, _ = rpc(client, refreshed["access_token"], {"jsonrpc": "2.0", "id": 1, "method": "ping"})
            self.assertEqual(gone.status_code, 401)


class RealServerTest(unittest.TestCase):
    def test_the_pje_server_mounts_without_its_write_tools(self):
        from jurisync.install.serve import load

        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "s.sqlite")
            vault = Vault(Path(directory) / "v", load_key(generate_key()))
            server, _ = load("pje")
            build_app(BASE, store, vault, ["pje"])
            build_app(BASE, store, vault, ["pje"])  # a second app in the same process must not fail
            after = {tool.name for tool in server._tool_manager.list_tools()}
            self.assertFalse({"pje_draft_write", "pje_draft_delete"} & after)
            self.assertIn("pje_get_process", after)


class ImportTest(unittest.TestCase):
    def test_env_import_keeps_portal_secrets_and_reads_the_certificate_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            p12 = Path(directory) / "a1.p12"
            p12.write_bytes(b"cert-bytes")
            values = {
                "PJE_A1_P12": str(p12),
                "PJE_A1_PIN": "1234",
                "PJE_TRF5_2FA_DATA": "otp",
                "ADVBOX_PASSWORD": "x",
                "PJE_1G_CERT": "true",
            }
            wanted = cli.wanted_names(["trf5-1g"], "victor")
            out_values, out_files = cli.normalize(values, {}, wanted)
        self.assertEqual(out_files, {"PJE_A1_P12": b"cert-bytes"})
        self.assertEqual(out_values, {"PJE_A1_PIN": "1234", "PJE_TRF5_2FA_DATA": "otp", "PJE_1G_CERT": "true"})

    def test_qr_images_become_totp_text(self):
        with mock.patch.object(cli, "qr_text", return_value="otpauth://totp/x?secret=ABC") as decode:
            out_values, _ = cli.normalize({"PJE_TRF5_2FA_QR": "qr.png"}, {"PJE_TRF5_2FA_QR": b"png"})
        decode.assert_called_once_with(b"png")
        self.assertEqual(out_values, {"PJE_TRF5_2FA_DATA": "otpauth://totp/x?secret=ABC"})


class TenantFoldersTest(unittest.TestCase):
    def test_saved_documents_and_filing_runs_are_per_office(self):
        from jurisync.core.documents_dir import documents_directory
        from jurisync.core.runs import run_directory

        with tempfile.TemporaryDirectory() as directory:
            env = {"JURISYNC_DOCUMENTS_DIR": directory, "JURISYNC_FILING_DIR": directory}
            with mock.patch.dict(os.environ, env):
                self.assertEqual(documents_directory(), Path(directory))
                with credentials.scope("office-a"):
                    self.assertEqual(documents_directory(), Path(directory) / "tenants" / "office-a")
                    self.assertEqual(run_directory("r1"), Path(directory).resolve() / "tenants" / "office-a" / "r1")


def make_p12_bytes(common_name, pin):
    """A self-signed A1 (.p12) in memory, so login tests never touch a real certificate."""
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import BestAvailableEncryption, pkcs12
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    data = pkcs12.serialize_key_and_certificates(b"a1", key, cert, None, BestAvailableEncryption(pin.encode()))
    return data, pin


class CertificateLoginTest(unittest.TestCase):
    """The A1 login reads each office's certificate and PIN from its own scope, never the server's file."""

    def test_each_office_signs_with_its_own_a1(self):
        from cryptography.x509.oid import NameOID

        from jurisync.connectors.pje.auth import certificate
        from jurisync.connectors.pje.auth.sessions import auth_method

        offices = {
            "office-a": make_p12_bytes("Escritorio A", "pin-a"),
            "office-b": make_p12_bytes("Escritorio B", "pin-b"),
        }
        signed = {}

        def fake_login(session, config, account, signer):
            signed[credentials.tenant()] = signer.cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
            return session

        server_env = {"PJE_A1_P12": "/nao/existe/servidor.p12", "PJE_A1_PIN": "pin-do-servidor"}
        with mock.patch.dict(os.environ, server_env), mock.patch.object(certificate, "_login", fake_login):
            for tenant, (data, pin) in offices.items():
                values = {"PJE_A1_PIN": pin, "PJE_TRF5_2FA_DATA": "otpauth://totp/x?secret=JBSWY3DPEHPK3PXP"}
                with credentials.scope(tenant, values, {"PJE_A1_P12": data}):
                    self.assertEqual(auth_method("trf5-1g"), "certificate_totp")
                    certificate.cert_login("trf5-1g", session=object())
            with credentials.scope("office-c", {"PJE_A1_PIN": "x"}), self.assertRaises(RuntimeError):
                certificate.cert_login("trf5-1g", session=object())
            data, _ = offices["office-a"]
            with credentials.scope("office-a", {"PJE_A1_PIN": "pin-b"}, {"PJE_A1_P12": data}):
                with self.assertRaises(ValueError):
                    certificate.cert_login("trf5-1g", session=object())
        self.assertEqual(signed, {"office-a": "Escritorio A", "office-b": "Escritorio B"})


class OpenTenantTest(unittest.TestCase):
    def test_open_mode_serves_one_tenant_without_a_token(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "s.sqlite")
            vault = Vault(Path(directory) / "v", load_key(generate_key()))
            store.add_tenant("office-a", "A", connectors=["fake"])
            vault.put("office-a", {"PJE_A1_PIN": "pin-a"})
            fake = MCPServer("fake")

            @fake.tool(name="whoami")
            def whoami() -> dict:
                return {"tenant": credentials.tenant(), "pin": credentials.env("PJE_A1_PIN")}

            load = lambda name: (fake, None)  # noqa: E731
            with mock.patch("jurisync.hosted.app.TENANT_READY", ("fake",)):
                with self.assertRaises(ValueError):
                    build_app(BASE, store, vault, ["fake"], load=load, open_tenant="nope")
                with self.assertRaises(ValueError):  # no development flag
                    build_app(BASE, store, vault, ["fake"], load=load, open_tenant="office-a")
                with mock.patch.dict(os.environ, {"JURISYNC_HOSTED_DEV": "1"}):
                    app, _ = build_app(BASE, store, vault, ["fake"], load=load, open_tenant="office-a")
            with TestClient(app, base_url=BASE) as client:
                names, result = HostedAppTest.call_whoami(self, client, "sem-token")
        self.assertEqual(result, {"tenant": "office-a", "pin": "pin-a"})


if __name__ == "__main__":
    unittest.main()
