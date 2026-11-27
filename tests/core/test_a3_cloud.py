"""The A3 through a cloud app's PKCS#11 module (Certisign desktopID): named module, login authorised in the app
without a PIN, and the chain completed from the system store when the token carries the holder's certificate alone."""

import datetime
import os
import threading
from unittest import mock

import PyKCS11
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import NameOID

from jurisync import customers
from jurisync.connectors.eproc import client as eproc
from jurisync.connectors.pje.auth import sessions as pje
from jurisync.core import a3
from jurisync.install import smoke


def _cert(name, issuer=None, ca=False):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    issuer_name, signing_key = (issuer[0].subject, issuer[1]) if issuer else (subject, key)
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .sign(signing_key, hashes.SHA256())
    )
    return cert, key


@pytest.fixture(scope="module")
def chain():
    root = _cert("Raiz Ficticia", ca=True)
    intermediate = _cert("AC Ficticia", issuer=root, ca=True)
    leaf = _cert("FULANA DE TAL FICTICIA", issuer=intermediate)
    return leaf[0], intermediate[0], root[0]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("certisign", "desktopID_Provider.dll"),
        (" Certisign ", "desktopID_Provider.dll"),
        (r"C:\x\aetpkss1.dll", r"C:\x\aetpkss1.dll"),
    ],
)
def test_the_module_is_named_or_a_path(value, expected, monkeypatch):
    monkeypatch.setenv("PJE_A3_MODULE", value)
    assert a3.module_path() == expected


def test_the_token_issuers_come_first_and_the_store_completes_the_rest(chain):
    leaf, intermediate, root = chain
    assert a3.certificate_chain([(b"1", leaf)])[1] == [leaf]
    assert a3.certificate_chain([(b"1", leaf)], (root, intermediate))[1] == [leaf, intermediate, root]
    assert a3.certificate_chain([(b"1", leaf), (b"2", intermediate)], (root,))[1] == [leaf, intermediate, root]


class FakeSession:
    def __init__(self, certs, error=None):
        self.certs, self.error, self.pins = certs, error, []

    def findObjects(self, template):
        return list(range(len(self.certs)))

    def getAttributeValue(self, obj, attributes):
        return list(self.certs[obj].public_bytes(Encoding.DER)), [obj + 1]

    def login(self, pin, user_type):
        self.pins.append(pin)
        if self.error is not None:
            raise PyKCS11.PyKCS11Error(self.error)

    def logout(self):
        pass

    def closeSession(self):
        pass


def fake_library(session, flags):
    class Info:
        serialNumber = "0"

        def flags2text(self):
            return flags

    class Library:
        def load(self, path):
            self.path = path

        def getSlotList(self, tokenPresent):
            return [8]

        def getTokenInfo(self, slot):
            return Info()

        def openSession(self, slot, flags):
            return session

    return Library


def open_token(monkeypatch, certs, flags, error=None, issuers=()):
    session = FakeSession(certs, error)
    monkeypatch.setattr(PyKCS11, "PyKCS11Lib", fake_library(session, flags))
    monkeypatch.setattr(a3, "system_issuers", lambda: issuers)
    monkeypatch.setattr(a3, "stored_pin", lambda serial: pytest.fail("a protected token must not read a PIN"))
    return a3.A3Signer(module="desktopID_Provider.dll"), session


def test_a_cloud_a3_logs_in_without_a_pin_and_completes_its_chain(chain, monkeypatch):
    leaf, intermediate, root = chain
    token, session = open_token(
        monkeypatch, [leaf], ["CKF_LOGIN_REQUIRED", "CKF_PROTECTED_AUTHENTICATION_PATH"], issuers=(root, intermediate)
    )
    assert token.protected
    assert token.chain == [leaf, intermediate, root]
    token.login()
    assert session.pins == [None]


def test_a_cancelled_authorisation_is_a_pin_error_and_is_not_retried(chain, monkeypatch):
    token, session = open_token(
        monkeypatch, [chain[0]], ["CKF_PROTECTED_AUTHENTICATION_PATH"], error=PyKCS11.CKR_FUNCTION_CANCELED
    )
    with pytest.raises(a3.PinError, match="authorisation"):
        token.login()
    assert session.pins == [None]


def test_an_authorisation_never_given_times_out_and_leaves_the_session_alone(chain, monkeypatch):
    released = threading.Event()
    token, session = open_token(monkeypatch, [chain[0]], ["CKF_PROTECTED_AUTHENTICATION_PATH"])
    monkeypatch.setattr(session, "login", lambda pin, user_type: released.wait(5))
    monkeypatch.setattr(session, "closeSession", lambda: pytest.fail("the session of a pending login is not touched"))
    monkeypatch.setenv("PJE_A3_APPROVAL_TIMEOUT", "0.1")
    try:
        with pytest.raises(a3.PinError, match="not authorised within 0.1 s"):
            token.login()
        token.close()
        assert token.abandoned and not token.logged_in
    finally:
        released.set()


def test_a_token_with_its_chain_does_not_read_the_store(chain, monkeypatch):
    leaf, intermediate, _root = chain
    token, _session = open_token(monkeypatch, [leaf, intermediate], ["CKF_LOGIN_REQUIRED"], issuers=(_root,))
    assert not token.protected
    assert token.chain == [leaf, intermediate]


def test_a_refused_a3_stops_the_smoke_run():
    tried = []

    def attempt(target):
        tried.append(target.key)
        raise a3.PinError("The A3 authorisation was cancelled")

    found = [smoke.Target("pje", "trf1-1g", "a3_totp"), smoke.Target("eproc", "tjsc-1g", "a3_totp")]
    results = smoke.run(found, say=lambda _line: None, attempt=attempt, wait=lambda _say: None)
    assert tried == ["trf1-1g"]
    assert [result.status for result in results] == ["falha", "nao_testado"]


@pytest.fixture
def acme(tmp_path):
    (tmp_path / "acme").mkdir()

    def declare(toml, **env):
        (tmp_path / "acme" / "portals.toml").write_text(toml, encoding="utf-8")
        return mock.patch.dict(
            os.environ, {"JURISYNC_CUSTOMERS_DIR": str(tmp_path), "JURISYNC_CUSTOMER": "acme", **env}, clear=True
        )

    return declare


def test_a_declared_a3_logs_in_with_the_a3_on_pje_and_eproc(acme):
    toml = '[trf1-1g]\nmethod = "a3_totp"\n[tjsc-1g]\nmethod = "a3_totp"\n'
    with acme(toml, PJE_A1_P12="a1.p12", PJE_A1_PIN="0000"):
        assert pje.auth_method("trf1-1g") == "a3_totp"
        assert eproc.auth_method("tjsc-1g") == "a3_totp"
        assert eproc.uses_a3("tjsc-1g")


def test_a3_totp_is_only_for_pje_and_eproc(acme):
    with acme('[tjsp-esaj]\nmethod = "a3_totp"\n'), pytest.raises(ValueError, match="a3_totp"):
        customers.load_setup("acme")


def test_check_customer_flags_a_pin_or_an_unknown_module_for_the_cloud_a3(acme):
    pin = '[trf1-1g]\nmethod = "a3_totp"\nsecrets = ["PJE_A3_PIN", "TRF1_1G_2FA_DATA"]\n'
    with acme(pin + '[env]\nset = { PJE_A3_MODULE = "certisign" }\n'):
        assert any("PJE_A3_PIN" in problem for problem in customers.validate("acme"))
    with acme('[trf1-1g]\nmethod = "a3_totp"\n[env]\nset = { PJE_A3_MODULE = "remoteid" }\n'):
        assert any("PJE_A3_MODULE" in problem for problem in customers.validate("acme"))
    with acme(
        '[trf1-1g]\nmethod = "a3_totp"\nsecrets = ["TRF1_1G_2FA_DATA"]\n[env]\nset = { PJE_A3_MODULE = "certisign" }\n'
    ):
        assert customers.validate("acme") == []
