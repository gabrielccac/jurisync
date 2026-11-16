"""Offline checks of the password + TOTP SSO entry for PJe-JT portals (TST). No portal requests."""

from types import SimpleNamespace
from unittest.mock import Mock

from jurisync.connectors.pje.auth import sso


def page(status, url, location=None, text=""):
    return SimpleNamespace(status_code=status, url=url, headers={"Location": location} if location else {}, text=text)


def test_jt_login_page_enters_the_sso_through_authenticate_sso():
    session = Mock()
    sso_page = page(200, "https://sso.cloud.pje.jus.br/auth/realms/pje/protocol/openid-connect/auth")
    session.get.side_effect = [
        page(200, "https://pje.tst.jus.br/tst/login.seam"),
        page(302, "https://pje.tst.jus.br/tst/authenticateSSO.seam", sso_page.url),
        sso_page,
    ]
    assert sso._sso_page(session, "https://pje.tst.jus.br/tst/login.seam") is sso_page
    assert session.get.call_args_list[1].args[0] == "https://pje.tst.jus.br/tst/authenticateSSO.seam"


def test_redirecting_login_page_keeps_the_trf1_path():
    session = Mock()
    target = page(200, "https://sso.cloud.pje.jus.br/auth/x")
    session.get.side_effect = [page(302, "https://pje1g.trf1.jus.br/pje/login.seam", target.url), target]
    assert sso._sso_page(session, "https://pje1g.trf1.jus.br/pje/login.seam") is target
    assert session.get.call_count == 2


def test_login_form_accepts_the_keycloak_form_id():
    html = '<form id="kc-form-login" action="/auth/realms/pje/login-actions/authenticate?x=1"></form>'
    url = sso._login_form(html, "https://sso.cloud.pje.jus.br/auth/realms/pje/protocol/openid-connect/auth")
    assert url.startswith("https://sso.cloud.pje.jus.br/auth/realms/pje/login-actions/authenticate")


def test_plain_http_bounce_to_a_pje_host_is_upgraded_only_there():
    assert sso._https_pje("http://pje.tst.jus.br/pjekz/") == "https://pje.tst.jus.br/pjekz/"
    assert sso._https_pje("http://example.com/") == "http://example.com/"
