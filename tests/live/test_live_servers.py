"""Read-only smoke tests against the real portals. Opt-in: ``uv run pytest -m live``.

Each test launches a server exactly as .mcp.json does (stdio, project .env) and
makes read-only calls only. Wait ~30 s between runs: some portals reject a TOTP
code reused within its window.
"""

import asyncio
import os
import sys
import time
from pathlib import Path

import pytest
from mcp.client.client import Client
from mcp.client.stdio import StdioServerParameters

pytestmark = pytest.mark.live

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / ".env"


def configured():
    values = dict(os.environ)
    if ENV_FILE.is_file():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and not key.startswith("#") and value.strip().strip("\"'"):
                values.setdefault(key.strip(), value.strip())
    return values


def require(*names):
    values = configured()
    # JFRS_* replaced the misspelled JRFS_*; either satisfies the requirement.
    missing = [name for name in names if not values.get(name) and not values.get(name.replace("JFRS_", "JRFS_"))]
    if missing:
        pytest.skip(f"not configured: {', '.join(missing)}")


def call_all(module, calls, **env):
    """Run tool calls in one server process; return [(seconds, result), ...]."""
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", module],
        cwd=ROOT,
        env={
            "PATH": os.environ["PATH"],
            "HOME": os.environ["HOME"],
            "DISPLAY": os.environ.get("DISPLAY", ""),
            "JURISYNC_ENV_FILE": str(ENV_FILE),
            "ADVBOX_ALLOW_WRITES": "false",
            "JURIDIQ_ALLOW_WRITES": "false",
            "PJE_ALLOW_DRAFT_WRITES": "false",
            **env,
        },
    )

    async def run():
        results = []
        async with Client(params, read_timeout_seconds=600) as client:
            for name, arguments in calls:
                started = time.monotonic()
                result = await client.call_tool(name, arguments)
                results.append((time.monotonic() - started, result))
        return results

    return asyncio.run(run())


def ok(result):
    assert not result.is_error, result.content[0].text
    return result.structured_content


def test_djen_public_search_and_deadline():
    (_, search), (_, deadline) = call_all(
        "jurisync.connectors.djen.server",
        [
            (
                "djen_search_communications",
                {"numeroOab": "14663", "ufOab": "PB", "inicio": "2026-10-01", "fim": "2026-10-02", "itens": 5},
            ),
            ("djen_calculate_deadline", {"availableOn": "2026-10-01", "days": 5}),
        ],
    )
    assert ok(search)["count"] >= 0
    assert ok(deadline)["success"]


def test_pje_trf1_reuses_session_across_calls():
    require("PJE_1G_USERNAME", "PJE_2G_USERNAME")
    results = call_all(
        "jurisync.connectors.pje.server",
        [
            ("pje_list_expediente_counters", {"portal": "trf1-1g"}),
            ("pje_get_process", {"portal": "trf1-1g", "processNumber": "1024853-70.2023.4.01.3400", "scope": "latest"}),
            ("pje_list_expediente_counters", {"portal": "trf1-1g"}),
            ("pje_get_process", {"portal": "trf1-2g", "processNumber": "1016572-38.2026.4.01.0000", "scope": "latest"}),
        ],
    )
    first, process, again, second_instance = results
    assert ok(first[1])["buckets"]
    assert ok(process[1])["timeline"]
    assert ok(again[1])["buckets"]
    assert again[0] < first[0] or again[0] < 1.0
    assert ok(second_instance[1])["processNumber"] == "1016572-38.2026.4.01.0000"


def test_pje_trf5_certificate_login():
    require("PJE_A1_PIN")
    (_, counters), (_, process) = call_all(
        "jurisync.connectors.pje.server",
        [
            ("pje_list_expediente_counters", {"portal": "trf5-1g"}),
            ("pje_get_process", {"portal": "trf5-1g", "processNumber": "0001271-74.2026.4.05.8304", "scope": "latest"}),
        ],
    )
    assert ok(counters)["buckets"]
    assert ok(process)["documentInventory"]


VICTOR_PORTALS = (
    "trf5-1g", "trf5-2g", "trf5-tru", "trf1-1g", "trf1-2g", "trf3-1g", "trf3-2g", "tjpe-1g", "tjpe-2g",
)  # fmt: skip


@pytest.mark.parametrize("portal", VICTOR_PORTALS)
def test_victor_pje_portal_reads(portal):
    """Counters, first inbox process, process page and one visible document, on Victor's A1 certificate.

    Run with JURISYNC_CUSTOMER=victor. Wait ~30 s between portals: the SSO rejects a reused TOTP code.
    """
    require("PJE_A1_P12", "PJE_A1_PIN")
    env = {"JURISYNC_CUSTOMER": "victor"}
    (_, counters), (_, inbox) = call_all(
        "jurisync.connectors.pje.server",
        [
            ("pje_list_expediente_counters", {"portal": portal}),
            ("pje_list_inbox", {"portal": portal, "maxPages": 1}),
        ],
        **env,
    )
    assert ok(counters)["buckets"]
    numbers = [
        row["processNumber"] for box in ok(inbox)["inboxes"] for row in box.get("rows", []) if row.get("processNumber")
    ]
    if not numbers:
        pytest.skip(f"{portal}: empty inbox, nothing to open")
    ((_, process),) = call_all(
        "jurisync.connectors.pje.server",
        [("pje_get_process", {"portal": portal, "processNumber": numbers[0], "scope": "page"})],
        **env,
    )
    inventory = ok(process)["documentInventory"]
    readable = [d["id"] for d in inventory if d.get("visible_in_timeline") and not d.get("first_instance_copy")]
    if readable:
        ((_, read),) = call_all(
            "jurisync.connectors.pje.server",
            [("pje_read_documents", {"portal": portal, "processNumber": numbers[0], "documentIds": readable[:1]})],
            **env,
        )
        assert ok(read)["documents"]


@pytest.mark.parametrize("portal, prefix", [("trf4-1g-sc", "JFSC"), ("trf4-1g-rs", "JFRS")])
def test_eproc_logs_in_once_then_reuses(portal, prefix):
    require(f"{prefix}_USUARIO", f"{prefix}_EXPORT_DATA")
    (first, open_deadlines), (second, pending) = call_all(
        "jurisync.connectors.eproc.server",
        [
            ("eproc_list_notices", {"portal": portal, "queue": "open_deadlines"}),
            ("eproc_list_notices", {"portal": portal, "queue": "pending"}),
        ],
    )
    assert ok(open_deadlines)["success"]
    assert ok(pending)["success"]
    assert second < 3.0 < first  # Selenium login once, panel refresh afterwards
    assert ok(open_deadlines)["portal"] == portal


def test_advbox_reads():
    require("ADVBOX_EMAIL", "ADVBOX_DEVICE_ID")
    (_, session), (_, customers) = call_all(
        "jurisync.connectors.advbox.server",
        [("advbox_ensure_session", {}), ("advbox_list_customers", {"pageSize": 1})],
    )
    assert ok(session)["authenticated"]
    assert ok(customers)["records"]


def test_juridiq_reads():
    require("JURIDIQ_EMAIL", "JURIDIQ_PASSWORD")
    ((_, tags),) = call_all("jurisync.connectors.juridiq.server", [("juridiq_list_tags", {"limit": 5})])
    assert "result" in ok(tags)
