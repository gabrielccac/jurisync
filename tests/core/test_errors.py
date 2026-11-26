"""The one error model: every anticipated failure reaches the agent in the same format, a bug never leaks its text."""

import asyncio
import re
import unittest
from unittest import mock

from mcp.server.mcpserver.exceptions import ToolError

from jurisync import registry
from jurisync.core import credentials, errors
from jurisync.core.mcp import JurisyncServer, tool_errors
from jurisync.core.session_pool import LoginRejected, MissingLogin, SessionBusy

FORMAT = re.compile(
    r"^\[[a-z_]+\] [^\n]+: [^\n]+\nPróximo passo: [^\n]+\nretryable=(true|false) · quem resolve: (agente|usuário|suporte)"
)


def rendered(error):
    with tool_errors("Exemplo"), mock.patch.object(errors.calls, "current", return_value=""):
        raise error


class FormatTest(unittest.TestCase):
    def test_every_anticipated_failure_has_code_next_step_retry_and_actor(self):
        for error in (
            errors.InputError("processNumber must be a CNJ number"),
            errors.NotFound("process not found"),
            errors.PortalError("the portal refused"),
            errors.PortalUnavailable("timeout"),
            errors.NotConfigured("no certificate", setting="PJE_A1_P12"),
            errors.WritesDisabled("writes are disabled", setting="ADVBOX_ALLOW_WRITES=true"),
            LoginRejected("code refused", reason="otp_refused"),
            MissingLogin("no login"),
            SessionBusy("busy"),
            TimeoutError("slow"),
            ValueError("bad"),
            RuntimeError("portal said no"),
            PermissionError("read-only connector"),
        ):
            with self.subTest(error=type(error).__name__), self.assertRaises(ToolError) as raised:
                rendered(error)
            self.assertRegex(str(raised.exception), FORMAT)

    def test_login_reasons_keep_their_code_and_say_whether_to_repeat(self):
        with self.assertRaises(ToolError) as raised:
            rendered(LoginRejected("code refused", reason="otp_refused"))
        self.assertTrue(
            str(raised.exception).startswith("[otp_refused] Exemplo: code refused\nPróximo passo: não repita")
        )
        self.assertIn("retryable=false", str(raised.exception))
        with self.assertRaises(ToolError) as raised:
            rendered(LoginRejected("captcha", reason="captcha"))
        self.assertIn("retryable=true", str(raised.exception))

    def test_a_bug_is_not_anticipated(self):
        for error in (KeyError("payload"), AttributeError("x"), TypeError("y"), NotImplementedError("z")):
            with self.subTest(error=type(error).__name__), self.assertRaises(type(error)):
                rendered(error)

    def test_messages_are_sanitized(self):
        with self.assertRaises(ToolError) as raised:
            rendered(errors.PortalError("failed at https://portal.example/x?token=abc for CPF 123.456.789-09"))
        self.assertNotIn("https://", str(raised.exception))
        self.assertNotIn("456", str(raised.exception))


class ActorTest(unittest.TestCase):
    def test_configuration_is_the_user_locally_and_support_when_hosted(self):
        error = errors.WritesDisabled("ADVBOX writes are disabled", setting="ADVBOX_ALLOW_WRITES=true")
        self.assertIn("ADVBOX_ALLOW_WRITES=true", str(error))
        self.assertEqual(errors.describe(error).actor, errors.USER)
        self.assertIn(".env", error.next_step)
        with credentials.scope("office-a", values={}):
            hosted = errors.WritesDisabled("ADVBOX writes are disabled", setting="ADVBOX_ALLOW_WRITES=true")
            self.assertEqual(errors.describe(hosted).actor, errors.OPERATOR)
            self.assertNotIn(".env", hosted.next_step)
            self.assertNotIn(".env", errors.describe(MissingLogin("no login")).next_step)

    def test_a_refusal_only_the_lawyer_can_fix_is_the_user_in_both_modes(self):
        with credentials.scope("office-a", values={}):
            self.assertEqual(errors.describe(LoginRejected("x", reason="password_expired")).actor, errors.USER)


class ServerTest(unittest.TestCase):
    def server(self, failure):
        server = JurisyncServer("exemplo")

        @server.tool(name="exemplo_get_process")
        def exemplo_get_process() -> dict:
            with tool_errors("Exemplo"):
                raise failure

        return server

    def call(self, server):
        with self.assertRaises(ToolError) as raised:
            asyncio.run(server.call_tool("exemplo_get_process", {}))
        return str(raised.exception)

    def test_each_call_gets_its_own_ref(self):
        first = self.call(self.server(errors.PortalError("refused")))
        second = self.call(self.server(errors.PortalError("refused")))
        refs = [re.search(r"ref=(c-[0-9a-f]{8})$", text).group(1) for text in (first, second)]
        self.assertNotEqual(*refs)

    def test_a_bug_reaches_the_agent_as_unexpected_with_the_ref_and_without_its_text(self):
        with self.assertLogs("jurisync.core.mcp", "ERROR"):
            text = self.call(self.server(KeyError("internal detail")))
        self.assertIn("[unexpected] exemplo_get_process: falha inesperada no servidor", text)
        self.assertRegex(text, r"ref=c-[0-9a-f]{8}$")
        self.assertNotIn("internal detail", text)

    def test_every_connector_builds_the_same_server(self):
        for name, connector in registry.CONNECTORS.items():
            with self.subTest(connector=name):
                server, _ = connector.load()
                self.assertIsInstance(server, JurisyncServer)


class SummaryTest(unittest.TestCase):
    def test_a_batch_item_keeps_the_code_and_message_of_an_anticipated_failure(self):
        self.assertEqual(errors.summary(ValueError("data inválida")), "[invalid_request] data inválida")

    def test_a_bug_in_a_batch_item_keeps_its_text_in_the_log_only(self):
        with self.assertLogs("jurisync.core.errors", "ERROR") as logged, errors.calls.bound() as ref:
            line = errors.summary(KeyError("segredo-interno"))
        self.assertEqual(line, f"[unexpected] falha inesperada no servidor (ref={ref})")
        self.assertIn("segredo-interno", "\n".join(logged.output))


if __name__ == "__main__":
    unittest.main()
