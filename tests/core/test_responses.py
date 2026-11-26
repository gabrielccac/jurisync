"""Answers stay within the budget, say how to read the rest, and go out as compact JSON."""

import asyncio
import json
import unittest
from typing import Any
from unittest import mock

from mcp.types import CallToolResult, TextContent

from jurisync.connectors.pje import server as pje_server
from jurisync.core import responses
from jurisync.core.mcp import JurisyncServer

NUMBER = "0000001-00.2026.4.05.8300"


class FitTextsTest(unittest.TestCase):
    def test_short_texts_keep_everything_and_long_ones_share_the_rest(self):
        reads = [{"text": "a" * 100}, {"text": "b" * 40_000}, {"text": "c" * 40_000}]
        self.assertTrue(responses.fit_texts(reads, budget=50_000))
        self.assertEqual([len(read["text"]) for read in reads], [100, 24_950, 24_950])
        self.assertNotIn("truncated", reads[0])
        self.assertEqual(reads[1]["truncated"], {"shown": 24_950, "total": 40_000, "nextOffset": 24_950})
        self.assertTrue(reads[1]["textTruncated"])

    def test_the_offset_reads_the_rest(self):
        reads = [{"text": "0123456789"}]
        self.assertFalse(responses.fit_texts(reads, offset=4, budget=50_000))
        self.assertEqual(reads[0]["text"], "456789")

    def test_without_an_offset_no_next_offset_is_promised_and_an_earlier_cut_is_kept(self):
        reads = [{"text": "x" * 30_000, "textTruncated": True}, {"text": "y" * 30_000}, {"text": "z" * 30_000}]
        self.assertEqual(responses.fitted(reads)["note"][:16], "Texts were cut t")
        self.assertNotIn("nextOffset", reads[1]["truncated"])
        self.assertTrue(reads[0]["textTruncated"])

    def test_a_list_has_the_common_envelope(self):
        self.assertEqual(
            responses.listing([1, 2], key="processes", total=7, more="page=2"),
            {"processes": [1, 2], "returned": 2, "total": 7, "next": "page=2"},
        )


class CompactTextTest(unittest.TestCase):
    def test_the_text_copy_is_the_structured_content_without_indentation(self):
        server = JurisyncServer("exemplo")

        @server.tool(name="exemplo_get_process")
        def exemplo_get_process() -> dict[str, Any]:
            return {"success": True, "parties": [{"name": "Parte Fictícia"}]}

        result = asyncio.run(server.call_tool("exemplo_get_process", {}))
        self.assertEqual(result.content[0].text, '{"success":true,"parties":[{"name":"Parte Fictícia"}]}')
        self.assertEqual(json.loads(result.content[0].text), result.structured_content)

    def test_extra_blocks_such_as_extracted_markdown_are_kept(self):
        server = JurisyncServer("exemplo")

        @server.tool(name="exemplo_download_document")
        def exemplo_download_document() -> CallToolResult:
            return CallToolResult(
                content=[
                    TextContent(type="text", text='{\n  "saved": true,\n  "textReturned": true\n}'),
                    TextContent(type="text", text="# Texto"),
                ],
                structured_content={"saved": True, "textReturned": True},
            )

        result = asyncio.run(server.call_tool("exemplo_download_document", {}))
        self.assertEqual([block.text for block in result.content], ['{"saved":true,"textReturned":true}', "# Texto"])

    def test_a_text_copy_that_differs_from_the_structured_content_is_left_alone(self):
        server = JurisyncServer("exemplo")

        @server.tool(name="exemplo_download_document")
        def exemplo_download_document() -> CallToolResult:
            return CallToolResult(
                content=[TextContent(type="text", text='{"saved": true}')],
                structured_content={"saved": True, "textReturned": True},
            )

        result = asyncio.run(server.call_tool("exemplo_download_document", {}))
        self.assertEqual(result.content[0].text, '{"saved": true}')


class FittedTest(unittest.TestCase):
    def test_a_document_read_alone_keeps_the_connector_cap(self):
        reads = [{"text": "x" * 55_000}]
        self.assertEqual(responses.fitted(reads), {})
        self.assertEqual(len(reads[0]["text"]), 55_000)

    def test_several_documents_share_the_text_budget(self):
        reads = [{"text": "x" * 60_000}, {"text": "y" * 60_000}]
        self.assertIn("note", responses.fitted(reads))
        self.assertEqual(sum(len(read["text"]) for read in reads), responses.TEXT_BUDGET)


class PjeReadDocumentsBudgetTest(unittest.TestCase):
    """The worst case of JURI-12: five documents of 100k characters, each with three annexes of 100k."""

    def read(self, **arguments):
        def document(index):
            anexos = [{"documentId": f"{index}{n}", "text": "a" * 100_000} for n in range(3)]
            return {"documentId": str(index), "text": "d" * 100_000, "anexos": anexos}

        result = {"success": True, "documents": [document(index) for index in range(1, 6)]}
        arguments = {
            "processNumber": NUMBER,
            "portal": "trf5-1g",
            "documentIds": ["1", "2", "3", "4", "5"],
            **arguments,
        }
        with (
            mock.patch.object(pje_server, "require_own_number"),
            mock.patch.object(pje_server.sessions, "run", return_value=result),
        ):
            return asyncio.run(pje_server.create_server().call_tool("pje_read_documents", arguments))

    def test_the_answer_fits_the_budget_and_says_how_to_continue(self):
        answer = self.read(textLimit=100_000)
        self.assertLessEqual(len(answer.content[0].text), responses.BUDGET)
        first = answer.structured_content["documents"][0]
        self.assertEqual(first["truncated"]["nextOffset"], len(first["text"]))
        self.assertIn("offset=truncated.nextOffset", answer.structured_content["note"])

    def test_the_offset_continues_where_the_text_stopped(self):
        answer = self.read(textLimit=100_000, offset=99_990)
        self.assertEqual(answer.structured_content["documents"][0]["text"], "d" * 10)
        self.assertNotIn("note", answer.structured_content)


if __name__ == "__main__":
    unittest.main()
