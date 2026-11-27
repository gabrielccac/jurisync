"""PJe answers in camelCase (snake_case names kept for one cycle) and pje_get_process dates in ISO."""

import asyncio
import unittest
from unittest import mock

from jurisync.connectors.pje import server
from jurisync.connectors.pje.parsers import iso_pt

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
        self.assertEqual(day["movements"][0]["typeCode"], "1")


if __name__ == "__main__":
    unittest.main()
