"""Two offices on one hosted server: each Eproc login uses the office's own password and authenticator, and each
office keeps its own session (its cookies, and the pending-queue reading kept per session)."""

from types import SimpleNamespace

from jurisync.connectors.eproc import http_login, server
from jurisync.core import credentials

PORTAL = "trf4-1g-sc"
OFFICES = {"office-a": "JBSWY3DPEHPK3PXP", "office-b": "GEZDGNBVGY3TQOJQ"}


def scope(tenant):
    return credentials.scope(
        tenant,
        {
            "TRF4_1G_SC_USERNAME": f"user-{tenant}",
            "TRF4_1G_SC_PASSWORD": f"password-{tenant}",
            "TRF4_1G_SC_2FA_DATA": f"otpauth://totp/Eproc?secret={OFFICES[tenant]}",
        },
    )


def test_each_office_logs_in_with_its_own_account_and_keeps_its_session(monkeypatch):
    # The operator's own login in the server's environment is never used for an office.
    monkeypatch.setenv("TRF4_1G_SC_USERNAME", "operator")
    monkeypatch.setenv("TRF4_1G_SC_PASSWORD", "operator-password")
    monkeypatch.setenv("TRF4_1G_SC_2FA_DATA", "otpauth://totp/Eproc?secret=MFRGGZDFMZTWQ2LK")
    logins = []

    def login(username, password, account, *, portal, certificate=False):
        session = SimpleNamespace(owner=credentials.tenant(), close=lambda: None)
        logins.append((credentials.tenant(), username, password, account["secret"], portal, certificate))
        return session, "<html>painel</html>", f"https://eproc1g.trf4.jus.br/painel?{credentials.tenant()}"

    monkeypatch.setattr(http_login, "login", login)
    monkeypatch.setattr(server.sessions, "_refresh", lambda portal, value: value)
    seen = {}
    try:
        for tenant in ("office-a", "office-b", "office-a"):
            with scope(tenant):
                seen.setdefault(tenant, []).append(server.sessions.run(PORTAL, lambda value: value))
    finally:
        server.sessions.close()
    assert logins == [
        ("office-a", "user-office-a", "password-office-a", OFFICES["office-a"], PORTAL, False),
        ("office-b", "user-office-b", "password-office-b", OFFICES["office-b"], PORTAL, False),
    ]
    assert seen["office-a"][0] is seen["office-a"][1]
    assert seen["office-a"][0][0].owner == "office-a" and seen["office-b"][0][0].owner == "office-b"
