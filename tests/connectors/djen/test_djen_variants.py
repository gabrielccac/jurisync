"""DJEN OAB variants: read a few at a time, each failure reported for its own variant."""

import contextvars
import threading
import time
import unittest
from unittest import mock

from jurisync.connectors.djen import client
from jurisync.connectors.djen import server as djen_server
from jurisync.connectors.djen.client import DjenError

MARK = contextvars.ContextVar("mark", default=None)


class Gauge:
    """Counts the reads in flight at once; each read holds for ``hold`` seconds."""

    def __init__(self, hold=0.05):
        self.hold, self.now, self.peak, self.lock = hold, 0, 0, threading.Lock()

    def __enter__(self):
        with self.lock:
            self.now += 1
            self.peak = max(self.peak, self.now)

    def __exit__(self, *_):
        time.sleep(self.hold)
        with self.lock:
            self.now -= 1


class VariantTest(unittest.TestCase):
    def test_variants_run_in_parallel_within_the_limit_and_keep_their_order(self):
        gauge = Gauge()

        def read(variant):
            with gauge:
                return variant, MARK.get()

        MARK.set("office-a")
        results = client.each_variant(read)
        self.assertEqual([variant for variant, _ in results], list(client.OAB_VARIANTS))
        self.assertEqual([value for _, value in results], [(v, "office-a") for v in client.OAB_VARIANTS])
        self.assertEqual(gauge.peak, client.MAX_PARALLEL_VARIANTS)

    def test_fetch_all_variants_merges_by_id(self):
        found = {"14663": [{"id": 1}], "14663-A": [{"id": 1}, {"id": 2}]}

        def fetch_all(number, *args, **kwargs):
            return len(found.get(number, [])), found.get(number, [])

        with mock.patch.object(client, "fetch_all", side_effect=fetch_all) as fetch:
            result = client.fetch_all_variants("14663", "PB", "2026-10-01", "2026-10-02")
        self.assertEqual(
            sorted(call.args[0] for call in fetch.call_args_list), sorted(f"14663{v}" for v in client.OAB_VARIANTS)
        )
        self.assertEqual(result, {"count": 3, "unique": 2, "items": [{"id": 1}, {"id": 2}]})

    def test_fetch_all_variants_names_every_failed_variant_after_reading_the_rest(self):
        def fetch_all(number, *args, **kwargs):
            if number in {"14663-B", "14663-S"}:
                raise DjenError("HTTP 500")
            return 0, []

        with mock.patch.object(client, "fetch_all", side_effect=fetch_all) as fetch:
            with self.assertRaises(DjenError) as failed:
                client.fetch_all_variants("14663", "PB", "2026-10-01", "2026-10-02")
        self.assertEqual(fetch.call_count, len(client.OAB_VARIANTS))
        self.assertIn("14663-B: DjenError", str(failed.exception))
        self.assertIn("14663-S: DjenError", str(failed.exception))

    def test_search_keeps_the_variants_that_answered(self):
        def page(params, pagina, itens):
            if params["numeroOab"] == "14663-E":
                raise DjenError("DJEN request failed after 3 attempt(s): HTTP 429")
            items = [{"id": 1}] if params["numeroOab"] == "14663" else []
            return {"status": "success", "count": len(items), "items": items}

        with mock.patch.object(djen_server, "search_page", side_effect=page):
            result = djen_server.run_search({"numeroOab": "14663", "ufOab": "PB"}, 1, 50)
        self.assertEqual(result["items"], [{"id": 1}])
        failed = [v for v in result["oabVariants"] if "error" in v]
        self.assertEqual([v["numeroOab"] for v in failed], ["14663-E"])
        self.assertIn("HTTP 429", failed[0]["error"])
        self.assertEqual(djen_server._failed_variants(result), ["14663-E"])

    def test_search_raises_when_every_variant_failed(self):
        with mock.patch.object(djen_server, "search_page", side_effect=DjenError("fora do ar")):
            with self.assertRaises(DjenError):
                djen_server.run_search({"numeroOab": "14663", "ufOab": "PB"}, 1, 50)


if __name__ == "__main__":
    unittest.main()
