import logging
import os
from unittest import mock

import pytest

from jurisync.core.mcp import is_machine_name, load_env_file, sanitize_diagnostics


@pytest.fixture
def files(tmp_path):
    """A customer ``acme`` with its own .env, a shared file, and an environment restored afterwards."""
    (tmp_path / "acme").mkdir()
    (tmp_path / "acme" / "portals.toml").write_text(
        '[env]\nset = { ADVBOX_ALLOW_WRITES = "false" }\n', encoding="utf-8"
    )
    keep = {name: os.environ[name] for name in ("PATH", "HOME", "USERPROFILE") if name in os.environ}
    with mock.patch.dict(os.environ, {**keep, "JURISYNC_CUSTOMERS_DIR": str(tmp_path)}, clear=True):
        yield tmp_path / "acme" / ".env", tmp_path / "shared.env"


SHARED = """
PJE_1G_USERNAME=someone-else
PJE_1G_PASSWORD=shared-password
PJE_1G_2FA_DATA=shared-totp
PJE_A1_P12=/shared/a1.p12
PJE_1G_CERT=true
PJE_1G_SESSION_ACCOUNT=other-account
PJE_1G_OTP_PROFILE_INDEX=3
PJE_ALLOW_DRAFT_WRITES=true
ADVBOX_ALLOW_WRITES=true
JURIDIQ_ALLOW_WRITES=true
JURIDIQ_LOGIN_URL=https://elsewhere.example/login
ADVBOX_BASE_URL=https://elsewhere.example
ADVBOX_SESSION_FILE=/shared/advbox.json
ESAJ_BRIDGE_PROFILE=/shared/chrome
OPENROUTER_API_KEY=shared-key
JURISYNC_VAULT_KEY=vault-key
DJEN_SEARCHES=[]
JURISYNC_DOCUMENTS_DIR=/machine/documents
JURISYNC_CHROME_PATH="/machine/chrome"
LIBREOFFICE_BIN=/machine/soffice # the local build
ADVBOX_TIMEOUT_SECONDS=90
"""
MACHINE = {"JURISYNC_DOCUMENTS_DIR", "JURISYNC_CHROME_PATH", "LIBREOFFICE_BIN", "ADVBOX_TIMEOUT_SECONDS"}
SHARED_WRITES = ("PJE_ALLOW_DRAFT_WRITES", "JURIDIQ_ALLOW_WRITES")


def test_a_write_grant_in_the_shared_file_reaches_no_office(files):
    own, shared = files
    own.write_text("PJE_1G_USERNAME=acme-user\n", encoding="utf-8")
    shared.write_text(SHARED, encoding="utf-8")
    os.environ["JURISYNC_CUSTOMER"] = "acme"
    load_env_file(shared)
    for name in SHARED_WRITES:
        assert name not in os.environ


def test_an_office_grants_its_own_writes(files):
    own, shared = files
    own.write_text("JURIDIQ_ALLOW_WRITES=true\n", encoding="utf-8")
    shared.write_text("PJE_ALLOW_DRAFT_WRITES=true\n", encoding="utf-8")
    os.environ["JURISYNC_CUSTOMER"] = "acme"
    load_env_file(shared)
    assert os.environ["JURIDIQ_ALLOW_WRITES"] == "true"
    assert "PJE_ALLOW_DRAFT_WRITES" not in os.environ


def test_with_a_customer_the_shared_file_gives_machine_settings_only(files, caplog):
    own, shared = files
    own.write_text("PJE_1G_USERNAME=acme-user\nJURISYNC_DOCUMENTS_DIR=/acme/documents\n", encoding="utf-8")
    shared.write_text(SHARED, encoding="utf-8")
    os.environ["JURISYNC_CUSTOMER"] = "acme"
    before = set(os.environ)
    with caplog.at_level(logging.INFO, logger="jurisync.core.mcp"):
        load_env_file(shared)
    assert os.environ["PJE_1G_USERNAME"] == "acme-user"  # the customer's value wins
    assert os.environ["JURISYNC_DOCUMENTS_DIR"] == "/acme/documents"
    assert os.environ["ADVBOX_ALLOW_WRITES"] == "false"  # the office pin beats the shared flag
    assert os.environ["JURISYNC_CHROME_PATH"] == "/machine/chrome"
    assert os.environ["LIBREOFFICE_BIN"] == "/machine/soffice"
    assert os.environ["ADVBOX_TIMEOUT_SECONDS"] == "90"
    assert set(os.environ) - before == {"PJE_1G_USERNAME", "ADVBOX_ALLOW_WRITES", *MACHINE}
    skipped = caplog.records[-1].getMessage()
    for name in ("PJE_1G_PASSWORD", "PJE_1G_CERT", "JURIDIQ_LOGIN_URL", "JURISYNC_VAULT_KEY", *SHARED_WRITES):
        assert name in skipped
    for value in ("shared-password", "shared-totp", "vault-key", "elsewhere"):
        assert value not in caplog.text


def test_the_environment_beats_both_files(files):
    own, shared = files
    own.write_text("PJE_1G_USERNAME=acme-user\n", encoding="utf-8")
    shared.write_text("LIBREOFFICE_BIN=/machine/soffice\n", encoding="utf-8")
    os.environ.update(JURISYNC_CUSTOMER="acme", PJE_1G_USERNAME="from-process", LIBREOFFICE_BIN="from-process")
    load_env_file(shared)
    assert (os.environ["PJE_1G_USERNAME"], os.environ["LIBREOFFICE_BIN"]) == ("from-process", "from-process")


def test_without_a_customer_the_whole_shared_file_loads(files):
    _, shared = files
    shared.write_text(SHARED, encoding="utf-8")
    load_env_file(shared)
    assert os.environ["PJE_1G_PASSWORD"] == "shared-password"
    assert os.environ["ADVBOX_ALLOW_WRITES"] == "true"
    assert os.environ["PJE_1G_CERT"] == "true"
    assert os.environ["JURIDIQ_LOGIN_URL"] == "https://elsewhere.example/login"
    assert os.environ["LIBREOFFICE_BIN"] == "/machine/soffice"


def test_a_customer_named_by_the_shared_file_is_isolated_too(files):
    own, shared = files
    own.write_text("PJE_1G_USERNAME=acme-user\n", encoding="utf-8")
    shared.write_text("JURISYNC_CUSTOMER=acme\n" + SHARED, encoding="utf-8")
    load_env_file(shared)
    assert os.environ["PJE_1G_USERNAME"] == "acme-user"
    assert "PJE_1G_PASSWORD" not in os.environ
    assert "PJE_1G_CERT" not in os.environ


@pytest.mark.parametrize("named_by", ["server", "file"])
def test_an_installed_office_reads_its_whole_user_file(files, named_by):
    # A plugin (JURISYNC_CUSTOMER in the server env) or a bundle (named in the file) keeps the office's own logins
    # in ~/.config/jurisync/.env and ships no customers/<name>/.env.
    _, shared = files
    shared.write_text(("JURISYNC_CUSTOMER=acme\n" if named_by == "file" else "") + SHARED, encoding="utf-8")
    if named_by == "server":
        os.environ["JURISYNC_CUSTOMER"] = "acme"
    load_env_file(shared)
    assert os.environ["PJE_1G_PASSWORD"] == "shared-password"
    assert os.environ["PJE_1G_2FA_DATA"] == "shared-totp"
    assert os.environ["ADVBOX_ALLOW_WRITES"] == "false"  # the office pin still wins


@pytest.mark.parametrize(
    ("name", "machine"),
    [
        ("JURISYNC_FILING_DIR", True),
        ("JURISYNC_MAX_LOGINS", True),
        ("ESAJ_CHROME_PATH", True),
        ("PDFIUM_LIB_PATH", True),
        ("DJEN_BRIDGE_URL", True),
        ("JURISYNC_VAULT_KEY", False),
        ("TJSP_1G_A1_P12", False),
        ("TRF6_OTP_PROFILE_MATCH", False),
        ("PJE_TRF5_1G_SESSION_ACCOUNT", False),
        ("TYPESAFE_API_KEY", False),
        ("JURIDIQ_API_BASE_URL", False),
        ("ADVBOX_ALLOW_WRITES", False),
        ("PJE_ALLOW_DRAFT_WRITES", False),
        ("ESAJ_BRIDGE_CDP", False),
    ],
)
def test_machine_names(name, machine):
    assert is_machine_name(name) is machine


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (None, ""),
        ("Publicaca: 12 itens; indicação: 5; Vara: 3", "Publicaca: 12 itens; indicação: 5; Vara: 3"),
        ("Autor/Réu e/ou 1/2 em 10/11/2026, ~10 itens", "Autor/Réu e/ou 1/2 em 10/11/2026, ~10 itens"),
        ("processo 0001234-56.2024.4.05.8300", "processo 0001234-56.2024.4.05.8300"),
        ("senha incorreta, token expirado", "senha incorreta, token expirado"),
        ("failed at https://x.jus.br/a?ca=1 session=secret", "failed at [url] [redacted]"),
        ("login password=abc123 senha: xyz, fim", "login [redacted] [redacted], fim"),
        ('PASSWORD="a b c" depois', "[redacted] depois"),
        ("token: abc.def", "[redacted]"),
        ("access_token=abc&refresh_token=def", "access_[redacted]&refresh_[redacted]"),
        ("Authorization: Bearer eyJhbGciOi.abc.def depois", "[redacted] depois"),
        ("sent Bearer eyJhbGciOi.abc.def", "sent [redacted]"),
        ("JSESSIONID=0A1B2C; depois", "[redacted]; depois"),
        ("Cookie: PHPSESSID=abc; outro=def falhou", "[redacted] falhou"),
        ("Set-Cookie: X=1; Path=/; HttpOnly", "[redacted] HttpOnly"),
        ("javax.faces.ViewState=-123:456 hash: z ca=9", "javax.faces.[redacted] [redacted] [redacted]"),
        ("CPF 123.456.789-09 e cpf: 12345678909", "CPF 123.***.***-09 e cpf: 123******09"),
        ("open /run/user/1000/key.pem, /tmp/a and /home/ana/b.p12", "open [path] [path] and [path]"),
        ("wrote /srv/jurisync/vault/x and ~/notes.txt", "wrote [path] and [path]"),
        ("see /pje/login.seam and /opt", "see [path] and [path]"),
        ("No such file: '/home/ana/ANA FLAVIA_04318368173.p12'", "No such file: [path]"),
        (r"missing C:\Users\Ana Flavia\cert.p12 here", "missing [path] here"),
        ("missing C:/Users/ana/cert.p12", "missing [path]"),
        ("relative data/x.json and a /etcetera b", "relative data/x.json and a /etcetera b"),
        ("line one\n\n  line two", "line one line two"),
    ],
)
def test_sanitize_diagnostics(text, expected):
    assert sanitize_diagnostics(text) == expected


def test_sanitize_diagnostics_bounds_the_length():
    assert len(sanitize_diagnostics("palavra " * 1000)) == 2000
