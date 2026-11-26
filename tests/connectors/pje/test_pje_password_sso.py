"""Offline checks of the password + TOTP SSO entry for PJe-JT portals (TST). No portal requests."""

import functools
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from jurisync.connectors.pje.auth import sso
from jurisync.core import totp
from jurisync.core.session_pool import LoginFailed, LoginRejected


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
    assert sso.https_pje("http://pje.tst.jus.br/pjekz/") == "https://pje.tst.jus.br/pjekz/"
    assert sso.https_pje("http://example.com/") == "http://example.com/"


def test_superior_court_accepts_origin_numbers_of_its_branch_only():
    from jurisync import portals
    from jurisync.connectors.pje.parsers import parse_process_number

    tst, trt12 = portals.pje("tst"), portals.pje("trt12-1g")
    origin = parse_process_number("0001514-43.2024.5.12.0048")
    assert tst.owns_number(origin) and trt12.owns_number(origin)
    assert not tst.owns_number(parse_process_number("0815689-46.2025.8.19.0042"))
    assert not trt12.owns_number(parse_process_number("0000371-73.2026.5.18.0018"))


def test_trf6_pje_accepts_the_numbers_it_inherited_from_the_trf1():
    from jurisync.connectors.pje.process import require_own_number

    for number in ("0004400-74.2019.4.01.3800", "1001234-56.2023.4.06.3800"):
        assert require_own_number("trf6-pje-1g", number)
    for number in ("1001234-56.2023.4.05.8300", "1001234-56.2023.8.01.0001", "1001234-56.2023.5.06.0001"):
        with pytest.raises(ValueError, match="does not belong to TRF6"):
            require_own_number("trf6-pje-1g", number)
    with pytest.raises(ValueError, match="does not belong to TRF1"):
        require_own_number("trf1-1g", "1001234-56.2023.4.06.3800")


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
    assert sso.credential_failure(html)[0] == "flow"
    assert sso.credential_failure("<p>invalid request id</p>")[0] == "unknown"
    other = '<div id="kc-error-message"><p>Parâmetro inválido: redirect_uri</p></div>'
    assert sso.credential_failure(other)[0] == "unknown"


def alert(message, theme="pje-v2"):
    if theme == "pdpj":
        return f'<div id="kc-error-message"><p class="instruction">{message}</p></div>'
    return f'<form id="loginForm"></form><div class="alert alert-error"><span class="kc-feedback-text">{message}</span></div>'


# The Keycloak messages (pt-BR and en) of the credential step, as the PDPJ themes render them.
REFUSALS = (
    "Usuário ou senha inválidos.",
    "Nome de usuário ou senha inválida.",
    "Invalid username or password.",
    "Invalid username, email or password.",
    "Credenciais inválidas",
)
FLOW = (
    "Cookie não encontrado. Favor habilitar os cookies no seu navegador.",
    "Cookie not found. Please make sure cookies are enabled in your browser.",
    "O tempo de tentativa de login expirou. O login será reiniciado desde o início.",
    "Your login attempt timed out. Login will start from the beginning.",
    "Página expirou.",
    "Page has expired. Please restart the login process.",
    "Ação expirada. Por favor, continue com o login agora.",
    "Sessão de autenticação expirada ou inválida",
    "Requisição inválida",
    "Invalid Request",
    "Você já está autenticado.",
    "You are already logged in.",
    "Erro inesperado ao autenticar com provedor de identidade",
)


@pytest.mark.parametrize("message", REFUSALS)
@pytest.mark.parametrize("theme", ["pje-v2", "pdpj"])
def test_each_keycloak_refusal_is_credentials_refused_with_its_text(message, theme):
    error = sso.credential_refusal(alert(message, theme))
    assert type(error) is LoginRejected and error.reason == "credentials_refused"
    assert f'SSO message: "{message}"' in str(error)


@pytest.mark.parametrize("message", FLOW)
@pytest.mark.parametrize("theme", ["pje-v2", "pdpj"])
def test_each_keycloak_flow_error_is_a_retryable_login_failure_with_its_text(message, theme):
    error = sso.credential_refusal(alert(message, theme))
    assert type(error) is LoginFailed and error.reason == "unexpected" and error.flow_error
    assert f'SSO message: "{message}"' in str(error)


def test_expired_password_and_blocked_account_keep_their_reasons():
    assert sso.credential_refusal(alert("Senha expirada.")).reason == "password_expired"
    assert sso.credential_refusal(alert("Conta desativada.")).reason == "blocked"
    assert not getattr(sso.credential_refusal(alert("Parâmetro inválido: client_id")), "flow_error", True)


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
    form = '<form id="kc-form-login" action="https://sso.cloud.pje.jus.br/auth/realms/pje/login-actions/authenticate">'
    sso_url = "https://sso.cloud.pje.jus.br/auth/realms/pje/protocol/openid-connect/auth"
    monkeypatch.setattr(sso, "_sso_page", lambda session, url: page(200, sso_url, text=form))
    for answer, error, posts in ((PDPJ_FIELD_ERROR, LoginRejected, 1), (PDPJ_ERROR_PAGE, LoginFailed, 2)):
        posted = page(200, sso_url, text=answer)
        session = Mock()
        session.post.return_value = posted
        monkeypatch.setattr(sso.requests, "Session", Mock(return_value=session))
        with pytest.raises(error) as raised:
            sso.login_session("123.456.789-09", "pw", None, portal="tst")
        assert type(raised.value) is error
        # A refusal is never sent again; a flow error gets one fresh attempt, then says it happened twice.
        assert session.post.call_count == posts
        assert ("twice" in str(raised.value)) is (posts == 2)
        assert session.post.call_args.kwargs["data"]["username"] == "12345678909"


def test_a_flow_error_then_a_good_login_succeeds_on_a_fresh_session(monkeypatch):
    base = "https://sso.cloud.pje.jus.br/auth/realms/pje"
    form = f'<form id="kc-form-login" action="{base}/login-actions/authenticate">'
    otp = f'<form id="kc-otp-login-form" action="{base}/login-actions/authenticate?step=otp">'
    monkeypatch.setattr(
        sso, "_sso_page", lambda session, url: page(200, f"{base}/protocol/openid-connect/auth", text=form)
    )
    first, second = Mock(), Mock()
    first.post.return_value = page(200, f"{base}/login-actions/authenticate", text=PDPJ_ERROR_PAGE)
    second.post.return_value = page(200, f"{base}/login-actions/authenticate", text=otp)
    monkeypatch.setattr(sso.requests, "Session", Mock(side_effect=[first, second]))
    monkeypatch.setattr(sso, "fresh_code", lambda account: "000000")
    # The second attempt, on a new session, reaches the OTP step (whose answer here does not redirect).
    with pytest.raises(LoginRejected, match="OTP submission"):
        sso.login_session("123.456.789-09", "pw", None, portal="tst")
    assert first.post.call_count == 1 and second.post.call_count == 2


def test_a_jt_landing_without_the_panel_text_is_confirmed_by_the_rest_api(monkeypatch):
    """TRT19 lands on the /pjekz/ loader and its search page forwards to /consultaprocessual/: neither carries the
    panel markers, but the REST token knows the user, so the login worked."""
    from jurisync.connectors.pje import kz
    from jurisync.connectors.pje.auth import certificate

    base = "https://sso.cloud.pje.jus.br/auth/realms/pje"
    form = f'<form id="kc-form-login" action="{base}/login-actions/authenticate">'
    otp = f'<form id="kc-otp-login-form" action="{base}/login-actions/authenticate?step=otp">'
    monkeypatch.setattr(
        sso, "_sso_page", lambda session, url: page(200, f"{base}/protocol/openid-connect/auth", text=form)
    )
    monkeypatch.setattr(sso, "fresh_code", lambda account: "000000")
    loader = page(200, "https://pje.trt19.jus.br/pjekz/", text="<title>Processo Judicial Eletrônico</title>")
    session = Mock()
    session.post.side_effect = [
        page(200, f"{base}/login-actions/authenticate", text=otp),
        page(302, f"{base}/login-actions/authenticate", "https://pje.trt19.jus.br/pjekz/"),
    ]
    session.get.return_value = loader
    monkeypatch.setattr(sso.requests, "Session", Mock(return_value=session))
    monkeypatch.setattr(certificate, "_follow", lambda s, r: page(200, "https://pje.trt19.jus.br/consultaprocessual/"))
    checked = []
    monkeypatch.setattr(kz, "is_authenticated", lambda s, portal: checked.append(portal) or True)
    assert sso.login_session("123.456.789-09", "pw", None, portal="trt19-1g")[0] is session
    assert checked == ["trt19-1g"]

    monkeypatch.setattr(kz, "is_authenticated", lambda s, portal: False)
    session.post.side_effect = [
        page(200, f"{base}/login-actions/authenticate", text=otp),
        page(302, f"{base}/login-actions/authenticate", "https://pje.trt19.jus.br/pjekz/"),
    ]
    with pytest.raises(RuntimeError, match="at https://pje.trt19.jus.br/consultaprocessual/"):
        sso.login_session("123.456.789-09", "pw", None, portal="trt19-1g")


def test_the_one_code_sent_is_never_about_to_expire(monkeypatch):
    base = "https://sso.cloud.pje.jus.br/auth/realms/pje"
    form = f'<form id="kc-form-login" action="{base}/login-actions/authenticate">'
    otp = f'<form id="kc-otp-login-form" action="{base}/login-actions/authenticate?step=otp">'
    monkeypatch.setattr(
        sso, "_sso_page", lambda session, url: page(200, f"{base}/protocol/openid-connect/auth", text=form)
    )
    account = totp.parse_accounts("JBSWY3DPEHPK3PXP")[0]
    for now, expected_waits in ((1_800_000_010.0, []), (1_800_000_027.0, [4])):
        waits = []
        code = functools.partial(totp.fresh_code, clock=functools.partial(float, now), sleep=waits.append)
        monkeypatch.setattr(sso, "fresh_code", code)
        session = Mock()
        session.post.side_effect = [page(200, f"{base}/login-actions/authenticate", text=otp)] * 2
        monkeypatch.setattr(sso.requests, "Session", Mock(return_value=session))
        with pytest.raises(LoginRejected):
            sso.login_session("123.456.789-09", "pw", account, portal="trt19-1g")
        assert waits == expected_waits
        assert session.post.call_count == 2  # the credentials, then exactly one code
        if not waits:
            assert session.post.call_args.kwargs["data"]["otp"] == totp.totp(account).at(now)
