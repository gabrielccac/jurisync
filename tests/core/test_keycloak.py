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


def code_strings(text):
    """The string literals of a module that are not docstrings."""
    tree = ast.parse(text)
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            yield node.value


def test_no_connector_repeats_a_keycloak_id():
    ids = re.compile(r"\bkc-[a-z0-9-]+")
    copies = [
        f"{path.relative_to(CONNECTORS)}: {found}"
        for path, text in sources()
        for value in code_strings(text)
        for found in ids.findall(value)
    ]
    assert not copies, "use the constants of jurisync.core.keycloak:\n" + "\n".join(copies)


def test_the_a3_token_is_only_read_from_core():
    found = []
    for path, text in sources():
        if path.relative_to(CONNECTORS).as_posix() == "pje/auth/a3.py":
            continue  # the old command and names, kept for installs
        for node in ast.walk(ast.parse(text)):
            modules = []
            if isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module + "." + alias.name for alias in node.names]
            elif isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            if any(module.startswith("jurisync.connectors.pje.auth.a3") for module in modules):
                found.append(str(path.relative_to(CONNECTORS)))
    assert not found, found


@pytest.mark.parametrize("system", KEYCLOAK_USERS)
def test_each_keycloak_connector_reads_the_pages_from_core(system):
    text = "\n".join(path.read_text(encoding="utf-8") for path in (CONNECTORS / system).rglob("*.py"))
    assert re.search(r"jurisync\.core\.keycloak|from jurisync\.core import [^\n]*\bkeycloak\b", text)


def test_the_eproc_keycloak_message_masks_identifiers():
    from jurisync.connectors.eproc.a3 import eproc_message

    page = '<span id="input-error">Usuário 000.000.001-91 (ficticia@example.com) sem cadastro</span>'
    assert eproc_message("https://eproc.example/login", page) == "Usuário […] ([…]) sem cadastro"
    url = "https://eproc.example/login?msg=CPF+00000000191+bloqueado"
    assert eproc_message(url, "") == "CPF […] bloqueado"
    # A maintenance notice in a generic alert box is not a Keycloak message and stays out.
    assert eproc_message("https://eproc.example/", '<div class="alert">Manutenção às 22h</div>') == ""


def test_the_old_a3_module_keeps_its_names():
    from jurisync.connectors.pje.auth import a3 as old
    from jurisync.core import a3

    for name in ("A3Signer", "PinError", "token_lock", "stored_pin", "held_token", "main"):
        assert getattr(old, name) is getattr(a3, name)


class FakeToken:
    closed = 0
    protected = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        FakeToken.closed += 1


def test_held_token_closes_the_token_and_releases_the_lock_on_error(monkeypatch):
    from contextlib import contextmanager

    from jurisync.core import a3

    events = []

    @contextmanager
    def lock():
        events.append("locked")
        try:
            yield
        finally:
            events.append("released")

    monkeypatch.setattr(a3, "token_lock", lock)
    monkeypatch.setattr(a3, "A3Signer", FakeToken)
    FakeToken.closed = 0
    with pytest.raises(RuntimeError), a3.held_token() as token:
        assert isinstance(token, FakeToken)
        raise RuntimeError("portal down")
    assert FakeToken.closed == 1
    assert events == ["locked", "released"]


def test_held_token_with_a_signer_takes_no_lock(monkeypatch):
    from jurisync.core import a3

    monkeypatch.setattr(a3, "token_lock", lambda: (_ for _ in ()).throw(AssertionError("locked")))
    signer = object()
    with a3.held_token(signer) as token:
        assert token is signer
