import os

import pytest

from jurisync import customers
from jurisync.core.mcp import load_env_file


@pytest.fixture
def root(tmp_path, monkeypatch):
    acme = tmp_path / "acme"
    acme.mkdir()
    (acme / "portals.toml").write_text(
        '[trf1-1g]\nmethod = "password_totp"\nsecrets = ["ACME_USER", "ACME_PASS", "ACME_2FA|ACME_2FA_QR"]\n'
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
        '[env]\nset = { ACME_CERT = "true", ACME_KEEP = "pinned" }\n'
    )
    shared = tmp_path / "shared.env"
    shared.write_text("ACME_CERT=false\nACME_KEEP=shared\n")
    monkeypatch.setenv("ACME_KEEP", "from-process")
    monkeypatch.delenv("ACME_CERT", raising=False)
    load_env_file(shared)
    assert os.environ["ACME_CERT"] == "true"
    assert os.environ["ACME_KEEP"] == "from-process"
    monkeypatch.delenv("ACME_CERT")


def test_invalid_setup_fails_loudly(root):
    (root / "acme" / "portals.toml").write_text('[trf9-1g]\nmethod = "password_totp"\n')
    with pytest.raises(ValueError, match="portal must be one of"):
        customers.load_setup("acme")
    (root / "acme" / "portals.toml").write_text('[trf1-1g]\nmethod = "magic"\n')
    with pytest.raises(ValueError, match="method must be one of"):
        customers.load_setup("acme")


def test_a_certificate_method_switches_the_portal_to_the_a1_login(root):
    (root / "acme" / "portals.toml").write_text(
        '[trf1-1g]\nmethod = "certificate_totp"\n[trf3-1g]\nmethod = "password_totp"\n[trf4-2g]\nmethod = "certificate_mtls"\n'
        '[env]\nset = { ADVBOX_ALLOW_WRITES = "true" }\n'
    )
    assert customers.fixed_variables("acme") == {
        "PJE_1G_CERT": "true",
        "TRF4_2G_CERT": "true",
        "ADVBOX_ALLOW_WRITES": "true",
    }


def test_a_pinned_switch_that_contradicts_the_method_fails_loudly(root):
    (root / "acme" / "portals.toml").write_text(
        '[trf1-1g]\nmethod = "password_totp"\n[env]\nset = { PJE_1G_CERT = "true" }\n'
    )
    with pytest.raises(ValueError, match="PJE_1G_CERT"):
        customers.load_setup("acme")
    (root / "acme" / "portals.toml").write_text(
        '[trf1-1g]\nmethod = "certificate_totp"\n[env]\nset = { PJE_1G_CERT = "false" }\n'
    )
    with pytest.raises(ValueError, match="contradicts"):
        customers.load_setup("acme")


def test_customer_name_cannot_escape_directory(root):
    with pytest.raises(ValueError):
        customers.customer_dir("../etc")


def test_victor_setup_is_valid():
    monkeypatch_dir = os.environ.pop("JURISYNC_CUSTOMERS_DIR", None)
    try:
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
    finally:
        if monkeypatch_dir:
            os.environ["JURISYNC_CUSTOMERS_DIR"] = monkeypatch_dir


def test_ciencia_on_read_defaults_to_ask_and_follows_settings_toml(root):
    assert customers.ciencia_on_read() == "ask"
    (root / "acme" / "settings.toml").write_text('[pje]\nciencia_on_read = "allow"\n')
    assert customers.ciencia_on_read() == "allow"
    (root / "acme" / "settings.toml").write_text('[pje]\nciencia_on_read = "sometimes"\n')
    with pytest.raises(ValueError, match="ciencia_on_read"):
        customers.ciencia_on_read()


def test_ciencia_on_read_without_a_customer_is_ask(monkeypatch):
    monkeypatch.delenv("JURISYNC_CUSTOMER", raising=False)
    assert customers.ciencia_on_read() == "ask"
