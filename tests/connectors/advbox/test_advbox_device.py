"""Offline tests for ADVBOX device verification: the pending attempt, advbox_verify_device and the MCP elicitation."""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from mcp import types
from mcp.client.client import Client

from jurisync.connectors.advbox import server
from jurisync.connectors.advbox import session as advbox_session
from jurisync.connectors.advbox.parsers import parse_verification_form, parse_verification_recipient
from jurisync.connectors.advbox.session import AdvboxDeviceMissing, AdvboxDeviceVerification, AdvboxSession, CodeRefused
from jurisync.core import credentials

ENV = {"ADVBOX_EMAIL": "Joana@Dominio.com", "ADVBOX_PASSWORD": "pw", "ADVBOX_DEVICE_ID": "a" * 32}
CODE = "123456"
TWO_FA_PAGE = (
    "<html><body><p>Enviamos um código para j***@dominio.com.</p>"
    '<form method="POST" action="/user/2fa"><input type="hidden" name="_token" value="tok2">'
    '<input type="text" name="code" autocomplete="one-time-code"><button>Verificar</button></form>'
    "<footer>suporte@advbox.com.br</footer></body></html>"
)


class Headers(dict):
    def __init__(self, values=None, cookies=()):
        super().__init__(values or {})
        self.cookies = list(cookies)

    def get_list(self, name):
        return self.cookies if name == "set-cookie" else []


class Response:
    def __init__(self, status, text="", location=None, cookies=(), content_type="text/html"):
        headers = {"content-type": content_type}
        if location:
            headers["location"] = location
        self.status_code, self.text, self.headers = status, text, Headers(headers, cookies)

    def json(self):
        return {"data": [], "recordsTotal": 0}


class FakeAdvbox:
    """ADVBOX as the login sees it: an untrusted device lands on /user/2fa until the right code is sent."""

    def __init__(self, trusted=()):
        self.trusted = set(trusted)
        self.logins, self.codes, self.devices = 0, [], []

    def __call__(self, session, method, url, headers=None, use_cookies=True, data=None, **kwargs):
        path = urlsplit(url).path
        if path == "/login" and method == "GET":
            return Response(200, '<input type="hidden" name="_token" value="tok1">', cookies=["XSRF=x; path=/"])
        if path == "/login":
            self.logins += 1
            device = parse_qs(data)["_device"][0]
            self.devices.append(device)
            if device in self.trusted:
                return Response(302, location="/home", cookies=["advbox_session=ok; path=/"])
            return Response(302, location="/user/2fa", cookies=["pending=1; path=/"])
        if path == "/user/2fa" and method == "GET":
            return Response(200, TWO_FA_PAGE)
        if path == "/user/2fa":
            fields = parse_qs(data)
            self.codes.append(fields["code"][0])
            assert fields["_token"] == ["tok2"] and "pending" in session.jar
            if fields["code"] == [CODE]:
                return Response(302, location="/home", cookies=["advbox_session=ok; path=/"])
            return Response(302, location="/user/2fa")
        if path == "/home":
            return Response(200, "home")
        if path.startswith("/content/"):
            return Response(200, "{}", content_type="application/json")
        raise AssertionError(f"unexpected {method} {path}")


class DeviceCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.cache = Path(folder.name) / "advbox.json"
        self.advbox = FakeAdvbox()
        advbox_session._pending.clear()
        self.addCleanup(advbox_session._pending.clear)
        for patcher in (
            mock.patch.dict(os.environ, ENV),
            mock.patch.object(
                AdvboxSession, "_send", lambda session, *args, **kwargs: self.advbox(session, *args, **kwargs)
            ),
            mock.patch.object(advbox_session, "session_file", lambda: self.cache),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.session = AdvboxSession()
        self.addCleanup(self.session.close)

    def login(self):
        with self.assertRaises(AdvboxDeviceVerification) as raised:
            self.session.using(lambda s: None)
        return raised.exception


class PendingVerificationTest(DeviceCase):
    def test_landing_on_2fa_keeps_the_attempt_and_says_where_the_code_went(self):
        error = self.login()
        self.assertIn("sent a code to j***@dominio.com", str(error))
        self.assertIn("advbox_verify_device", str(error))
        pending = advbox_session.pending_verification()
        self.assertEqual((pending.device, pending.form["codeField"]), ("a" * 32, "code"))
        self.assertEqual(pending.form["action"], f"{advbox_session.BASE_URL}/user/2fa")
        self.assertIn("pending", pending.jar)
        self.assertIsNone(self.session.jar)

    def test_without_an_address_on_the_page_the_account_email_is_shown(self):
        with mock.patch.object(advbox_session, "parse_verification_recipient", return_value=None):
            self.assertIn("sent a code to Joana@Dominio.com", str(self.login()))

    def test_a_new_login_while_pending_sends_nothing(self):
        self.login()
        self.login()
        self.assertEqual(self.advbox.logins, 1)

    def test_the_attempt_expires(self):
        self.login()
        with mock.patch.object(advbox_session.time, "time", return_value=advbox_session.time.time() + 16 * 60):
            self.assertIsNone(advbox_session.pending_verification())
        self.login()
        self.assertEqual(self.advbox.logins, 2)

    def test_expired_attempts_of_other_offices_are_dropped_on_any_read_or_new_attempt(self):
        with credentials.scope("t1", ENV):
            self.login()
        later = advbox_session.time.time() + 16 * 60
        with mock.patch.object(advbox_session.time, "time", return_value=later):
            with credentials.scope("t2", ENV):
                self.assertIsNone(advbox_session.pending_verification())
            self.assertEqual(list(advbox_session._pending), [])  # t1's expired attempt went too
        with credentials.scope("t1", ENV):
            self.login()
        with mock.patch.object(advbox_session.time, "time", return_value=later):
            with credentials.scope("t2", ENV):
                self.login()
            self.assertEqual([key[0] for key in advbox_session._pending], ["t2"])

    def test_the_right_code_saves_the_session_without_rewriting_an_unchanged_device(self):
        self.login()
        with mock.patch.object(credentials, "save") as save:
            self.assertEqual(self.session.verify_device(" 123 456 "), {"verified": True, "deviceSaved": False})
        save.assert_not_called()
        self.assertIn("advbox_session", self.cache.read_text(encoding="utf-8"))
        self.assertIsNone(advbox_session.pending_verification())
        self.assertEqual(self.session.using(lambda s: "ok"), "ok")
        self.assertEqual(self.advbox.logins, 1)

    def test_a_wrong_code_keeps_the_attempt_and_three_end_it(self):
        self.login()
        for left in (2, 1):
            with self.assertRaises(CodeRefused) as refused:
                self.session.verify_device("000000")
            self.assertEqual(refused.exception.left, left)
            self.assertEqual(advbox_session.pending_verification().tries, 3 - left)
        with self.assertRaisesRegex(RuntimeError, "ended the verification"):
            self.session.verify_device("000000")
        self.assertIsNone(advbox_session.pending_verification())

    def test_a_new_device_gets_a_fresh_id_that_is_saved_once_verified(self):
        with self.assertRaises(AdvboxDeviceVerification):
            self.session.start_new_device()
        device = self.advbox.devices[-1]
        self.assertRegex(device, r"^[0-9a-f]{32}$")
        self.assertNotEqual(device, "a" * 32)
        with mock.patch.object(credentials, "save") as save:
            self.assertEqual(self.session.verify_device(CODE), {"verified": True, "deviceSaved": True})
        save.assert_called_once_with("ADVBOX_DEVICE_ID", device)

    def test_an_account_without_a_device_sends_nothing_until_the_user_agrees(self):
        values = {name: value for name, value in ENV.items() if name != "ADVBOX_DEVICE_ID"}
        saved = []
        with credentials.scope("t1", values, saver=lambda name, value: saved.append((name, value))):
            with self.assertRaisesRegex(AdvboxDeviceMissing, "newDevice=true"):
                self.session.using(lambda s: None)
            self.assertEqual(self.advbox.logins, 0)
            with self.assertRaises(AdvboxDeviceVerification):
                self.session.start_new_device()
            self.login()  # another call meanwhile: the code is awaited, no second one is sent
            self.assertEqual(self.advbox.logins, 1)
            self.assertEqual(self.session.verify_device(CODE), {"verified": True, "deviceSaved": True})
            self.assertEqual(self.session.using(lambda s: "ok"), "ok")
        self.assertEqual(saved, [("ADVBOX_DEVICE_ID", self.advbox.devices[-1])])

    def test_offices_do_not_share_attempts(self):
        saved = []
        with credentials.scope("t1", ENV):
            self.login()
        with credentials.scope("t2", ENV, saver=lambda name, value: saved.append((name, value))):
            self.assertIsNone(advbox_session.pending_verification())
            self.session.jar = None
            self.advbox.trusted.add("b" * 32)
            with mock.patch.object(advbox_session.secrets, "token_hex", return_value="b" * 32):
                self.assertEqual(self.session.start_new_device(), {"verified": True, "deviceSaved": True})
            self.assertEqual(credentials.env("ADVBOX_DEVICE_ID"), "b" * 32)
        self.assertEqual(saved, [("ADVBOX_DEVICE_ID", "b" * 32)])
        with credentials.scope("t1", ENV):
            self.assertEqual(advbox_session.pending_verification().device, "a" * 32)

    def test_a_scope_without_a_saver_cannot_store_the_device(self):
        with credentials.scope("t1", ENV), self.assertRaises(AdvboxDeviceVerification):
            self.session.start_new_device()
        with credentials.scope("t1", ENV), self.assertRaisesRegex(RuntimeError, "could not be stored"):
            self.session.verify_device(CODE)


class VerificationFormTest(unittest.TestCase):
    def test_the_code_field_is_preferred_by_name_and_a_checked_box_is_sent(self):
        form = parse_verification_form(
            '<form action="/logout" method="post"><input type="hidden" name="_token" value="t"></form>'
            '<form action="confirm?x=1&amp;y=2" method="post"><input type="hidden" name="_token" value="t">'
            '<input type="text" name="nickname"><input type="number" name="codigo_verificacao">'
            '<input type="checkbox" name="remember" checked></form>',
            "https://app.advbox.com.br/user/2fa",
        )
        self.assertEqual(
            form,
            {
                "action": "https://app.advbox.com.br/user/confirm?x=1&y=2",
                "method": "POST",
                "fields": {"_token": "t", "remember": "on"},
                "codeField": "codigo_verificacao",
            },
        )

    def test_a_page_without_a_code_field_names_what_it_has(self):
        with self.assertRaisesRegex(RuntimeError, r"fields seen: _token, remember"):
            parse_verification_form(
                '<form><input type="hidden" name="_token" value="secret"><input type="checkbox" name="remember"></form>',
                "https://app.advbox.com.br/user/2fa",
            )

    def test_the_recipient_is_the_address_next_to_the_message_not_the_footer(self):
        self.assertEqual(parse_verification_recipient(TWO_FA_PAGE), "j***@dominio.com")
        self.assertIsNone(parse_verification_recipient("<footer>suporte@advbox.com.br</footer>"))


class ElicitationTest(DeviceCase):
    def setUp(self):
        super().setUp()
        server.client.close()
        self.addCleanup(server.client.close)
        self.prompts = []

    def call(self, name, arguments=None, answers=None, mode="legacy"):
        """Through an MCP client; with ``answers`` it declares elicitation and answers each prompt (None declines).
        The handshake protocol lets the server ask mid-call; the 2026-07-28 one has no back-channel for it."""
        answers = list(answers or [])

        async def elicit(context, params):
            self.prompts.append(params.message)
            answer = answers.pop(0)
            if answer is None:
                return types.ElicitResult(action="decline")
            return types.ElicitResult(action="accept", content={"codigo": answer})

        async def run():
            options = {"elicitation_callback": elicit} if answers else {}
            async with Client(server.create_server(), mode=mode, **options) as client:
                return await client.call_tool(name, arguments or {})

        return asyncio.run(run())

    def text(self, result):
        return " ".join(item.text for item in result.content)

    def test_an_accepted_code_finishes_the_original_call(self):
        result = self.call("advbox_ensure_session", answers=[CODE])
        self.assertFalse(result.is_error, self.text(result))
        self.assertTrue(result.structured_content["authenticated"])
        self.assertEqual(len(self.prompts), 1)
        self.assertIn("enviou um código para j***@dominio.com", self.prompts[0])
        self.assertIn("advbox_session", self.cache.read_text(encoding="utf-8"))

    def test_a_wrong_code_is_asked_again(self):
        result = self.call("advbox_ensure_session", answers=["999999", CODE])
        self.assertFalse(result.is_error, self.text(result))
        self.assertEqual(self.advbox.codes, ["999999", CODE])
        self.assertIn("recusou o código (2 tentativa(s) restante(s))", self.prompts[1])

    def test_a_new_device_is_saved_after_the_elicited_code(self):
        with mock.patch.object(credentials, "save") as save:
            result = self.call("advbox_verify_device", {"newDevice": True}, answers=[CODE])
        self.assertEqual(result.structured_content, {"verified": True, "deviceSaved": True})
        save.assert_called_once_with("ADVBOX_DEVICE_ID", self.advbox.devices[-1])

    def test_declined_falls_back_to_the_tool(self):
        result = self.call("advbox_ensure_session", answers=[None])
        self.assertTrue(result.is_error)
        self.assertIn("call advbox_verify_device", self.text(result))
        self.assertIsNotNone(advbox_session.pending_verification())

    def test_a_connection_without_a_back_channel_gets_the_fallback(self):
        result = self.call("advbox_ensure_session", answers=[CODE], mode="auto")
        self.assertTrue(result.is_error)
        self.assertIn("call advbox_verify_device", self.text(result))
        self.assertEqual(self.advbox.codes, [])

    def test_a_client_without_elicitation_gets_the_fallback_and_the_tool_finishes_it(self):
        result = self.call("advbox_ensure_session")
        self.assertTrue(result.is_error)
        self.assertIn("sent a code to j***@dominio.com", self.text(result))
        refused = self.call("advbox_verify_device", {"code": "000000"})
        self.assertTrue(refused.is_error)
        self.assertIn("refused the code", self.text(refused))
        verified = self.call("advbox_verify_device", {"code": CODE})
        self.assertEqual(verified.structured_content, {"verified": True, "deviceSaved": False})
        nothing = self.call("advbox_verify_device", {"code": CODE})
        self.assertIn("Nothing to verify", nothing.structured_content["message"])
        self.assertEqual(self.advbox.logins, 1)

    def test_a_write_is_not_repeated_after_the_code(self):
        with mock.patch.dict(os.environ, {"ADVBOX_ALLOW_WRITES": "true"}):
            result = self.call("advbox_delete_task", {"postId": "1"}, answers=[CODE])
        self.assertTrue(result.is_error)
        self.assertIn("was not repeated", self.text(result))
        self.assertRegex(self.text(result), r"ref=c-[0-9a-f]{8}$")

    def test_the_elicited_retry_keeps_the_ref_of_the_call(self):
        refs = []
        original = server.calls.new_id

        def new_id():
            refs.append(original())
            return refs[-1]

        with (
            mock.patch.object(server.calls, "new_id", new_id),
            mock.patch.dict(os.environ, {"ADVBOX_ALLOW_WRITES": "true"}),
        ):
            result = self.call("advbox_delete_task", {"postId": "1"}, answers=[CODE])
        self.assertEqual(len(refs), 1)
        self.assertIn(f"ref={refs[0]}", self.text(result))


# The real page (23/11): the code form posts back to /login and leaves _device for the page's FingerprintJS to fill.
REAL_TWO_FA_PAGE = (
    "<html><body><p>Enviamos um código para j***@dominio.com.</p>"
    '<form method="POST" action="https://app.advbox.com.br/invite/validation" class="do__validation">'
    '<input name="_token" type="hidden" value="tok2"><input name="two_factor_type" type="radio" value="email">'
    '<input name="two_factor_type" type="radio" value="mobile"><input name="ev__item" type="hidden" value="two_factor">'
    '<input name="_device" type="hidden"><button type="submit">Enviar</button></form>'
    '<form method="POST" action="https://app.advbox.com.br/login" class="do__login">'
    '<input name="_token" type="hidden" value="tok2">'
    '<input placeholder="Insira seu código aqui" maxlength="20" name="two_factor_code" type="text">'
    '<input name="email" type="hidden" value="joana@dominio.com"><input name="password" type="hidden" value="pw">'
    '<input name="_device" type="hidden"><input name="token" type="hidden" value="t"><button>Entrar</button></form>'
    "</body></html>"
)


class RealPageAdvbox(FakeAdvbox):
    """The code goes back to /login with the account and the device; ADVBOX refuses a right code sent without it."""

    def __call__(self, session, method, url, headers=None, use_cookies=True, data=None, **kwargs):
        path = urlsplit(url).path
        fields = parse_qs(data or "")
        if path == "/login" and method == "POST" and "two_factor_code" in fields:
            self.codes.append(fields["two_factor_code"][0])
            if fields["two_factor_code"] == [CODE] and fields.get("_device") == [ENV["ADVBOX_DEVICE_ID"]]:
                return Response(302, location="/home", cookies=["advbox_session=ok; path=/"])
            return Response(302, location="/user/2fa")
        if path == "/user/2fa" and method == "GET":
            return Response(200, REAL_TWO_FA_PAGE)
        return super().__call__(session, method, url, headers, use_cookies, data, **kwargs)


class RealPageTest(DeviceCase):
    def setUp(self):
        super().setUp()
        self.advbox = RealPageAdvbox()

    def test_the_code_goes_with_the_device_the_page_leaves_empty(self):
        self.login()
        self.assertEqual(self.session.verify_device("123456"), {"verified": True, "deviceSaved": False})
        self.assertEqual(self.advbox.codes, ["123456"])
