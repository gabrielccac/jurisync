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
