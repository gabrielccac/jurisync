"""Every ADVBOX write tool is refused by the MCP server itself without ADVBOX_ALLOW_WRITES=true, before any request."""

import asyncio
import contextlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mcp.client.client import Client

from jurisync import registry
from jurisync.connectors.advbox import server, session
from jurisync.connectors.advbox.session import AdvboxSession
from jurisync.core import credentials

WRITE_CALLS = {
    "advbox_create_folder": {"customerId": "1", "name": "Pasta"},
    "advbox_upload_files": {"customerId": "1", "folder": "Pasta", "files": ["a.pdf"]},
    "advbox_create_task": {"lawsuitId": "1", "tasksId": "1", "guests": [1], "date": "2026-11-30", "comments": "x"},
    "advbox_update_task": {"postId": "1", "comments": "x"},
    "advbox_complete_task": {"postId": "1"},
    "advbox_delete_task": {"postId": "1"},
    "advbox_update_lawsuit_stage": {"lawsuitId": "1", "stageId": "3"},
}
# Not ADVBOX writes: the device check changes the account's trusted devices only; the others save files locally.
NOT_GATED = {"advbox_verify_device", "advbox_download_file", "advbox_download_folder", "advbox_save_folder"}


class WriteGateTest(unittest.TestCase):
    def setUp(self):
        self.sent = []

        def send(session, method, url, **_):
            self.sent.append((method, url))
            raise ConnectionError("offline test: no request may leave")

        patcher = mock.patch.object(AdvboxSession, "_send", send)
        patcher.start()
        self.addCleanup(patcher.stop)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        state = mock.patch.object(session, "STATE_DIR", Path(directory.name))  # no stored cookie is ever read
        state.start()
        self.addCleanup(state.stop)
        env = mock.patch.dict(
            os.environ,
            {
                "ADVBOX_EMAIL": "offline@example.invalid",
                "ADVBOX_PASSWORD": "offline-only",
                "ADVBOX_SESSION_FILE": str(Path(directory.name) / "session.json"),
                "JURISYNC_DOCUMENTS_DIR": directory.name,
                "ADVBOX_UPLOAD_FOLDER": directory.name,
            },
        )
        env.start()
        self.addCleanup(env.stop)

    def call_all(self, scope=None):
        async def run():
            async with Client(server.create_server()) as client:
                return {name: await client.call_tool(name, arguments) for name, arguments in WRITE_CALLS.items()}

        with scope or credentials.scope("office-a", {}):
            return asyncio.run(run())

    def test_the_gated_tools_are_every_advbox_write(self):
        async def tools():
            async with Client(server.create_server()) as client:
                return (await client.list_tools()).tools

        writes = {tool.name for tool in asyncio.run(tools()) if not tool.annotations.read_only_hint} - NOT_GATED
        self.assertEqual(writes, set(WRITE_CALLS))
        self.assertEqual(writes, registry.CONNECTORS["advbox"].write_tools)

    def test_every_write_is_refused_before_any_request_without_the_flag(self):
        for flag in ("", "false", "TRUE "):
            with mock.patch.dict(os.environ, {"ADVBOX_ALLOW_WRITES": flag}):
                local = self.call_all(scope=contextlib.nullcontext())  # local install: the environment decides
            # Hosted: an office without the grant does not inherit the server's own "true".
            with mock.patch.dict(os.environ, {"ADVBOX_ALLOW_WRITES": "true"}):
                hosted = self.call_all()
            for name in WRITE_CALLS:
                for where, results in (("local", local), ("hosted", hosted)):
                    with self.subTest(flag=flag, where=where, tool=name):
                        self.assertTrue(results[name].is_error)
                        self.assertIn("ADVBOX_ALLOW_WRITES=true", results[name].content[0].text)
        self.assertEqual(self.sent, [])

    def test_with_the_flag_the_same_calls_get_past_the_gate(self):
        with mock.patch.dict(os.environ, {"ADVBOX_ALLOW_WRITES": "true"}):
            office = {
                "ADVBOX_EMAIL": "b@example.invalid",
                "ADVBOX_PASSWORD": "offline-only",
                "ADVBOX_DEVICE_ID": "offline-device",
                "ADVBOX_ALLOW_WRITES": "true",
            }
            results = self.call_all(credentials.scope("office-b", office))
        for name, result in results.items():
            with self.subTest(tool=name):
                self.assertNotIn("ADVBOX_ALLOW_WRITES", result.content[0].text if result.is_error else "")
        self.assertTrue(self.sent)  # the calls went on to log in, which this test blocks


if __name__ == "__main__":
    unittest.main()
