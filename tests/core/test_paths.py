import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jurisync import paths


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


if __name__ == "__main__":
    unittest.main()
