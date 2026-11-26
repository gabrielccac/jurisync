import threading
import time

import pytest

from jurisync.core import credentials, session_pool
from jurisync.core.session_pool import LoginRejected, SessionPool


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Portal:
    """A fake portal: counts logins per tenant and rejects the ones listed in ``rejecting``."""

    def __init__(self, **options):
        self.clock, self.logins, self.closed, self.rejecting = Clock(), [], [], set()
        self.pool = SessionPool(
            self.login, lambda portal, value: value, self.closed.append, clock=self.clock, **options
        )

    def login(self, portal):
        self.logins.append((credentials.tenant(), portal))
        if portal in self.rejecting:
            raise LoginRejected("wrong password")
        return f"{credentials.tenant()}/{portal}/{len(self.logins)}"

    def use(self, portal):
        with self.pool.session(portal) as session:
            return session


@pytest.fixture(autouse=True)
def fresh_login_slots(monkeypatch):
    monkeypatch.setattr(session_pool, "_login_slots", None)
    monkeypatch.delenv("JURISYNC_MAX_LOGINS", raising=False)
    monkeypatch.delenv("JURISYNC_LOGIN_COOLDOWN_SECONDS", raising=False)


def test_rejected_credentials_are_not_sent_again_during_the_cooldown():
    portal = Portal(login_cooldown=900, wall_clock=lambda: time.mktime((2026, 11, 9, 14, 5, 0, 0, 0, -1)))
    portal.rejecting.add("trf1-1g")
    with pytest.raises(LoginRejected, match="wrong password"):
        portal.use("trf1-1g")
    portal.rejecting.clear()
    portal.clock.now += 899
    with pytest.raises(LoginRejected, match=r"rejected these credentials at 14:05.*wait 1 min"):
        portal.use("trf1-1g")
    assert portal.logins == [("", "trf1-1g")]
    assert portal.use("trf5-1g") == "/trf5-1g/2"  # another portal is not blocked
    portal.clock.now += 2
    assert portal.use("trf1-1g") == "/trf1-1g/3"


def test_run_never_repeats_a_rejected_login():
    portal = Portal()
    portal.rejecting.add("tjsp")
    with pytest.raises(LoginRejected):
        portal.pool.run("tjsp", lambda session: session)
    assert len(portal.logins) == 1


def test_forget_ends_the_cooldown():
    portal = Portal()
    portal.rejecting.add("trf1-1g")
    with pytest.raises(LoginRejected):
        portal.use("trf1-1g")
    portal.rejecting.clear()
    portal.pool.forget("trf1-1g")
    assert portal.use("trf1-1g") == "/trf1-1g/2"


def test_cooldown_length_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("JURISYNC_LOGIN_COOLDOWN_SECONDS", "60")
    portal = Portal()
    portal.rejecting.add("trf1-1g")
    with pytest.raises(LoginRejected):
        portal.use("trf1-1g")
    portal.rejecting.clear()
    portal.clock.now += 61
    assert portal.use("trf1-1g") == "/trf1-1g/2"


def test_other_login_errors_do_not_start_a_cooldown():
    attempts = []

    def login(portal):
        attempts.append(portal)
        if len(attempts) == 1:
            raise RuntimeError("portal offline")
        return "session"

    pool = SessionPool(login, lambda portal, value: value, lambda value: None)
    with pytest.raises(RuntimeError), pool.session("jfsc"):
        pass
    with pool.session("jfsc") as session:
        assert session == "session"


def test_two_tenants_on_one_portal_are_independent():
    portal = Portal()
    portal.rejecting.add("trf1-1g")
    with credentials.scope("office-a"), pytest.raises(LoginRejected):
        portal.use("trf1-1g")
    portal.rejecting.clear()
    with credentials.scope("office-b"):
        assert portal.use("trf1-1g") == "office-b/trf1-1g/2"
        assert portal.use("trf1-1g") == "office-b/trf1-1g/2"
    with credentials.scope("office-a"), pytest.raises(LoginRejected, match="no new login"):
        portal.use("trf1-1g")
    with credentials.scope("office-b"):
        portal.pool.forget("trf1-1g")  # one office changing its credentials does not unblock the other
    with credentials.scope("office-a"), pytest.raises(LoginRejected):
        portal.use("trf1-1g")
    assert portal.closed == ["office-b/trf1-1g/2"]


def test_logins_are_bounded_across_pools_but_calls_are_not(monkeypatch):
    monkeypatch.setenv("JURISYNC_MAX_LOGINS", "2")
    active, peaks, calls = [], [], []
    gate = threading.Lock()

    def login(portal):
        with gate:
            active.append(portal)
            peaks.append(len(active))
        time.sleep(0.03)
        with gate:
            active.remove(portal)
        return portal

    pools = [SessionPool(login, lambda portal, value: value, lambda value: None) for _ in range(2)]
    barrier = threading.Barrier(6)

    def work(pool, portal):
        with pool.session(portal):
            calls.append(portal)
            barrier.wait(timeout=5)  # all six tool bodies run together: the bound is on logins only

    threads = [threading.Thread(target=work, args=(pools[n % 2], f"portal-{n}")) for n in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert len(calls) == 6
    assert max(peaks) == 2


def test_idle_sessions_are_closed_with_their_locks_when_the_pool_is_used():
    portal = Portal(idle_ttl=1800)
    portal.use("trf1-1g")
    portal.clock.now += 1000
    portal.use("trf5-1g")
    portal.clock.now += 1000
    portal.use("tjpe-1g")
    assert portal.closed == ["/trf1-1g/1"]
    assert set(portal.pool._locks) == {("", "trf5-1g"), ("", "tjpe-1g")}
    assert portal.use("trf1-1g") == "/trf1-1g/4"


def test_a_session_in_use_is_never_evicted():
    portal = Portal(idle_ttl=1800)
    entered, release = threading.Event(), threading.Event()

    def long_call():
        with portal.pool.session("trf1-1g"):
            entered.set()
            release.wait(5)

    thread = threading.Thread(target=long_call)
    thread.start()
    entered.wait(5)
    portal.clock.now += 5000
    portal.use("trf5-1g")
    assert portal.closed == []
    release.set()
    thread.join(5)
    assert portal.use("trf1-1g") == "/trf1-1g/1"


def test_an_input_error_keeps_the_session_but_other_errors_mark_it_stale():
    refreshed = []
    pool = SessionPool(
        lambda portal: "session", lambda portal, value: refreshed.append(value) or value, lambda value: None
    )
    with pytest.raises(ValueError), pool.session("jfsc"):
        raise ValueError("invalid process number")
    with pool.session("jfsc"):
        pass
    assert refreshed == []
    with pytest.raises(RuntimeError), pool.session("jfsc"):
        raise RuntimeError("unexpected page")
    with pool.session("jfsc"):
        pass
    assert refreshed == ["session"]


def test_changed_credentials_drop_the_session_and_the_cooldown():
    portal = Portal()
    with credentials.scope("office", {"PASSWORD": "old"}):
        first = portal.use("trf1-1g")
    with credentials.scope("office", {"PASSWORD": "old"}):
        assert portal.use("trf1-1g") == first  # same credentials: the login is kept
    with credentials.scope("office", {"PASSWORD": "new"}):
        assert portal.use("trf1-1g") != first
    assert portal.closed == [first]
    portal.rejecting.add("trf1-1g")
    with credentials.scope("office", {"PASSWORD": "wrong"}), pytest.raises(LoginRejected, match="wrong password"):
        portal.pool.check("trf1-1g")
    with credentials.scope("office", {"PASSWORD": "wrong"}), pytest.raises(LoginRejected, match="no new login"):
        portal.pool.check("trf1-1g")
    portal.rejecting.clear()
    with credentials.scope("office", {"PASSWORD": "fixed"}):
        portal.pool.check("trf1-1g")  # the operator stored a new password: the cool-down is lifted
    assert len(portal.logins) == 4


def test_a_call_waits_for_the_offices_session_only_so_long():
    portal = Portal(session_wait=0.2)
    inside, release = threading.Event(), threading.Event()

    def hold():
        with portal.pool.session("tjx"):
            inside.set()
            release.wait(5)

    holder = threading.Thread(target=hold)
    holder.start()
    assert inside.wait(5)
    began = time.monotonic()
    with pytest.raises(session_pool.SessionBusy, match="another call of this office"):
        portal.use("tjx")
    assert time.monotonic() - began < 3
    release.set()
    holder.join(5)
    assert portal.use("tjx") == "/tjx/1"


def test_the_session_wait_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("JURISYNC_SESSION_WAIT_SECONDS", "0.1")
    portal = Portal()
    with portal.pool.session("tjx"):
        failed = []
        thread = threading.Thread(
            target=lambda: failed.append(pytest.raises(session_pool.SessionBusy, portal.use, "tjx"))
        )
        thread.start()
        thread.join(5)
    assert failed and not thread.is_alive()


def test_a_login_waits_for_a_slot_only_so_long_and_sends_nothing(monkeypatch):
    monkeypatch.setenv("JURISYNC_MAX_LOGINS", "1")
    inside, release = threading.Event(), threading.Event()

    def stuck_login(portal):
        inside.set()
        release.wait(5)
        return portal

    busy = SessionPool(stuck_login, lambda portal, value: value, lambda value: None)
    waiting = Portal(login_wait=0.2)
    holder = threading.Thread(target=lambda: busy.check("tjx"))
    holder.start()
    assert inside.wait(5)
    with pytest.raises(session_pool.SessionBusy, match="login slot"):
        waiting.use("tjy")
    assert waiting.logins == []
    release.set()
    holder.join(5)
    assert waiting.use("tjy") == "/tjy/1"


def test_a_failed_login_gives_back_its_slot_and_the_session_lock(monkeypatch):
    monkeypatch.setenv("JURISYNC_MAX_LOGINS", "1")
    attempts = []

    def failing(portal):
        attempts.append(portal)
        raise session_pool.LoginFailed("captcha", reason="captcha")

    pool = SessionPool(failing, lambda portal, value: value, lambda value: None, session_wait=0.2, login_wait=0.2)
    for _ in range(3):
        with pytest.raises(session_pool.LoginFailed):
            with pool.session("tjx"):
                pass
    assert attempts == ["tjx"] * 3
