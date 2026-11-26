import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mcp.client.client import Client

from jurisync.core import credentials
from jurisync.core.documents_dir import save_folder_file
from jurisync.workflows.filing import server as filing
from jurisync.workflows.filing.profile import load_profile, profile_names

REVIEW = {
    "source": "docs1",
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
        async with Client(filing.create_server()) as client:
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
        documents = tempfile.TemporaryDirectory()
        self.addCleanup(documents.cleanup)
        documents_env = mock.patch.dict(os.environ, {"JURISYNC_DOCUMENTS_DIR": documents.name})
        documents_env.start()
        self.addCleanup(documents_env.stop)
        for name in ("a.docx", "b.pdf"):
            save_folder_file("docs1", name, b"x")

    def fake_prepare(self, calls):
        def prepare(files, facts, out_dir, *, profile, source="", jev=True):
            calls.append(
                {"files": sorted(Path(f).name for f in files), "source": source, "jev": jev, "profile": profile.name}
            )
            Path(out_dir, "review.json").write_text(json.dumps(REVIEW))
            return {"ready": False}

        return mock.patch.object(filing, "prepare_folder", prepare)

    def test_tools_are_registered_and_only_prepare_and_finalize_write(self):
        async def listing():
            async with Client(filing.create_server()) as client:
                return {tool.name: tool.annotations.read_only_hint for tool in (await client.list_tools()).tools}

        self.assertEqual(
            asyncio.run(listing()),
            {
                "filing_prepare": False,
                "filing_review": True,
                "filing_finalize": False,
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
            result = call(
                "filing_prepare", {"profile": "nobody", "runId": "run1", "sourceFolder": "docs1", "facts": {}}
            )
        self.assertTrue(result.is_error)
        self.assertIn("No filing profile named nobody", result.content[0].text)
        self.assertEqual(calls, [])

    def test_prepare_returns_ids_and_problems_without_paths_or_hashes_and_keeps_jev_off(self):
        calls = []
        with self.fake_prepare(calls):
            result = call(
                "filing_prepare", {"profile": "victor", "runId": "run1", "sourceFolder": "docs1", "facts": {"a": 1}}
            )
        view = result.structured_content
        self.assertEqual(calls, [{"files": ["a.docx", "b.pdf"], "source": "docs1", "jev": False, "profile": "victor"}])
        self.assertEqual([file["id"] for file in view["files"]], ["aaa:0", "bbb:1"])
        self.assertEqual(view["candidates"]["cpfs"], ["12345678909"])
        text = json.dumps(view)
        for hidden in ("/secret", "sha256", "uploadSha256"):
            self.assertNotIn(hidden, text)
        self.assertEqual(json.loads(self.run_dir.joinpath("facts.json").read_text()), {"a": 1})

    def test_a_source_folder_that_was_not_pulled_is_a_clear_error(self):
        calls = []
        with self.fake_prepare(calls):
            result = call("filing_prepare", {"profile": "victor", "runId": "run1", "sourceFolder": "nope", "facts": {}})
        self.assertTrue(result.is_error)
        self.assertEqual(calls, [])

    def test_jev_is_only_used_when_asked(self):
        calls = []
        with self.fake_prepare(calls):
            call(
                "filing_prepare",
                {"profile": "victor", "runId": "run1", "sourceFolder": "docs1", "facts": {}, "useJev": True},
            )
        self.assertTrue(calls[0]["jev"])

    def test_a_run_with_a_draft_cannot_be_prepared_or_finalized_again(self):
        self.run_dir.mkdir()
        self.run_dir.joinpath("checkpoint.json").write_text("{}")
        self.run_dir.joinpath("review.json").write_text(json.dumps(REVIEW))
        for name, arguments in (
            ("filing_prepare", {"profile": "victor", "runId": "run1", "sourceFolder": "docs1", "facts": {}}),
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

    def test_subjects_come_from_the_profiles_own_reference_files(self):
        view = call("filing_subjects", {"profile": "victor", "lawsuitRef": "17519594"}).structured_content
        self.assertGreaterEqual(len(view["candidates"]), 8)
        self.assertTrue(all({"benefit", "code", "name", "search"} <= set(row) for row in view["candidates"]))
        self.assertTrue(any("17519594" in paragraph for paragraph in view["history"]))
        self.assertEqual(call("filing_subjects", {"profile": "victor"}).structured_content["history"], [])

    def test_prepare_that_ends_in_review_does_not_report_an_earlier_payload(self):
        self.run_dir.mkdir()
        self.run_dir.joinpath("payload.json").write_text('{"stale": true}')
        with self.fake_prepare([]):
            result = call(
                "filing_prepare", {"profile": "victor", "runId": "run1", "sourceFolder": "docs1", "facts": {}}
            )
        self.assertFalse(result.structured_content["payloadReady"])
        self.assertFalse(self.run_dir.joinpath("payload.json").exists())
        self.assertFalse(call("filing_review", {"runId": "run1"}).structured_content["payloadReady"])

    def test_prepare_that_fails_leaves_no_payload(self):
        self.run_dir.mkdir()
        self.run_dir.joinpath("payload.json").write_text('{"stale": true}')
        result = call("filing_prepare", {"profile": "victor", "runId": "run1", "sourceFolder": "nope", "facts": {}})
        self.assertTrue(result.is_error)
        self.assertFalse(self.run_dir.joinpath("payload.json").exists())

    def test_finalize_that_raises_leaves_no_payload(self):
        self.run_dir.mkdir()
        self.run_dir.joinpath("review.json").write_text(json.dumps(REVIEW))
        self.run_dir.joinpath("facts.json").write_text("{}")
        self.run_dir.joinpath("payload.json").write_text('{"stale": true}')

        def finalize(review_path, facts, overrides=None, *, profile):
            raise ValueError("Expected one main petition, found 2")

        with mock.patch.object(filing, "finalize_review", finalize):
            result = call("filing_finalize", {"profile": "victor", "runId": "run1"})
        self.assertTrue(result.is_error)
        self.assertIn("Expected one main petition", result.content[0].text)
        self.assertFalse(self.run_dir.joinpath("payload.json").exists())
        self.assertFalse(call("filing_review", {"runId": "run1"}).structured_content["payloadReady"])

    def test_finalize_overrides_cannot_replace_a_documents_path(self):
        self.run_dir.mkdir()
        self.run_dir.joinpath("review.json").write_text(json.dumps(REVIEW))
        self.run_dir.joinpath("facts.json").write_text("{}")
        calls = []
        with mock.patch.object(filing, "finalize_review", lambda *args, **kwargs: calls.append(args)):
            result = call(
                "filing_finalize",
                {"profile": "victor", "runId": "run1", "overrides": {"bbb:1": {"role": "main", "path": "/etc/hosts"}}},
            )
        self.assertTrue(result.is_error)
        self.assertIn("only set role and type", result.content[0].text)
        self.assertEqual(calls, [])


class OfficeScopeTests(unittest.TestCase):
    """With a customer selected for the process, only that office's profile and notes are reachable."""

    OWN = "Lawsuit `1234567` filed in 2026 with subject 6101."
    OTHER = "Lawsuit 91234567 of another client, 2026-10-06, CNJ 0001234-56.2026.4.05.8300."
    HISTORY = f"# Notes\n\n{OWN}\n\n{OTHER}\n\nGeneral note written in 2026 about nothing.\n"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for name in ("alpha", "beta"):
            folder = Path(tmp.name, name)
            folder.mkdir()
            profile = {
                "portal": "trf5-1g",
                "description": f"Office {name}",
                "folderPatterns": ["PROTOCOLAR$"],
                "defaults": {"attorney": {"cpfEnv": []}, "passive": {}},
                "references": {"history": "notes.md"},
            }
            folder.joinpath("filing-profile.json").write_text(json.dumps(profile))
            folder.joinpath("notes.md").write_text(self.HISTORY)
        self.environment({"JURISYNC_PROFILES_DIR": tmp.name, "JURISYNC_FILING_DIR": tmp.name + "/runs"})

    def environment(self, values):
        patcher = mock.patch.dict(os.environ, values)
        patcher.start()
        self.addCleanup(patcher.stop)

    def names(self):
        return [item["name"] for item in call("filing_profiles", {}).structured_content["profiles"]]

    def test_without_a_customer_every_profile_is_available(self):
        self.assertEqual(self.names(), ["alpha", "beta"])
        self.assertFalse(call("filing_subjects", {"profile": "beta"}).is_error)

    def test_a_selected_customer_sees_and_uses_only_its_own_profile(self):
        self.environment({"JURISYNC_CUSTOMER": "alpha"})
        self.assertEqual(self.names(), ["alpha"])
        self.assertFalse(call("filing_subjects", {"profile": "alpha"}).is_error)
        for name, arguments in (
            ("filing_subjects", {"profile": "beta"}),
            ("filing_prepare", {"profile": "beta", "runId": "run1", "sourceFolder": "docs1", "facts": {}}),
            ("filing_finalize", {"profile": "beta", "runId": "run1", "facts": {}}),
        ):
            result = call(name, arguments)
            self.assertTrue(result.is_error, name)
            self.assertNotIn("alpha", result.content[0].text)
        self.assertIn("not available to this office", call("filing_subjects", {"profile": "beta"}).content[0].text)

    def test_a_hosted_office_without_a_customer_has_no_profiles(self):
        with credentials.scope("tenant-1", {}):
            self.assertEqual(profile_names(), [])
            with self.assertRaisesRegex(ValueError, "not available to this office"):
                load_profile("alpha")
        with credentials.scope("tenant-1", {"JURISYNC_CUSTOMER": "beta"}):
            self.assertEqual(profile_names(), ["beta"])
            self.assertEqual(load_profile("beta").name, "beta")

    def test_history_is_matched_by_the_whole_lawsuit_reference(self):
        def history(reference):
            result = call("filing_subjects", {"profile": "alpha", "lawsuitRef": reference})
            return [] if result.is_error else result.structured_content["history"]

        self.assertEqual(history("1234567"), [self.OWN])
        self.assertEqual(history("91234567"), [self.OTHER])
        self.assertEqual(history("0001234-56.2026.4.05.8300"), [self.OTHER])
        self.assertEqual(history("00012345620264058300"), [self.OTHER])
        for partial in ("2026", "234567", "123456", "20261006", "Lawsuit"):
            self.assertEqual(history(partial), [], partial)
        refused = call("filing_subjects", {"profile": "alpha", "lawsuitRef": "2026"})
        self.assertTrue(refused.is_error)
        self.assertIn("whole lawsuit id or CNJ number", refused.content[0].text)


if __name__ == "__main__":
    unittest.main()
