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
from tests.portability import assert_mode


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
        assert_mode(self, self.session_file, 0o600)
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
            ),
            encoding="utf-8",
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
            ),
            encoding="utf-8",
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

    def test_tag_and_task_template_mutations_use_captured_routes(self):
        fake = FakeJuridiq(routes={})
        api = JuridiqApi(send=fake)
        with mock.patch.dict(os.environ, {"JURIDIQ_ALLOW_WRITES": "true"}):
            api.create_tag({"name": "Urgente", "color": "#f10707", "modules": ["tarefas"]})
            api.update_tag("tag-1", {"name": "Urgente 2"})
            api.delete_tag("tag-1")
            api.create_task_template({"title": "Modelo", "subtasks": [{"title": "Passo 1"}]})
            api.update_task_template("tpl-1", {"title": "Modelo 2"})
            api.delete_task_template("tpl-1")
        self.assertEqual(
            [(r["path"], r["method"], r["body"]) for r in fake.requests],
            [
                ("/tag", "POST", {"name": "Urgente", "color": "#f10707", "modules": ["tarefas"]}),
                ("/tag/tag-1", "PATCH", {"name": "Urgente 2"}),
                ("/tag", "DELETE", {"id": ["tag-1"]}),
                (
                    "/task/task-templates",
                    "POST",
                    {
                        "title": "Modelo",
                        "points": 0,
                        "priority": "Baixa",
                        "daysForDue": 1,
                        "daysForStart": 0,
                        "isBusinessDay": False,
                        "createSheet": False,
                        "estimatedTimeMinutes": 0,
                        "subtasks": [
                            {
                                "title": "Passo 1",
                                "description": "",
                                "status": "Pendente",
                                "priority": "Baixa",
                                "isBusinessDay": False,
                                "daysForDue": 1,
                                "daysForStart": 0,
                                "points": 0,
                                "tags": [],
                                "responsiblesId": [],
                            }
                        ],
                    },
                ),
                ("/task/task-templates/tpl-1", "PATCH", {"title": "Modelo 2"}),
                ("/task/task-templates/tpl-1", "DELETE", None),
            ],
        )

    def test_tag_defaults_are_filled_before_sending(self):
        fake = FakeJuridiq(routes={})
        api = JuridiqApi(send=fake)
        with mock.patch.dict(os.environ, {"JURIDIQ_ALLOW_WRITES": "true"}):
            api.create_tag({"name": "Sem cor"})
        self.assertEqual(fake.requests[0]["body"], {"name": "Sem cor", "color": "#0296F9", "modules": ["tarefas"]})

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

        fake = FakeJuridiq(routes={"/lawSuit": page})
        api = JuridiqApi(send=fake)
        self.assertEqual(api.all_lawsuits(limit=200, maxResults=2), [{"id": "pub-1"}, {"id": "pub-2"}])
        self.assertIn("limit=100", fake.requests[0]["query"])
        with self.assertRaisesRegex(RuntimeError, "duplicate/invalid IDs"):
            api.all_lawsuits(limit=2)
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


class SessionReuseTest(JuridiqTestCase):
    def test_token_file_is_read_once_and_kept_in_memory(self):
        fake = FakeJuridiq()
        api = JuridiqApi(send=fake)
        api.request("/user/me")
        with mock.patch.object(Path, "read_text", side_effect=AssertionError("token file read again")):
            for _ in range(3):
                api.request("/user/me")
        self.assertEqual(fake.logins, 1)
        self.assertEqual(len({request["auth"] for request in fake.requests}), 1)

    def test_deleting_the_file_or_changing_the_account_drops_the_memory_copy(self):
        fake = FakeJuridiq()
        api = JuridiqApi(send=fake)
        first = api.token()
        self.session_file.unlink()
        self.assertNotEqual(api.token(), first)
        self.assertEqual(fake.logins, 2)
        other = self.session_file.with_name("other.json")
        with mock.patch.dict(os.environ, {"JURIDIQ_SESSION_FILE": str(other)}):
            api.token()
        self.assertEqual(fake.logins, 3)  # another account's file: its own entry, not the first one's token

    def test_one_http_session_per_client_without_cookies(self):
        api = JuridiqApi()
        self.addCleanup(api.close)
        with mock.patch("jurisync.connectors.juridiq.api.requests.Session") as session:
            session.return_value.request.return_value = Response({"data": []})
            self.session_file.write_text(
                json.dumps({"token": "t", "baseUrl": "https://api.example.invalid", "expiresAt": None}),
                encoding="utf-8",
            )
            api.request("/user/me")
            api.request("/user/me")
        self.assertEqual(session.call_count, 1)
        self.assertEqual(session.return_value.request.call_count, 2)
        self.assertTrue(session.return_value.request.call_args.kwargs["discard_cookies"])
        api.close()
        session.return_value.close.assert_called_once()


class GuardTest(JuridiqTestCase):
    def setUp(self):
        super().setUp()
        patch = mock.patch.dict(os.environ, {"JURIDIQ_ALLOW_WRITES": "true"})
        patch.start()
        self.addCleanup(patch.stop)

    def test_unread_listing_stops_at_the_page_cap_and_says_so(self):
        def page(parts):
            number = int(dict(item.split("=") for item in parts.query.split("&"))["page"])
            return {"data": [{"id": f"p{number}-{n}", "readAt": "2026-09-01" if n else None} for n in range(2)]}

        fake = FakeJuridiq(routes={"/publication": page})
        api = JuridiqApi(send=fake)
        result = api.unread_publications(limit=2, maxPages=3)
        self.assertEqual([item["id"] for item in result["result"]], ["p1-0", "p2-0", "p3-0"])
        self.assertEqual((result["truncated"], result["pagesRead"], len(fake.requests)), (True, 3, 3))
        short = JuridiqApi(send=FakeJuridiq(routes={"/publication": {"data": [{"id": "a"}]}})).unread_publications()
        self.assertEqual((short["truncated"], len(short["result"])), (False, 1))
        capped = api.unread_publications(limit=2, maxResults=1, maxPages=3)
        self.assertEqual((len(capped["result"]), capped["truncated"]), (1, False))

    def test_delete_guards_and_dry_run(self):
        routes = {
            "/task/t-1": {"id": "t-1", "title": "Contestar ação"},
            "/lawSuit/l-1": {"data": {"id": "l-1", "processNumber": "0800123-45.2026.4.05.8300"}},
        }
        fake = FakeJuridiq(routes=routes)
        api = JuridiqApi(send=fake)
        preview = api.delete_task("t-1", dry_run=True)
        self.assertEqual((preview["dryRun"], preview["wouldDelete"]["title"]), (True, "Contestar ação"))
        self.assertEqual(api.delete_lawsuit("l-1", dry_run=True)["wouldDelete"]["id"], "l-1")
        with self.assertRaisesRegex(ValueError, "lacks the expected text"):
            api.delete_task("t-1", expect_title="Recurso")
        with self.assertRaisesRegex(ValueError, "not the expected one"):
            api.delete_lawsuit("l-1", expect_process_number="0800999-45.2026.4.05.8300")
        self.assertEqual([r["method"] for r in fake.requests if r["method"] != "GET"], [])
        api.delete_task("t-1", expect_title="contestar")
        api.delete_lawsuit("l-1", expect_process_number="08001234520264058300")
        api.delete_task("t-2")  # no guard asked: no extra read, as before
        deletes = [(r["path"], r["body"]) for r in fake.requests if r["method"] == "DELETE"]
        self.assertEqual(
            deletes, [("/task/", {"id": ["t-1"]}), ("/lawSuit/", {"id": ["l-1"]}), ("/task/", {"id": ["t-2"]})]
        )
        self.assertNotIn("/task/t-2", [r["path"] for r in fake.requests])

    def test_unconfirmed_creation_says_the_record_may_exist(self):
        from curl_cffi.requests.exceptions import Timeout

        from jurisync.connectors.juridiq.api import JuridiqError

        calls = []

        def timeout(method, url, headers, body):
            if url.endswith("/auth/login"):
                return FakeJuridiq()(method, url, headers, body)
            calls.append(method)
            raise Timeout("no answer")

        with self.assertRaisesRegex(JuridiqError, "task MAY have been created.*Search for it before retrying"):
            JuridiqApi(send=timeout).create_task({"title": "x"})
        self.assertEqual(calls, ["POST"])  # never replayed
        api = JuridiqApi(send=FakeJuridiq(statuses=[502]))
        with self.assertRaisesRegex(JuridiqError, "event MAY have been created"):
            api.create_event({"title": "x"})
        refused = JuridiqApi(send=FakeJuridiq(routes={"/lawSuit/": {"message": "inválido"}}, statuses=[422]))
        with self.assertRaises(JuridiqError) as caught:
            refused.create_lawsuit({"processNumber": "1"})
        self.assertNotIn("MAY have been created", str(caught.exception))

    def test_delete_tools_take_the_optional_guards(self):
        fake = FakeJuridiq(routes={"/task/00000000-0000-0000-0000-000000000001": {"id": "x", "title": "Contestar"}})
        task = "00000000-0000-0000-0000-000000000001"

        async def run():
            async with Client(create_server(allow_writes=True, api=JuridiqApi(send=fake))) as client:
                tools = {tool.name: tool for tool in (await client.list_tools()).tools}
                preview = await client.call_tool("juridiq_delete_task", {"taskId": task, "dryRun": True})
                refused = await client.call_tool("juridiq_delete_task", {"taskId": task, "expectTitle": "Recurso"})
                pending = await client.call_tool("juridiq_list_pending_publications", {})
                return tools, preview, refused, pending

        tools, preview, refused, pending = asyncio.run(run())
        self.assertEqual(tools["juridiq_delete_task"].input_schema["required"], ["taskId"])
        self.assertEqual(tools["juridiq_delete_lawsuit"].input_schema["required"], ["lawSuitId"])
        self.assertIn("expectProcessNumber", tools["juridiq_delete_lawsuit"].input_schema["properties"])
        self.assertTrue(preview.structured_content["dryRun"])
        self.assertTrue(refused.is_error)
        self.assertEqual(pending.structured_content, {"result": [], "truncated": False, "pagesRead": 1})
        self.assertFalse([r for r in fake.requests if r["method"] == "DELETE"])


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
        self.assertNotIn("juridiq_create_task_template", readonly)
        self.assertIn("juridiq_create_task", writable)
        self.assertIn("juridiq_create_tag", writable)
        self.assertIn("juridiq_create_task_template", writable)
        self.assertEqual(len(readonly), 18)
        self.assertEqual(len(writable), 32)

    def test_create_task_sends_only_supplied_fields_and_rejects_unknown(self):
        fake = FakeJuridiq(routes={"/task/": {"id": "task-1"}})

        async def run(payload):
            async with Client(create_server(allow_writes=True, api=JuridiqApi(send=fake))) as client:
                return await client.call_tool("juridiq_create_task", {"payload": payload})

        payload = {
            "title": "Réplica",
            "priority": "Alta",
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

    def test_task_priority_must_be_a_known_value_and_lawsuit_is_optional(self):
        fake = FakeJuridiq(routes={"/task/": {"id": "task-1"}})

        async def run(payload):
            async with Client(create_server(allow_writes=True, api=JuridiqApi(send=fake))) as client:
                return await client.call_tool("juridiq_create_task", {"payload": payload})

        base = {"title": "Réplica", "columnId": "3f2b8f0e-1c2d-4e5f-8a9b-0c1d2e3f4a5b", "initialDate": "2026-10-07"}
        law = "9a1c2d3e-4f50-4a6b-8c7d-0e1f2a3b4c5d"
        with mock.patch.dict(os.environ, {"JURIDIQ_ALLOW_WRITES": "true"}):
            bad = asyncio.run(run({**base, "priority": "Urgente"}))
            standalone = asyncio.run(run({**base, "priority": "Média"}))
            linked = asyncio.run(run({**base, "priority": "Baixa", "lawSuitId": law}))
        self.assertTrue(bad.is_error)
        self.assertFalse(standalone.is_error)
        self.assertFalse(linked.is_error)
        self.assertEqual(len(fake.requests), 2)
        self.assertNotIn("lawSuitId", fake.requests[0]["body"])
        self.assertEqual(fake.requests[1]["body"]["lawSuitId"], law)

    def test_tag_rejects_bad_color_and_unknown_module(self):
        fake = FakeJuridiq(routes={"/tag": {"id": "tag-1"}})

        async def run(payload):
            async with Client(create_server(allow_writes=True, api=JuridiqApi(send=fake))) as client:
                return await client.call_tool("juridiq_create_tag", {"payload": payload})

        with mock.patch.dict(os.environ, {"JURIDIQ_ALLOW_WRITES": "true"}):
            bad_color = asyncio.run(run({"name": "X", "color": "vermelho"}))
            bad_module = asyncio.run(run({"name": "X", "modules": ["clientes"]}))
            good = asyncio.run(run({"name": "X", "color": "#f10707", "modules": ["processos", "tarefas"]}))
        self.assertTrue(bad_color.is_error)
        self.assertTrue(bad_module.is_error)
        self.assertFalse(good.is_error)
        self.assertEqual(len(fake.requests), 1)

    def test_task_template_delete_encodes_the_id_in_the_path(self):
        fake = FakeJuridiq(routes={})
        api = JuridiqApi(send=fake)
        with mock.patch.dict(os.environ, {"JURIDIQ_ALLOW_WRITES": "true"}):
            api.delete_task_template("a/../b")
        self.assertEqual(fake.requests[0]["path"], "/task/task-templates/a%2F..%2Fb")

    def test_list_tasks_tools_send_only_their_filters(self):
        fake = FakeJuridiq(routes={"/task/": {"data": [{"id": "task-1"}]}, "/task/kanban": {"columns": []}})

        async def run(name):
            async with Client(create_server(allow_writes=False, api=JuridiqApi(send=fake))) as client:
                return await client.call_tool(name, {"lawSuitId": "law-1", "limit": 5})

        listed = asyncio.run(run("juridiq_list_tasks"))
        kanban = asyncio.run(run("juridiq_list_tasks_kanban"))
        self.assertFalse(listed.is_error)
        self.assertFalse(kanban.is_error)
        self.assertEqual([r["path"] for r in fake.requests], ["/task/", "/task/kanban"])
        for request in fake.requests:
            self.assertIn("lawSuit=law-1", request["query"])
            self.assertIn("limit=5", request["query"])

    def test_list_results_are_wrapped_like_the_node_server(self):
        fake = FakeJuridiq(routes={"/lawSuit": {"data": [{"id": "law-1", "processNumber": "1"}]}})

        async def run():
            async with Client(create_server(allow_writes=False, api=JuridiqApi(send=fake))) as client:
                return await client.call_tool("juridiq_find_lawsuit", {"processNumber": "1"})

        self.assertEqual(asyncio.run(run()).structured_content, {"result": [{"id": "law-1", "processNumber": "1"}]})


UUID = "00000000-0000-4000-8000-000000000001"
WRITE_CALLS = {
    "juridiq_create_lawsuit": {
        "payload": {
            "serviceStep": "Inicial",
            "responsibleIds": [UUID],
            "persons": [{"name": "A", "personOrigin": "Cliente"}],
        }
    },
    "juridiq_update_lawsuit": {"lawSuitId": UUID, "patch": {"serviceStep": "Recurso"}},
    "juridiq_delete_lawsuit": {"lawSuitId": UUID},
    "juridiq_create_task": {
        "payload": {"title": "Réplica", "priority": "Alta", "columnId": UUID, "initialDate": "2026-10-07"}
    },
    "juridiq_update_task": {"taskId": UUID, "patch": {"title": "Réplica"}},
    "juridiq_delete_task": {"taskId": UUID},
    "juridiq_create_tag": {"payload": {"name": "X"}},
    "juridiq_update_tag": {"tagId": UUID, "patch": {"name": "Y"}},
    "juridiq_delete_tag": {"tagId": UUID},
    "juridiq_create_task_template": {"payload": {"title": "Modelo"}},
    "juridiq_update_task_template": {"templateId": UUID, "patch": {"title": "Modelo"}},
    "juridiq_delete_task_template": {"templateId": UUID},
    "juridiq_create_event": {
        "payload": {
            "title": "A",
            "date": "2026-10-07",
            "initialHour": "09:00",
            "finalHour": "10:00",
            "responsibleIds": [UUID],
        }
    },
    "juridiq_mark_publications_read": {"publicationIds": ["publication-1"]},
}


class WriteGateTest(JuridiqTestCase):
    def call_all(self, allow_writes):
        fake = FakeJuridiq()

        async def run():
            async with Client(create_server(allow_writes=allow_writes, api=JuridiqApi(send=fake))) as client:
                listed = {tool.name for tool in (await client.list_tools()).tools}
                return listed, {name: await client.call_tool(name, args) for name, args in WRITE_CALLS.items()}

        listed, results = asyncio.run(run())
        return listed, results, [r for r in fake.requests if r["method"] not in ("GET", "HEAD")]

    def test_every_write_tool_is_covered(self):
        from jurisync import registry

        self.assertEqual(set(WRITE_CALLS), registry.CONNECTORS["juridiq"].write_tools)

    def test_without_the_grant_no_write_tool_is_offered_or_sends_anything(self):
        listed, results, sent = self.call_all(allow_writes=False)
        self.assertFalse(listed & set(WRITE_CALLS))
        for name, result in results.items():
            self.assertTrue(result.is_error, name)
        self.assertEqual(sent, [])

    def test_a_server_built_with_writes_still_refuses_them_without_the_grant(self):
        # The hosted build registers the writes for every office; an office without JURIDIQ_ALLOW_WRITES=true is
        # refused at call time, before anything is sent.
        listed, results, sent = self.call_all(allow_writes=True)
        self.assertLessEqual(set(WRITE_CALLS), listed)
        for name, result in results.items():
            with self.subTest(tool=name):
                self.assertTrue(result.is_error)
                self.assertIn("JURIDIQ_ALLOW_WRITES", result.content[0].text)
        self.assertEqual(sent, [])
        # The same calls with the grant do reach Juridiq: the refusals above came from the gate, not the arguments.
        with mock.patch.dict(os.environ, {"JURIDIQ_ALLOW_WRITES": "true"}):
            _, results, sent = self.call_all(allow_writes=True)
        self.assertEqual({name for name, result in results.items() if result.is_error}, set())
        self.assertGreaterEqual(len(sent), len(WRITE_CALLS))


class TenantTest(JuridiqTestCase):
    def test_two_offices_with_the_same_login_never_share_a_token(self):
        from jurisync.core import credentials

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        fake = FakeJuridiq()
        api = JuridiqApi(send=fake)
        office = {
            "JURIDIQ_EMAIL": "same@example.invalid",  # the same account name: only the office tells them apart
            "JURIDIQ_API_BASE_URL": "https://api.example.invalid",
            "JURIDIQ_LOGIN_URL": "https://api.example.invalid/auth/login",
        }
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": directory.name}):
            for tenant in ("office-a", "office-b", "office-a", "office-b"):
                with credentials.scope(tenant, {**office, "JURIDIQ_PASSWORD": f"password-{tenant}"}):
                    api.request("/user/me")
        tokens = [request["auth"] for request in fake.requests]
        self.assertEqual(fake.logins, 2)
        self.assertEqual(tokens[0], tokens[2])
        self.assertEqual(tokens[1], tokens[3])
        self.assertNotEqual(tokens[0], tokens[1])
        files = sorted(Path(directory.name).rglob("*.json"))
        self.assertEqual([path.relative_to(directory.name).parts[:3] for path in files],
                         [("Juridiq", "tenants", "office-a"), ("Juridiq", "tenants", "office-b")])  # fmt: skip
        self.assertFalse(self.session_file.exists())  # the local install's own token file is not touched


if __name__ == "__main__":
    unittest.main()
