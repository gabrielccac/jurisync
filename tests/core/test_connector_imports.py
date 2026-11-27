"""Connectors do not reach into each other: shared code lives in jurisync.core, and what one connector offers the others
is exported by its package (``from jurisync.connectors import djen``), never taken from its modules."""

import ast
import pathlib

CONNECTORS = pathlib.Path(__file__).resolve().parents[2] / "src" / "jurisync" / "connectors"


def imports(path):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module:
            yield node.lineno, node.module, [alias.name for alias in node.names]
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name, []


def test_no_connector_imports_the_modules_of_another():
    found = []
    for path in CONNECTORS.rglob("*.py"):
        own = path.relative_to(CONNECTORS).parts[0]
        for line, module, names in imports(path):
            parts = module.split(".")
            if parts[:2] != ["jurisync", "connectors"]:
                continue
            if len(parts) == 2:  # from jurisync.connectors import <package>
                others = [name for name in names if name != own]
                if all((CONNECTORS / name / "__init__.py").is_file() for name in others):
                    continue
            elif parts[2] == own:
                continue
            found.append(f"{path.relative_to(CONNECTORS)}:{line} {module} {names}")
    assert not found, "import from jurisync.core, or from the other connector's package:\n" + "\n".join(found)


def test_what_a_package_exports_is_used():
    from jurisync.connectors import djen

    assert callable(djen.fetch_page) and callable(djen.normalize_item)
