import sys
import unittest
from unittest import mock

from mcp.client.client import Client
from mcp.client.stdio import StdioServerParameters

from jurisync.connectors.djen import server as djen_server
from jurisync.connectors.djen.client import DjenError

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
            ["djen_calculate_deadline", "djen_get_certidao_text", "djen_search_communications"],
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

    async def test_search_normalizes_items(self):
        page = {"status": "success", "count": 1, "items": [dict(RAW_ITEM)]}
        with mock.patch.object(djen_server, "search_page", return_value=page) as search:
            result = await self.call(
                "djen_search_communications",
                {"numeroOab": "14663", "ufOab": "PB", "inicio": "2026-09-29", "fim": "2026-09-30", "tribunal": "TRF5"},
            )
        self.assertFalse(result.is_error)
        params, pagina, itens = search.call_args.args
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

    async def test_deadline_uses_sourced_calendar(self):
        result = await self.call("djen_calculate_deadline", {"availableOn": "2026-10-01", "days": 5})
        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content["availableOn"], "2026-10-01")
        self.assertEqual(len(result.structured_content["countedDates"]), 5)

    async def test_deadline_outside_calendar_is_tool_error(self):
        result = await self.call("djen_calculate_deadline", {"availableOn": "2025-01-01", "days": 5})
        self.assertTrue(result.is_error)
        self.assertIn("outside the verified court calendar", result.content[0].text)

    async def test_stdio_entrypoint_starts(self):
        params = StdioServerParameters(command=sys.executable, args=["-m", "jurisync.connectors.djen.server"])
        async with Client(params) as client:
            tools = (await client.list_tools()).tools
        self.assertEqual(len(tools), 3)


if __name__ == "__main__":
    unittest.main()
