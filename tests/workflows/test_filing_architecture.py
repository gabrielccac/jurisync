"""The filing is independent of where documents come from: no filing module may import a source connector."""

import ast
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
FILING_PACKAGES = ("jurisync/workflows/filing", "jurisync/connectors/pje/filing")
# The filing may use the PJe connector it files with; every other connector is a data source.
ALLOWED_CONNECTORS = ("jurisync.connectors.pje",)


def _imports(path):
    module = ".".join(path.relative_to(SRC).with_suffix("").parts)
    package = module.rsplit(".", 1)[0] if path.name != "__init__.py" else module.removesuffix(".__init__")
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")[: len(package.split(".")) - node.level + 1]
                yield ".".join(base + ([node.module] if node.module else []))
            else:
                yield node.module


class FilingArchitectureTests(unittest.TestCase):
    def test_no_filing_module_imports_a_source_connector(self):
        offenders = []
        for package in FILING_PACKAGES:
            for path in sorted((SRC / package).rglob("*.py")):
                for name in _imports(path):
                    if name.startswith("jurisync.connectors.") and not name.startswith(ALLOWED_CONNECTORS):
                        offenders.append(f"{path.relative_to(SRC)} imports {name}")
        self.assertEqual(offenders, [])

    def test_the_input_contract_depends_on_the_standard_library_only(self):
        names = list(_imports(SRC / "jurisync/workflows/filing/contract.py"))
        self.assertEqual([name for name in names if name.startswith("jurisync")], [])

    def test_relative_imports_are_resolved_so_they_cannot_slip_through(self):
        names = set(_imports(SRC / "jurisync/workflows/filing/prepare.py"))
        self.assertIn("jurisync.workflows.filing.classify", names)
        self.assertIn("jurisync.connectors.pje.filing.store", names)


if __name__ == "__main__":
    unittest.main()
