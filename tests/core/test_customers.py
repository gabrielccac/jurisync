import os

import pytest

from jurisync import customers
from jurisync.core import credentials
from jurisync.customers import load_env_file


@pytest.fixture
def root(tmp_path, monkeypatch):
    acme = tmp_path / "acme"
    acme.mkdir()
    (acme / "portals.toml").write_text(
        '[trf1-1g]\nmethod = "password_totp"\nsecrets = ["ACME_USER", "ACME_PASS", "ACME_2FA|ACME_2FA_QR"]\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("JURISYNC_CUSTOMERS_DIR", str(tmp_path))
    monkeypatch.setenv("JURISYNC_CUSTOMER", "acme")
    for name in ("ACME_USER", "ACME_PASS", "ACME_2FA", "ACME_2FA_QR", "JURISYNC_ENV_FILE"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def test_check_reports_missing_names_only(root, monkeypatch):
    monkeypatch.setenv("ACME_USER", "secret-user")
    status = customers.check("acme")
    assert status == {"trf1-1g": {"method": "password_totp", "missing": ["ACME_PASS", "ACME_2FA|ACME_2FA_QR"]}}
    assert "secret-user" not in repr(status)


def test_alternative_group_is_satisfied_by_either(root, monkeypatch):
    for name in ("ACME_USER", "ACME_PASS", "ACME_2FA_QR"):
        monkeypatch.setenv(name, "x")
    assert customers.check("acme")["trf1-1g"]["missing"] == []


def test_pinned_values_beat_the_shared_file_but_not_the_environment(root, monkeypatch, tmp_path):
    (root / "acme" / "portals.toml").write_text(
        '[trf1-1g]\nmethod = "certificate_totp"\nsecrets = ["ACME_USER"]\n'
        '[env]\nset = { ACME_CERT = "true", ACME_KEEP = "pinned" }\n',
        encoding="utf-8",
    )
    shared = tmp_path / "shared.env"
    shared.write_text("ACME_CERT=false\nACME_KEEP=shared\n", encoding="utf-8")
    monkeypatch.setenv("ACME_KEEP", "from-process")
    monkeypatch.delenv("ACME_CERT", raising=False)
    load_env_file(shared)
    assert os.environ["ACME_CERT"] == "true"
    assert os.environ["ACME_KEEP"] == "from-process"
    monkeypatch.delenv("ACME_CERT")


def test_invalid_setup_fails_loudly(root):
    (root / "acme" / "portals.toml").write_text('[trf9-1g]\nmethod = "password_totp"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="portal must be one of"):
        customers.load_setup("acme")
    (root / "acme" / "portals.toml").write_text('[trf1-1g]\nmethod = "magic"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="method must be one of"):
        customers.load_setup("acme")


def test_a_certificate_method_switches_the_portal_to_the_a1_login(root):
    (root / "acme" / "portals.toml").write_text(
        '[trf1-1g]\nmethod = "certificate_totp"\n[trf3-1g]\nmethod = "password_totp"\n[trf4-2g]\nmethod = "certificate_mtls"\n'
        '[env]\nset = { ADVBOX_ALLOW_WRITES = "true" }\n',
        encoding="utf-8",
    )
    assert customers.fixed_variables("acme") == {
        "TRF1_1G_CERT": "true",
        "TRF4_2G_CERT": "true",
        "ADVBOX_ALLOW_WRITES": "true",
    }


def test_a_pinned_switch_that_contradicts_the_method_fails_loudly(root):
    (root / "acme" / "portals.toml").write_text(
        '[trf1-1g]\nmethod = "password_totp"\n[env]\nset = { PJE_1G_CERT = "true" }\n', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="PJE_1G_CERT"):
        customers.load_setup("acme")
    (root / "acme" / "portals.toml").write_text(
        '[trf1-1g]\nmethod = "certificate_totp"\n[env]\nset = { PJE_1G_CERT = "false" }\n', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="contradicts"):
        customers.load_setup("acme")


def test_one_patch_of_credentials_customers_dir_moves_every_customer_lookup(tmp_path, monkeypatch):
    monkeypatch.delenv("JURISYNC_CUSTOMERS_DIR", raising=False)
    (tmp_path / "acme").mkdir()
    (tmp_path / "_template").mkdir()
    monkeypatch.setattr(credentials, "customers_dir", lambda: tmp_path)
    assert customers.customer_dir("acme") == tmp_path / "acme"
    assert customers.names() == ["acme"]
    assert customers.customer_env_path("acme") == tmp_path / "acme" / ".env"


def test_customer_name_cannot_escape_directory(root):
    with pytest.raises(ValueError):
        customers.customer_dir("../etc")


@pytest.mark.parametrize("name", [*customers.names(), "_template"])
def test_every_customer_folder_is_valid(name, monkeypatch):
    monkeypatch.delenv("JURISYNC_CUSTOMERS_DIR", raising=False)
    assert customers.validate(name) == []


def test_victor_setup_is_valid(monkeypatch):
    monkeypatch.delenv("JURISYNC_CUSTOMERS_DIR", raising=False)
    assert set(customers.load_setup("victor")) == {
        "trf5-1g",
        "trf5-2g",
        "trf5-tru",
        "trf1-1g",
        "trf1-2g",
        "trf3-1g",
        "trf3-2g",
        "tjpe-1g",
        "tjpe-2g",
    }


def test_validate_reports_what_breaks_a_folder(root):
    (root / "acme" / ".env.example").write_text("ACME_USER=\nACME_2FA_QR=\n", encoding="utf-8")
    (root / "acme" / "settings.toml").write_text('[hosted]\nmode = "cloud"\n', encoding="utf-8")
    problems = customers.validate("acme")
    assert "acme: .env.example lacks ACME_PASS" in problems
    assert not any("ACME_2FA" in problem for problem in problems)  # one name of an A|B group is enough
    assert any("mode must be one of" in problem for problem in problems)
    (root / "empty").mkdir()
    assert customers.validate("empty") == [f"customer 'empty' has no portals.toml at {root / 'empty' / 'portals.toml'}"]
    assert customers.names() == ["acme", "empty"]


def test_check_customer_command(root):
    from jurisync.install import setup

    lines = []
    assert setup.main(["check-customer", "acme"], say=lines.append) == 0
    assert lines == ["Tudo certo."]
    (root / "acme" / "portals.toml").write_text('[trf1-1g]\nmethod = "magic"\n', encoding="utf-8")
    assert setup.main(["check-customer"], say=lines.append) == 1
    assert lines[-1] == "1 problema(s)."


def test_ciencia_on_read_defaults_to_ask_and_follows_settings_toml(root):
    assert customers.ciencia_on_read() == "ask"
    (root / "acme" / "settings.toml").write_text('[pje]\nciencia_on_read = "allow"\n', encoding="utf-8")
    assert customers.ciencia_on_read() == "allow"
    (root / "acme" / "settings.toml").write_text('[pje]\nciencia_on_read = "sometimes"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="ciencia_on_read"):
        customers.ciencia_on_read()


def test_eproc_ciencia_on_read_has_its_own_table(root):
    (root / "acme" / "settings.toml").write_text('[pje]\nciencia_on_read = "allow"\n', encoding="utf-8")
    assert customers.ciencia_on_read(system="eproc") == "ask"
    (root / "acme" / "settings.toml").write_text('[eproc]\nciencia_on_read = "allow"\n', encoding="utf-8")
    assert customers.ciencia_on_read(system="eproc") == "allow"
    assert customers.ciencia_on_read() == "ask"
    (root / "acme" / "settings.toml").write_text('[eproc]\nciencia_on_read = "yes"\n', encoding="utf-8")
    with pytest.raises(ValueError, match=r"\[eproc\] ciencia_on_read"):
        customers.ciencia_on_read(system="eproc")


def test_ciencia_on_read_without_a_customer_is_ask(monkeypatch):
    monkeypatch.delenv("JURISYNC_CUSTOMER", raising=False)
    assert customers.ciencia_on_read() == "ask"
