"""Portal variables: <KEY>_<FIELD>, the earlier prefixes still read, and .env.example made from portals.toml."""

import os
import re
from unittest import mock

import pytest

from jurisync import customers, portals
from jurisync.hosted import provision
from jurisync.install import setup


def test_every_prefix_is_the_key_in_upper_case_or_a_listed_exception():
    for portal in portals.PORTALS.values():
        expected = portals.ENV_PREFIX_EXCEPTIONS.get(portal.key, re.sub(r"[^A-Z0-9]+", "_", portal.key.upper()))
        assert portal.env_prefix == expected, portal.key


def test_no_current_prefix_reads_another_portals_variables():
    for portal in portals.PORTALS.values():
        others = [other for other in portals.PORTALS.values() if other.key != portal.key]
        assert all(portal.env_prefix not in other.legacy_env_prefixes for other in others), portal.key
        sharing = {other.key for other in others if other.env_prefix == portal.env_prefix}
        assert not sharing or portal.key in portals.ENV_PREFIX_EXCEPTIONS, (portal.key, sharing)


def test_an_older_prefix_is_still_read_and_the_current_one_wins():
    trf1 = portals.pje("trf1-1g")
    with mock.patch.dict(os.environ, {"PJE_1G_USERNAME": "antigo"}, clear=True):
        assert trf1.env("USERNAME") == "antigo"
    with mock.patch.dict(os.environ, {"PJE_1G_USERNAME": "antigo", "TRF1_1G_USERNAME": "novo"}, clear=True):
        assert trf1.env("USERNAME") == "novo"


def test_a_group_accepts_the_same_variable_under_every_prefix_of_the_portal():
    trf5 = portals.pje("trf5-1g")
    assert trf5.variable_names("PJE_TRF5_2FA_DATA|PJE_A1_PIN") == (
        "TRF5_1G_2FA_DATA|PJE_TRF5_1G_2FA_DATA|PJE_TRF5_2FA_DATA|PJE_A1_PIN"
    )
    assert trf5.variable_names("PJE_TRF5_1G_USERNAME").split("|")[0] == "TRF5_1G_USERNAME"


@pytest.fixture
def root(tmp_path):
    (tmp_path / "acme").mkdir()
    with mock.patch.object(customers, "customers_dir", return_value=tmp_path):
        yield tmp_path


def test_a_profile_with_older_names_is_satisfied_by_the_current_ones(root):
    (root / "acme" / "portals.toml").write_text(
        '[trf1-1g]\nmethod = "password_totp"\nsecrets = ["PJE_1G_USERNAME", "PJE_1G_2FA_DATA|PJE_1G_2FA_QR"]\n',
        encoding="utf-8",
    )
    env = {"TRF1_1G_USERNAME": "1", "TRF1_1G_2FA_QR": "qr.png"}
    with mock.patch.dict(os.environ, env, clear=True):
        assert customers.load_setup("acme")["trf1-1g"].missing() == ()


def test_a_pinned_older_switch_that_contradicts_the_method_fails_loudly(root):
    (root / "acme" / "portals.toml").write_text(
        '[trf1-1g]\nmethod = "certificate_totp"\n[env]\nset = { PJE_1G_CERT = "false" }\n', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="PJE_1G_CERT"):
        customers.load_setup("acme")


def test_a_shared_older_pin_shadowed_by_the_portals_own_does_not_count(root):
    (root / "acme" / "portals.toml").write_text(
        '[trf5-1g]\nmethod = "password_totp"\n[trf5-2g]\nmethod = "certificate_totp"\n'
        '[env]\nset = { PJE_TRF5_1G_CERT = "false", PJE_TRF5_CERT = "true" }\n',
        encoding="utf-8",
    )
    assert set(customers.load_setup("acme")) == {"trf5-1g", "trf5-2g"}


def test_a_shared_export_accepts_the_current_name_of_an_older_declaration(root):
    (root / "acme" / "portals.toml").write_text(
        '[trf1-1g]\nmethod = "password_totp"\nsecrets = ["PJE_1G_USERNAME"]\n', encoding="utf-8"
    )
    names, _ = setup.declared_names("acme")
    assert {"PJE_1G_USERNAME", "TRF1_1G_USERNAME"} <= set(names)


def test_the_a3_switch_is_found_under_an_older_prefix():
    assert provision.a3_switch(portals.pje("trf1-1g"), {"PJE_1G_AUTH": "a3"}) == "PJE_1G_AUTH"


def test_the_env_example_lists_each_declared_variable_once(root):
    (root / "acme" / "portals.toml").write_text(
        '[trf1-1g]\nmethod = "certificate_totp"\nsecrets = ["PJE_A1_P12", "TRF1_1G_2FA_DATA|TRF1_1G_2FA_QR"]\n'
        '[trf1-2g]\nmethod = "certificate_totp"\nsecrets = ["PJE_A1_P12"]\n'
        '[env]\nsecrets = ["ADVBOX_EMAIL"]\noptional = ["ADVBOX_UPLOAD_FOLDER"]\n',
        encoding="utf-8",
    )
    text = customers.env_example("acme")
    assert text.splitlines()[1:] == [
        "# trf1-1g (certificate_totp)",
        "PJE_A1_P12=",
        "TRF1_1G_2FA_DATA=",
        "# TRF1_1G_2FA_QR=",
        "# trf1-2g (certificate_totp)",
        "# env",
        "ADVBOX_EMAIL=",
        "# ADVBOX_UPLOAD_FOLDER=",
    ]
    (root / "acme" / ".env.example").write_text(text, encoding="utf-8")
    assert customers.validate("acme") == []


@pytest.mark.parametrize("name", customers.names())
def test_the_generated_env_example_passes_the_check_of_every_customer(name, tmp_path):
    (tmp_path / name).mkdir()
    (tmp_path / name / "portals.toml").write_bytes((customers.customer_dir(name) / "portals.toml").read_bytes())
    with mock.patch.object(customers, "customers_dir", return_value=tmp_path):
        (tmp_path / name / ".env.example").write_text(customers.env_example(name), encoding="utf-8")
        assert [problem for problem in customers.validate(name) if ".env.example" in problem] == []
