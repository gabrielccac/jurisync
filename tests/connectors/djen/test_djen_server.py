import json
import sys
import unittest
from datetime import date
from unittest import mock

from mcp.client.client import Client
from mcp.client.stdio import StdioServerParameters
from mcp.server.mcpserver.exceptions import ToolError

from jurisync.connectors.djen import server as djen_server
from jurisync.connectors.djen.client import DjenError
from jurisync.core import credentials

RAW_ITEM = {
    "id": 1,
    "numero_processo": "0800123-45.2026.4.05.8300",
    "siglaTribunal": "TRF5",
    "texto": "<p>Intime-se&nbsp;a parte</p>",
    "hash": "abc",
    "destinatarioadvogados": [{"advogado": {"nome": "A", "numero_oab": "14663-A", "uf_oab": "PB"}}],
}


class DjenServerTest(unittest.IsolatedAsyncioTestCase):
    async def call(self, name, arguments):
        async with Client(djen_server.server) as client:
            return await client.call_tool(name, arguments)

    async def test_lists_read_only_tools(self):
        async with Client(djen_server.server) as client:
            tools = (await client.list_tools()).tools
        self.assertEqual(
            sorted(tool.name for tool in tools),
            [
                "djen_calculate_deadline",
                "djen_get_certidao_text",
                "djen_list_saved_searches",
                "djen_search_communications",
                "djen_search_saved",
            ],
        )
        self.assertTrue(all(tool.annotations.read_only_hint for tool in tools))

    async def test_rejects_invalid_oab_before_any_request(self):
        with mock.patch.object(djen_server, "search_page") as search:
            result = await self.call(
                "djen_search_communications",
                {"numeroOab": "invalid", "ufOab": "PB", "inicio": "2026-09-29", "fim": "2026-09-29"},
            )
        self.assertTrue(result.is_error)
        search.assert_not_called()

    async def test_accepts_optional_fields_sent_as_null(self):
        payload = {"status": "success", "count": 0, "items": []}
        nulls = dict.fromkeys(
            ["numeroOab", "ufOab", "nomeAdvogado", "nomeParte", "texto", "numeroProcesso", "meio", "tribunal"]
        )
        with mock.patch.object(djen_server, "search_page", return_value=payload) as search:
            result = await self.call(
                "djen_search_communications",
                {**nulls, "nomeAdvogado": "ELISANDRA BECKER", "inicio": "2026-10-20", "fim": "2026-10-20"},
            )
        self.assertFalse(result.is_error)
        self.assertEqual(search.call_args.args[0]["nomeAdvogado"], "ELISANDRA BECKER")

    async def test_formatted_process_number_is_still_refused(self):
        with mock.patch.object(djen_server, "search_page") as search:
            result = await self.call(
                "djen_search_communications",
                {"numeroProcesso": "5004320-76.2026.4.04.7104", "inicio": "2026-10-20", "fim": "2026-10-20"},
            )
        self.assertTrue(result.is_error)
        search.assert_not_called()

    async def test_search_by_advogado_name_passes_the_payload(self):
        payload = {"status": "success", "count": 0, "items": []}
        with mock.patch.object(djen_server, "search_page", return_value=payload) as search:
            result = await self.call(
                "djen_search_communications",
                {
                    "numeroOab": "61216",
                    "ufOab": "RS",
                    "nomeAdvogado": "ELISANDRA BECKER",
                    "tribunal": "STJ",
                    "meio": "D",
                    "inicio": "2026-09-03",
                    "fim": "2026-10-03",
                },
            )
        self.assertFalse(result.is_error)
        params = search.call_args_list[0].args[0]
        self.assertEqual(params["nomeAdvogado"], "ELISANDRA BECKER")
        self.assertEqual((params["siglaTribunal"], params["meio"], params["numeroOab"]), ("STJ", "D", "61216"))

    async def test_numeric_oab_and_process_number_are_taken_as_text(self):
        payload = {"status": "success", "count": 0, "items": []}
        with mock.patch.object(djen_server, "search_page", return_value=payload) as search:
            result = await self.call(
                "djen_search_communications",
                {
                    "numeroOab": 61216,
                    "ufOab": "RS",
                    "numeroProcesso": 50052895220264047117,
                    "inicio": "2026-10-19",
                    "fim": "2026-10-19",
                },
            )
        self.assertFalse(result.is_error)
        params = search.call_args_list[0].args[0]
        self.assertEqual((params["numeroOab"], params["numeroProcesso"]), ("61216", "50052895220264047117"))

    async def test_oab_without_uf_and_missing_keys_are_tool_errors(self):
        with mock.patch.object(
            djen_server, "search_page", side_effect=ValueError("pesquisa exige uma chave")
        ) as search:
            no_uf = await self.call(
                "djen_search_communications", {"numeroOab": "61216", "inicio": "2026-09-03", "fim": "2026-10-03"}
            )
            no_key = await self.call("djen_search_communications", {"inicio": "2026-09-03", "fim": "2026-10-03"})
        self.assertTrue(no_uf.is_error and no_key.is_error)
        self.assertEqual(search.call_count, 1)  # the OAB check fails before any request

    async def test_search_normalizes_items(self):
        page = {"status": "success", "count": 1, "items": [dict(RAW_ITEM)]}
        with mock.patch.object(djen_server, "search_page", return_value=page) as search:
            result = await self.call(
                "djen_search_communications",
                {"numeroOab": "14663", "ufOab": "PB", "inicio": "2026-09-29", "fim": "2026-09-30", "tribunal": "TRF5"},
            )
        self.assertFalse(result.is_error)
        params, pagina, itens = search.call_args_list[0].args
        self.assertEqual(
            params,
            {
                "numeroOab": "14663",
                "ufOab": "PB",
                "dataDisponibilizacaoInicio": "2026-09-29",
                "dataDisponibilizacaoFim": "2026-09-30",
                "siglaTribunal": "TRF5",
            },
        )
        self.assertEqual((pagina, itens), (1, 10))
        item = result.structured_content["items"][0]
        self.assertEqual(item["numero_processo"], "08001234520264058300")
        self.assertEqual(item["texto_limpo"], "Intime-se a parte")
        self.assertEqual(item["advogados"][0]["numero_digits"], "14663")
        self.assertEqual(item["portal"]["portal"], "trf5-1g/trf5-2g")  # no link: the number fits both instances

    async def test_each_item_names_the_pje_that_issued_it(self):
        turma = dict(RAW_ITEM, id=2, link="https://pje2g.trf5.jus.br/pje/Processo/ConsultaDocumento/listView.seam?x=1")
        vara = dict(RAW_ITEM, id=3, link="https://pje1g.trf5.jus.br/pje/Processo/ConsultaDocumento/listView.seam?x=2")
        page = {"status": "success", "count": 2, "items": [turma, vara]}
        with mock.patch.object(djen_server, "search_page", return_value=page):
            result = await self.call(
                "djen_search_communications",
                {"numeroOab": "14663", "ufOab": "PB", "inicio": "2026-10-19", "fim": "2026-10-19", "compact": True},
            )
        self.assertEqual(
            [item["portal"]["portal"] for item in result.structured_content["items"]], ["trf5-2g", "trf5-1g"]
        )

    async def test_compact_search_keeps_one_text_and_trims_lawyers(self):
        raw = dict(RAW_ITEM)
        raw["texto"] = "Intime-se a parte autora para manifestar-se sobre o laudo pericial."
        page = {"status": "success", "count": 1, "items": [raw]}
        with mock.patch.object(djen_server, "search_page", return_value=page):
            result = await self.call(
                "djen_search_communications",
                {
                    "numeroOab": "14663",
                    "ufOab": "PB",
                    "inicio": "2026-10-06",
                    "fim": "2026-10-06",
                    "compact": True,
                    "textLimit": 20,
                },
            )
        self.assertFalse(result.is_error)
        item = result.structured_content["items"][0]
        self.assertNotIn("texto_limpo", item)
        self.assertEqual((item["texto"], item["texto_truncado"]), ("Intime-se a parte au", True))
        self.assertEqual(item["texto_chars"], len(raw["texto"]))
        self.assertEqual(item["advogados"][0]["oab"], "14663/PB")
        self.assertEqual(item["id"], raw["id"])

    async def test_compact_without_limit_keeps_the_whole_text(self):
        raw = dict(RAW_ITEM)
        page = {"status": "success", "count": 1, "items": [raw]}
        with mock.patch.object(djen_server, "search_page", return_value=page):
            result = await self.call(
                "djen_search_communications",
                {"numeroOab": "14663", "ufOab": "PB", "inicio": "2026-10-06", "fim": "2026-10-06", "compact": True},
            )
        item = result.structured_content["items"][0]
        self.assertFalse(item["texto_truncado"])
        self.assertEqual(item["texto"], "Intime-se a parte")

    async def test_certidao_failure_is_sanitized_tool_error(self):
        error = DjenError("certidão failed at https://comunicaapi.pje.jus.br/x session=secret")
        with mock.patch.object(djen_server, "fetch_certidao", side_effect=error):
            result = await self.call("djen_get_certidao_text", {"hash": "offline", "textLimit": 100})
        self.assertTrue(result.is_error)
        text = result.content[0].text
        self.assertIn("DJEN request failed: certidão failed at [url] [redacted]", text)
        self.assertNotIn("https://", text)
        self.assertNotIn("secret", text)

    async def test_unexpected_errors_do_not_leak_text(self):
        with mock.patch.object(djen_server, "fetch_certidao", side_effect=RuntimeError("internal detail")):
            result = await self.call("djen_get_certidao_text", {"hash": "offline"})
        self.assertTrue(result.is_error)
        self.assertNotIn("internal detail", result.content[0].text)

    async def test_oab_search_runs_every_variant_and_merges_by_id(self):
        def page(params, pagina, itens):
            found = {"14663": [dict(RAW_ITEM)], "14663-A": [dict(RAW_ITEM), dict(RAW_ITEM, id=2)]}
            items = found.get(params["numeroOab"], [])
            return {"status": "success", "count": len(items), "items": items}

        with mock.patch.object(djen_server, "search_page", side_effect=page) as search:
            result = await self.call(
                "djen_search_communications",
                {"numeroOab": "14663-a", "ufOab": "PB", "inicio": "2026-09-29", "fim": "2026-09-29"},
            )
            exact = await self.call(
                "djen_search_communications",
                {
                    "numeroOab": "14663",
                    "ufOab": "PB",
                    "inicio": "2026-09-29",
                    "fim": "2026-09-29",
                    "oabVariants": False,
                },
            )
        sent = [call.args[0]["numeroOab"] for call in search.call_args_list]
        self.assertEqual(sent[:7], ["14663", "14663-A", "14663-B", "14663-E", "14663-N", "14663-O", "14663-S"])
        self.assertEqual(sent[7:], ["14663"])
        content = result.structured_content
        self.assertEqual([item["id"] for item in content["items"]], [1, 2])
        self.assertEqual(content["count"], 3)
        self.assertEqual(
            [(v["numeroOab"], v["count"]) for v in content["oabVariants"]][:3],
            [("14663", 1), ("14663-A", 2), ("14663-B", 0)],
        )
        self.assertNotIn("oabVariants", exact.structured_content)
        self.assertEqual(len(exact.structured_content["items"]), 1)

    async def test_inverted_window_is_refused_before_any_request(self):
        with mock.patch.object(djen_server, "search_page") as search:
            result = await self.call(
                "djen_search_communications", {"nomeParte": "FULANO", "inicio": "2026-10-03", "fim": "2026-10-01"}
            )
        self.assertTrue(result.is_error)
        search.assert_not_called()

    async def test_deadline_uses_sourced_calendar(self):
        result = await self.call("djen_calculate_deadline", {"availableOn": "2026-10-01", "days": 5})
        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content["availableOn"], "2026-10-01")
        self.assertEqual(len(result.structured_content["countedDates"]), 5)
        self.assertTrue(result.structured_content["calendarComplete"])
        self.assertEqual(result.structured_content["court"], "JFPE")

    async def test_deadline_outside_calendar_is_tool_error(self):
        result = await self.call("djen_calculate_deadline", {"availableOn": "2025-01-01", "days": 5})
        self.assertTrue(result.is_error)
        self.assertIn("outside the verified court calendar", result.content[0].text)

    async def test_deadline_takes_a_court_and_the_legacy_calendar(self):
        legacy = await self.call(
            "djen_calculate_deadline", {"availableOn": "2026-10-01", "days": 5, "calendar": "jfpe-2026-q4"}
        )
        other = await self.call("djen_calculate_deadline", {"availableOn": "2026-10-01", "days": 5, "court": "tjsp"})
        self.assertEqual(legacy.structured_content["calendar"]["id"], "jfpe-2026-q4")
        self.assertEqual(legacy.structured_content["warnings"], [])
        self.assertEqual(other.structured_content["court"], "TJSP")
        self.assertFalse(other.structured_content["calendarComplete"])
        self.assertTrue(any("TJSP" in w for w in other.structured_content["warnings"]))

    async def test_deadline_uses_the_office_court_setting(self):
        with mock.patch.object(djen_server.customers, "settings", return_value={"djen": {"court": "JFPB"}}):
            result = djen_server.deadline(date(2026, 10, 1), 5)
        self.assertEqual(result["court"], "JFPB")
        self.assertFalse(any("Nenhum tribunal" in w for w in result["warnings"]))
        with mock.patch.object(djen_server.customers, "settings", return_value={}):
            default = djen_server.deadline(date(2026, 10, 1), 5)
        self.assertEqual((default["court"], default["courtSource"]), ("JFPE", "default (JFPE)"))
        self.assertTrue(any("Nenhum tribunal" in w for w in default["warnings"]))

    def saved(self, searches, pages, **arguments):
        """Run the saved searches; ``pages`` maps a sent numeroOab to its page, an exception or nothing (empty)."""

        def page(params, pagina, itens):
            found = pages.get(params.get("numeroOab") or params.get("nomeAdvogado"))
            if isinstance(found, Exception):
                raise found
            return found or {"status": "success", "count": 0, "items": []}

        with (
            credentials.scope("office-a", {"DJEN_SEARCHES": json.dumps(searches)}),
            mock.patch.object(djen_server, "search_page", side_effect=page) as search,
        ):
            return djen_server.search_saved(date(2026, 10, 1), date(2026, 10, 2), **arguments), search

    async def test_saved_searches_run_together_without_duplicates(self):
        searches = [
            {"label": "Victor PE", "numeroOab": "42543", "ufOab": "PE"},
            {"label": "Victor PB", "numeroOab": "14663", "ufOab": "PB", "tribunal": "TRF5"},
        ]
        shared = {"id": 7, "numero_processo": "08001234520264058300", "texto": "Intime-se"}
        pages = {
            "42543": {"status": "success", "count": 2, "items": [shared, {"id": 8, "texto": "Cite-se"}]},
            "14663": {"status": "success", "count": 120, "items": [shared]},
            "14663-A": {"status": "success", "count": 1, "items": [{"id": 9, "texto": "Vista"}]},
        }
        result, search = self.saved(searches, pages, itens=50)
        sent = {call.args[0]["numeroOab"]: call.args[0] for call in search.call_args_list}
        self.assertEqual(len(sent), 14)  # seven registration suffixes per OAB
        self.assertEqual(sent["42543"]["ufOab"], "PE")
        self.assertNotIn("siglaTribunal", sent["42543"])
        self.assertEqual(sent["14663-A"]["siglaTribunal"], "TRF5")
        self.assertEqual([item["id"] for item in result["items"]], [7, 8, 9])
        self.assertEqual([(s["new"], s["more"]) for s in result["searches"]], [(2, False), (1, True)])
        self.assertEqual(result["searches"][1]["count"], 121)
        self.assertEqual(
            [(v["numeroOab"], v["count"]) for v in result["searches"][1]["oabVariants"]][:2],
            [("14663", 120), ("14663-A", 1)],
        )
        self.assertTrue(result["complete"])

    async def test_saved_search_sends_the_oab_as_typed_when_variants_are_off(self):
        searches = [{"label": "Victor PB", "numeroOab": "14663", "ufOab": "PB"}]
        result, search = self.saved(searches, {}, oabVariants=False)
        self.assertEqual([call.args[0]["numeroOab"] for call in search.call_args_list], ["14663"])
        self.assertNotIn("oabVariants", result["searches"][0])

    async def test_one_failing_saved_search_keeps_the_others(self):
        searches = [
            {"label": "quebra", "numeroOab": "42543", "ufOab": "PE"},
            {"label": "nome", "nomeAdvogado": "VICTOR HUGO"},
        ]
        pages = {
            "42543-B": DjenError("DJEN request failed after 1 attempt(s): API error: sistema muito ocupado"),
            "VICTOR HUGO": {"status": "success", "count": 1, "items": [{"id": 8, "texto": "Cite-se"}]},
        }
        result, _ = self.saved(searches, pages)
        self.assertFalse(result["complete"])
        self.assertEqual([item["id"] for item in result["items"]], [8])
        broken, fine = result["searches"]
        self.assertIn("sistema muito ocupado", broken["error"])
        self.assertNotIn("error", fine)
        text, _ = self.saved(searches, pages, formato="linhas")
        self.assertFalse(text["complete"])
        self.assertEqual(text["checagem"]["status"], "parcial")
        self.assertIn("quebra=ERRO", text["texto"])
        every = {"42543": DjenError("fora do ar"), "VICTOR HUGO": DjenError("fora do ar")}
        with self.assertRaises(ToolError):
            self.saved(searches, every)

    async def test_saved_searches_carry_the_office_context_into_the_threads(self):
        seen = []

        def page(params, pagina, itens):
            seen.append(credentials.env("DJEN_MARK"))
            return {"status": "success", "count": 0, "items": []}

        searches = json.dumps([{"label": str(n), "nomeAdvogado": f"NOME {n}"} for n in range(6)])
        with (
            credentials.scope("office-a", {"DJEN_SEARCHES": searches, "DJEN_MARK": "office-a"}),
            mock.patch.object(djen_server, "search_page", side_effect=page),
        ):
            djen_server.search_saved(date(2026, 10, 1), date(2026, 10, 1))
        self.assertEqual(seen, ["office-a"] * 6)

    async def test_saved_search_window_is_checked_before_any_request(self):
        searches = json.dumps([{"label": "x", "numeroOab": "1", "ufOab": "PE"}])
        with (
            credentials.scope("office-a", {"DJEN_SEARCHES": searches}),
            mock.patch.object(djen_server, "search_page") as search,
        ):
            with self.assertRaises(ToolError):
                djen_server.search_saved(date(2026, 10, 2), date(2026, 10, 1))
            with self.assertRaises(ToolError) as too_long:
                djen_server.search_saved(date(2026, 1, 1), date(2026, 10, 1))
        self.assertIn("máximo é 31 dias", str(too_long.exception))
        search.assert_not_called()

    async def test_saved_searches_validate_and_court_override(self):
        with credentials.scope("office-a", {"DJEN_SEARCHES": json.dumps([{"numeroOab": "1"}])}):
            with self.assertRaises(ValueError):
                djen_server.saved_searches()
        with credentials.scope("office-a", {"DJEN_SEARCHES": "[]"}), self.assertRaises(ToolError):
            djen_server.search_saved(date(2026, 10, 1), date(2026, 10, 1))
        empty = {"status": "success", "count": 0, "items": []}
        searches = json.dumps([{"label": "x", "numeroOab": "1", "ufOab": "PE", "tribunal": "TRF5"}])
        with (
            credentials.scope("office-a", {"DJEN_SEARCHES": searches}),
            mock.patch.object(djen_server, "search_page", return_value=empty) as search,
        ):
            djen_server.search_saved(date(2026, 10, 1), date(2026, 10, 1), tribunal="TRF1")
        self.assertEqual(search.call_args.args[0]["siglaTribunal"], "TRF1")

    def test_victor_profile_has_his_three_oabs(self):
        with credentials.scope("victor", {"JURISYNC_CUSTOMER": "victor"}):
            searches = djen_server.saved_searches()
        self.assertEqual(
            {(s["numeroOab"], s["ufOab"]) for s in searches}, {("42543", "PE"), ("14663", "PB"), ("48647", "PE")}
        )

    async def test_stdio_entrypoint_starts(self):
        params = StdioServerParameters(command=sys.executable, args=["-m", "jurisync.connectors.djen.server"])
        async with Client(params) as client:
            tools = (await client.list_tools()).tools
        self.assertEqual(len(tools), 5)


if __name__ == "__main__":
    unittest.main()
