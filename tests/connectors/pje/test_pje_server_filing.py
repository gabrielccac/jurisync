"""The PJe MCP server offers the filing preparation tools, so a filing runs end to end through one server."""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mcp.client.client import Client

from jurisync.connectors.pje import server as pje
from jurisync.workflows.filing import server as filing

FILING_TOOLS = {
    "filing_profiles",
    "filing_subjects",
    "filing_prepare",
    "filing_review",
    "filing_finalize",
    "filing_approve",
}
FACTS = {
    "authorCpf": "12345678909",
    "author": {"name": "EXAMPLE"},
    "classification": {"jurisdiction": "Seção Judiciária do Amapá"},
    "characteristics": {"claimValue": "5.000,00"},
}


async def _tools(server):
    async with Client(server) as client:
        return {tool.name: tool for tool in (await client.list_tools()).tools}


async def _calls(server, *calls):
    async with Client(server) as client:
        return [await client.call_tool(name, arguments) for name, arguments in calls]


class PjeServerFilingToolsTests(unittest.TestCase):
    def test_the_pje_server_offers_the_same_filing_tools(self):
        on_pje, on_filing = asyncio.run(_tools(pje.server)), asyncio.run(_tools(filing.server))
        self.assertEqual(FILING_TOOLS & set(on_pje), FILING_TOOLS)
        self.assertEqual(set(on_filing), FILING_TOOLS)
        for name in FILING_TOOLS:
            self.assertEqual(on_pje[name].description, on_filing[name].description, name)
            self.assertEqual(on_pje[name].input_schema, on_filing[name].input_schema, name)
        self.assertIn("never on your own", on_pje["filing_approve"].description)

    def test_prepare_then_write_needs_the_recorded_approval(self):
        started = []
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp, "input")
            folder.mkdir()
            folder.joinpath("Inicial BPC - Exemplo.pdf").write_bytes(b"%PDF-1.4\npetition")
            env = {"JURISYNC_FILING_DIR": str(Path(tmp, "runs")), "PJE_ALLOW_DRAFT_WRITES": "true"}
            run = {"runId": "case1"}
            with (
                mock.patch.dict(os.environ, env),
                mock.patch("jurisync.workflows.filing.prepare._petition_candidates", lambda path: ([], [], [])),
                mock.patch.object(pje.filing, "run_stage", lambda stage, **options: started.append(stage)),
            ):
                prepared, refused, approved, write = asyncio.run(
                    _calls(
                        pje.server,
                        ("filing_prepare", {"profile": "jean", **run, "facts": FACTS, "sourceFolder": str(folder)}),
                        ("pje_draft_write", {**run, "stage": "create-fill"}),
                        ("filing_approve", run),
                        ("pje_draft_write", {**run, "stage": "create-fill"}),
                    )
                )
                pje.fill_jobs["case1"].join(5)
        self.assertTrue(prepared.structured_content["payloadReady"], prepared.content)
        self.assertTrue(refused.is_error)
        self.assertIn("not been approved", refused.content[0].text)
        self.assertTrue(approved.structured_content["approved"])
        self.assertEqual(write.structured_content["state"], "started")
        self.assertEqual(started, ["create-fill"])


if __name__ == "__main__":
    unittest.main()
