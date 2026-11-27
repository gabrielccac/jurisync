"""eproc_list_intimacoes and eproc_list_processes carry the common list envelope: returned, total and next."""

import asyncio
import unittest
from unittest import mock

from jurisync.connectors.eproc import server

PORTAL = "trf4-1g-sc"


class EnvelopeTest(unittest.TestCase):
    def call(self, tool, reader, result, **arguments):
        def login(portal):
            return mock.Mock(), "<html>panel</html>", "https://x"

        srv = server.create_server([PORTAL])
        try:
            with (
                mock.patch.object(server.sessions, "_login", side_effect=login),
                mock.patch.object(server, reader, side_effect=lambda *_, **__: {"success": True, **result}),
            ):
                answer = asyncio.run(srv.call_tool(tool, {"portal": PORTAL, **arguments}))
        finally:
            server.sessions.close()
        self.assertFalse(answer.is_error)
        return answer.structured_content

    def test_a_queue_gives_its_rows_and_the_portal_count(self):
        result = {"items": [{"number": "0000001-00.2026.8.24.0001"}], "pagination": {"totalItems": 1, "complete": True}}
        answer = self.call("eproc_list_intimacoes", "list_notices", result, queue="pending")
        self.assertEqual((answer["returned"], answer["total"], answer["next"]), (1, 1, None))
        self.assertEqual(answer["pagination"]["totalItems"], 1)

    def test_the_attention_list_has_no_portal_total(self):
        answer = self.call("eproc_list_intimacoes", "list_notices", {"items": [], "pagination": {"complete": True}})
        self.assertEqual((answer["returned"], answer["total"], answer["next"]), (0, None, None))

    def test_unread_report_pages_are_asked_for_with_max_pages(self):
        pagination = {"totalItems": 120, "totalPages": 3, "itemCount": 50, "pagesRead": 1, "complete": False}
        answer = self.call("eproc_list_processes", "list_processes", {"items": [{}] * 50, "pagination": pagination})
        self.assertEqual((answer["returned"], answer["total"], answer["next"]), (50, 120, {"maxPages": 3}))

    def test_without_a_page_count_a_larger_max_pages_reaches_nothing(self):
        pagination = {"totalItems": 120, "totalPages": None, "itemCount": 50, "complete": False}
        answer = self.call("eproc_list_processes", "list_processes", {"items": [], "pagination": pagination})
        self.assertIsNone(answer["next"])


if __name__ == "__main__":
    unittest.main()
