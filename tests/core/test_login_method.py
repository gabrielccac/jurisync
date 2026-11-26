"""The login method an office declares in its portals.toml is the one the connectors use; the registry's
``Portal.auth`` is only the default for an office that declares none."""

import os
from unittest import mock

import pytest

from jurisync import customers, portals
from jurisync.connectors.eproc import client as eproc
from jurisync.connectors.esaj import base as esaj
from jurisync.connectors.pje.auth import sessions as pje
from jurisync.core.session_pool import MissingLogin

A1 = {"PJE_A1_P12": "a1.p12", "PJE_A1_PIN": "0000", "ESAJ_A1_P12": "a1.p12", "ESAJ_A1_PIN": "0000"}


def login_method(key):
    """The login the connector picks for the portal; systems with one login per portal keep the registry's."""
    portal = portals.get(key)
    if portal.system == "pje":
        return pje.auth_method(key)
    if portal.system == "eproc":
        return eproc.auth_method(key)
    if portal.system == "esaj":
        return "password_totp" if esaj.uses_password(portal) else "certificate_challenge"
    return portal.auth


def kind(method):
    return "certificate" if method.startswith("certificate") else "password"


def declared_portals():
    for name in customers.names():
        for key, setup in customers.load_setup(name).items():
            yield pytest.param(name, key, setup.method, id=f"{name}-{key}")


@pytest.mark.parametrize("a1", [True, False], ids=["with-a1", "without-a1"])
@pytest.mark.parametrize(("name", "key", "method"), list(declared_portals()))
def test_every_office_logs_in_with_the_method_it_declares(name, key, method, a1, monkeypatch):
    """Whatever else the office's .env holds (every portal's password, an A1), the declared method decides."""
    monkeypatch.delenv("JURISYNC_CUSTOMERS_DIR", raising=False)
    env = {"JURISYNC_CUSTOMER": name, **customers.fixed_variables(name), **(A1 if a1 else {})}
    for config in map(portals.get, customers.load_setup(name)):
        if config.env_prefix:
            env.update({config.env_name("USERNAME"): "usuario", config.env_name("PASSWORD"): "senha"})
    with mock.patch.dict(os.environ, env, clear=True):
        effective = login_method(key)
    if portals.get(key).system in ("pje", "eproc"):
        assert effective == method
    else:
        assert kind(effective) == kind(method)


@pytest.fixture
def acme(tmp_path, monkeypatch):
    (tmp_path / "acme").mkdir()
    monkeypatch.setenv("JURISYNC_CUSTOMERS_DIR", str(tmp_path))

    def declare(toml, **env):
        (tmp_path / "acme" / "portals.toml").write_text(toml, encoding="utf-8")
        return mock.patch.dict(
            os.environ, {"JURISYNC_CUSTOMERS_DIR": str(tmp_path), "JURISYNC_CUSTOMER": "acme", **env}, clear=True
        )

    return declare


def test_a_declared_password_wins_over_a_certificate_portal_and_a_present_a1(acme):
    password = {"PJE_TRF5_1G_USERNAME": "usuario", "PJE_TRF5_1G_PASSWORD": "senha", **A1}
    with acme('[trf5-1g]\nmethod = "password_totp"\n', **password):
        assert pje.auth_method("trf5-1g") == "password_totp"
    with mock.patch.dict(os.environ, password, clear=True):
        assert pje.auth_method("trf5-1g") == "certificate_totp"  # no office: the registry's default


def test_a_declared_certificate_wins_over_a_password_portal(acme):
    with acme('[trf1-1g]\nmethod = "certificate_totp"\n', PJE_1G_USERNAME="usuario", PJE_1G_PASSWORD="senha"):
        assert pje.auth_method("trf1-1g") == "certificate_totp"


def test_the_a3_token_still_comes_first(acme):
    with acme('[trf1-1g]\nmethod = "certificate_totp"\n', PJE_AUTH="a3"):
        assert pje.auth_method("trf1-1g") == "a3_totp"
    with acme('[tjsp-1g]\nmethod = "certificate_totp"\n', EPROC_AUTH="a3"):
        assert eproc.auth_method("tjsp-1g") == "a3_totp"


def test_eproc_follows_the_declaration_over_a_stray_cert_switch(acme):
    with acme('[tjsp-1g]\nmethod = "browser_totp"\n', TJSP_1G_CERT="true", **A1):
        assert eproc.auth_method("tjsp-1g") == "browser_totp"
    with acme('[tjsp-1g]\nmethod = "certificate_mtls"\n'):
        assert eproc.auth_method("tjsp-1g") == "certificate_mtls"
    with mock.patch.dict(os.environ, {"TJSP_1G_CERT": "true", **A1}, clear=True):
        assert eproc.auth_method("tjsp-1g") == "certificate_totp"  # no office: <PREFIX>_CERT still opts in


def test_esaj_uses_the_declared_password_even_with_an_a1(acme):
    config = portals.esaj("tjam-esaj")
    with acme(
        '[tjam-esaj]\nmethod = "password_totp"\n', TJAM_ESAJ_USERNAME="usuario", TJAM_ESAJ_PASSWORD="senha", **A1
    ):
        assert esaj.uses_password(config)
    with mock.patch.dict(
        os.environ, {"TJAM_ESAJ_USERNAME": "usuario", "TJAM_ESAJ_PASSWORD": "senha", **A1}, clear=True
    ):
        assert not esaj.uses_password(config)  # no office: the A1 first, as before


def test_esaj_names_the_missing_password_before_reaching_the_portal(acme):
    with acme('[tjam-esaj]\nmethod = "password_totp"\n', **A1):
        with mock.patch.object(esaj, "login_page", side_effect=AssertionError("no request expected")):
            with pytest.raises(MissingLogin, match="TJAM_ESAJ_USERNAME"):
                esaj.login("tjam-esaj")
