"""Customer code follows the platform: logins through the connector's session pool, credentials from the scope."""

import argparse
import hashlib
import json
from unittest import mock

import pytest

from jurisync.core import credentials


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
