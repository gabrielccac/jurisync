"""PJe parties in the common shape [{pole, role, name, taxId?}], with the old texts kept for one cycle."""

import asyncio
import unittest
from unittest import mock

from jurisync.connectors.pje import server
from jurisync.connectors.pje.parsers import parties_of_sides, party_entries

NUMBER = "0000001-00.2026.4.05.8300"


class PartiesTest(unittest.TestCase):
    def test_the_process_groups_become_one_list_with_role_and_masked_tax_id(self):
        groups = {
            "active": ["PARTE FICTICIA - CPF: 123.***.***-09 (EXEQUENTE)", "ADVOGADO FICTICIO (ADVOGADO)"],
            "passive": ["EMPRESA FICTICIA - CNPJ: 12.345.678/0001-90 (EXECUTADO)"],
            "other_interested": ["PERITA FICTICIA"],
        }
        self.assertEqual(
            party_entries(groups),
            [
                {"pole": "active", "role": "EXEQUENTE", "name": "PARTE FICTICIA", "taxId": "123.***.***-09"},
                {"pole": "active", "role": "ADVOGADO", "name": "ADVOGADO FICTICIO"},
                {"pole": "passive", "role": "EXECUTADO", "name": "EMPRESA FICTICIA", "taxId": "12.345.678/0001-90"},
                {"pole": "other", "role": None, "name": "PERITA FICTICIA"},
            ],
        )

    def test_a_list_text_gives_its_two_sides(self):
        self.assertEqual(
            parties_of_sides("FULANA DE TESTE e outros X EMPRESA EXEMPLO S/A"),
            [
                {"pole": "active", "role": None, "name": "FULANA DE TESTE", "andOthers": True},
                {"pole": "passive", "role": None, "name": "EMPRESA EXEMPLO S/A"},
            ],
        )
        self.assertEqual(
            [party["name"] for party in parties_of_sides("PARTE AUTORA DE TESTE x INSTITUTO DE TESTE")],
            ["PARTE AUTORA DE TESTE", "INSTITUTO DE TESTE"],
        )
        self.assertEqual(parties_of_sides(None), [])

    def run_tool(self, tool, result, **arguments):
        with (
            mock.patch.object(server, "require_own_number"),
            mock.patch.object(server.sessions, "run", return_value={"success": True, **result}),
        ):
            answer = asyncio.run(server.create_server().call_tool(tool, {"portal": "trf5-1g", **arguments}))
        self.assertFalse(answer.is_error)
        return answer.structured_content

    def test_get_process_keeps_the_old_groups_for_a_cycle(self):
        groups = {"active": ["PARTE FICTICIA (AUTOR)"], "passive": []}
        answer = self.run_tool("pje_get_process", {"parties": groups, "timeline": []}, processNumber=NUMBER)
        self.assertEqual(answer["parties"], [{"pole": "active", "role": "AUTOR", "name": "PARTE FICTICIA"}])
        self.assertEqual(answer["partiesByPole"], groups)

    def test_the_acervo_rows_carry_parties_beside_partes(self):
        result = {"items": [{"processNumber": NUMBER, "partes": "A FICTICIA X B FICTICIA"}], "jurisdictions": []}
        (item,) = self.run_tool("pje_list_processes", result)["items"]
        self.assertEqual(item["partes"], "A FICTICIA X B FICTICIA")
        self.assertEqual([party["pole"] for party in item["parties"]], ["active", "passive"])


if __name__ == "__main__":
    unittest.main()
