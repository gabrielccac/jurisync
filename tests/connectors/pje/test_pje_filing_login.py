"""A filing run that logs in on its own goes through the pool: login semaphore and cool-down after rejection."""

import contextvars
import threading
from unittest.mock import Mock

import pytest

from jurisync.connectors.pje.filing import run
from jurisync.core import credentials, session_pool
from jurisync.core.session_pool import LoginRejected


@pytest.fixture
def portal(monkeypatch):
    login = Mock(side_effect=lambda portal: Mock(name=f"session-{credentials.tenant()}"))
    monkeypatch.setattr(run, "cert_login", login)
    monkeypatch.setattr(run, "DraftClient", lambda session: Mock(search_pending_drafts=Mock(return_value=session)))
    monkeypatch.setattr(session_pool, "_login_slots", None)
    yield login
    run.logins.forget("trf5-1g")
    for tenant in ("office-a", "office-b"):
        with credentials.scope(tenant, {}):
            run.logins.forget("trf5-1g")


def search():
    return run.run_stage("pending-search", name="Fulano")


def test_each_run_logs_in_and_closes_its_own_session(portal):
    first, second = search(), search()
    assert portal.call_count == 2 and first is not second
    first.close.assert_called_once()
    second.close.assert_called_once()


def test_a_caller_session_is_used_as_given(portal):
    session = Mock()
    assert run.run_stage("pending-search", name="Fulano", session=session) is session
    portal.assert_not_called()
    session.close.assert_not_called()


def test_rejected_credentials_are_not_sent_again_within_the_cooldown(portal):
    with credentials.scope("office-a", {}):
        portal.side_effect = LoginRejected("PJe refused the certificate")
        with pytest.raises(LoginRejected, match="refused"):
            search()
        with pytest.raises(LoginRejected, match="no new login was attempted"):
            search()
        assert portal.call_count == 1
    portal.side_effect = lambda portal: Mock()
    with credentials.scope("office-b", {}):
        search()  # another office's cool-down is its own
    assert portal.call_count == 2


def test_logins_wait_for_a_login_slot(portal, monkeypatch):
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(session_pool, "_login_slots", slots)
    held_during_login = []
    portal.side_effect = lambda portal: held_during_login.append(not slots.acquire(blocking=False)) or Mock()
    search()
    assert held_during_login == [True]

    slots.acquire()  # every slot busy: a run must wait instead of logging in
    worker = threading.Thread(target=contextvars.copy_context().run, args=(search,), daemon=True)
    worker.start()
    worker.join(0.3)
    assert worker.is_alive() and len(held_during_login) == 1
    slots.release()
    worker.join(5)
    assert not worker.is_alive() and held_during_login == [True, True]
