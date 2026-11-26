"""Tool names follow <portal>_<verb>_<object>, and every old name still calls its tool."""

import asyncio
import inspect
import re
import unittest
from unittest import mock

from jurisync import registry
from jurisync.connectors.djen import server as djen_server
from jurisync.core import names


def all_tools():
    for name, connector in registry.CONNECTORS.items():
        module = __import__(connector.module, fromlist=["create_server"])
        options = {"allow_writes": True} if "allow_writes" in inspect.signature(module.create_server).parameters else {}
        server, _ = connector.load(**options)
        yield name, server, {tool.name for tool in server._tool_manager.list_tools()}


class NamesTest(unittest.TestCase):
    def test_every_tool_follows_the_vocabulary(self):
        verbs = "|".join(names.VERBS)
        for connector, _, tools in all_tools():
            pattern = re.compile(rf"^{connector}_({verbs})_[a-z][a-z0-9_]*$")
            for tool in tools:
                with self.subTest(tool=tool):
                    self.assertRegex(tool, pattern)
                    self.assertLessEqual(len(tool), 64)

    def test_every_alias_points_at_a_listed_tool_and_is_never_listed(self):
        listed = set().union(*(tools for _, _, tools in all_tools()))
        for old, new in names.ALIASES.items():
            with self.subTest(old=old):
                self.assertIn(new, listed)
                self.assertNotIn(old, listed)

    def test_an_old_name_calls_the_tool(self):
        with (
            mock.patch.object(djen_server, "fetch_certidao", return_value=b"%PDF"),
            mock.patch.object(djen_server, "extract_document_text", return_value=("extracted", "texto da certidão")),
        ):
            server = djen_server.create_server()
            old = asyncio.run(server.call_tool("djen_get_certidao_text", {"hash": "offline"}))
            new = asyncio.run(server.call_tool("djen_read_certidao", {"hash": "offline"}))
        self.assertEqual(old.structured_content, new.structured_content)


if __name__ == "__main__":
    unittest.main()
