import asyncio
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mcp.client.client import Client

from jurisync.workflows.filing import server as filing

REVIEW = {
    "customerId": "1",
    "folder": "00-PROTOCOLAR",
    "ready": False,
    "files": [
        {
            "id": "aaa:0",
            "name": "0-PA_1.pdf",
            "path": "/secret/dir/0-PA.pdf",
            "sha256": "x" * 64,
            "uploadSha256": "y" * 64,
            "role": "supporting",
            "type": "Processo Administrativo",
            "method": "filename",
            "pages": 3,
        },
        {"id": "bbb:1", "name": "peticao.docx", "path": "/secret/dir/p.pdf", "role": "main", "type": "Petição inicial"},
    ],
    "review": [{"name": "petition", "reason": "Expected one main petition, found 2"}],
    "candidates": {"claimValues": ["1.000,00"], "cpfs": ["12345678909"]},
    "fallbacks": [],
}


def call(name, arguments):
    async def run():
        async with Client(filing.server) as client:
            return await client.call_tool(name, arguments)

    return asyncio.run(run())


class FilingServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"JURISYNC_FILING_DIR": self.tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.run_dir = Path(self.tmp.name, "run1")

    def fake_prepare(self, calls):
        def prepare(customer_id, facts, out_dir, *, profile, folder=None, client=None, jev=True):
            calls.append({"customer": customer_id, "folder": folder, "jev": jev, "profile": profile.name})
            Path(out_dir, "review.json").write_text(json.dumps(REVIEW))
            return {"ready": False}

        return mock.patch.object(filing, "prepare_customer", prepare)

    def test_tools_are_registered_and_only_prepare_and_finalize_write(self):
        async def listing():
            async with Client(filing.server) as client:
                return {tool.name: tool.annotations.read_only_hint for tool in (await client.list_tools()).tools}

        self.assertEqual(
            asyncio.run(listing()),
            {
                "filing_prepare": False,
                "filing_review": True,
                "filing_finalize": False,
                "filing_approve": False,
                "filing_profiles": True,
                "filing_subjects": True,
            },
        )

    def test_profiles_lists_what_customers_provide_without_secrets(self):
        listed = call("filing_profiles", {}).structured_content["profiles"]
        victor = next(item for item in listed if item["name"] == "victor")
        self.assertEqual(victor["portal"], "trf5-1g")
        self.assertEqual(set(victor), {"name", "portal", "description"})

    def test_an_unknown_profile_is_rejected_before_any_work(self):
        calls = []
        with self.fake_prepare(calls):
            result = call("filing_prepare", {"profile": "nobody", "runId": "run1", "customerId": "1", "facts": {}})
        self.assertTrue(result.is_error)
        self.assertIn("No filing profile named nobody", result.content[0].text)
        self.assertEqual(calls, [])

    def test_prepare_returns_ids_and_problems_without_paths_or_hashes_and_keeps_jev_off(self):
        calls = []
        with self.fake_prepare(calls):
            result = call(
                "filing_prepare", {"profile": "victor", "runId": "run1", "customerId": "1", "facts": {"a": 1}}
            )
        view = result.structured_content
        self.assertEqual(calls, [{"customer": "1", "folder": None, "jev": False, "profile": "victor"}])
        self.assertEqual([file["id"] for file in view["files"]], ["aaa:0", "bbb:1"])
        self.assertEqual(view["candidates"]["cpfs"], ["12345678909"])
        text = json.dumps(view)
        for hidden in ("/secret", "sha256", "uploadSha256"):
            self.assertNotIn(hidden, text)
        self.assertEqual(json.loads(self.run_dir.joinpath("facts.json").read_text()), {"a": 1})

    def test_jev_is_only_used_when_asked(self):
        calls = []
        with self.fake_prepare(calls):
            call(
                "filing_prepare",
                {"profile": "victor", "runId": "run1", "customerId": "1", "facts": {}, "useJev": True},
            )
        self.assertTrue(calls[0]["jev"])

    def test_a_run_with_a_draft_cannot_be_prepared_or_finalized_again(self):
        self.run_dir.mkdir()
        self.run_dir.joinpath("checkpoint.json").write_text("{}")
        self.run_dir.joinpath("review.json").write_text(json.dumps(REVIEW))
        for name, arguments in (
            ("filing_prepare", {"profile": "victor", "runId": "run1", "customerId": "1", "facts": {}}),
            ("filing_finalize", {"profile": "victor", "runId": "run1", "facts": {}}),
        ):
            result = call(name, arguments)
            self.assertTrue(result.is_error, name)
            self.assertIn("new runId", result.content[0].text)

    def test_finalize_rejects_overrides_for_unknown_files_and_masks_the_cpf(self):
        self.run_dir.mkdir()
        self.run_dir.joinpath("review.json").write_text(json.dumps(REVIEW))
        self.run_dir.joinpath("facts.json").write_text("{}")
        bad = call("filing_finalize", {"profile": "victor", "runId": "run1", "overrides": {"nope:9": {"role": "skip"}}})
        self.assertTrue(bad.is_error)
        self.assertIn("nope:9", bad.content[0].text)

        payload = {
            "classification": {"area": "X"},
            "subjects": [{"code": "6101", "name": "Auxílio"}],
            "authorCpf": "12345678909",
            "author": {"name": "EXAMPLE", "address": {"cep": "56800-000", "numero": "15"}},
            "documents": {"main": {"path": "/secret/dir/p.pdf", "type": "Petição inicial", "description": "Petição"}},
        }

        def finalize(review_path, facts, overrides=None, *, profile):
            Path(review_path).with_name("payload.json").write_text(json.dumps(payload))
            return {"ready": True}

        with mock.patch.object(filing, "finalize_review", finalize):
            ok = call(
                "filing_finalize",
                {"profile": "victor", "runId": "run1", "overrides": {"bbb:1": {"role": "main"}}},
            )
        view = ok.structured_content["payload"]
        self.assertEqual(view["author"]["cpf"], "123.***.***-09")
        self.assertEqual(view["documents"][0]["name"], "p.pdf")
        self.assertNotIn("/secret", json.dumps(ok.structured_content))

    def test_approve_records_the_payload_and_document_hashes_and_voids_on_any_change(self):
        self.run_dir.mkdir()
        self.run_dir.joinpath("review.json").write_text(json.dumps(REVIEW))
        self.assertTrue(call("filing_approve", {"runId": "run1"}).is_error)
        petition = self.run_dir / "petition.pdf"
        petition.write_bytes(b"%PDF-1 petition")
        sha = hashlib.sha256(petition.read_bytes()).hexdigest()
        payload = {
            "subjects": [{"search": "s", "code": "1", "name": "S"}],
            "authorCpf": "12345678909",
            "author": {"name": "EXAMPLE"},
            "documents": {"main": {"path": str(petition), "type": "Petição inicial", "sha256": sha}},
        }
        payload_path = self.run_dir / "payload.json"
        payload_path.write_text(json.dumps(payload))
        self.assertFalse(call("filing_review", {"runId": "run1"}).structured_content["approved"])

        result = call("filing_approve", {"runId": "run1"}).structured_content
        self.assertTrue(result["approved"])
        self.assertEqual(result["documents"], [{"name": "petition.pdf", "type": "Petição inicial"}])
        approval_path = self.run_dir / "approval.json"
        approval = json.loads(approval_path.read_text())
        self.assertEqual(approval["payloadSha256"], hashlib.sha256(payload_path.read_bytes()).hexdigest())
        self.assertEqual(approval["documents"][0]["sha256"], sha)
        self.assertEqual(approval_path.stat().st_mode & 0o777, 0o600)
        self.assertTrue(call("filing_review", {"runId": "run1"}).structured_content["approved"])

        payload_path.write_text(json.dumps({**payload, "authorCpf": "98765432100"}))
        self.assertFalse(call("filing_review", {"runId": "run1"}).structured_content["approved"])
        payload_path.write_text(json.dumps(payload))
        petition.write_bytes(b"%PDF-1 edited after review")
        refused = call("filing_approve", {"runId": "run1"})
        self.assertTrue(refused.is_error)
        self.assertIn("changed since approval", refused.content[0].text)

    def test_finalize_drops_an_earlier_approval(self):
        self.run_dir.mkdir()
        self.run_dir.joinpath("review.json").write_text(json.dumps(REVIEW))
        self.run_dir.joinpath("facts.json").write_text("{}")
        self.run_dir.joinpath("approval.json").write_text("{}")

        def finalize(review_path, facts, overrides=None, *, profile):
            Path(review_path).with_name("payload.json").write_text(json.dumps({"author": {"name": "X"}}))
            return {"ready": True}

        with mock.patch.object(filing, "finalize_review", finalize):
            result = call("filing_finalize", {"profile": "victor", "runId": "run1"}).structured_content
        self.assertFalse(result["approved"])
        self.assertIn("filing_approve", result["next"])
        self.assertFalse(self.run_dir.joinpath("approval.json").exists())

    def test_subjects_come_from_the_profiles_own_reference_files(self):
        view = call("filing_subjects", {"profile": "victor", "lawsuitRef": "17519594"}).structured_content
        self.assertGreaterEqual(len(view["candidates"]), 8)
        self.assertTrue(all({"benefit", "code", "name", "search"} <= set(row) for row in view["candidates"]))
        self.assertTrue(any("17519594" in paragraph for paragraph in view["history"]))
        self.assertEqual(call("filing_subjects", {"profile": "victor"}).structured_content["history"], [])


if __name__ == "__main__":
    unittest.main()
