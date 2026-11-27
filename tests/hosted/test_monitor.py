"""The monitoring API (/monitor): its token, what it answers, and the telemetry behind it."""

import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from starlette.testclient import TestClient

from jurisync import portals
from jurisync.core import credentials, session_pool
from jurisync.core.session_pool import LoginRejected, SessionPool
from jurisync.hosted import monitor
from jurisync.hosted.store import Store

from . import test_office_endpoint as endpoint
from .test_signin_security import BASE

TOKEN = "operator-token-for-tests"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def with_token(**extra):
    return mock.patch.dict(os.environ, {monitor.TOKEN: TOKEN, "JURISYNC_PROBE_SECONDS": "0", **extra})


class TokenTest(endpoint.OfficeCase):
    def test_without_a_configured_token_nothing_is_served(self):
        with mock.patch.dict(os.environ, {monitor.TOKEN: ""}), TestClient(self.app, base_url=BASE) as client:
            self.assertEqual(client.get("/monitor/status", headers=HEADERS).status_code, 404)

    def test_the_token_is_required_and_guessing_is_throttled(self):
        with with_token(), TestClient(self.app, base_url=BASE) as client:
            for name in ("status", "portals", "sessions", "calls", "usage"):
                self.assertEqual(client.get(f"/monitor/{name}", headers=HEADERS).status_code, 200, name)
            self.assertEqual(client.get("/monitor/status").status_code, 401)
            for _ in range(7):
                client.get("/monitor/status", headers={"Authorization": "Bearer wrong"})
            self.assertEqual(client.get("/monitor/status", headers=HEADERS).status_code, 429)

    def test_an_office_token_does_not_open_it(self):
        with with_token(), TestClient(self.app, base_url=BASE) as client:
            token = self.token(client, resource=f"{BASE}/mcp")
            answer = client.get("/monitor/status", headers={"Authorization": f"Bearer {token}"})
            self.assertEqual(answer.status_code, 401)


class ActivityTest(endpoint.OfficeCase):
    def test_calls_usage_and_signed_in_users(self):
        with with_token(), TestClient(self.app, base_url=BASE) as client:
            send = self.mcp(client, self.token(client, resource=f"{BASE}/mcp"))
            self.call(send, "alpha_whoami")
            self.call(send, "alpha_fail", {"number": "0001234-00.2026.8.26.0100"})
            usage = client.get("/monitor/usage", headers=HEADERS).json()
            self.assertEqual(usage["total"]["chamadas"], 2)
            self.assertEqual(usage["total"]["erros"], 1)
            self.assertEqual([row["tenant"] for row in usage["por_escritorio"]], ["office-a"])
            self.assertIsNone(client.get("/monitor/usage?office=office-b", headers=HEADERS).json()["total"])
            errors = client.get("/monitor/calls?errors=1", headers=HEADERS).json()["chamadas"]
            self.assertEqual([row["tool"] for row in errors], ["alpha_fail"])
            (user,) = client.get("/monitor/sessions", headers=HEADERS).json()["usuarios"]
            self.assertEqual((user["escritorio"], user["usuario"]), ("office-a", "user@office-a.test"))
            self.assertIsNotNone(user["ultima_chamada"])
            status = client.get("/monitor/status", headers=HEADERS).json()
            self.assertEqual(status["usuarios_conectados"], 1)
            everything = "".join(
                client.get(f"/monitor/{name}", headers=HEADERS).text
                for name in ("status", "portals", "sessions", "calls", "usage")
            )
            for private in ("0001234", "pin-office-a", "portal fora do ar"):
                self.assertNotIn(private, everything)


class PortalTest(endpoint.OfficeCase):
    setUp = endpoint.PortalChoicesTest.setUp  # office-a: tjam-1g; office-b: tjgo-1g, tjam-1g and STJ

    def test_portals_list_the_offices_and_the_reachability(self):
        with with_token(), TestClient(self.app, base_url=BASE) as client:
            before = client.get("/monitor/portals", headers=HEADERS).json()["portais"]
            self.assertEqual({row["acesso"]["estado"] for row in before}, {monitor.UNKNOWN})
            running = self.app.state.monitor
            status = {"projudi.tjgo.jus.br": 200, "projudi.tjam.jus.br": 403}
            running._get = lambda url, _impersonate: status.get(url.split("/")[2])
            running.probe()
            running.store.log_login("office-b", "tjgo-1g", "captcha", 10)
            listed = {row["portal"]: row for row in client.get("/monitor/portals", headers=HEADERS).json()["portais"]}
        states = {key: row["acesso"]["estado"] for key, row in listed.items()}
        self.assertEqual(states, {"tjgo-1g": monitor.UP, "tjam-1g": monitor.REFUSING, "stj": monitor.DOWN})
        offices = {key: [office["escritorio"] for office in row["escritorios"]] for key, row in listed.items()}
        self.assertEqual(offices["tjam-1g"], ["office-a", "office-b"])
        self.assertEqual(offices["tjgo-1g"], ["office-b"])
        self.assertEqual(listed["tjgo-1g"]["logins_24h"], {"ok": 0, "falhas": 1, "motivos": ["captcha"]})

    def test_the_call_log_keeps_the_portal_key_and_nothing_else(self):
        with TestClient(self.app, base_url=BASE) as client:
            send = self.mcp(client, self.token(client, "user@office-b.test", resource=f"{BASE}/mcp"))
            self.call(send, "projudi_get_process", {"number": "1", "portal": "tjam-1g"})
            self.call(send, "projudi_get_process", {"number": "1"})
            self.call(send, "stj_get_process", {"number": "1"})
            self.call(send, "projudi_get_process", {"number": "1", "portal": "123.456.789-00"})
        rows = [row["portal"] for row in reversed(self.store.calls("office-b"))]
        self.assertEqual(rows, ["tjam-1g", "tjgo-1g", "stj", ""])


class ProbeStateTest(unittest.TestCase):
    def test_states(self):
        states = {status: monitor.probe_state(status) for status in (200, 302, 404, 403, 429, 500, None)}
        self.assertEqual([states[status] for status in (200, 302, 404)], [monitor.UP] * 3)
        self.assertEqual([states[status] for status in (403, 429)], [monitor.REFUSING] * 2)
        self.assertEqual([states[status] for status in (500, None)], [monitor.DOWN] * 2)

    def test_a_pje_is_up_when_only_its_application_answers(self):
        trf1 = portals.get("trf1-1g")
        self.assertEqual(monitor.entries(trf1)[0], "https://pje1g.trf1.jus.br/pje/login.seam")


class PoolTest(unittest.TestCase):
    def test_snapshot_and_login_reports(self):
        seen = []

        def login(portal):
            if portal == "bad":
                raise LoginRejected("no", reason="otp_refused")
            return object()

        pool = SessionPool(login=login, refresh=lambda p, s: s, close=lambda s: None, login_cooldown=900)
        report = lambda portal, error, ms: seen.append((credentials.tenant(), portal, error and error.reason))  # noqa: E731
        with mock.patch.object(session_pool, "on_login", report):
            with credentials.scope("office-a", {"PIN": "1"}):
                with pool.session("good"):
                    pass
                with self.assertRaises(LoginRejected):
                    pool.check("bad")
                with self.assertRaises(LoginRejected):
                    pool.check("bad")  # cooling down: no login, no report
        self.assertEqual(seen, [("office-a", "good", None), ("office-a", "bad", "otp_refused")])
        (active,), (cooling,) = pool.snapshot()
        self.assertEqual((active["escritorio"], active["portal"], active["estado"]), ("office-a", "good", "ativa"))
        self.assertEqual((cooling["portal"], cooling["motivo"]), ("bad", "otp_refused"))
        self.assertNotIn("PIN", json.dumps([active, cooling]))

    def test_a_failing_report_never_costs_a_login(self):
        pool = SessionPool(login=lambda p: object(), refresh=lambda p, s: s, close=lambda s: None)
        with mock.patch.object(session_pool, "on_login", mock.Mock(side_effect=sqlite3.Error)):
            pool.check("good")


class StoreTest(unittest.TestCase):
    def test_an_older_store_gains_the_columns(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "store.sqlite"
            old = sqlite3.connect(path)
            old.execute(
                "create table calls (id integer primary key autoincrement, at integer not null, tenant text not null, "
                "user text not null, connector text not null, tool text not null, ok integer not null, "
                "error text not null default '', duration_ms integer not null)"
            )
            old.execute(
                "insert into calls (at, tenant, user, connector, tool, ok, duration_ms) values (1,'a','u','pje','t',1,5)"
            )
            old.commit()
            old.close()
            with Store(path) as store:
                store.log_call("a", "u", "pje", "t", "LoginRejected", 7, "trf1-1g", "otp_refused", "otp_refused")
                new, before = store.calls("a")
            self.assertEqual((new["portal"], new["reason"], before["portal"]), ("trf1-1g", "otp_refused", ""))
            self.assertEqual((new["error_code"], before["error_code"]), ("otp_refused", ""))

    def test_logins_are_purged_and_removed_with_the_office(self):
        with tempfile.TemporaryDirectory() as root, Store(Path(root) / "store.sqlite") as store:
            store.add_tenant("office-a", "A")
            store.log_login("office-a", "trf1-1g", "", 900)
            store.log_login("office-a", "trf1-1g", "captcha", 900)
            store.purge()
            self.assertEqual([row["reason"] for row in store.logins_since(time.time() - 60)], ["", "captcha"])
            with mock.patch("time.time", return_value=time.time() + 91 * 24 * 3600):
                store.purge()
            self.assertEqual(store.logins_since(0), [])
            store.log_login("office-a", "trf1-1g", "", 900)
            store.remove_tenant("office-a")
            self.assertEqual(store.logins_since(0), [])
