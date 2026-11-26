"""Offline checks for the hosted sign-in: browser binding, what the page shows, throttling, audience and OAuth basics."""

import asyncio
import base64
import hashlib
import os
import secrets
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from mcp.server.mcpserver import MCPServer
from starlette.testclient import TestClient

from jurisync.core import credentials
from jurisync.hosted.app import REQUIRE_RESOURCE_FLAG, build_app
from jurisync.hosted.oauth import REDIRECT_HOSTS_VAR, login_cookie, redirect_hosts, redirect_target
from jurisync.hosted.store import Store
from jurisync.hosted.throttle import Throttle, client_address
from jurisync.hosted.vault import Vault, generate_key, load_key

BASE = "https://mcp.test"
CALLBACK = "https://claude.ai/api/mcp/auth_callback"
PASSWORD = "senha-bem-longa"
PING = {  # opens an MCP session: 200 when the request gets through the gate and the transport checks
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
}


def pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def ping(client, token, path="/fake/mcp", **headers):
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream", **headers}
    return client.post(path, json=PING, headers=headers)


class SignInCase(unittest.TestCase):
    """Two offices: office-a has the ``fake`` connector, office-b has ``other``."""

    patches = {}
    environment = {}

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.store = Store(root / "store.sqlite")
        self.addCleanup(self.store.close)
        self.vault = Vault(root / "vault", load_key(generate_key()))
        for tenant, connector in (("office-a", "fake"), ("office-b", "other")):
            self.store.add_tenant(tenant, tenant, connectors=[connector])
            self.store.add_user(f"user@{tenant}.test", tenant, PASSWORD)
            self.vault.put(tenant, {"PJE_A1_PIN": tenant})

        def server(name):
            made = MCPServer(name)

            @made.tool(name=f"{name}_whoami")
            def whoami() -> dict:
                return {"tenant": credentials.tenant()}

            return made

        servers = {"fake": server("fake"), "other": server("other")}
        with (
            mock.patch.dict(os.environ, {REQUIRE_RESOURCE_FLAG: "", **self.environment}),
            mock.patch.multiple("jurisync.hosted.app", TENANT_READY=("fake", "other"), **self.patches),
        ):
            self.app, _ = build_app(
                BASE, self.store, self.vault, list(servers), load=lambda name: (servers[name], None)
            )

    def register(self, client, redirect_uri=CALLBACK, name="Claude"):
        body = {
            "redirect_uris": [redirect_uri],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        }
        if name is not None:
            body["client_name"] = name
        response = client.post("/register", json=body)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["client_id"]

    def authorize(self, client, client_id, challenge, redirect_uri=CALLBACK, resource=f"{BASE}/fake/mcp"):
        params = {
            "response_type": "code",
            "client_id": client_id,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "redirect_uri": redirect_uri,
            "state": "s1",
        }
        if resource:
            params["resource"] = resource
        response = client.get("/authorize", params=params, follow_redirects=False)
        self.assertEqual(response.status_code, 302, response.text)
        location = urlsplit(response.headers["location"])
        self.assertEqual(location.path, "/login")
        return parse_qs(location.query)["request"][0], response

    def submit(self, client, request_id, email="user@office-a.test", password=PASSWORD):
        return client.post(
            "/login", data={"request": request_id, "email": email, "password": password}, follow_redirects=False
        )

    def code(self, client, client_id, challenge, email="user@office-a.test", **options):
        request_id, _ = self.authorize(client, client_id, challenge, **options)
        login = self.submit(client, request_id, email)
        self.assertEqual(login.status_code, 302, login.text)
        return parse_qs(urlsplit(login.headers["location"]).query)

    def exchange(self, client, client_id, code, verifier, redirect_uri=CALLBACK):
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
        }
        return client.post("/token", data=data)

    def token(self, client, email="user@office-a.test", **options):
        client_id = self.register(client)
        verifier, challenge = pkce()
        query = self.code(client, client_id, challenge, email, **options)
        response = self.exchange(client, client_id, query["code"][0], verifier)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["access_token"]


class BrowserBindingTest(SignInCase):
    def test_authorize_sets_a_cookie_only_for_the_sign_in_page(self):
        client = TestClient(self.app, base_url=BASE)
        request_id, response = self.authorize(client, self.register(client), pkce()[1])
        cookie = response.headers["set-cookie"]
        self.assertTrue(cookie.startswith(login_cookie(request_id) + "="))
        for attribute in ("Path=/login", "HttpOnly", "SameSite=Lax", "Secure", "Max-Age=600"):
            self.assertIn(attribute, cookie)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_a_forwarded_login_link_is_dead_in_another_browser(self):
        attacker, victim = TestClient(self.app, base_url=BASE), TestClient(self.app, base_url=BASE)
        request_id, _ = self.authorize(attacker, self.register(attacker), pkce()[1])
        shown = victim.get(f"/login?request={request_id}")
        self.assertEqual(shown.status_code, 400)
        self.assertNotIn("<form", shown.text)
        refused = self.submit(victim, request_id)
        self.assertEqual(refused.status_code, 400)
        self.assertNotIn("location", refused.headers)
        self.assertEqual(self.store._run("select count(*) as n from codes")[0]["n"], 0)
        # A guessed or wrong cookie value does not open it either.
        victim.cookies.set(login_cookie(request_id), "x" * 43, domain="mcp.test", path="/login")
        self.assertEqual(victim.get(f"/login?request={request_id}").status_code, 400)
        # The browser that started it still can.
        self.assertEqual(attacker.get(f"/login?request={request_id}").status_code, 200)

    def test_two_sign_ins_side_by_side_in_one_browser_both_work(self):
        client = TestClient(self.app, base_url=BASE)
        client_id = self.register(client)
        first, _ = self.authorize(client, client_id, pkce()[1])
        second, _ = self.authorize(client, client_id, pkce()[1])
        self.assertEqual(self.submit(client, first).status_code, 302)
        self.assertEqual(self.submit(client, second).status_code, 302)

    def test_a_request_is_used_once_and_expires(self):
        client = TestClient(self.app, base_url=BASE)
        client_id = self.register(client)
        request_id, _ = self.authorize(client, client_id, pkce()[1])
        done = self.submit(client, request_id)
        self.assertEqual(done.status_code, 302)
        self.assertIn("Max-Age=0", done.headers["set-cookie"])
        self.assertEqual(self.submit(client, request_id).status_code, 400)
        with mock.patch("jurisync.hosted.oauth.PENDING_TTL", -1):
            expired, _ = self.authorize(client, client_id, pkce()[1])
        self.assertEqual(client.get(f"/login?request={expired}").status_code, 400)
        self.assertEqual(self.submit(client, expired).status_code, 400)

    def test_pending_requests_are_capped_dropping_the_oldest(self):
        client = TestClient(self.app, base_url=BASE)
        client_id = self.register(client)
        with mock.patch("jurisync.hosted.oauth.MAX_PENDING", 3):
            requests = [self.authorize(client, client_id, pkce()[1])[0] for _ in range(4)]
        self.assertEqual(client.get(f"/login?request={requests[0]}").status_code, 400)
        for request_id in requests[1:]:
            self.assertEqual(client.get(f"/login?request={request_id}").status_code, 200)


class SignInPageTest(SignInCase):
    def page(self, redirect_uri, name):
        client = TestClient(self.app, base_url=BASE)
        client_id = self.register(client, redirect_uri, name)
        request_id, _ = self.authorize(client, client_id, pkce()[1], redirect_uri=redirect_uri)
        response = client.get(f"/login?request={request_id}")
        self.assertEqual(response.status_code, 200)
        return response.text

    def test_known_host_shows_client_and_host_without_warning(self):
        text = self.page(CALLBACK, "Claude")
        self.assertIn("“Claude”", text)
        self.assertIn("<strong>claude.ai</strong>", text)
        self.assertNotIn("Atenção", text)

    def test_other_hosts_are_refused_at_registration(self):
        client = TestClient(self.app, base_url=BASE)
        for uri in ("https://claude.ai.evil.test/cb", "http://claude.ai/cb", "cursor://anysphere/cb"):
            body = {"redirect_uris": [uri], "token_endpoint_auth_method": "none", "client_name": "Claude"}
            response = client.post("/register", json=body)
            self.assertEqual(response.status_code, 400, uri)
            self.assertEqual(response.json()["error"], "invalid_redirect_uri")
        mixed = {"redirect_uris": [CALLBACK, "https://evil.test/cb"], "token_endpoint_auth_method": "none"}
        self.assertEqual(client.post("/register", json=mixed).status_code, 400)
        self.assertEqual(self.store._run("select count(*) as total from clients")[0]["total"], 0)

    def test_a_client_stored_before_the_rule_is_unknown(self):
        client = TestClient(self.app, base_url=BASE)
        client_id = self.register(client)
        info = self.store.client(client_id).replace("claude.ai", "evil.test")
        self.store.save_client(client_id, info)
        params = {
            "response_type": "code",
            "client_id": client_id,
            "code_challenge": pkce()[1],
            "code_challenge_method": "S256",
            "redirect_uri": "https://evil.test/api/mcp/auth_callback",
            "state": "s1",
        }
        response = client.get("/authorize", params=params, follow_redirects=False)
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("evil.test", response.headers.get("location", ""))

    def test_redirect_hosts_parse_the_operator_list(self):
        self.assertEqual(
            redirect_hosts(" App.Example.com, other.test  third.test,"), {"app.example.com", "other.test", "third.test"}
        )
        self.assertEqual(redirect_hosts(None), frozenset())

    def test_the_client_name_is_escaped_and_may_be_missing(self):
        text = self.page(CALLBACK, '<script>alert(1)</script>"><b>')
        self.assertNotIn("<script>", text)
        self.assertIn("&lt;script&gt;", text)
        self.assertIn("Um aplicativo sem nome", self.page(CALLBACK, None))

    def test_which_redirect_targets_count_as_known(self):
        known = ["https://claude.ai/cb", "https://claude.com/cb", "https://chatgpt.com/x", "https://chat.openai.com/x"]
        known += ["http://localhost:3334/cb", "http://127.0.0.1/cb", "http://[::1]:8080/cb"]
        for uri in known:
            self.assertTrue(redirect_target(uri)[1], uri)
        for uri in (
            "http://claude.ai/cb",
            "https://evil.test/claude.ai",
            "https://claude.ai.evil.test/",
            "cursor://x/cb",
        ):
            self.assertFalse(redirect_target(uri)[1], uri)
        self.assertEqual(redirect_target("cursor://anysphere/cb")[0], "cursor://anysphere")


class AllowedRedirectHostTest(SignInCase):
    environment = {REDIRECT_HOSTS_VAR: "app.example.test"}

    def test_an_allowed_host_signs_in_with_the_warning(self):
        callback = "https://app.example.test/cb"
        client = TestClient(self.app, base_url=BASE)
        client_id = self.register(client, callback, "App")
        verifier, challenge = pkce()
        request_id, _ = self.authorize(client, client_id, challenge, redirect_uri=callback)
        text = client.get(f"/login?request={request_id}").text
        self.assertIn("<strong>app.example.test</strong>", text)
        self.assertIn("Atenção", text)
        login = self.submit(client, request_id)
        self.assertTrue(login.headers["location"].startswith(callback))
        code = parse_qs(urlsplit(login.headers["location"]).query)["code"][0]
        self.assertEqual(self.exchange(client, client_id, code, verifier, callback).status_code, 200)

    def test_only_https_and_only_the_named_host(self):
        client = TestClient(self.app, base_url=BASE)
        for uri in ("http://app.example.test/cb", "https://other.example.test/cb"):
            body = {"redirect_uris": [uri], "token_endpoint_auth_method": "none"}
            self.assertEqual(client.post("/register", json=body).status_code, 400, uri)


class AvailabilityTest(SignInCase):
    patches = {"IP_MAX_FAILURES": 2}

    def test_the_password_check_runs_off_the_event_loop(self):
        seen = []

        def credential(email, password):
            try:
                asyncio.get_running_loop()
                seen.append("loop")
            except RuntimeError:
                seen.append("thread")
            return "office-a", "hash"

        client = TestClient(self.app, base_url=BASE)
        request_id, _ = self.authorize(client, self.register(client), pkce()[1])
        with mock.patch.object(self.store, "credential", credential):
            self.assertEqual(self.submit(client, request_id).status_code, 302)
        self.assertEqual(seen, ["thread"])

    def test_one_address_is_throttled_across_different_emails(self):
        client = TestClient(self.app, base_url=BASE)
        request_id, _ = self.authorize(client, self.register(client), pkce()[1])
        for attempt in range(2):
            self.assertEqual(
                self.submit(client, request_id, f"guess{attempt}@x.test", "errada-errada").status_code, 200
            )
        blocked = self.submit(client, request_id)  # right password, same address
        self.assertEqual(blocked.status_code, 429)
        self.assertIn("Muitas tentativas", blocked.text)

    def test_client_address_trusts_forwarded_for_only_from_this_host(self):
        self.assertEqual(client_address("127.0.0.1", "203.0.113.9, 10.0.0.1"), "203.0.113.9")
        self.assertEqual(client_address("::1", "203.0.113.9"), "203.0.113.9")
        self.assertEqual(client_address("198.51.100.7", "203.0.113.9"), "198.51.100.7")
        self.assertEqual(client_address("127.0.0.1", ""), "127.0.0.1")
        self.assertEqual(client_address("127.0.0.1", "not-an-address"), "127.0.0.1")
        self.assertEqual(client_address("2001:db8:1:2:aaaa::1"), client_address("2001:db8:1:2:bbbb::2"))
        self.assertEqual(client_address("::ffff:198.51.100.7"), "198.51.100.7")
        self.assertEqual(client_address("testclient"), "testclient")

    def test_throttle_memory_is_bounded(self):
        now = [0.0]
        throttle = Throttle(max_failures=2, window=60, clock=lambda: now[0], max_keys=3)
        for key in range(50):
            self.assertFalse(throttle.blocked(f"never-failed-{key}"))
        self.assertEqual(len(throttle), 0)
        for key in "abcd":
            now[0] += 1
            throttle.fail(key)
        self.assertEqual(len(throttle), 3)
        throttle.fail("d")
        self.assertTrue(throttle.blocked("d"))
        for _ in range(100):
            throttle.fail("d")
        self.assertEqual(len(throttle._failures["d"]), 2)
        now[0] += 61
        throttle.fail("e")
        self.assertEqual(len(throttle), 1)

    def test_expired_tokens_and_codes_are_purged_on_start_and_hourly(self):
        from jurisync.hosted.oauth import PURGE_EVERY, Provider

        def stale():
            past = int(time.time()) - 10
            self.store.save_code(secrets.token_hex(8), "office-a", {}, past)
            self.store.save_token(secrets.token_hex(8), kind="access", client_id="c", tenant="office-a", subject="s",
                                  scopes=["jurisync"], resource=None, expires=past, pair="p")  # fmt: skip

        def rows():
            count = "select (select count(*) from codes) + (select count(*) from tokens) as n"
            return self.store._run(count)[0]["n"]

        stale()
        provider = Provider(self.store, BASE)
        self.assertEqual(rows(), 0)
        stale()
        provider.housekeeping()
        self.assertEqual(rows(), 2)
        provider._purged -= PURGE_EVERY
        provider.housekeeping()
        self.assertEqual(rows(), 0)

    def test_registered_clients_are_capped_keeping_those_with_live_tokens(self):
        client = TestClient(self.app, base_url=BASE)
        with mock.patch("jurisync.hosted.oauth.MAX_CLIENTS", 2):
            verifier, challenge = pkce()
            in_use = self.register(client)
            query = self.code(client, in_use, challenge)
            self.assertEqual(self.exchange(client, in_use, query["code"][0], verifier).status_code, 200)
            idle = [self.register(client) for _ in range(3)]
        left = {row["client_id"] for row in self.store._run("select client_id from clients")}
        self.assertEqual(left, {in_use, idle[2]})

    def test_oversized_client_metadata_is_refused(self):
        client = TestClient(self.app, base_url=BASE)
        body = {"redirect_uris": [CALLBACK], "client_name": "x" * 20_000, "token_endpoint_auth_method": "none"}
        response = client.post("/register", json=body)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.store._run("select count(*) as n from clients")[0]["n"], 0)


class IssuerTest(SignInCase):
    def test_the_redirect_carries_the_issuer_the_metadata_announces(self):
        client = TestClient(self.app, base_url=BASE)
        metadata = client.get("/.well-known/oauth-authorization-server").json()
        self.assertIs(metadata["authorization_response_iss_parameter_supported"], True)
        self.assertEqual(metadata["issuer"], BASE)
        self.assertEqual(metadata["authorization_endpoint"], f"{BASE}/authorize")
        self.assertTrue(metadata["registration_endpoint"] and metadata["revocation_endpoint"])
        query = self.code(client, self.register(client), pkce()[1])
        self.assertEqual(query["iss"], [metadata["issuer"]])
        self.assertEqual(query["state"], ["s1"])


class HostAndOriginTest(SignInCase):
    def test_requests_without_origin_and_from_the_public_or_local_host_pass(self):
        with TestClient(self.app, base_url=BASE) as client:
            token = self.token(client)
            self.assertEqual(ping(client, token).status_code, 200)  # no Origin: how Claude's servers call
            self.assertEqual(ping(client, token, Origin=BASE).status_code, 200)
            self.assertEqual(ping(client, token, Host="127.0.0.1:8000").status_code, 200)
            self.assertEqual(ping(client, token, Host="localhost").status_code, 200)

    def test_other_hosts_and_origins_are_refused(self):
        with TestClient(self.app, base_url=BASE) as client:
            token = self.token(client)
            self.assertEqual(ping(client, token, Host="evil.test").status_code, 421)
            self.assertEqual(ping(client, token, Host="mcp.test.evil.test").status_code, 421)
            self.assertEqual(ping(client, token, Origin="https://evil.test").status_code, 403)
            self.assertEqual(ping(client, token, Origin="https://claude.ai").status_code, 403)


class AudienceTest(SignInCase):
    def test_a_token_without_resource_is_accepted_and_logged_by_default(self):
        with TestClient(self.app, base_url=BASE) as client:
            token = self.token(client, resource=None)
            with self.assertLogs("jurisync.hosted.app", level="INFO") as logs:
                self.assertEqual(ping(client, token).status_code, 200)
            self.assertEqual(len(logs.output), 1)
            self.assertIn("token without resource accepted on fake", logs.output[0])
            self.assertNotIn(token, logs.output[0])
            with self.assertNoLogs("jurisync.hosted.app", level="INFO"):  # once per client and office
                self.assertEqual(ping(client, token).status_code, 200)
            with self.assertNoLogs("jurisync.hosted.app", level="INFO"):
                self.assertEqual(ping(client, self.token(client)).status_code, 200)


class StrictAudienceTest(SignInCase):
    environment = {REQUIRE_RESOURCE_FLAG: "1"}

    def test_the_switch_refuses_missing_and_mismatched_resources(self):
        with TestClient(self.app, base_url=BASE) as client:
            self.assertEqual(ping(client, self.token(client, resource=None)).status_code, 401)
            self.assertEqual(ping(client, self.token(client, resource=f"{BASE}/other/mcp")).status_code, 401)
            self.assertEqual(ping(client, self.token(client)).status_code, 200)
            self.assertEqual(ping(client, self.token(client, resource=f"{BASE}/fake/mcp/")).status_code, 200)


class OAuthBasicsTest(SignInCase):
    def test_a_wrong_pkce_verifier_is_refused(self):
        client = TestClient(self.app, base_url=BASE)
        client_id = self.register(client)
        query = self.code(client, client_id, pkce()[1])
        response = self.exchange(client, client_id, query["code"][0], pkce()[0])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_grant")

    def test_an_authorization_code_works_once(self):
        client = TestClient(self.app, base_url=BASE)
        client_id = self.register(client)
        verifier, challenge = pkce()
        code = self.code(client, client_id, challenge)["code"][0]
        self.assertEqual(self.exchange(client, client_id, code, verifier).status_code, 200)
        again = self.exchange(client, client_id, code, verifier)
        self.assertEqual(again.status_code, 400)
        self.assertEqual(again.json()["error"], "invalid_grant")

    def test_a_code_belongs_to_the_client_that_asked(self):
        client = TestClient(self.app, base_url=BASE)
        client_id, other = self.register(client), self.register(client)
        verifier, challenge = pkce()
        code = self.code(client, client_id, challenge)["code"][0]
        self.assertEqual(self.exchange(client, other, code, verifier).status_code, 400)

    def test_a_different_redirect_uri_at_the_token_exchange_is_refused(self):
        client = TestClient(self.app, base_url=BASE)
        client_id = self.register(client)
        verifier, challenge = pkce()
        code = self.code(client, client_id, challenge)["code"][0]
        response = self.exchange(client, client_id, code, verifier, redirect_uri="https://evil.test/cb")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_request")

    def test_an_unregistered_redirect_uri_is_refused_at_authorize(self):
        client = TestClient(self.app, base_url=BASE)
        params = {
            "response_type": "code",
            "client_id": self.register(client),
            "code_challenge": pkce()[1],
            "code_challenge_method": "S256",
            "redirect_uri": "https://evil.test/cb",
        }
        response = client.get("/authorize", params=params, follow_redirects=False)
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("set-cookie", response.headers)

    def test_one_offices_token_does_not_reach_another_offices_connector(self):
        with TestClient(self.app, base_url=BASE) as client:
            bound = self.token(client)  # office-a, issued for /fake/mcp
            self.assertEqual(ping(client, bound).status_code, 200)
            self.assertEqual(ping(client, bound, "/other/mcp").status_code, 401)  # wrong audience
            # Even with no audience on the token, office-a has no access to the connector only office-b has.
            self.assertEqual(ping(client, self.token(client, resource=None), "/other/mcp").status_code, 403)
            theirs = self.token(client, "user@office-b.test", resource=f"{BASE}/other/mcp")
            self.assertEqual(ping(client, theirs, "/other/mcp").status_code, 200)
            self.assertEqual(ping(client, theirs).status_code, 401)


class TokenLifecycleTest(SignInCase):
    """Refresh rotation, /revoke and a removed office: an old or revoked token never reaches a connector again."""

    def issue(self, client, email="user@office-a.test", **options):
        client_id = self.register(client)
        verifier, challenge = pkce()
        query = self.code(client, client_id, challenge, email, **options)
        response = self.exchange(client, client_id, query["code"][0], verifier)
        self.assertEqual(response.status_code, 200, response.text)
        return client_id, response.json()

    def refresh(self, client, client_id, refresh_token):
        return client.post(
            "/token", data={"grant_type": "refresh_token", "client_id": client_id, "refresh_token": refresh_token}
        )

    def test_a_used_refresh_token_is_refused_and_the_new_pair_works(self):
        with TestClient(self.app, base_url=BASE) as client:
            client_id, first = self.issue(client)
            rotated = self.refresh(client, client_id, first["refresh_token"])
            self.assertEqual(rotated.status_code, 200, rotated.text)
            second = rotated.json()
            again = self.refresh(client, client_id, first["refresh_token"])
            self.assertEqual(again.status_code, 400)
            self.assertEqual(again.json()["error"], "invalid_grant")
            self.assertEqual(ping(client, first["access_token"]).status_code, 401)
            self.assertEqual(ping(client, second["access_token"]).status_code, 200)
            # Another client cannot use this client's refresh token either.
            other = self.register(client)
            self.assertEqual(self.refresh(client, other, second["refresh_token"]).status_code, 400)
            self.assertEqual(ping(client, second["access_token"]).status_code, 200)

    def test_revoke_ends_the_access_and_refresh_token_together(self):
        # client_secret="": the SDK's /revoke form declares it without a default, so even a public client sends it.
        with TestClient(self.app, base_url=BASE) as client:
            client_id, first = self.issue(client)
            _, kept = self.issue(client)
            response = client.post(
                "/revoke", data={"token": first["access_token"], "client_id": client_id, "client_secret": ""}
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(ping(client, first["access_token"]).status_code, 401)
            self.assertEqual(self.refresh(client, client_id, first["refresh_token"]).status_code, 400)
            self.assertEqual(ping(client, kept["access_token"]).status_code, 200)  # another sign-in is untouched

            client_id, second = self.issue(client)
            response = client.post(
                "/revoke", data={"token": second["refresh_token"], "client_id": client_id, "client_secret": ""}
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(ping(client, second["access_token"]).status_code, 401)

    def test_a_removed_offices_tokens_are_refused(self):
        with TestClient(self.app, base_url=BASE) as client:
            client_id, mine = self.issue(client)
            _, theirs = self.issue(client, "user@office-b.test", resource=f"{BASE}/other/mcp")
            self.assertEqual(ping(client, mine["access_token"]).status_code, 200)
            self.store.remove_tenant("office-a")
            self.assertEqual(ping(client, mine["access_token"]).status_code, 401)
            refused = self.refresh(client, client_id, mine["refresh_token"])
            self.assertEqual(refused.status_code, 400)
            self.assertEqual(refused.json()["error"], "invalid_grant")
            self.assertEqual(ping(client, theirs["access_token"], "/other/mcp").status_code, 200)


class OldStoreMigrationTest(unittest.TestCase):
    def test_web_sessions_are_gone_from_the_store_and_old_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "store.sqlite"
            old = sqlite3.connect(path)
            old.execute("create table web_sessions (digest text primary key, email text, tenant text)")
            old.commit()
            old.close()
            with Store(path) as store:
                tables = {row["name"] for row in store._run("select name from sqlite_master where type = 'table'")}
                self.assertNotIn("web_sessions", tables)
                store.add_tenant("office-a", "A")
                store.add_user("u@a.test", "office-a", PASSWORD)
                store.set_password("u@a.test", PASSWORD + "2")
                store.remove_user("u@a.test")
                store.remove_tenant("office-a")
                store.purge()

    def test_users_stored_before_the_password_change_must_change_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "store.sqlite"
            store = Store(path)
            store.add_tenant("office-a", "A")
            store._db.close()
            old = sqlite3.connect(path)
            old.execute("alter table users drop column must_change")
            old.execute("insert into users values ('u@a.test', 'office-a', 'scrypt$00$00', 0)")
            old.commit()
            old.close()
            store = Store(path)
            self.assertTrue(store.must_change("u@a.test"))
            store.add_user("new@a.test", "office-a", PASSWORD)
            self.assertFalse(store.must_change("new@a.test"))
            store._db.close()


class PasswordChangeTest(SignInCase):
    EMAIL = "user@office-a.test"
    NEW = "outra-senha-bem-longa"

    def start(self, client, **form):
        client_id = self.register(client)
        verifier, challenge = pkce()
        request_id, _ = self.authorize(client, client_id, challenge)
        data = {"request": request_id, "email": self.EMAIL, "password": PASSWORD, **form}
        return client_id, verifier, request_id, client.post("/login", data=data, follow_redirects=False)

    def change(self, client, request_id, password, confirm=None):
        data = {"request": request_id, "email": self.EMAIL, "new_password": password, "confirm": confirm or password}
        return client.post("/login", data=data, follow_redirects=False)

    def test_an_operator_set_password_is_changed_before_the_code_is_issued(self):
        self.store.set_password(self.EMAIL, PASSWORD, must_change=True)
        client = TestClient(self.app, base_url=BASE)
        client_id, verifier, request_id, response = self.start(client)
        self.assertEqual(response.status_code, 200)
        self.assertIn("definida pelo administrador", response.text)
        self.assertNotIn("code=", response.headers.get("location", ""))
        self.assertIn("não conferem", self.change(client, request_id, self.NEW, self.NEW + "x").text)
        self.assertIn("pelo menos 12", self.change(client, request_id, "curta").text)
        self.assertIn("diferente da atual", self.change(client, request_id, PASSWORD).text)
        done = self.change(client, request_id, self.NEW)
        self.assertEqual(done.status_code, 302, done.text)
        code = parse_qs(urlsplit(done.headers["location"]).query)["code"][0]
        self.assertEqual(self.exchange(client, client_id, code, verifier).status_code, 200)
        self.assertFalse(self.store.must_change(self.EMAIL))
        self.assertIsNone(self.store.authenticate(self.EMAIL, PASSWORD))
        self.assertEqual(self.store.authenticate(self.EMAIL, self.NEW), "office-a")
        self.assertEqual(self.store.history("office-a")[0]["action"], "senha trocada pelo usuário")

    def test_any_user_may_choose_to_change_the_password(self):
        client = TestClient(self.app, base_url=BASE)
        _, _, request_id, response = self.start(client, change="1")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Escolha a senha nova", response.text)
        self.assertEqual(self.change(client, request_id, self.NEW).status_code, 302)
        self.assertEqual(self.store.authenticate(self.EMAIL, self.NEW), "office-a")

    def test_without_the_box_a_user_signs_in_directly(self):
        client = TestClient(self.app, base_url=BASE)
        self.assertEqual(self.start(client)[3].status_code, 302)

    def test_the_new_password_step_needs_the_current_password_first(self):
        client = TestClient(self.app, base_url=BASE)
        request_id, _ = self.authorize(client, self.register(client), pkce()[1])
        self.assertEqual(self.change(client, request_id, self.NEW).status_code, 400)
        self.assertEqual(self.store.authenticate(self.EMAIL, PASSWORD), "office-a")

    def test_a_reset_or_removal_after_the_sign_in_voids_the_pending_change(self):
        for revoke in (
            lambda: self.store.set_password(self.EMAIL, "senha-do-operador-nova", must_change=True),
            lambda: self.store.remove_user(self.EMAIL),
        ):
            self.store.add_user(self.EMAIL, "office-a", PASSWORD, must_change=True)
            client = TestClient(self.app, base_url=BASE)
            _, _, request_id, response = self.start(client)
            self.assertEqual(response.status_code, 200)
            revoke()
            self.assertEqual(self.change(client, request_id, self.NEW).status_code, 400)
            self.assertIsNone(self.store.authenticate(self.EMAIL, self.NEW))

    def test_a_wrong_password_never_reaches_the_new_password_step(self):
        self.store.set_password(self.EMAIL, PASSWORD, must_change=True)
        client = TestClient(self.app, base_url=BASE)
        request_id, _ = self.authorize(client, self.register(client), pkce()[1])
        wrong = self.submit(client, request_id, password="senha-errada-longa")
        self.assertIn("incorretos", wrong.text)
        self.assertEqual(self.change(client, request_id, self.NEW).status_code, 400)


if __name__ == "__main__":
    unittest.main()
