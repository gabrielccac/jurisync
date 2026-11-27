"""pje_list_intimacoes and pje_list_processes carry the common list envelope: returned, total and next."""

import asyncio
import unittest
from unittest import mock

from jurisync.connectors.pje import server


def box(rows, total, pages, complete=None):
    complete = len(rows) >= total if complete is None else complete
    return {"path": ["Vara Fictícia"], "total": total, "pages": pages, "returned": len(rows), "complete": complete,
            "rows": rows}  # fmt: skip


class EnvelopeTest(unittest.TestCase):
    def call(self, tool, result, **arguments):
        with mock.patch.object(server.sessions, "run", return_value={"success": True, **result}):
            answer = asyncio.run(server.create_server().call_tool(tool, {"portal": "trf5-1g", **arguments}))
        self.assertFalse(answer.is_error)
        return answer.structured_content

    def test_the_inbox_sums_its_rows_and_asks_for_the_pages_left(self):
        rows = [{"expedienteId": str(n)} for n in range(3)]
        result = {"inboxes": [box(rows[:2], 90, 3, complete=False), box(rows[2:], 1, 1)], "complete": False}
        answer = self.call("pje_list_intimacoes", result, maxPages=1)
        self.assertEqual((answer["returned"], answer["total"], answer["next"]), (3, 91, {"maxPages": 3}))
        self.assertEqual(len(answer["inboxes"]), 2)

    def test_a_whole_inbox_has_no_next_and_a_bucket_shortfall_counts(self):
        result = {"inboxes": [], "complete": False, "bucketShortfall": {"bucketCount": 15, "foundInInboxes": 0}}
        answer = self.call("pje_list_intimacoes", result)
        self.assertEqual((answer["returned"], answer["total"], answer["next"]), (0, 15, None))

    def test_the_portal_count_wins_over_the_rows_read_and_is_null_when_unknown(self):
        result = {"inboxes": [box([{"expedienteId": "1"}], 1, 3, complete=False)], "total": 90, "complete": False}
        self.assertEqual(self.call("pje_list_intimacoes", result)["total"], 90)
        self.assertIsNone(self.call("pje_list_intimacoes", {**result, "total": None})["total"])

    def test_the_page_cap_leaves_no_next(self):
        result = {"inboxes": [box([{"expedienteId": "1"}], 5000, 125, complete=False)], "complete": False}
        self.assertIsNone(self.call("pje_list_intimacoes", result, maxPages=50)["next"])

    def test_the_acervo_reads_unread_jurisdictions_first(self):
        result = {"items": [{"processNumber": "0000001-00.2026.4.05.8300"}], "total": 9, "jurisdictions": [],
                  "notRead": ["Comarca Fictícia"], "complete": False}  # fmt: skip
        answer = self.call("pje_list_processes", result, maxPages=2)
        self.assertEqual((answer["returned"], answer["total"]), (1, 9))
        self.assertEqual(answer["next"], {"regions": ["Comarca Fictícia"], "maxPages": 2})
        self.assertNotIn("note", answer)

    def test_unread_jurisdictions_and_pages_left_both_reach_the_agent(self):
        jurisdiction = {"label": "Vara Fictícia", "count": 120, "complete": False,
                        "boxes": [box([], 120, 6, complete=False)]}  # fmt: skip
        result = {"items": [], "total": 130, "jurisdictions": [jurisdiction], "notRead": ["Comarca Fictícia"],
                  "note": "Call pje_list_processes again with regions set to notRead.", "complete": False}  # fmt: skip
        answer = self.call("pje_list_processes", result)
        self.assertEqual(answer["next"], {"regions": ["Comarca Fictícia"], "maxPages": 5})
        self.assertIn("without regions and with maxPages=6", answer["note"])

    def test_the_acervo_asks_for_deeper_pages_of_its_boxes(self):
        jurisdiction = {"label": "Comarca Fictícia", "count": 120, "complete": False,
                        "boxes": [box([], 120, 6, complete=False)]}  # fmt: skip
        result = {"items": [], "total": 120, "jurisdictions": [jurisdiction], "complete": False}
        self.assertEqual(self.call("pje_list_processes", result)["next"], {"maxPages": 6})


if __name__ == "__main__":
    unittest.main()
