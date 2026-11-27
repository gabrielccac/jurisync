"""PJe answers in camelCase (snake_case names kept for one cycle) and pje_get_process dates in ISO."""

import asyncio
import unittest
from unittest import mock

from jurisync.connectors.pje import process, server
from jurisync.connectors.pje.parsers import iso_pt
from jurisync.core import responses

NUMBER = "0000001-00.2026.4.05.8300"


class CamelTest(unittest.TestCase):
    def test_portuguese_page_dates(self):
        self.assertEqual(iso_pt("19 jun. 2025"), "2025-06-19")
        self.assertEqual(iso_pt("3 Out 2026"), "2026-10-03")
        for value in ("31 fev. 2026", "ontem", None):
            self.assertEqual(iso_pt(value), value)

    def test_get_process_gains_camel_keys_and_iso_dates(self):
        result = {
            "success": True,
            "numero_processo": NUMBER,
            "metadata": {"filed_at": "19 jun. 2025", "judicial_class": {"name": "Classe Fictícia"}},
            "timeline": [{"date": "29 out. 2026", "movements": [{"movement_texts": [], "type_code": "1"}]}],
            "pagination": {"loaded_pages": 1, "loadedPages": 1},
        }
        with (
            mock.patch.object(server, "require_own_number"),
            mock.patch.object(server.sessions, "run", return_value=result),
        ):
            answer = asyncio.run(
                server.create_server().call_tool("pje_get_process", {"portal": "trf5-1g", "processNumber": NUMBER})
            ).structured_content
        self.assertEqual((answer["numeroProcesso"], answer["numero_processo"]), (NUMBER, NUMBER))
        metadata = answer["metadata"]
        self.assertEqual((metadata["filedAt"], metadata["filed_at"]), ("2025-06-19", "19 jun. 2025"))
        self.assertEqual(metadata["judicialClass"], {"name": "Classe Fictícia"})
        day = answer["timeline"][0]
        self.assertEqual((day["date"], day["dateText"]), ("2026-10-29", "29 out. 2026"))
        self.assertEqual(day["movements"][0], {"movementTexts": [], "typeCode": "1"})
        (candidate,) = answer["movementCandidates"]
        self.assertEqual((candidate["date"], candidate["dateText"]), ("2026-10-29", "29 out. 2026"))
        self.assertNotIn("movement_candidates", answer)

    def test_a_timeline_near_the_budget_stays_within_it(self):
        movement = {"time": "10:00", "movement": "Juntada de petição fictícia " + "x" * 120, "movement_texts": [],
                    "documents": [{"id": "1", "title": "Petição", "visible_in_timeline": True}],
                    "entry_type": "movement", "type_code": "85", "origin": "Advogado", "origin_code": "1"}  # fmt: skip
        timeline = [{"date": "29 out. 2026", "movements": [dict(movement) for _ in range(3)]} for _ in range(20)]
        result = {"success": True, "numero_processo": NUMBER, "metadata": {}, "timeline": timeline}
        raw = len(responses.compact({**result, "movement_candidates": process.movement_candidates(result)}))
        with (
            mock.patch.object(server, "require_own_number"),
            mock.patch.object(server.sessions, "run", return_value=result),
        ):
            answer = asyncio.run(
                server.create_server().call_tool("pje_get_process", {"portal": "trf5-1g", "processNumber": NUMBER})
            )
        self.assertGreater(raw, 45_000)
        self.assertLess(raw, responses.BUDGET)
        self.assertLessEqual(len(answer.content[0].text), responses.BUDGET)


if __name__ == "__main__":
    unittest.main()
