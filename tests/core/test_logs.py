"""Log lines an agent can read: JSON with the call, its tool and portal, and the error code; never the arguments."""

import asyncio
import io
import json
import logging
import os
import unittest
from typing import Any
from unittest import mock

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent

from jurisync.core import errors, logs
from jurisync.core.mcp import JurisyncServer, tool_errors


def captured(run):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logs.JsonFormatter())
    logger = logging.getLogger("jurisync")
    level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        run()
    finally:
        logger.removeHandler(handler)
        logger.setLevel(level)
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def server():
    built = JurisyncServer("exemplo")

    @built.tool(name="exemplo_get_process")
    def exemplo_get_process(portal: str, number: str) -> dict[str, Any]:
        logging.getLogger("jurisync.exemplo").info("reading one process")
        return {"ok": True}

    @built.tool(name="exemplo_list_intimacoes")
    def exemplo_list_intimacoes(portal: str) -> dict[str, Any]:
        with tool_errors("Exemplo"):
            raise errors.InputError("período inválido")

    @built.tool(name="exemplo_get_document")
    def exemplo_get_document() -> dict[str, Any]:
        return {}["missing"]

    return built


class CallLinesTest(unittest.TestCase):
    def test_a_call_logs_its_lines_with_the_call_tool_and_portal_but_never_the_arguments(self):
        built = server()
        lines = captured(
            lambda: asyncio.run(
                built.call_tool("exemplo_get_process", {"portal": "trf1-1g", "number": "0000000-00.0000.0.00.0000"})
            )
        )
        inside, last = lines
        self.assertEqual(inside["message"], "reading one process")
        self.assertEqual(inside["call"], last["call"])
        self.assertEqual((last["event"], last["ok"], last["error_code"]), ("call", True, ""))
        self.assertEqual((last["tool"], last["portal"]), ("exemplo_get_process", "trf1-1g"))
        self.assertIsInstance(last["duration_ms"], int)
        self.assertNotIn("0000000-00", json.dumps(lines))

    def test_a_portal_value_without_the_shape_of_a_key_is_left_out(self):
        built = server()
        for value in ("000.000.000-00", "0000000-00.0000.0.00.0000", "Parte Fictícia", "a" * 40):
            call = built.call_tool("exemplo_get_process", {"portal": value, "number": "1"})
            lines = captured(lambda call=call: asyncio.run(call))
            self.assertNotIn("portal", lines[-1], value)

    def test_an_error_result_without_a_code_is_still_a_failure(self):
        built = server()

        @built.tool(name="exemplo_get_status")
        def exemplo_get_status() -> CallToolResult:
            return CallToolResult(content=[TextContent(type="text", text="falhou")], is_error=True)

        lines = captured(lambda: asyncio.run(built.call_tool("exemplo_get_status", {})))
        self.assertEqual((lines[-1]["ok"], lines[-1]["error_code"]), (False, "tool_error"))

    def test_an_anticipated_failure_logs_its_code(self):
        built = server()
        lines = captured(
            lambda: self.assertRaises(
                Exception, asyncio.run, built.call_tool("exemplo_list_intimacoes", {"portal": "trf1-1g"})
            )
        )
        self.assertEqual((lines[-1]["ok"], lines[-1]["error_code"]), (False, "invalid_input"))

    def test_a_bug_logs_its_trace_under_the_ref_the_agent_got(self):
        built = server()
        caught = []

        def run():
            try:
                asyncio.run(built.call_tool("exemplo_get_document", {}))
            except ToolError as error:
                caught.append(str(error))

        lines = captured(run)
        failure, last = lines
        self.assertEqual(failure["exception"], "KeyError")
        self.assertIn("Traceback", failure["trace"])
        self.assertIn(f"ref={failure['call']}", caught[0])
        self.assertEqual(last["error_code"], "unexpected")


class FormatTest(unittest.TestCase):
    def test_servers_log_json_and_clis_text_unless_the_environment_says(self):
        with mock.patch.dict(os.environ, {"JURISYNC_LOG_FORMAT": ""}):
            self.assertIsInstance(logs.formatter(True), logs.JsonFormatter)
            self.assertNotIsInstance(logs.formatter(False), logs.JsonFormatter)
        with mock.patch.dict(os.environ, {"JURISYNC_LOG_FORMAT": "text"}):
            self.assertNotIsInstance(logs.formatter(True), logs.JsonFormatter)
        with mock.patch.dict(os.environ, {"JURISYNC_LOG_FORMAT": "json"}):
            self.assertIsInstance(logs.formatter(False), logs.JsonFormatter)


if __name__ == "__main__":
    unittest.main()
