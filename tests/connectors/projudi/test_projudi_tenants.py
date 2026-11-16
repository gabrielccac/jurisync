"""Two offices on one hosted server: each Projudi call sees only its own certificate, session and files."""

import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import BestAvailableEncryption, pkcs12
from cryptography.x509.oid import NameOID

from jurisync.connectors.projudi import base, server
from jurisync.core import credentials
from jurisync.core.session_pool import SessionPool

NUMBER = "0000001-23.2026.8.09.0001"


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
    for name in ("PJE_A1_P12", "PROJUDI_A1_P12"):
        monkeypatch.setenv(name, str(path))
    for name in ("PJE_A1_PIN", "PROJUDI_A1_PIN"):
        monkeypatch.setenv(name, "pin-s")


def common_name(cert):
    return cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value


def test_each_office_loads_only_its_own_a1_bytes(server_account, certificates):
    with credentials.scope("office-a", {"PROJUDI_A1_PIN": "pin-a"}, {"PROJUDI_A1_P12": certificates["a"]}):
        assert common_name(base.load_certificate()[1]) == "office-a"
    with credentials.scope("office-b", {"PJE_A1_PIN": "pin-b"}, {"PJE_A1_P12": certificates["b"]}):
        assert common_name(base.load_certificate()[1]) == "office-b"
        with base.client_certificate() as (cert_pem, _key_pem):
            assert b"BEGIN CERTIFICATE" in Path(cert_pem).read_bytes()
    assert common_name(base.load_certificate()[1]) == "server"  # local install: the environment, as before


def test_an_office_without_a_credential_never_reads_the_environment(server_account, certificates):
    with credentials.scope("office-c", {}), pytest.raises(RuntimeError, match="PJE_A1_PIN"):
        base.load_certificate()
    with credentials.scope("office-c", {"PJE_A1_PIN": "pin-s"}), pytest.raises(RuntimeError, match="not set"):
        base.load_certificate()
    with credentials.scope("office-a", {}, {"PROJUDI_A1_P12": certificates["a"]}), pytest.raises(RuntimeError):
        base.load_certificate()


class Adapter(base.Adapter):
    flavor = "tjgo-servlet"

    def __init__(self):
        self.logins = []

    def login(self, portal):
        self.logins.append(credentials.tenant())
        return SimpleNamespace(owner=credentials.env("PROJUDI_A1_PIN"), close=lambda: None)

    def refresh(self, portal, session):
        return session

    def open_process(self, session, portal, process_number):
        return {"number": process_number, "movements": [{"title": "Decisão"}]}

    def fetch_index(self, session, portal):
        return [{"title": "Decisão", "complement": "", "files": [{"id": "100", "name": "a.pdf", "hash": "h"}]}]

    def download(self, session, portal, document_id, hash_value):
        return f"%PDF-{session.owner}".encode(), "application/pdf"


@pytest.fixture
def adapter(monkeypatch):
    fake = Adapter()
    monkeypatch.setitem(server.ADAPTERS, "tjgo-servlet", fake)
    pool = SessionPool(
        login=lambda portal: server.adapter_for(portal).login(portal),
        refresh=lambda portal, session: server.adapter_for(portal).refresh(portal, session),
        close=lambda session: None,
    )
    monkeypatch.setattr(server, "sessions", pool)
    monkeypatch.setattr(base, "extract_document_text", lambda *a, **k: ("extracted", "text"))
    return fake


def test_sessions_and_saved_documents_are_kept_per_office(adapter, monkeypatch, tmp_path):
    monkeypatch.setenv("JURISYNC_DOCUMENTS_DIR", str(tmp_path))
    monkeypatch.setenv("PROJUDI_A1_PIN", "server")
    saved = {}
    for tenant in ("office-a", "office-b", "office-a"):
        with credentials.scope(tenant, {"PROJUDI_A1_PIN": tenant}):
            result = server.call(
                "tjgo-1g", lambda a, session: base.download_document(a, session, "tjgo-1g", NUMBER, "100")
            )
        saved[tenant] = tmp_path / "tenants" / tenant / result["savedAs"]
    assert adapter.logins == ["office-a", "office-b"]
    assert saved["office-a"].read_bytes() == b"%PDF-office-a"
    assert saved["office-b"].read_bytes() == b"%PDF-office-b"
    assert not [entry for entry in tmp_path.iterdir() if entry.is_file()]


def test_an_office_without_a_value_does_not_borrow_the_servers(adapter, monkeypatch):
    monkeypatch.setenv("PROJUDI_A1_PIN", "server")
    with credentials.scope("office-c", {}):
        owner = server.call("tjgo-1g", lambda a, session: session.owner)
    assert owner == ""
