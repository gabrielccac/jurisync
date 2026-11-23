"""One vocabulary for failed logins: the reason codes, where they are set and how they are reported."""

import contextlib
from types import SimpleNamespace
from unittest import mock

import pytest
from curl_cffi.requests import exceptions as curl_errors
from mcp.server.mcpserver.exceptions import ToolError

from jurisync.connectors.eproc import a3
from jurisync.connectors.jpe.client import JpeError
from jurisync.connectors.pje.auth import certificate as pje_certificate
from jurisync.connectors.pje.auth import sso
from jurisync.connectors.stj import client as stj
from jurisync.core import certificate, session_pool, totp
from jurisync.core.mcp import tool_errors
from jurisync.core.session_pool import (
    EXPLANATIONS,
    REASONS,
    REFUSALS,
    LoginFailed,
    LoginRejected,
    MissingLogin,
    SessionPool,
    login_reason,
)
from jurisync.hosted import access, cli
from jurisync.install import setup

ACCOUNT = {"type": "totp", "secret": "JBSWY3DPEHPK3PXP"}


@pytest.fixture(autouse=True)
def fresh_login_slots(monkeypatch):
    monkeypatch.setattr(session_pool, "_login_slots", None)


def test_every_reason_has_an_explanation_and_refusals_are_reasons():
    assert set(EXPLANATIONS) == set(REASONS)
    assert set(REFUSALS) < set(REASONS)
    assert "OTP_PROFILE_MATCH" in EXPLANATIONS["otp_refused"]


def test_reason_comes_from_the_exception_or_its_type():
    assert login_reason(LoginRejected("wrong")) == "credentials_refused"
    assert login_reason(LoginRejected("code", reason="otp_refused")) == "otp_refused"
    assert login_reason(MissingLogin("Set X")) == "login_missing"
    assert login_reason(LoginFailed("odd")) == "unexpected"
    assert login_reason(curl_errors.Timeout("slow")) == "portal_unavailable"
    assert login_reason(ConnectionResetError()) == "portal_unavailable"
    assert login_reason(RuntimeError("anything")) == "unexpected"
    assert isinstance(LoginRejected("x"), RuntimeError)


def test_tool_errors_carry_the_reason_code():
    with pytest.raises(ToolError, match=r"^PJe request failed \[otp_refused\]: code refused$"):
        with tool_errors("PJe", RuntimeError):
            raise LoginRejected("code refused", reason="otp_refused")
    with pytest.raises(ToolError, match=r"^PJe request failed: not found$"):
        with tool_errors("PJe", ValueError):
            raise ValueError("not found")


def test_the_cooldown_repeats_the_reason_of_the_refusal():
    def login(portal):
        raise LoginRejected("expired", reason="password_expired")

    pool = SessionPool(login, lambda portal, value: value, lambda value: None, login_cooldown=900)
    for _ in range(2):
        with pytest.raises(LoginRejected) as raised:
            pool.check("trf1-1g")
        assert raised.value.reason == "password_expired"


def test_setup_and_hosted_checks_explain_the_reason_in_portuguese():
    with mock.patch.object(setup.registry, "get") as registry:
        registry.return_value.sessions.return_value.check.side_effect = LoginRejected("x", reason="not_registered")
        ok, message = setup.check_portal("projudi", "tjgo-1g")
    assert not ok
    assert message.startswith("o tribunal não tem cadastro para este certificado/CPF [not_registered]")

    said, store = [], mock.Mock()
    with (
        mock.patch.object(access, "tenant_scope", return_value=contextlib.nullcontext()),
        mock.patch.object(access, "check", side_effect=LoginRejected("refused", reason="otp_refused")),
    ):
        assert cli.run_check(store, "office-a", "trf1-1g", {}, {}, said.append) is False
    assert "o código do autenticador foi recusado" in said[-1]
    assert store.save_check.call_args.args[3] == "[otp_refused] refused"


def test_a_code_asked_again_after_two_codes_is_an_otp_refusal():
    pages = []
    with mock.patch.object(totp, "next_code", return_value="222222"):
        with pytest.raises(LoginRejected, match="eproc rejected the OTP") as raised:
            totp.answer_code(
                "otp", ACCOUNT, lambda page: page, lambda page, form, code: pages.append(code) or "otp", label="eproc"
            )
    assert raised.value.reason == "otp_refused"
    assert len(pages) == 2 and pages[1] == "222222"
    lazy = mock.Mock(side_effect=AssertionError("not read"))
    assert totp.answer_code("panel", lazy, lambda page: None, None, label="eproc") == "panel"


def test_the_authenticator_and_the_pin_are_login_missing(monkeypatch):
    monkeypatch.delenv("PJE_A1_PIN", raising=False)
    monkeypatch.delenv("PJE_A1_P12", raising=False)
    portal = SimpleNamespace(env=lambda name: "", env_name=lambda name: f"X_{name}")
    with pytest.raises(MissingLogin, match="X_2FA_DATA or X_2FA_QR"):
        totp.configured_account(portal)
    assert totp.configured_account(portal, None) is None
    with pytest.raises(MissingLogin, match="Set PJE_A1_PIN"):
        certificate.load_configured()
    assert not certificate.is_configured("STJ")


# --- Connectors ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("alert", "reason"),
    [
        ("Senha expirada", "password_expired"),
        ("Conta desativada", "blocked"),
        ("Usuário ou senha inválidos", "credentials_refused"),
    ],
)
def test_pdpj_sso_refusals_name_their_reason(alert, reason):
    error = sso.credential_refusal(f'<span id="input-error">{alert}</span>', JpeError)
    assert isinstance(error, LoginRejected)
    assert error.reason == reason


def test_pdpj_sso_waf_page_is_a_captcha_of_the_callers_type():
    error = sso.credential_refusal("<p>AWS WAF captcha</p>", JpeError)
    assert type(error) is JpeError
    assert login_reason(error) == "captcha"


def test_pje_pre_registration_page_is_not_registered():
    response = SimpleNamespace(url="https://pje.tjce.jus.br/pje2grau/publico/usuario/cadastro.seam")
    with pytest.raises(LoginRejected) as raised:
        pje_certificate._require_registered(response, SimpleNamespace(key="tjce-2g"))
    assert raised.value.reason == "not_registered"


def test_stj_without_registration_is_not_registered():
    http = mock.Mock()
    http.get.return_value = SimpleNamespace(status_code=200, json=lambda: False)
    with pytest.raises(stj.StjNotRegistered) as raised:
        stj.check_registered(http, "12345678909")
    assert login_reason(raised.value) == "not_registered"


def test_eproc_a3_keycloak_otp_refusal_is_a_rejection():
    form = '<form id="kc-otp-login-form" action="/realms/eproc/login-actions/authenticate"></form>'
    page = SimpleNamespace(url="https://sso.tjrs.jus.br/realms/eproc/", text=form)
    session = mock.Mock()
    session.post.return_value = page
    with (
        mock.patch.object(a3, "portal_totp_account", return_value=ACCOUNT),
        mock.patch.object(totp, "next_code", return_value="222222"),
        pytest.raises(LoginRejected) as raised,
    ):
        a3._submit_otp(session, page, "tjrs-1g")
    assert raised.value.reason == "otp_refused"
    assert session.post.call_count == 2
