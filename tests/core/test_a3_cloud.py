"""The A3 through a cloud app's PKCS#11 module (Certisign desktopID): named module, login authorised in the app
without a PIN, and the chain completed from the system store when the token carries the holder's certificate alone."""

import contextlib
import datetime
import os
import sys
import threading
import time
from types import SimpleNamespace
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
        self.certs, self.error, self.pins, self.sign_errors, self.signed = certs, error, [], [], 0
        self.logouts = self.closes = self.opened = 0
        self.state = PyKCS11.CKS_RO_PUBLIC_SESSION

    def findObjects(self, template):
        return list(range(len(self.certs)))

    def getAttributeValue(self, obj, attributes):
        return list(self.certs[obj].public_bytes(Encoding.DER)), [obj + 1]

    def login(self, pin, user_type):
        self.pins.append(pin)
        if self.error is not None:
            raise PyKCS11.PyKCS11Error(self.error)
        self.state = PyKCS11.CKS_RO_USER_FUNCTIONS

    def getSessionInfo(self):
        return SimpleNamespace(state=self.state)

    def sign(self, key, data, mechanism):
        self.signed += 1
        if self.sign_errors:
            raise PyKCS11.PyKCS11Error(self.sign_errors.pop(0))
        return b"assinatura"

    def logout(self):
        self.logouts += 1
        self.state = PyKCS11.CKS_RO_PUBLIC_SESSION

    def closeSession(self):
        self.closes += 1


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
            session.opened += 1
            return session

    return Library


def open_token(monkeypatch, certs, flags, error=None, issuers=(), module="desktopID_Provider.dll"):
    session = FakeSession(certs, error)
    monkeypatch.setattr(PyKCS11, "PyKCS11Lib", fake_library(session, flags))
    monkeypatch.setattr(a3, "system_issuers", lambda: issuers)
    monkeypatch.setattr(a3, "stored_pin", lambda serial: pytest.fail("the cloud A3 must not read a PIN"))
    return a3.A3Signer(module=module), session


CLOUD = ["CKF_LOGIN_REQUIRED", "CKF_PROTECTED_AUTHENTICATION_PATH"]


def test_a_cloud_a3_logs_in_without_a_pin_and_completes_its_chain(chain, monkeypatch):
    leaf, intermediate, root = chain
    token, session = open_token(monkeypatch, [leaf], CLOUD, issuers=(root, intermediate))
    assert token.cloud
    assert token.chain == [leaf, intermediate, root]
    token.login()
    assert session.pins == [None]


def test_a_physical_pin_pad_token_keeps_its_pin_login_and_its_chain(chain, monkeypatch):
    leaf, intermediate, root = chain
    token, session = open_token(monkeypatch, [leaf], CLOUD, issuers=(root, intermediate), module="aetpkss1.dll")
    monkeypatch.setattr(a3, "stored_pin", lambda serial: "1234")
    assert not token.cloud
    assert token.chain == [leaf]
    token.login()
    assert session.pins == ["1234"]


@pytest.fixture(autouse=True)
def no_shared_token():
    a3._shared.clear()
    a3._pending.clear()
    yield
    a3._shared.clear()
    a3._pending.clear()


def shared_cloud_token(chain, monkeypatch):
    """held_token() over a fake cloud A3 (a new signer, same fake session, per opening); returns the session and the
    signers opened."""
    _token, session = open_token(monkeypatch, [chain[0]], CLOUD)
    real, opened = a3.A3Signer, []

    def signer():
        opened.append(real(module="desktopID_Provider.dll"))
        return opened[-1]

    monkeypatch.setattr(a3, "A3Signer", signer)
    monkeypatch.setattr(a3, "token_lock", contextlib.nullcontext)
    return session, opened


def test_one_authorisation_serves_every_login_of_the_process(chain, monkeypatch):
    session, opened = shared_cloud_token(chain, monkeypatch)
    for _portal in ("trf1-1g", "trf3-1g", "tjsc-1g"):
        with a3.held_token() as token:
            # authorised before any connection (desktopID asks at the first signature), never in a TLS handshake
            assert token.logged_in and session.signed >= 1
            token.sign_digest_info(b"digest-info")
    assert len(opened) == 1 and session.pins == [None]
    assert session.signed == 4  # the authorisation, then one per portal


def test_an_idle_or_old_session_is_logged_out_and_authorised_again(chain, monkeypatch):
    session, opened = shared_cloud_token(chain, monkeypatch)
    clock = [1000.0]
    monkeypatch.setattr(a3, "_now", lambda: clock[0])
    monkeypatch.setenv("PJE_A3_SESSION_IDLE", "20")
    monkeypatch.setenv("PJE_A3_SESSION_TTL", "60")
    with a3.held_token():
        pass
    clock[0] += 19 * 60
    with a3.held_token():
        pass
    assert len(opened) == 1 and session.logouts == 0
    clock[0] += 21 * 60  # idle
    with a3.held_token():
        pass
    assert len(opened) == 2 and session.logouts == 1 and session.closes == 1
    for _ in range(5):  # used every 15 minutes, but older than an hour
        clock[0] += 15 * 60
        with a3.held_token():
            pass
    assert len(opened) == 3 and session.logouts == 2


def test_the_process_exit_logs_the_shared_session_out(chain, monkeypatch):
    session, _opened = shared_cloud_token(chain, monkeypatch)
    with a3.held_token():
        pass
    a3.drop_shared_token()  # registered with atexit
    assert session.logouts == 1 and session.closes == 1 and a3._shared == {}


def test_a_session_the_module_ended_is_authorised_again_before_the_block_never_mid_handshake(chain, monkeypatch):
    session, opened = shared_cloud_token(chain, monkeypatch)
    with a3.held_token():
        pass
    session.state = PyKCS11.CKS_RO_PUBLIC_SESSION  # the module ended it
    with a3.held_token() as token:
        assert token.logged_in and session.signed == 2  # authorised again, once, before the caller connects
    assert len(opened) == 2 and session.pins == [None, None]
    session.sign_errors = [PyKCS11.CKR_USER_NOT_LOGGED_IN]  # ended while signing in a handshake: no new window there
    with pytest.raises(PyKCS11.PyKCS11Error), a3.held_token() as token:
        token.sign_digest_info(b"digest-info")
    assert session.pins == [None, None]
    assert a3._shared == {}  # dropped: the next login authorises again before connecting


def test_a_physical_token_is_still_opened_and_closed_per_login(chain, monkeypatch):
    token, session = open_token(monkeypatch, [chain[0]], ["CKF_LOGIN_REQUIRED"], module="aetpkss1.dll")
    opened = []
    monkeypatch.setattr(a3, "A3Signer", lambda: opened.append(token) or token)
    monkeypatch.setattr(a3, "token_lock", contextlib.nullcontext)
    for _ in range(2):
        with a3.held_token():
            pass
    assert len(opened) == 2 and session.closes == 2 and a3._shared == {}


def test_a_cancelled_authorisation_is_a_pin_error_and_is_not_retried(chain, monkeypatch):
    token, session = open_token(monkeypatch, [chain[0]], CLOUD, error=PyKCS11.CKR_FUNCTION_CANCELED)
    with pytest.raises(a3.PinError, match="authorisation"):
        token.login()
    assert session.pins == [None]


def test_an_authorisation_answered_too_late_is_closed_by_its_own_thread(chain, monkeypatch):
    released = threading.Event()
    token, session = open_token(monkeypatch, [chain[0]], CLOUD)
    monkeypatch.setattr(session, "login", lambda pin, user_type: released.wait(5))
    monkeypatch.setenv("PJE_A3_APPROVAL_TIMEOUT", "0.1")
    with pytest.raises(a3.PinError, match="not authorised within 0.1 s"):
        token.login()
    token.close()
    assert token.abandoned and session.closes == 0  # the pending call's session is not touched
    released.set()
    a3._pending["worker"].join(5)
    assert session.closes == 1 and not token.abandoned


def test_a_timeout_inside_held_token_releases_it_and_the_module_waits_for_the_pending_call(chain, monkeypatch):
    released = threading.Event()
    session, opened = shared_cloud_token(chain, monkeypatch)
    monkeypatch.setattr(session, "sign", lambda key, data, mechanism: released.wait(5) and b"assinatura")
    monkeypatch.setenv("PJE_A3_APPROVAL_TIMEOUT", "0.1")
    with pytest.raises(a3.PinError, match="not authorised"), a3.held_token():
        pass
    assert a3._shared == {} and len(opened) == 1
    with pytest.raises(a3.PinError, match="still open"), a3.held_token():
        pass
    assert len(opened) == 1  # the module was not opened again while the first window is pending
    released.set()
    a3._pending["worker"].join(5)
    with a3.held_token() as token:
        assert token.logged_in
    assert len(opened) == 2


def test_the_token_lock_waits_for_the_holder_across_handles(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr(a3.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("PJE_A3_APPROVAL_TIMEOUT", "30")
    held, order = threading.Event(), []

    def holder():
        with a3.token_lock():
            held.set()
            time.sleep(12 if sys.platform == "win32" else 1)  # past msvcrt's own 10 s LK_LOCK limit on Windows
            order.append("first")

    thread = threading.Thread(target=holder)
    thread.start()
    held.wait(5)
    with a3.token_lock():
        order.append("second")
    thread.join()
    assert order == ["first", "second"]


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows lock retries until the approval timeout")
def test_the_windows_token_lock_gives_up_at_the_approval_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(a3.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("PJE_A3_APPROVAL_TIMEOUT", "0.5")
    held, done = threading.Event(), threading.Event()

    def holder():
        with a3.token_lock():
            held.set()
            done.wait(5)

    thread = threading.Thread(target=holder)
    thread.start()
    held.wait(5)
    try:
        with pytest.raises(TimeoutError), a3.token_lock():
            pass
    finally:
        done.set()
        thread.join()


def test_a_token_with_its_chain_does_not_read_the_store(chain, monkeypatch):
    leaf, intermediate, root = chain
    token, _session = open_token(monkeypatch, [leaf, intermediate], ["CKF_LOGIN_REQUIRED"], issuers=(root,))
    assert not token.cloud
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


def test_check_customer_takes_a_module_by_full_path_or_by_a_library_name_it_finds(acme, tmp_path):
    (tmp_path / "eTPKCS11.dll").write_bytes(b"")
    toml = '[trf1-1g]\nmethod = "a3_totp"\nsecrets = ["TRF1_1G_2FA_DATA"]\n[env]\nset = {{ PJE_A3_MODULE = {} }}\n'
    for module in ("'C:\\Windows\\System32\\eTPKCS11.dll'", '"/usr/lib/libaetpkss.so"', '"eTPKCS11.dll"'):
        with acme(toml.format(module), PATH=str(tmp_path), SYSTEMROOT=str(tmp_path / "nada")):
            assert customers.validate("acme") == [], module
    with acme(toml.format('"naoexiste.dll"'), PATH=str(tmp_path), SYSTEMROOT=str(tmp_path / "nada")):
        assert any("PJE_A3_MODULE" in problem for problem in customers.validate("acme"))


def test_the_hosted_plan_knows_a_declared_a3_without_a_pin():
    from jurisync.hosted import provision

    assert provision.is_a3(customers.PortalSetup("trf1-1g", "a3_totp", ("TRF1_1G_2FA_DATA",)))
    assert not provision.is_a3(customers.PortalSetup("trf1-1g", "certificate_totp", ("TRF1_1G_2FA_DATA",)))
