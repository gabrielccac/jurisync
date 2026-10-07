import json
import subprocess
import sys
import threading
import time
import unittest

from jurisync.core.session_pool import SessionPool

NOISY_SERVER = """
from mcp.server.mcpserver import MCPServer
from jurisync.core.mcp import run_stdio

server = MCPServer("noisy")

@server.tool()
def noisy() -> str:
    print("GET  /pje/progress -> 200")  # connector code prints progress like this
    return "ok"

run_stdio(server)
"""


class StdioProtocolTest(unittest.TestCase):
    def test_connector_prints_never_reach_the_protocol_stream(self):
        messages = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "t", "version": "1"},
                },
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "noisy", "arguments": {}}},
        ]
        process = subprocess.Popen(
            [sys.executable, "-c", NOISY_SERVER],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        lines = []
        for message in messages:
            process.stdin.write(json.dumps(message) + "\n")
            process.stdin.flush()
            if "id" in message:
                lines.append(process.stdout.readline())
        process.stdin.close()  # Shutdown is where buffered prints used to leak.
        lines += process.stdout.read().splitlines()
        stderr = process.stderr.read()
        process.wait(timeout=20)
        parsed = [json.loads(line) for line in lines if line.strip()]
        self.assertEqual(parsed[-1]["result"]["content"][0]["text"], "ok")
        self.assertIn("GET  /pje/progress -> 200", stderr)


class SessionPoolTest(unittest.TestCase):
    def make_pool(self, valid=True, revalidate_after=120):
        self.logins, self.closed = [], []
        login = lambda key: self.logins.append(key) or f"{key}-session-{len(self.logins)}"
        return SessionPool(
            login, lambda key, value: value if valid else None, self.closed.append, revalidate_after=revalidate_after
        )

    def test_reuses_one_login_per_key(self):
        pool = self.make_pool()
        for _ in range(3):
            with pool.session("trf1-1g") as session:
                self.assertEqual(session, "trf1-1g-session-1")
        with pool.session("trf5-1g"):
            pass
        self.assertEqual(self.logins, ["trf1-1g", "trf5-1g"])

    def test_failed_call_forces_revalidation_and_relogin(self):
        pool = self.make_pool(valid=False)
        with self.assertRaises(RuntimeError), pool.session("jfsc"):
            raise RuntimeError("session expired")
        with pool.session("jfsc") as session:
            self.assertEqual(session, "jfsc-session-2")
        self.assertEqual(self.closed, ["jfsc-session-1"])

    def test_idle_session_is_revalidated(self):
        pool = self.make_pool(valid=False, revalidate_after=0)
        with pool.session("jfsc"):
            pass
        time.sleep(0.01)
        with pool.session("jfsc") as session:
            self.assertEqual(session, "jfsc-session-2")

    def test_calls_for_one_key_are_serialized(self):
        pool = self.make_pool()
        active, overlaps = [], []

        def work():
            with pool.session("trf5-1g"):
                active.append(1)
                overlaps.append(len(active))
                time.sleep(0.02)
                active.pop()

        threads = [threading.Thread(target=work) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(max(overlaps), 1)
        self.assertEqual(self.logins, ["trf5-1g"])


if __name__ == "__main__":
    unittest.main()
