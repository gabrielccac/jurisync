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


def test_superior_court_accepts_origin_numbers_of_its_branch_only():
    from jurisync import portals
    from jurisync.connectors.pje.parsers import parse_process_number

    tst, trt12 = portals.pje("tst"), portals.pje("trt12-1g")
    origin = parse_process_number("0001514-43.2024.5.12.0048")
    assert tst.owns_number(origin) and trt12.owns_number(origin)
    assert not tst.owns_number(parse_process_number("0815689-46.2025.8.19.0042"))
    assert not trt12.owns_number(parse_process_number("0000371-73.2026.5.18.0018"))


def test_court_refusal_after_the_sso_is_read_from_the_callback_page():
    page = "<script>\n\t\tvar mensagemErroLogin = 'Senha expirada. Solicite uma nova senha';\n</script>"
    assert sso.portal_login_error(page) == "Senha expirada. Solicite uma nova senha"
    assert sso.portal_login_error("<script>var mensagemErroLogin = '';</script>") == ""
    assert sso.portal_login_error("<html></html>") == ""


# Anonymized shapes of the two PDPJ SSO themes: pje-v2 (TRF1, loginForm) and pdpj (PJe-JT, kc-form-login).
PJE_V2_REFUSAL = (
    '<form id="loginForm" action="/x"></form><div class="alert alert-error">'
    '<span class="kc-feedback-text">Usuário ou senha inválidos.</span></div>'
)
PDPJ_FIELD_ERROR = (
    '<form id="kc-form-login" action="/x"><input name="username" value="123.456.789-09">'
    '<span id="input-error" class="kc-feedback-text">Usuário ou senha inválidos.</span></form>'
)
PDPJ_ERROR_PAGE = (
    '<div id="kc-error-message"><p class="instruction">Cookie não encontrado. Favor habilitar os cookies no seu '
    'navegador.</p><p><a id="backToApplication" href="/x">Voltar para login</a></p></div>'
)


def test_a_password_refusal_quotes_the_sso_alert():
    for html in (PJE_V2_REFUSAL, PDPJ_FIELD_ERROR):
        kind, reason = sso.credential_failure(html)
        assert kind == "credentials"
        assert 'SSO message: "Usuário ou senha inválidos."' in reason
        assert "123" not in reason


def test_a_keycloak_flow_error_is_not_a_password_refusal():
    kind, reason = sso.credential_failure(PDPJ_ERROR_PAGE)
    assert kind == "flow"
    assert "Cookie não encontrado" in reason and "Voltar" not in reason


def test_a_bare_invalid_elsewhere_on_the_page_is_not_a_password_refusal():
    html = '<form id="kc-form-login"></form><div class="alert alert-error"><span class="kc-feedback-text">Sessão inválida</span></div>'
    assert sso.credential_failure(html)[0] == "unknown"
    assert sso.credential_failure("<p>invalid request id</p>")[0] == "unknown"


def test_a_disabled_account_is_told_apart_and_identifiers_are_masked():
    html = (
        '<div class="alert-error"><span class="kc-feedback-text">Conta desativada 123.456.789-09 a@b.com</span></div>'
    )
    kind, reason = sso.credential_failure(html)
    assert kind == "blocked"
    assert "123.456" not in reason and "a@b.com" not in reason


def test_the_pdpj_theme_gets_the_cpf_as_digits_and_the_trf_theme_as_configured():
    assert sso.sso_username("123.456.789-09", "kc-form-login") == "12345678909"
    assert sso.sso_username("12.345.678/0001-95", "kc-form-login") == "12345678000195"
    assert sso.sso_username("123.456.789-09", "loginForm") == "123.456.789-09"
    assert sso.sso_username("someone@example.com", "kc-form-login") == "someone@example.com"


def test_only_a_refusal_raises_login_rejected(monkeypatch):
    import pytest

    from jurisync.core.session_pool import LoginRejected

    form = '<form id="kc-form-login" action="https://sso.cloud.pje.jus.br/auth/realms/pje/login-actions/authenticate">'
    sso_url = "https://sso.cloud.pje.jus.br/auth/realms/pje/protocol/openid-connect/auth"
    monkeypatch.setattr(sso, "_sso_page", lambda session, url: page(200, sso_url, text=form))
    for answer, error in ((PDPJ_FIELD_ERROR, LoginRejected), (PDPJ_ERROR_PAGE, RuntimeError)):
        posted = page(200, sso_url, text=answer)
        session = Mock()
        session.post.return_value = posted
        monkeypatch.setattr(sso.requests, "Session", Mock(return_value=session))
        with pytest.raises(error) as raised:
            sso.login_session("123.456.789-09", "pw", None, portal="tst")
        assert type(raised.value) is error
        assert session.post.call_args.kwargs["data"]["username"] == "12345678909"
