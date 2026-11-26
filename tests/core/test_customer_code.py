"""Customer code follows the platform: logins through the connector's session pool, credentials from the scope."""

import argparse
import hashlib
import json
from unittest import mock

import pytest

from jurisync.core import credentials


def test_victor_watcher_reads_through_the_pje_session_pool(tmp_path):
    from customers.victor.workflows import pje1g_watch as watch

    sheet = tmp_path / "sheet.json"
    sheet.write_text(json.dumps([{"Número do processo": "0000001-00.2026.4.01.3400"}]), encoding="utf-8")
    args = argparse.Namespace(sheet_json=sheet, state_file=tmp_path / "state.json", limit=0)
    pool = mock.Mock()
    pool.run.side_effect = lambda portal, read: read("pooled-session")
    quick = {"success": True, "latest_marker": "Juntada de petição"}
    with (
        mock.patch.object(watch, "sessions", pool),
        mock.patch.object(watch, "quick_check", return_value=quick) as check,
    ):
        result = watch.poll_once(args)
    pool.run.assert_called_once()
    assert pool.run.call_args.args[0] == "trf1-1g"
    check.assert_called_once_with("pooled-session", "0000001-00.2026.4.01.3400")
    assert result["baselined"] == 1 and result["success"]


def test_jean_publications_take_the_juridiq_account_from_the_credential_scope(tmp_path, monkeypatch):
    from customers.jean import run_publications

    monkeypatch.setenv("JURIDIQ_EMAIL", "outro@exemplo.com")
    with mock.patch.object(run_publications, "run_publication_pipeline") as pipeline:
        with credentials.scope("escritorio-teste", {"JURIDIQ_EMAIL": "Jean@Exemplo.com"}):
            run_publications.run_jean_publications("agent", state_dir=tmp_path)
    account = hashlib.sha256(b"jean@exemplo.com").hexdigest()[:16]
    state_file = pipeline.call_args.kwargs["state_file"]
    assert state_file.parent == tmp_path.resolve()
    assert state_file.name.startswith(f"juridiq-{account}-")


def test_jean_publications_need_a_juridiq_account(tmp_path, monkeypatch):
    from customers.jean import run_publications

    monkeypatch.delenv("JURIDIQ_EMAIL", raising=False)
    with pytest.raises(RuntimeError, match="JURIDIQ_EMAIL"):
        run_publications.run_jean_publications("agent", state_dir=tmp_path)
