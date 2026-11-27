"""Two offices on one hosted server: each e-SAJ call sees only its own certificate, settings, session and files."""

import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import BestAvailableEncryption, pkcs12
from cryptography.x509.oid import NameOID

from jurisync.connectors.esaj import base, bridge, common, documents, server
from jurisync.core import credentials
from jurisync.core.session_pool import SessionPool

NUMBER = "0000001-00.2026.8.26.0001"


def a1(common_name, pin):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    return pkcs12.serialize_key_and_certificates(b"a1", key, cert, None, BestAvailableEncryption(pin.encode()))


@pytest.fixture(scope="module")
def certificates():
    return {"a": a1("office-a", "pin-a"), "b": a1("office-b", "pin-b"), "server": a1("server", "pin-s")}


@pytest.fixture
def server_account(monkeypatch, tmp_path, certificates):
    """The operator's own account in the process environment: no office may ever log in with it."""
    path = tmp_path / "server.p12"
    path.write_bytes(certificates["server"])
    for name in ("PJE_A1_P12", "ESAJ_A1_P12"):
        monkeypatch.setenv(name, str(path))
    for name in ("PJE_A1_PIN", "ESAJ_A1_PIN"):
        monkeypatch.setenv(name, "pin-s")


def common_name(cert):
    return cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value


def test_each_office_loads_only_its_own_a1_bytes(server_account, certificates):
    with credentials.scope("office-a", {"ESAJ_A1_PIN": "pin-a"}, {"ESAJ_A1_P12": certificates["a"]}):
        assert common_name(base.load_certificate()[1]) == "office-a"
    with credentials.scope("office-b", {"PJE_A1_PIN": "pin-b"}, {"PJE_A1_P12": certificates["b"]}):
        assert common_name(base.load_certificate()[1]) == "office-b"
    assert common_name(base.load_certificate()[1]) == "server"  # local install: the environment, as before


def test_an_office_without_a_credential_never_reads_the_environment(server_account, certificates):
    with credentials.scope("office-c", {}), pytest.raises(RuntimeError, match="PJE_A1_PIN"):
        base.load_certificate()
    with credentials.scope("office-c", {"PJE_A1_PIN": "pin-s"}), pytest.raises(RuntimeError, match="not set"):
        base.load_certificate()
    # Its own file with the other office's PIN name unset: the server's ESAJ_A1_PIN is not borrowed.
    with credentials.scope("office-a", {}, {"ESAJ_A1_P12": certificates["a"]}), pytest.raises(RuntimeError):
        base.load_certificate()


def test_sessions_are_kept_per_office(monkeypatch):
    logins = []

    def login(portal):
        logins.append(credentials.tenant())
        return SimpleNamespace(owner=credentials.env("ESAJ_A1_PIN"), close=lambda: None)

    monkeypatch.setattr(server, "sessions", SessionPool(login=login, refresh=lambda p, s: s, close=lambda s: None))
    seen = {}
    for tenant in ("office-a", "office-b", "office-a"):
        with credentials.scope(tenant, {"ESAJ_A1_PIN": f"pin-{tenant}"}):
            seen.setdefault(tenant, []).append(server.call("tjsp-esaj", lambda s: {"owner": s.owner})["owner"])
    assert seen == {"office-a": ["pin-office-a", "pin-office-a"], "office-b": ["pin-office-b"]}
    assert logins == ["office-a", "office-b"]


def test_downloaded_documents_land_in_the_offices_folder(monkeypatch, tmp_path):
    monkeypatch.setenv("JURISYNC_DOCUMENTS_DIR", str(tmp_path))
    monkeypatch.setattr(documents, "document_inventory", lambda *a: [{"documentId": "38", "parameters": "fixture"}])

    def fake_request(session, portal, method, path, **kwargs):
        if "getPDF" not in path:
            return SimpleNamespace(status_code=204, text="", content=b"", headers={})
        pdf = Mock(status_code=200, headers={"content-type": "application/pdf"})
        pdf.iter_content.return_value = iter([f"%PDF-{credentials.tenant()}".encode()])
        return pdf

    monkeypatch.setattr(common, "request", fake_request)
    for tenant in ("office-a", "office-b"):
        with credentials.scope(tenant, {}):
            saved = base.document(None, "tjsp-esaj", NUMBER, "38", download=True, read={"estado": "nenhuma"})["savedAs"]
        path = tmp_path / "tenants" / tenant / saved
        assert path.read_bytes() == f"%PDF-{tenant}".encode()
    assert not [entry for entry in tmp_path.iterdir() if entry.is_file()]


def test_bridge_settings_come_from_the_office_and_never_reach_a_shared_browser(monkeypatch):
    monkeypatch.setenv("ESAJ_BRIDGE_MODE", "attach")
    monkeypatch.setenv("ESAJ_BRIDGE_CDP", "http://10.0.0.1:9333")
    monkeypatch.setenv("ESAJ_BRIDGE_PROFILE", "/srv/operator-chrome")
    with credentials.scope("office-c", {}):
        assert not bridge.BrowserBridge().enabled  # the server's attach is not this office's setting
    office = {"ESAJ_BRIDGE_MODE": "auto", "ESAJ_BRIDGE_HEADLESS": "false", "ESAJ_BRIDGE_CDP": "http://10.0.0.2:1"}
    with credentials.scope("office-a", office):
        browser = bridge.BrowserBridge()
        assert browser.mode == "launch" and browser.headless
        assert browser.endpoint == bridge.DEFAULT_ENDPOINT
        assert str(browser.profile).endswith("esaj-chrome")
    with credentials.scope("office-b", {"ESAJ_BRIDGE_MODE": "attach"}):
        with pytest.raises(bridge.BrowserBridgeError, match="launches"):
            bridge.BrowserBridge()
        assert bridge.bridge_status()["mode"] == "invalid"
    assert bridge.BrowserBridge().mode == "attach"  # a local install keeps the operator's choice
