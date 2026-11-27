"""The core knows no portal: nothing under src/jurisync/core imports the layers above it, not even inside a function."""

import ast
import importlib
import warnings
from pathlib import Path

import pytest

from jurisync.core import mcp

SRC = Path(__file__).resolve().parents[2] / "src"
CORE = SRC / "jurisync" / "core"
FORBIDDEN = ("jurisync.customers", "jurisync.portals", "jurisync.registry", "jurisync.connectors")
# The one exception: core.mcp's __getattr__ resolves the names that moved up in F6-4 (core.mcp.MOVED) for old imports,
# with a DeprecationWarning. It leaves with those names in a future cycle; nothing else in the core may name a layer.
DEPRECATED_SHIM = {"mcp.py": ["jurisync.customers", "jurisync.portals"]}


def imported_modules(source, package):
    """Every module ``source`` (code of a module in ``package``) imports, at any depth, relative ones resolved, and
    every string that names a module (what ``importlib.import_module`` would take)."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = package.rsplit(".", node.level - 1)[0] if node.level else ""
            module = ".".join(part for part in (base, node.module) if part)
            yield module
            yield from (f"{module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value


def forbidden(modules):
    return sorted({module for module in modules if any(module == m or module.startswith(m + ".") for m in FORBIDDEN)})


@pytest.mark.parametrize("path", sorted(CORE.rglob("*.py")), ids=lambda path: path.relative_to(CORE).as_posix())
def test_the_core_imports_no_customer_portal_registry_or_connector(path):
    package = ".".join(path.relative_to(SRC).parent.parts)
    found = forbidden(imported_modules(path.read_text(encoding="utf-8"), package))
    assert found == DEPRECATED_SHIM.get(path.relative_to(CORE).as_posix(), [])


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


@pytest.mark.parametrize("name", sorted(mcp.MOVED))
def test_each_moved_name_still_works_from_core_mcp_and_warns(name):
    with pytest.warns(DeprecationWarning, match=f"jurisync.core.mcp.{name} moved to {mcp.MOVED[name]}.{name}"):
        old = getattr(mcp, name)
    assert old is getattr(importlib.import_module(mcp.MOVED[name]), name)


def test_a_from_import_of_a_moved_name_warns_and_an_unknown_name_still_fails():
    with pytest.warns(DeprecationWarning):
        from jurisync.core.mcp import load_env_file
    assert load_env_file is importlib.import_module("jurisync.customers").load_env_file
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with pytest.raises(ImportError):
            from jurisync.core.mcp import no_such_name  # noqa: F401
