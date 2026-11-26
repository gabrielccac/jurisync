"""Every tool takes an annotation preset from core.mcp, and one rule for ciência holds in every portal."""

import inspect
import unittest

from jurisync import registry
from jurisync.core import mcp
from jurisync.workflows.filing import server as filing

# Opens documents of a process that may have a pending intimação, without an acknowledgeCiencia parameter.
OPENS_DOCUMENTS = {"eproc_get_process"}


def all_tools():
    for name, connector in registry.CONNECTORS.items():
        module = __import__(connector.module, fromlist=["create_server"])
        options = {"allow_writes": True} if "allow_writes" in inspect.signature(module.create_server).parameters else {}
        server, _ = connector.load(**options)
        for tool in server._tool_manager.list_tools():
            yield name, tool
    for tool in filing.create_server()._tool_manager.list_tools():
        yield "filing", tool


class AnnotationsTest(unittest.TestCase):
    def test_every_tool_has_a_title_and_a_preset(self):
        for connector, tool in all_tools():
            with self.subTest(connector=connector, tool=tool.name):
                self.assertTrue(tool.title)
                self.assertIn(tool.annotations, mcp.PRESETS)

    def test_a_read_that_may_give_ciencia_is_never_a_plain_read(self):
        for connector, tool in all_tools():
            properties = tool.parameters.get("properties", {})
            with self.subTest(connector=connector, tool=tool.name):
                if "acknowledgeCiencia" in properties or tool.name in OPENS_DOCUMENTS:
                    self.assertEqual(tool.annotations, mcp.MAY_GIVE_CIENCIA)
                if "acknowledgeCiencia" in properties or "save" in properties:
                    self.assertFalse(tool.annotations.read_only_hint)


if __name__ == "__main__":
    unittest.main()
