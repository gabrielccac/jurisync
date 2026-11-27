from jurisync.core.session_pool import LoginFailed, LoginRejected, SessionPool
from jurisync.install import smoke


def targets(*keys):
    return [smoke.Target("pje", key, "password_totp") for key in keys]


def test_one_attempt_each_and_a_wait_between_portal_logins():
    tried, waits = [], []
    results = smoke.run(
        [*targets("trf1-1g", "trf1-2g"), smoke.Target("advbox", "advbox", "password")],
        say=lambda _line: None,
        attempt=lambda target: tried.append(target.key),
        wait=waits.append,
    )
    assert tried == ["trf1-1g", "trf1-2g", "advbox"]
    assert len(waits) == 1  # before the second authenticator login; a service asks no code
    assert [result.status for result in results] == ["ok", "ok", "ok"]


def test_a_refusal_stops_the_run_and_the_rest_is_not_tried():
    tried = []

    def attempt(target):
        tried.append(target.key)
        if target.key == "trf1-1g":
            raise LoginRejected("password Fict1cia-123 refused at https://pje1g.example/login", reason="otp_refused")

    results = smoke.run(targets("trf1-1g", "trf1-2g"), say=lambda _line: None, attempt=attempt, wait=lambda _say: None)
    assert tried == ["trf1-1g"]
    assert [(result.status, result.reason) for result in results] == [("falha", "otp_refused"), ("nao_testado", "")]
    assert "https://" not in results[0].detail


def test_the_detail_carries_no_secret_cpf_or_e_mail(monkeypatch):
    monkeypatch.setenv("TRF1_1G_PASSWORD", "Fict1cia-123")

    def attempt(_target):
        raise LoginRejected("password Fict1cia-123 user 12345678909 (ficticia@example.com) refused")

    (result,) = smoke.run(targets("trf1-1g"), say=lambda _line: None, attempt=attempt, wait=lambda _say: None)
    for value in ("Fict1cia-123", "12345678909", "ficticia@example.com"):
        assert value not in result.detail


def test_a_captcha_stops_but_an_unavailable_portal_does_not():
    def attempt(target):
        raise LoginFailed("down", reason="portal_unavailable" if target.key == "trf1-1g" else "captcha")

    results = smoke.run(
        targets("trf1-1g", "trf1-2g", "trf3-1g"), say=lambda _line: None, attempt=attempt, wait=lambda _say: None
    )
    assert [result.status for result in results] == ["falha", "falha", "nao_testado"]


def test_verify_closes_the_session_and_fails_when_it_is_not_logged_in():
    closed = []
    pool = SessionPool(login=lambda _portal: "session", refresh=lambda _portal, _session: None, close=closed.append)
    try:
        pool.verify("trf1-1g")
    except LoginFailed as error:
        assert "logged-in" in str(error)
    else:
        raise AssertionError("verify accepted a session the portal does not show as logged in")
    assert closed == ["session"]


def test_verify_uses_the_given_login_instead_of_the_pools():
    pool = SessionPool(
        login=lambda _portal: "cached", refresh=lambda _portal, session: session, close=lambda _session: None
    )
    used = []
    pool.verify("trf1-1g", lambda portal: used.append(portal) or "fresh")
    assert used == ["trf1-1g"]


def test_compare_lists_only_changes():
    before = {"results": [{"target": "trf1-1g", "method": "password_totp", "status": "ok", "reason": ""}]}
    after = {"results": [{"target": "trf1-1g", "method": "password_totp", "status": "falha", "reason": "captcha"}]}
    assert smoke.compare(before, before) == ["sem diferenças de método ou resultado"]
    assert smoke.compare(before, after) == ["trf1-1g: password_totp ok  -> password_totp falha captcha"]


def test_targets_follow_the_configured_portals(monkeypatch):
    for name in ("ADVBOX_EMAIL", "ADVBOX_PASSWORD", "JURIDIQ_EMAIL", "JURIDIQ_PASSWORD", "JURISYNC_CUSTOMER"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ADVBOX_EMAIL", "ficticio@example.com")
    monkeypatch.setenv("ADVBOX_PASSWORD", "Fict1cia")
    assert "advbox" not in {target.key for target in smoke.targets()}  # a login may e-mail the lawyer a code
    found = {target.key: target for target in smoke.targets(services=True)}
    assert found["advbox"].system == "advbox"
    assert "juridiq" not in found
    assert set(smoke.targets({"advbox"})) == {found["advbox"]}


def test_a_device_code_stops_the_run_and_no_e_mail_is_shown():
    from jurisync.connectors.advbox.session import AdvboxDeviceVerification

    def attempt(_target):
        raise AdvboxDeviceVerification("advogada.ficticia@example.com")

    results = smoke.run(
        [smoke.Target("advbox", "advbox", "password"), smoke.Target("juridiq", "juridiq", "password")],
        say=lambda _line: None,
        attempt=attempt,
        wait=lambda _say: None,
    )
    assert [result.status for result in results] == ["falha", "nao_testado"]
    assert "@" not in results[0].detail


class FakeAdvbox:
    writes = True

    def ensure_session(self):
        import os
        from pathlib import Path

        path = Path(os.environ["ADVBOX_SESSION_FILE"])
        assert not path.exists()  # starts from an empty session, never the server's cache
        if FakeAdvbox.writes:
            path.write_text("{}", encoding="utf-8")

    def close(self):
        pass


def test_a_service_logs_in_from_a_scratch_session_file(monkeypatch, tmp_path):
    from jurisync.connectors.advbox import client

    server_file = tmp_path / "advbox-server.json"
    server_file.write_text('{"cookies": []}', encoding="utf-8")
    monkeypatch.setenv("ADVBOX_SESSION_FILE", str(server_file))
    monkeypatch.setattr(client, "AdvboxClient", FakeAdvbox)
    FakeAdvbox.writes = True
    smoke.login(smoke.Target("advbox", "advbox", "password"))
    assert server_file.read_text(encoding="utf-8") == '{"cookies": []}'
    assert smoke.os.environ["ADVBOX_SESSION_FILE"] == str(server_file)
    FakeAdvbox.writes = False  # the check passed on something other than a new login: not ok
    try:
        smoke.login(smoke.Target("advbox", "advbox", "password"))
    except RuntimeError as error:
        assert "no login was proven" in str(error)
    else:
        raise AssertionError("an ADVBOX check without a new session counted as a login")


def test_main_runs_the_targets_and_saves_without_values(monkeypatch, tmp_path):
    loaded = []
    monkeypatch.setattr(smoke, "load_env", loaded.append)
    monkeypatch.setattr(smoke, "targets", lambda only, services=False: targets("trf1-1g"))
    monkeypatch.setattr(smoke, "run", lambda found, say: [smoke.Result("trf1-1g", "password_totp", "ok", seconds=1.0)])
    saved = tmp_path / "antes.json"
    assert smoke.main(["victor", "--save", str(saved)], say=lambda _line: None) == 0
    assert loaded == ["victor"]
    assert '"status": "ok"' in saved.read_text(encoding="utf-8")


def test_load_env_reads_the_shared_file(monkeypatch):
    from jurisync import customers

    calls = []
    monkeypatch.setattr(customers, "load_env_file", lambda: calls.append("loaded"))
    monkeypatch.delenv("JURISYNC_CUSTOMER", raising=False)
    smoke.load_env("")
    assert calls == ["loaded"]
