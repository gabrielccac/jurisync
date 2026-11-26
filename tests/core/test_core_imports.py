"""The core knows no portal: nothing under src/jurisync/core imports the layers above it, not even inside a function."""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
CORE = SRC / "jurisync" / "core"
FORBIDDEN = ("jurisync.customers", "jurisync.portals", "jurisync.registry", "jurisync.connectors")


def imported_modules(source, package):
    """Every module ``source`` (code of a module in ``package``) imports, at any depth, relative ones resolved."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = package.rsplit(".", node.level - 1)[0] if node.level else ""
            module = ".".join(part for part in (base, node.module) if part)
            yield module
            yield from (f"{module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", None)) in (
            "import_module",
            "__import__",
        ):
            yield from (
                arg.value for arg in node.args[:1] if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
            )


def forbidden(modules):
    return sorted({module for module in modules if any(module == m or module.startswith(m + ".") for m in FORBIDDEN)})


@pytest.mark.parametrize("path", sorted(CORE.rglob("*.py")), ids=lambda path: path.relative_to(CORE).as_posix())
def test_the_core_imports_no_customer_portal_registry_or_connector(path):
    package = ".".join(path.relative_to(SRC).parent.parts)
    assert forbidden(imported_modules(path.read_text(encoding="utf-8"), package)) == []


def test_the_check_sees_every_form_of_import():
    probe = (
        "import jurisync.registry\n"
        "from jurisync import customers\n"
        "def late():\n"
        "    from .. import portals\n"
        "    from jurisync.connectors.pje import server\n"
        "    importlib.import_module('jurisync.connectors.djen')\n"
        "    from jurisync.core import credentials\n"
        "    from jurisync.paths import REPO_ROOT\n"
    )
    assert forbidden(imported_modules(probe, "jurisync.core")) == [
        "jurisync.connectors.djen",
        "jurisync.connectors.pje",
        "jurisync.connectors.pje.server",
        "jurisync.customers",
        "jurisync.portals",
        "jurisync.registry",
    ]
