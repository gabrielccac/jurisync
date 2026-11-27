"""The PJe {por, em} of each expediente is cienciaDada; ciencia stays for one cycle as its old name."""

import asyncio
import unittest
from unittest import mock

from jurisync.connectors.pje import process, server

GIVEN = {"por": "O sistema", "em": "2026-11-06"}


class CienciaDadaTest(unittest.TestCase):
    def test_inbox_rows_carry_ciencia_dada(self):
        rows = [{"expedienteId": "1", "ciencia": GIVEN, "cienciaPendente": False}, {"expedienteId": "2"}]
        result = {"success": True, "inboxes": [{"total": 2, "pages": 1, "complete": True, "rows": rows}]}
        with mock.patch.object(server.sessions, "run", return_value=result):
            answer = asyncio.run(server.create_server().call_tool("pje_list_intimacoes", {"portal": "trf5-1g"}))
        first, second = answer.structured_content["inboxes"][0]["rows"]
        self.assertEqual((first["cienciaDada"], first["ciencia"]), (GIVEN, GIVEN))
        self.assertNotIn("cienciaDada", second)

    def test_process_expedientes_carry_ciencia_dada(self):
        items = [{"expedienteId": "1", "atoPath": None, "ciencia": GIVEN}]
        (item,) = process._expedientes(items)["items"]
        self.assertEqual((item["cienciaDada"], item["ciencia"]), (GIVEN, GIVEN))


if __name__ == "__main__":
    unittest.main()
