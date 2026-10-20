import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from jurisync import setup, skills_sync

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull,
}


def run(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


class SkillsSyncTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(mock.patch.dict(os.environ, GIT_ENV))
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.origin = self.root / "origin"
        self.skill_source = self.origin / "customers" / "demo" / "skills" / "demo-skill"
        (self.skill_source / "references").mkdir(parents=True)
        (self.skill_source / "SKILL.md").write_text("---\nname: demo-skill\n---\nv1\n", encoding="utf-8")
        (self.skill_source / "references" / "notes.md").write_text("notes\n", encoding="utf-8")
        run("init", "-q", "-b", "main", cwd=self.origin)
        self.commit("first")
        self.src = self.root / "src"
        self.skills_dir = self.root / "claude-skills"
        self.zip_dir = self.root / "Downloads"
        self.lines = []

    def commit(self, message):
        run("add", "-A", cwd=self.origin)
        run("commit", "-q", "-m", message, cwd=self.origin)

    def sync(self, customer="demo"):
        return skills_sync.sync(
            customer,
            src=self.src,
            skills_dir=self.skills_dir,
            zip_dir=self.zip_dir,
            state_file=self.root / "state.json",
            repository=str(self.origin),
            branch="main",
            say=self.lines.append,
        )

    def linked_text(self):
        return (self.skills_dir / "demo-skill" / "SKILL.md").read_text(encoding="utf-8")

    def test_first_run_clones_links_and_zips(self):
        self.assertEqual(self.sync(), ["demo-skill"])
        self.assertTrue((self.skills_dir / "demo-skill").is_symlink())
        self.assertIn("v1", self.linked_text())
        with zipfile.ZipFile(self.zip_dir / "demo-skill.zip") as archive:
            self.assertIn("demo-skill/SKILL.md", archive.namelist())
            self.assertIn("demo-skill/references/notes.md", archive.namelist())

    def test_unchanged_skill_keeps_its_zip(self):
        self.sync()
        self.assertEqual(self.sync(), [])
        self.assertIn("zip sem mudanças", self.lines[-1])

    def test_update_on_github_reaches_the_link_and_the_zip(self):
        self.sync()
        (self.skill_source / "SKILL.md").write_text("---\nname: demo-skill\n---\nv2\n", encoding="utf-8")
        self.commit("second")
        self.assertEqual(self.sync(), ["demo-skill"])
        self.assertIn("v2", self.linked_text())
        with zipfile.ZipFile(self.zip_dir / "demo-skill.zip") as archive:
            self.assertIn("v2", archive.read("demo-skill/SKILL.md").decode())

    def test_local_edit_stops_without_overwriting(self):
        self.sync()
        (self.skill_source / "SKILL.md").write_text("remote change\n", encoding="utf-8")
        self.commit("second")
        (self.src / "customers" / "demo" / "skills" / "demo-skill" / "SKILL.md").write_text("mine\n", encoding="utf-8")
        with self.assertRaisesRegex(skills_sync.SkillsError, "mudanças locais"):
            self.sync()
        self.assertEqual(self.linked_text(), "mine\n")

    def test_local_commit_stops(self):
        self.sync()
        run("commit", "-q", "--allow-empty", "-m", "local", cwd=self.src)
        with self.assertRaisesRegex(skills_sync.SkillsError, "commit"):
            self.sync()

    def test_existing_copy_is_kept_as_backup(self):
        copy = self.skills_dir / "demo-skill"
        copy.mkdir(parents=True)
        (copy / "SKILL.md").write_text("old copy\n", encoding="utf-8")
        self.sync()
        backups = list(self.skills_dir.glob("demo-skill.bak-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "SKILL.md").read_text(encoding="utf-8"), "old copy\n")
        self.assertIn("v1", self.linked_text())

    def test_unknown_or_invalid_customer_is_refused(self):
        with self.assertRaisesRegex(skills_sync.SkillsError, "nenhuma skill"):
            self.sync("nobody")
        with self.assertRaisesRegex(skills_sync.SkillsError, "inválido"):
            self.sync("../etc")


class SkillsCommandTest(unittest.TestCase):
    def test_setup_dispatches_the_skills_command(self):
        with mock.patch.object(skills_sync, "skills_main", return_value=0) as skills_main:
            self.assertEqual(setup.main(["skills", "--customer", "victor"], say=lambda *_: None), 0)
        self.assertEqual(skills_main.call_args.args[0], ["--customer", "victor"])

    def test_install_without_git_metadata_uses_the_defaults(self):
        with mock.patch.object(
            skills_sync.metadata, "distribution", side_effect=skills_sync.metadata.PackageNotFoundError
        ):
            self.assertEqual(skills_sync.installed_source(), (skills_sync.REPOSITORY, skills_sync.DEFAULT_BRANCH))
