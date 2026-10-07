"""Exercise CLI dispatch through query construction without network requests."""

import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from jurisync.connectors.djen import client


class DjenCliFilterChecks(unittest.TestCase):
    def test_filters_survive_every_page_and_oab_variant(self):
        for mode in ("--all", "--all-variants"):
            queries = []

            def read(url, timeout, queries=queries):
                query = parse_qs(urlsplit(url).query)
                queries.append(query)
                page = int(query["pagina"][0])
                oab = query["numeroOab"][0]
                items = [{"id": f"{oab}-{page}-{index}"} for index in range(5 if page == 1 else 1)]
                return {"count": 6, "items": items}, 200

            argv = [
                "djen",
                mode,
                "--numero-oab",
                "123",
                "--uf-oab",
                "SP",
                "--inicio",
                "2026-10-01",
                "--fim",
                "2026-10-02",
                "--itens",
                "5",
                "--texto",
                "specific phrase",
                "--nome-parte",
                "Fixture person",
                "--meio",
                "D",
            ]
            output = StringIO()
            with (
                self.subTest(mode=mode),
                patch("sys.argv", argv),
                patch.object(client, "_read_json", side_effect=read),
                redirect_stdout(output),
            ):
                client.main()
            self.assertEqual(len(queries), 2 if mode == "--all" else 14)
            for query in queries:
                self.assertEqual(query["texto"], ["specific phrase"])
                self.assertEqual(query["nomeParte"], ["Fixture person"])
                self.assertEqual(query["meio"], ["D"])
            self.assertEqual(len(json.loads(output.getvalue())["items"]), 6 if mode == "--all" else 42)

    def test_poll_rejects_filters_before_mutating_broader_oab_state(self):
        argv = [
            "djen",
            "--poll",
            "--all-variants",
            "--numero-oab",
            "123",
            "--uf-oab",
            "SP",
            "--inicio",
            "2026-10-01",
            "--fim",
            "2026-10-02",
            "--texto",
            "subset",
        ]
        with (
            patch("sys.argv", argv),
            patch("jurisync.connectors.djen.poll.poll_window") as poll,
            redirect_stderr(StringIO()),
            self.assertRaises(SystemExit),
        ):
            client.main()
        poll.assert_not_called()
