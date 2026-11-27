"""One real login per portal and service the office configures, its session checked, then closed. Opt-in:

    JURISYNC_CUSTOMER=victor uv run pytest -m live_login      # or: uv run jurisync-smoke victor

No reads beyond the login check, no writes, no ciência. One attempt per portal; the run stops at a refusal or CAPTCHA.
ADVBOX and Juridiq stay out, as in ``jurisync-smoke`` without ``--services``.
"""

import os

import pytest

from jurisync.install import smoke

pytestmark = pytest.mark.live_login


def test_every_configured_login():
    smoke.load_env(os.environ.get("JURISYNC_CUSTOMER", "").strip())
    found = smoke.targets()
    if not found:
        pytest.skip("no portal or service login configured")
    results = smoke.run(found)
    failed = [result for result in results if result.status != "ok"]
    assert not failed, "\n" + smoke.table(results)
