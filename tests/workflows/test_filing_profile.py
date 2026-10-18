import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jurisync.workflows.filing.profile import load_profile, profile_names

BASE = {
    "portal": "trf5-1g",
    "defaults": {
        "passive": {"search": "x", "cnpj": "1", "name": "X"},
    },
}


class ProfileTests(unittest.TestCase):
    def profiles_dir(self, **overrides):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        folder = Path(tmp.name, "office")
        folder.mkdir()
        folder.joinpath("filing-profile.json").write_text(json.dumps({**BASE, **overrides}))
        patcher = mock.patch.dict(os.environ, {"JURISYNC_PROFILES_DIR": tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        return folder

    def test_victors_profile_holds_only_protocol_settings(self):
        profile = load_profile("victor")
        self.assertIn("victor", profile_names())
        self.assertNotIn("attorney", profile.defaults())
        with mock.patch.dict(os.environ, {"VICTOR_PJE_GRAZIELE_CPF": "11122233344"}):
            self.assertNotIn("11122233344", json.dumps(profile.defaults()), "nothing is read from the environment")

    def test_any_customer_folder_with_a_profile_file_is_available(self):
        self.profiles_dir()
        self.assertEqual(profile_names(), ["office"])
        profile = load_profile("office")
        self.assertEqual((profile.portal, profile.uncertain_type), ("trf5-1g", "Outros Documentos"))

    def test_bad_profiles_are_rejected_with_a_clear_reason(self):
        self.profiles_dir(documentRules=[["(", "petition"]])
        with self.assertRaises(Exception):
            load_profile("office")
        self.profiles_dir(folderPatterns=["PROTOCOLAR$"])
        with self.assertRaisesRegex(ValueError, "belongs to the source's exporter"):
            load_profile("office")
        self.profiles_dir(defaults={})
        with self.assertRaisesRegex(ValueError, "defaults.passive"):
            load_profile("office")
        self.profiles_dir(defaults={**BASE["defaults"], "attorney": {"name": "A"}})
        with self.assertRaisesRegex(ValueError, "come with each case's facts"):
            load_profile("office")
        with self.assertRaisesRegex(ValueError, "lowercase"):
            load_profile("../etc")

    def test_references_cannot_leave_the_profile_folder(self):
        folder = self.profiles_dir(references={"history": "../../secrets.txt", "subjects": "notes/s.md"})
        profile = load_profile("office")
        with self.assertRaisesRegex(ValueError, "outside"):
            profile.reference("history")
        self.assertEqual(profile.reference("subjects"), (folder / "notes/s.md").resolve())
        self.assertIsNone(profile.reference("missing"))


if __name__ == "__main__":
    unittest.main()
