"""The connector template in templates/connector/ builds, follows docs/padroes.md and answers like a connector."""

import asyncio
import re
import unittest
from unittest import mock

from mcp.server.mcpserver.exceptions import ToolError

from jurisync.core import mcp as core_mcp
from jurisync.core.session_pool import LoginRejected, SessionExpired, SessionPool
from templates.connector import client, server

NAME = re.compile(r"^[a-z]+_(get|list|search|read|download|calculate|create|update|delete|upload|complete)_[a-z_]+$")
INTIMACOES = [{"id": 7, "numeroProcesso": "0000001-00.2026.8.00.0001", "tipo": "intimação", "prazo": "2026-12-01"}]
PROCESS = {
    "numero": "0000001-00.2026.8.00.0001",
    "classe": "Procedimento Comum",
    "orgao": "1ª Vara Cível",
    "movimentos": [
        {"data": "2026-11-01", "descricao": "Distribuído"},
        {"data": "2026-11-10", "descricao": "Intimação expedida"},
    ],
}


def call(name, arguments=None):
    tool_server = server.create_server()
    return asyncio.run(tool_server.call_tool(name, arguments or {}))


class TemplateTest(unittest.TestCase):
    def setUp(self):
        fake = client.Session(http=mock.Mock(), token="t")
        login = mock.patch.object(server.sessions, "_login", lambda portal: fake)
        login.start()
        self.addCleanup(login.stop)
        self.addCleanup(server.sessions.close)

    def test_tools_follow_the_naming_and_annotation_rules(self):
        tools = server.create_server()._tool_manager.list_tools()
        self.assertEqual(sorted(tool.name for tool in tools), ["exemplo_get_process", "exemplo_list_intimacoes"])
        for tool in tools:
            self.assertRegex(tool.name, NAME)
            self.assertTrue(tool.title)
            self.assertIs(tool.annotations, core_mcp.READ_ONLY)

    def test_lists_intimacoes_in_the_platform_shape(self):
        with mock.patch.object(client, "intimacoes", return_value=INTIMACOES):
            result = call("exemplo_list_intimacoes")
        structured = result[1] if isinstance(result, tuple) else result.structured_content
        self.assertEqual((structured["returned"], structured["next"]), (1, None))
        self.assertEqual(structured["intimacoes"][0]["processNumber"], "0000001-00.2026.8.00.0001")
        self.assertIn("untrusted", structured["notice"])

    def test_orders_movements_newest_first(self):
        with mock.patch.object(client, "process", return_value=PROCESS):
            result = call("exemplo_get_process", {"processNumber": "0000001-00.2026.8.00.0001"})
        structured = result[1] if isinstance(result, tuple) else result.structured_content
        self.assertEqual([item["date"] for item in structured["movements"]], ["2026-11-10", "2026-11-01"])

    def test_a_portal_failure_is_a_sanitized_tool_error(self):
        failure = ValueError("refused at https://portal.exemplo.jus.br/api?token=abc")
        with mock.patch.object(client, "intimacoes", side_effect=failure), self.assertRaises(ToolError) as raised:
            call("exemplo_list_intimacoes")
        self.assertNotIn("https://", str(raised.exception))
        self.assertNotIn("abc", str(raised.exception))


class LoginTest(unittest.TestCase):
    def test_a_refused_login_is_a_rejection_not_an_expired_session(self):
        with (
            mock.patch.object(client.credentials, "env", return_value="fictitious"),
            mock.patch.object(client.requests, "Session"),
            mock.patch.object(client, "request", side_effect=SessionExpired("401")),
            self.assertRaises(LoginRejected) as raised,
        ):
            client.login(client.PORTAL)
        self.assertEqual(raised.exception.reason, "credentials_refused")

    def test_a_refused_login_reaches_the_agent_with_its_reason(self):
        refused = LoginRejected("the portal refused the user or the password", reason="credentials_refused")
        pool = SessionPool(login=mock.Mock(side_effect=refused), refresh=client.refresh, close=lambda session: None)
        with mock.patch.object(server, "sessions", pool), self.assertRaises(ToolError) as raised:
            call("exemplo_list_intimacoes")
        self.assertIn("[credentials_refused]", str(raised.exception))

    def test_redirects_are_refused_not_followed(self):
        http = mock.Mock()
        http.request.return_value = mock.Mock(status_code=302)
        with self.assertRaises(RuntimeError):
            client.request(http, "GET", "/api/intimacoes")
        self.assertFalse(http.request.call_args.kwargs["allow_redirects"])


if __name__ == "__main__":
    unittest.main()
