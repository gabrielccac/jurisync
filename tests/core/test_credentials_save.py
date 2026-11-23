import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jurisync.core import credentials


class SaveTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)

    def test_locally_only_that_line_of_the_loaded_file_changes_and_it_stays_private(self):
        path = self.folder / ".env"
        path.write_text("# ADVBOX_DEVICE_ID=commented\nADVBOX_EMAIL=a@b.c\nexport ADVBOX_DEVICE_ID=old\nOTHER='x y'\n")
        path.chmod(0o644)
        with mock.patch.dict(os.environ, {"JURISYNC_ENV_FILE": str(path), "JURISYNC_CUSTOMER": ""}):
            credentials.save("ADVBOX_DEVICE_ID", "f" * 32)
            self.assertEqual(os.environ["ADVBOX_DEVICE_ID"], "f" * 32)
            credentials.save("NEW_SETTING", "with space")
        self.assertEqual(
            path.read_text(),
            "# ADVBOX_DEVICE_ID=commented\nADVBOX_EMAIL=a@b.c\nexport ADVBOX_DEVICE_ID="
            + "f" * 32
            + "\nOTHER='x y'\nNEW_SETTING=\"with space\"\n",
        )
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(sorted(item.name for item in self.folder.iterdir()), [".env"])

    def test_a_value_with_a_line_break_is_refused_and_the_file_is_untouched(self):
        path = self.folder / ".env"
        path.write_text("ADVBOX_EMAIL=a@b.c\n")
        with mock.patch.dict(os.environ, {"JURISYNC_ENV_FILE": str(path), "JURISYNC_CUSTOMER": ""}):
            for value in ("abc\nJURISYNC_X=1", "abc\rJURISYNC_X=1"):
                with self.subTest(value=value), self.assertRaisesRegex(ValueError, "ADVBOX_DEVICE_ID must be a single"):
                    credentials.save("ADVBOX_DEVICE_ID", value)
            self.assertNotIn("ADVBOX_DEVICE_ID", os.environ)
        self.assertEqual(path.read_text(), "ADVBOX_EMAIL=a@b.c\n")

    def test_the_file_is_written_through_a_new_temporary_file_never_a_fixed_name(self):
        path = self.folder / ".env"
        path.write_text("ADVBOX_EMAIL=a@b.c\n")
        elsewhere = self.folder / "elsewhere"
        elsewhere.write_text("keep\n")
        (self.folder / ".env.tmp").symlink_to(elsewhere)  # a leftover (or planted) file at the old fixed name
        with mock.patch.dict(os.environ, {"JURISYNC_ENV_FILE": str(path), "JURISYNC_CUSTOMER": ""}):
            credentials.save("ADVBOX_DEVICE_ID", "abc")
            with mock.patch("os.replace", side_effect=OSError), self.assertRaises(OSError):
                credentials.save("ADVBOX_DEVICE_ID", "def")
        self.assertEqual(path.read_text(), "ADVBOX_EMAIL=a@b.c\nADVBOX_DEVICE_ID=abc\n")
        self.assertEqual(elsewhere.read_text(), "keep\n")
        self.assertEqual(sorted(item.name for item in self.folder.iterdir()), [".env", ".env.tmp", "elsewhere"])

    def test_a_customer_checkout_writes_its_own_file(self):
        own = self.folder / "victor" / ".env"
        own.parent.mkdir()
        own.write_text("ADVBOX_EMAIL=a@b.c\n")
        shared = self.folder / "shared.env"
        shared.write_text("JURISYNC_X=1\n")
        with (
            mock.patch.dict(os.environ, {"JURISYNC_ENV_FILE": str(shared), "JURISYNC_CUSTOMER": "victor"}),
            mock.patch("jurisync.customers.customers_dir", return_value=self.folder),
        ):
            credentials.save("ADVBOX_DEVICE_ID", "abc")
        self.assertEqual(own.read_text(), "ADVBOX_EMAIL=a@b.c\nADVBOX_DEVICE_ID=abc\n")
        self.assertEqual(shared.read_text(), "JURISYNC_X=1\n")

    def test_hosted_it_goes_to_the_saver_and_the_rest_of_the_request_sees_it(self):
        saved = []
        with mock.patch.dict(os.environ, {"JURISYNC_ENV_FILE": str(self.folder / "never.env")}):
            with credentials.scope("t1", {"ADVBOX_DEVICE_ID": "old"}, saver=lambda *pair: saved.append(pair)):
                credentials.save("ADVBOX_DEVICE_ID", "new")
                self.assertEqual(credentials.env("ADVBOX_DEVICE_ID"), "new")
            with credentials.scope("t2", {}), self.assertRaisesRegex(RuntimeError, "cannot store credentials"):
                credentials.save("ADVBOX_DEVICE_ID", "new")
        self.assertEqual(saved, [("ADVBOX_DEVICE_ID", "new")])
        self.assertFalse((self.folder / "never.env").exists())
