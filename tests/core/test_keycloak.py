"""core.keycloak and core.a3 are the one place the Keycloak pages and the A3 token are known; every connector that
logs in through Keycloak (PJe, JPe, Eproc, Projudi) or with the A3 (PJe, Eproc) reads them from there."""

import ast
import pathlib
import re

import pytest

from jurisync.core import keycloak
from jurisync.core.session_pool import LoginFailed, LoginRejected

CONNECTORS = pathlib.Path(__file__).resolve().parents[2] / "src" / "jurisync" / "connectors"
KEYCLOAK_USERS = ("pje", "jpe", "eproc", "projudi")


def page(form_id, body=""):
    return f'<html><form id="{form_id}" action="/auth/realms/pje/login-actions/x">{body}</form></html>'


def test_form_ids_of_each_step():
    assert keycloak.find_form(page(keycloak.OTP_FORM), keycloak.OTP_FORM) is not None
    assert keycloak.find_form(page(keycloak.LOGIN_FORM), keycloak.OTP_FORM) is None
    assert keycloak.find_form(None, keycloak.OTP_FORM) is None


@pytest.mark.parametrize(
    ("html", "form_id"),
    [(page("loginForm"), "loginForm"), (page("kc-form-login"), "kc-form-login"), ("<p>other</p>", "kc-form-login")],
)
def test_login_form_of_the_trf_and_pdpj_themes(html, form_id):
    assert keycloak.login_form_id(html) == form_id


def test_pdpj_theme_gets_the_cpf_as_digits_and_the_trf_theme_as_configured():
    form = keycloak.credentials_form("000.000.001-91", "Fict1cia", keycloak.LOGIN_FORM)
    assert form == {"username": "00000000191", "password": "Fict1cia", "pjeoffice-code": "", "phrase": ""}
    assert keycloak.credentials_form("000.000.001-91", "x", keycloak.TRF_LOGIN_FORM)["username"] == "000.000.001-91"


@pytest.mark.parametrize(
    ("alert", "reason"),
    [
        ("Usuário ou senha inválidos.", "credentials_refused"),
        ("Invalid username or password.", "credentials_refused"),
        ("Senha expirada.", "password_expired"),
        ("Conta desativada.", "blocked"),
    ],
)
def test_refusals_of_each_theme_are_login_rejected(alert, reason):
    for theme in (
        '<span id="input-error">{}</span>',
        '<div class="alert-error"><span class="kc-feedback-text">{}</span></div>',
    ):
        error = keycloak.credential_refusal(theme.format(alert))
        assert isinstance(error, LoginRejected)
        assert error.reason == reason


def test_a_broken_flow_is_not_a_refusal_and_identifiers_never_leave():
    error = keycloak.credential_refusal('<p id="kc-error-message">Cookie não encontrado para 000.000.001-91</p>')
    assert not isinstance(error, LoginRejected)
    assert isinstance(error, LoginFailed)
    assert error.flow_error
    assert "000.000.001-91" not in str(error)


def test_a_waf_page_is_a_captcha():
    assert keycloak.credential_refusal("<p>AWS WAF captcha</p>").reason == "captcha"


def sources():
    for path in CONNECTORS.rglob("*.py"):
        yield path, path.read_text(encoding="utf-8")


def test_no_connector_repeats_a_keycloak_form_id():
    ids = re.compile(r"[\"']kc-[a-z0-9-]+[\"']")
    copies = [f"{path.relative_to(CONNECTORS)}: {match}" for path, text in sources() for match in ids.findall(text)]
    assert not copies, "use the constants of jurisync.core.keycloak:\n" + "\n".join(copies)


def test_the_a3_token_is_only_read_from_core():
    found = [
        str(path.relative_to(CONNECTORS))
        for path, text in sources()
        for node in ast.walk(ast.parse(text))
        if isinstance(node, ast.ImportFrom)
        and (node.module or "").startswith("jurisync.connectors.pje.auth")
        and ((node.module or "").endswith(".a3") or any(alias.name == "a3" for alias in node.names))
    ]
    assert not found, found


@pytest.mark.parametrize("system", KEYCLOAK_USERS)
def test_each_keycloak_connector_reads_the_pages_from_core(system):
    text = "\n".join(path.read_text(encoding="utf-8") for path in (CONNECTORS / system).rglob("*.py"))
    assert re.search(r"jurisync\.core\.keycloak|from jurisync\.core import [^\n]*\bkeycloak\b", text)
