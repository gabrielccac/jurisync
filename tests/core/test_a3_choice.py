"""The A3 holder certificate chosen once at setup (PJE_A3_CERT) for a token that carries several."""

import datetime
import os
from types import SimpleNamespace
from unittest import mock

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID, ObjectIdentifier

from jurisync.core import a3

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
CPF = ObjectIdentifier("2.16.76.1.3.1")


def _cert(name, *, days=30, company=False, ca=False, serial=None):
    now = datetime.datetime.now(datetime.UTC)
    other = x509.OtherName(ObjectIdentifier(a3.ICP_BRASIL_CNPJ) if company else CPF, b"\x04\x0800000000")
    return (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "AC")]))
        .public_key(KEY.public_key())
        .serial_number(serial or x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=400))
        .not_valid_after(now + datetime.timedelta(days=days))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([other]), critical=False)
        .sign(KEY, hashes.SHA256())
    )


OLD = _cert("JOAO", days=-200)
PERSON = _cert("JOAO")
COMPANY = _cert("ESCRITORIO", company=True)
CA = _cert("AC", ca=True)
TOKEN = [(b"1", OLD), (b"2", PERSON), (b"3", COMPANY), (b"9", CA)]


def test_several_holders_without_a_choice_are_refused_with_the_setup_command():
    with pytest.raises(RuntimeError, match="a3 choose"):
        a3.certificate_chain(TOKEN)


def test_the_chosen_serial_is_the_holder_whatever_else_the_token_carries():
    ident, chain = a3.certificate_chain(TOKEN, chosen=a3.serial_hex(COMPANY))
    assert ident == b"3"
    assert chain[0] == COMPANY


def test_a_chosen_certificate_no_longer_on_the_token_is_not_replaced_by_another():
    with pytest.raises(RuntimeError, match="not on the A3 token"):
        a3.certificate_chain(TOKEN, chosen="abc123")


def test_the_choice_comes_from_the_environment_in_any_spelling():
    serial = a3.serial_hex(PERSON)
    spaced = ":".join(serial.upper()[i : i + 2] for i in range(0, len(serial), 2))
    with mock.patch.dict(os.environ, {a3.CHOSEN_CERTIFICATE: f" 00{spaced} "}):
        assert a3.chosen_serial() == serial


def test_company_and_expired_certificates_are_told_apart():
    assert a3.is_company(COMPANY) and not a3.is_company(PERSON)
    assert "expired" in a3.describe(OLD) and "e-CNPJ" in a3.describe(COMPANY)


def _token():
    token = SimpleNamespace(certs=TOKEN)
    signer = mock.MagicMock()
    signer.return_value.__enter__.return_value = token
    return signer


def test_choose_stores_the_only_valid_personal_certificate(tmp_path, capsys):
    path = tmp_path / ".env"
    path.write_text("OTHER=1\n")
    with mock.patch.object(a3, "A3Signer", _token()), mock.patch.dict(os.environ, {"JURISYNC_ENV_FILE": str(path)}):
        a3._choose(None)
    assert path.read_text().splitlines() == ["OTHER=1", f"{a3.CHOSEN_CERTIFICATE}={a3.serial_hex(PERSON)}"]
    assert "until another is chosen" in capsys.readouterr().out


def test_choose_takes_the_serial_given(tmp_path):
    path = tmp_path / ".env"
    with mock.patch.object(a3, "A3Signer", _token()), mock.patch.dict(os.environ, {"JURISYNC_ENV_FILE": str(path)}):
        a3._choose(a3.serial_hex(COMPANY).upper())
    assert path.read_text().strip() == f"{a3.CHOSEN_CERTIFICATE}={a3.serial_hex(COMPANY)}"


def test_choose_stores_nothing_when_it_cannot_tell(tmp_path):
    path = tmp_path / ".env"
    two = SimpleNamespace(certs=[(b"1", PERSON), (b"2", _cert("JOAO NOVO"))])
    signer = mock.MagicMock()
    signer.return_value.__enter__.return_value = two
    with mock.patch.object(a3, "A3Signer", signer), mock.patch.dict(os.environ, {"JURISYNC_ENV_FILE": str(path)}):
        with pytest.raises(SystemExit, match="--serial"):
            a3._choose(None)
        with pytest.raises(SystemExit, match="nothing was stored"):
            a3._choose("ffff")
    assert not path.exists()
