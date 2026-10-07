import asyncio
import base64
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit

from mcp.client.client import Client

from jurisync.connectors.juridiq.api import JuridiqApi
from jurisync.connectors.juridiq.server import create_server


class Response:
    def __init__(self, payload, status=200):
        self.status_code, self.text = status, json.dumps(payload)

    def json(self):
        return json.loads(self.text)


class FakeJuridiq:
    """Records requests; answers login and API calls like the live service."""

    def __init__(self, routes=None, statuses=None):
        self.requests, self.logins, self.routes, self.statuses = [], 0, routes or {}, statuses or []

    def __call__(self, method, url, headers, body):
        parts = urlsplit(url)
        if parts.path.endswith("/auth/login"):
            self.logins += 1
            payload = (
                base64.urlsafe_b64encode(json.dumps({"exp": int(time.time()) + 3600}).encode()).decode().rstrip("=")
            )
            return Response({"token": f"offline.{payload}.test{self.logins}"})
        self.requests.append(
            {
                "method": method,
                "path": parts.path,
                "query": parts.query,
                "auth": headers.get("authorization"),
                "body": body and json.loads(body),
            }
        )
        route = self.routes.get(parts.path)
        payload = route(parts) if callable(route) else (route if route is not None else {"data": []})
        return Response(payload, self.statuses.pop(0) if self.statuses else 200)


class JuridiqTestCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.session_file = Path(directory.name) / "session.json"
        patch = mock.patch.dict(
            os.environ,
            {
                "JURIDIQ_EMAIL": "offline@example.invalid",
                "JURIDIQ_PASSWORD": "offline-only",
                "JURIDIQ_ALLOW_WRITES": "false",
                "JURIDIQ_API_BASE_URL": "https://api.example.invalid",
                "JURIDIQ_LOGIN_URL": "https://api.example.invalid/auth/login",
                "JURIDIQ_SESSION_FILE": str(self.session_file),
            },
        )
        patch.start()
        self.addCleanup(patch.stop)


class SessionTest(JuridiqTestCase):
    def test_cached_auth_one_401_refresh_no_429_retry_and_read_guard(self):
        fake = FakeJuridiq()
        api = JuridiqApi(send=fake)
        api.request("/user/me")
        api.request("/publication?page=1&limit=10")
        self.assertEqual(fake.logins, 1)
        self.assertEqual(fake.requests[0]["auth"], fake.requests[1]["auth"])
        self.assertEqual(oct(self.session_file.stat().st_mode & 0o777), "0o600")
        fake.statuses += [401, 200]
        self.assertEqual(api.request("/user/me").status_code, 200)
        self.assertEqual(fake.logins, 2)
        fake.statuses.append(429)
        self.assertEqual(api.request("/user/me").status_code, 429)
        self.assertEqual(fake.logins, 2)
        with self.assertRaisesRegex(PermissionError, "writes are disabled"):
            api.request("/task", "POST")
        with self.assertRaisesRegex(RuntimeError, "configured API origin"):
            api.request("https://other.example.invalid/task")
        api.request("/generic-tables", "POST", {"module": "task"})
        self.assertEqual(fake.logins, 2)

    def test_cached_session_from_another_api_base_is_not_reused(self):
        self.session_file.write_text(
            json.dumps(
                {
                    "token": "wrong-origin-token",
                    "baseUrl": "https://other.example.invalid",
                    "expiresAt": "2999-01-01T00:00:00.000Z",
                }
            )
        )
        fake = FakeJuridiq()
        self.assertTrue(JuridiqApi(send=fake).token().startswith("offline."))
        self.assertEqual(fake.logins, 1)

    def test_node_written_cache_is_reused(self):
        self.session_file.write_text(
            json.dumps(
                {
                    "token": "node-token",
                    "baseUrl": "https://api.example.invalid",
                    "expiresAt": "2999-01-01T00:00:00.000Z",
                }
            )
        )
        fake = FakeJuridiq()
        JuridiqApi(send=fake).request("/user/me")
        self.assertEqual((fake.logins, fake.requests[0]["auth"]), (0, "Bearer node-token"))


class ClientTest(JuridiqTestCase):
    def test_mutations_use_documented_routes_and_payloads(self):
        fake = FakeJuridiq(routes={})
        api = JuridiqApi(send=fake)
        with mock.patch.dict(os.environ, {"JURIDIQ_ALLOW_WRITES": "true"}):
            api.create_lawsuit({"processNumber": "123"})
            api.update_lawsuit("law-1", {"court": "TRF1"})
            api.delete_lawsuit("law-1")
            api.update_task("task-1", {"description": "updated"})
            api.delete_task("task-1")
            api.create_event(
                {
                    "title": "Test",
                    "initialHour": "10:00",
                    "finalHour": "11:00",
                    "date": "2026-09-08",
                    "responsibleIds": [],
                }
            )
            api.mark_publications_read(["pub-1"])
        self.assertEqual(
            [(r["path"], r["method"], r["body"]) for r in fake.requests],
            [
                ("/lawSuit/", "POST", {"processNumber": "123"}),
                ("/lawSuit/law-1", "PATCH", {"court": "TRF1"}),
                ("/lawSuit/", "DELETE", {"id": ["law-1"]}),
                ("/task/task-1", "PATCH", {"description": "updated"}),
                ("/task/", "DELETE", {"id": ["task-1"]}),
                (
                    "/event/",
                    "POST",
                    {
                        "title": "Test",
                        "initialHour": "10:00",
                        "finalHour": "11:00",
                        "date": "2026-09-08",
                        "responsibleIds": [],
                    },
                ),
                ("/publication/read-movements-many", "PATCH", {"ids": ["pub-1"]}),
            ],
        )

    def test_api_errors_preserve_response_details(self):
        fake = FakeJuridiq(
            routes={
                "/lawSuit/": {"message": "Campo inválido", "statusCode": 400, "translateMessage": "Verifique os dados"}
            },
            statuses=[400],
        )
        with (
            mock.patch.dict(os.environ, {"JURIDIQ_ALLOW_WRITES": "true"}),
            self.assertRaisesRegex(RuntimeError, "Campo inválido.*Verifique os dados"),
        ):
            JuridiqApi(send=fake).create_lawsuit({"processNumber": "123"})

    def test_reads_preserve_documented_filters_and_routes(self):
        fake = FakeJuridiq()
        api = JuridiqApi(send=fake)
        api.publication_page(
            page=2, limit=10, unreadOnly=False, isHandled=True, keySearch="CNJ", start="2026-09-01", end="2026-09-09"
        )
        api.lawsuit_movements("law-1", content="intimação", instance=1)
        api.get_publication("pub-1")
        api.events(lawSuitId="law-1", statusFilter="pending", itemType="events", start="2026-09-01", end="2026-09-09")
        api.get_event("event-1")
        api.tasks(lawSuitId="law-1", isArchived=False, dateMethod="finalDate", tag=["tag-1", "tag-2"])
        api.task_history("task-1")
        api.task_comments("task-1")
        self.assertEqual(
            [r["path"] + ("?" + r["query"] if r["query"] else "") for r in fake.requests],
            [
                "/publication?page=2&limit=10&keySearch=CNJ&isHandled=true&start=2026-09-01&end=2026-09-09",
                "/lawSuit/movements/law-1?content=intima%C3%A7%C3%A3o&instance=1",
                "/publication/pub-1",
                "/event/?page=1&limit=50&lawSuitId=law-1&start=2026-09-01&end=2026-09-09&statusFilter=pending&itemType=events",
                "/event/event-1",
                "/task/?page=1&limit=30&isArchived=false&tag=tag-1%2Ctag-2&dateMethod=finalDate&lawSuit=law-1",
                "/history/task-1",
                "/comment/?taskId=task-1",
            ],
        )

    def test_pagination_rejects_repeated_pages_and_honors_max_results(self):
        def page(parts):
            three = "limit=100" in parts.query
            return {"data": [{"id": "pub-1"}, {"id": "pub-2"}] + ([{"id": "pub-3"}] if three else [])}

        fake = FakeJuridiq(routes={"/publication": page})
        api = JuridiqApi(send=fake)
        self.assertEqual(api.all_publications(limit=200, maxResults=2), [{"id": "pub-1"}, {"id": "pub-2"}])
        self.assertIn("limit=100", fake.requests[0]["query"])
        with self.assertRaisesRegex(RuntimeError, "duplicate/invalid IDs"):
            api.all_publications(limit=2)
        self.assertEqual(len(fake.requests), 3)

    def test_unread_pagination_filters_handled_items(self):
        pages = [
            [{"id": "unread-1"}, {"id": "read-1", "readAt": "2026-09-01"}],
            [{"id": "unread-2"}, {"id": "handled-1", "isHandled": True}],
            [],
        ]
        calls = []

        def page(parts):
            query = dict(item.split("=") for item in parts.query.split("&"))
            self.assertEqual(query["isHandled"], "false")
            calls.append(int(query["page"]))
            return {"data": pages[int(query["page"]) - 1]}

        api = JuridiqApi(send=FakeJuridiq(routes={"/publication": page}))
        self.assertEqual([item["id"] for item in api.all_unread_publications(limit=2)], ["unread-1", "unread-2"])
        self.assertEqual(calls, [1, 2, 3])
        calls.clear()
        self.assertEqual([item["id"] for item in api.all_unread_publications(limit=2, maxResults=1)], ["unread-1"])
        self.assertEqual(calls, [1])


class ServerTest(JuridiqTestCase):
    def tools(self, allow_writes):
        async def names():
            async with Client(create_server(allow_writes=allow_writes, api=JuridiqApi(send=FakeJuridiq()))) as client:
                return {tool.name for tool in (await client.list_tools()).tools}

        return asyncio.run(names())

    def test_write_tools_registered_only_when_enabled(self):
        readonly, writable = self.tools(False), self.tools(True)
        self.assertIn("juridiq_list_publications", readonly)
        self.assertNotIn("juridiq_create_task", readonly)
        self.assertIn("juridiq_create_task", writable)
        self.assertEqual(len(readonly), 18)
        self.assertEqual(len(writable), 26)

    def test_create_task_sends_only_supplied_fields_and_rejects_unknown(self):
        fake = FakeJuridiq(routes={"/task/": {"id": "task-1"}})

        async def run(payload):
            async with Client(create_server(allow_writes=True, api=JuridiqApi(send=fake))) as client:
                return await client.call_tool("juridiq_create_task", {"payload": payload})

        payload = {
            "title": "Réplica",
            "priority": "high",
            "columnId": "3f2b8f0e-1c2d-4e5f-8a9b-0c1d2e3f4a5b",
            "initialDate": "2026-10-07",
            "points": 5,
        }
        with mock.patch.dict(os.environ, {"JURIDIQ_ALLOW_WRITES": "true"}):
            result = asyncio.run(run(payload))
            rejected = asyncio.run(run({**payload, "responsibleIds": []}))
        self.assertFalse(result.is_error)
        self.assertEqual(fake.requests[0]["body"], payload)
        self.assertTrue(rejected.is_error)
        self.assertEqual(len(fake.requests), 1)

    def test_list_results_are_wrapped_like_the_node_server(self):
        fake = FakeJuridiq(routes={"/lawSuit": {"data": [{"id": "law-1", "processNumber": "1"}]}})

        async def run():
            async with Client(create_server(allow_writes=False, api=JuridiqApi(send=fake))) as client:
                return await client.call_tool("juridiq_find_lawsuit", {"processNumber": "1"})

        self.assertEqual(asyncio.run(run()).structured_content, {"result": [{"id": "law-1", "processNumber": "1"}]})


if __name__ == "__main__":
    unittest.main()
