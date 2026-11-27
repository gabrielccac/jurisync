"""Eproc answers carry ISO dates, null when empty, and the portal's text for one cycle in <field>Text."""

import asyncio
import unittest
from unittest import mock

from jurisync.connectors.eproc import server

PORTAL = "trf4-1g-sc"


class DatesTest(unittest.TestCase):
    def call(self, tool, reader, result, **arguments):
        srv = server.create_server([PORTAL])
        try:
            with (
                mock.patch.object(server.sessions, "_login", return_value=(mock.Mock(), "<html>panel</html>", "x")),
                mock.patch.object(server, reader, side_effect=lambda *_, **__: {"success": True, **result}),
            ):
                answer = asyncio.run(srv.call_tool(tool, {"portal": PORTAL, **arguments}))
        finally:
            server.sessions.close()
        self.assertFalse(answer.is_error)
        return answer.structured_content

    def test_queue_rows(self):
        row = {"requestedAt": "05/11/2026 17:00:38", "deadlineStart": "", "deadlineEnd": "02/12/2026 23:59:59"}
        (item,) = self.call("eproc_list_intimacoes", "list_notices", {"items": [row]})["items"]
        self.assertEqual(
            (item["requestedAt"], item["deadlineStart"], item["deadlineEnd"]),
            ("2026-11-05T17:00:38", None, "2026-12-02T23:59:59"),
        )
        self.assertEqual(item["requestedAtText"], "05/11/2026 17:00:38")
        self.assertNotIn("deadlineStartText", item)

    def test_a_deadline_start_in_words_is_kept(self):
        row = {"deadlineStart": "Aguarda publicação no DJEN"}
        (item,) = self.call("eproc_list_intimacoes", "list_notices", {"items": [row]})["items"]
        self.assertEqual(item["deadlineStart"], "Aguarda publicação no DJEN")
        self.assertNotIn("deadlineStartText", item)

    def test_report_rows_and_hearings(self):
        row = {"lastEventAt": "21/09/2026 12:19:13", "filedAt": "16/07/2026 15:54:12"}
        (item,) = self.call("eproc_list_processes", "list_processes", {"items": [row]})["items"]
        self.assertEqual((item["lastEventAt"], item["filedAt"]), ("2026-09-21T12:19:13", "2026-07-16T15:54:12"))
        hearing = {"start": "12/05/2027 15:30:00", "end": ""}
        (item,) = self.call("eproc_list_audiencias", "list_hearings", {"audiencias": [hearing]})["audiencias"]
        self.assertEqual(
            (item["start"], item["end"], item["startText"]), ("2027-05-12T15:30:00", None, "12/05/2027 15:30:00")
        )

    def test_the_process_timeline_cover_and_hearings(self):
        result = {
            "metadata": {"cover": {"filedAt": "16/07/2026 15:54:12"}},
            "timeline": [{"dateTime": "21/09/2026 12:19:13"}],
            "audiencias": [{"start": "12/05/2027 15:30:00", "end": "12/05/2027 16:30:00"}],
        }
        answer = self.call("eproc_get_process", "fetch_process", result, processNumber="0000001-00.2026.4.04.7200")
        self.assertEqual(answer["metadata"]["cover"]["filedAt"], "2026-07-16T15:54:12")
        self.assertEqual(answer["timeline"][0]["dateTime"], "2026-09-21T12:19:13")
        self.assertEqual(answer["timeline"][0]["dateTimeText"], "21/09/2026 12:19:13")
        self.assertEqual(answer["audiencias"][0]["end"], "2027-05-12T16:30:00")


if __name__ == "__main__":
    unittest.main()
