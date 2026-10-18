import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jurisync import paths
from jurisync.connectors.pje.filing.store import open_run


class CustomersRootTests(unittest.TestCase):
    def setUp(self):
        paths.installed_from.cache_clear()
        self.addCleanup(paths.installed_from.cache_clear)

    def test_the_variable_wins_then_the_checkout(self):
        with mock.patch.dict(os.environ, {"JURISYNC_CUSTOMERS_DIR": "~/elsewhere"}):
            self.assertEqual(paths.customers_root(), Path("~/elsewhere").expanduser())
        with mock.patch.dict(os.environ, {"JURISYNC_CUSTOMERS_DIR": ""}):
            self.assertEqual(paths.customers_root(), paths.REPO_ROOT / "customers")

    def test_an_installed_package_finds_the_folder_it_was_installed_from(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp, "my checkout")
            (source / "customers").mkdir(parents=True)
            record = json.dumps({"url": source.as_uri(), "dir_info": {}})
            distribution = mock.Mock(read_text=lambda name: record)
            with (
                mock.patch.dict(os.environ, {"JURISYNC_CUSTOMERS_DIR": ""}),
                mock.patch.object(paths, "REPO_ROOT", Path(tmp, "site-packages")),
                mock.patch.object(paths.metadata, "distribution", return_value=distribution),
            ):
                self.assertEqual(paths.installed_from(), source)
                self.assertEqual(paths.customers_root(), source / "customers")
                paths.installed_from.cache_clear()
                distribution.read_text = lambda name: json.dumps({"url": "https://pypi.org/x.whl", "archive_info": {}})
                with mock.patch.object(paths, "USER_CUSTOMERS_DIR", Path(tmp)):
                    self.assertEqual(paths.customers_root(), Path(tmp), "an index install falls back to ~/.config")


class FilingFolderTests(unittest.TestCase):
    def test_legacy_runs_and_inputs_move_under_the_new_names_and_a_taken_name_stays(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            for legacy, entry in (
                ("filings", "case-a"),
                ("filings", "case-b"),
                ("filing-input", "in-1"),
                ("intake", "x"),
            ):
                home.joinpath(legacy, entry).mkdir(parents=True)
                home.joinpath(legacy, entry, "payload.json").write_text("{}")
            home.joinpath("intake", "facts.json").write_text("{}")
            runs, inputs = home / "filing" / "runs", home / "filing" / "inputs"
            runs.joinpath("case-b").mkdir(parents=True)
            inputs.mkdir()
            self.assertEqual(paths.migrate_legacy_filing_folder("runs", runs, home), (["case-a"], ["filings/case-b"]))
            self.assertEqual(
                paths.migrate_legacy_filing_folder("inputs", inputs, home), (["in-1", "facts.json", "x"], [])
            )
            self.assertEqual(runs.joinpath("case-a", "payload.json").stat().st_mode & 0o777, 0o600)
            self.assertEqual(inputs.joinpath("in-1").stat().st_mode & 0o777, 0o700)
            self.assertTrue(home.joinpath("filings", "case-b").is_dir(), "a taken name is left where it was")
            self.assertFalse(home.joinpath("filing-input").exists() or home.joinpath("intake").exists())

    def test_runs_live_under_the_configured_root(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"JURISYNC_FILING_DIR": tmp}):
            self.assertEqual(open_run("case1").root, Path(tmp, "runs", "case1").resolve())
            self.assertEqual(Path(tmp, "runs").stat().st_mode & 0o777, 0o700)


if __name__ == "__main__":
    unittest.main()
