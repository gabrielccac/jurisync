import tomllib
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


class DependencyTest(unittest.TestCase):
    def test_tzdata_is_declared_because_windows_has_no_timezone_database(self):
        dependencies = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["dependencies"]
        self.assertTrue(any(item.startswith("tzdata") for item in dependencies))

    def test_brazil_timezone_resolves(self):
        self.assertEqual(ZoneInfo("America/Sao_Paulo").key, "America/Sao_Paulo")


if __name__ == "__main__":
    unittest.main()
