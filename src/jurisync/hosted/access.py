"""How an office reaches each PJe portal, as vault entries: password + TOTP, or the office's A1 certificate.

The vault keeps the same variable names a local ``.env`` uses, so the connectors need no hosted-specific code:

* certificate (office-wide): ``PJE_A1_P12`` (file bytes) and ``PJE_A1_PIN``;
* per portal, by its env prefix: ``<PREFIX>_USERNAME``/``_PASSWORD`` for the password login, ``<PREFIX>_CERT=true`` to
  use the certificate on a portal registered for passwords, and ``<PREFIX>_2FA_DATA`` for the authenticator the SSO asks.

Every change is tried against the portal first (``check_login``) and stored only when the login gets in.
"""

import io
from dataclasses import dataclass

import pyotp

from jurisync import portals
from jurisync.core import credentials
from jurisync.core.totp import parse_accounts, select_account

PASSWORD = "password"
CERTIFICATE = "certificate"
A1_FILE = "PJE_A1_P12"
A1_PIN = "PJE_A1_PIN"
PORTAL_FIELDS = ("USERNAME", "PASSWORD", "CERT", "2FA_DATA", "2FA_QR", "OTP_PROFILE_MATCH", "OTP_PROFILE_INDEX")


def pje_portals():
    return [portals.pje(key) for key in portals.keys("pje")]


def portal_names(config):
    """Every vault name that configures this portal, current and legacy prefixes."""
    return {
        f"{prefix}_{field}" for prefix in (config.env_prefix, *config.legacy_env_prefixes) for field in PORTAL_FIELDS
    }


# Certificate


@dataclass(frozen=True)
class CertificateInfo:
    holder: str
    expires: str
    expired: bool


def read_certificate(data, pin):
    """Open an A1 file with its PIN; ValueError in Portuguese when it does not open."""
    import datetime

    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import NameOID

    if not data:
        raise ValueError("Envie o arquivo do certificado (.pfx ou .p12)")
    try:
        key, cert, _ = pkcs12.load_key_and_certificates(data, (pin or "").encode("utf-8"))
    except (ValueError, TypeError):
        raise ValueError("O certificado não abriu: confira o arquivo e o PIN") from None
    if key is None or cert is None:
        raise ValueError("O arquivo não tem a chave privada do certificado")
    names = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    holder = names[0].value if names else cert.subject.rfc4514_string()
    expires = cert.not_valid_after_utc
    return CertificateInfo(
        holder=str(holder),
        expires=expires.strftime("%d/%m/%Y"),
        expired=expires < datetime.datetime.now(datetime.UTC),
    )


def certificate_info(values, files):
    if A1_FILE not in files:
        return None
    try:
        return read_certificate(files[A1_FILE], values.get(A1_PIN, ""))
    except ValueError:
        return None


# Authenticator


def qr_value(image_bytes):
    import zxingcpp
    from PIL import Image

    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            codes = zxingcpp.read_barcodes(image, formats=zxingcpp.BarcodeFormat.QRCode)
    except OSError:
        raise ValueError("A imagem do QR code não abriu") from None
    if len(codes) != 1:
        raise ValueError(f"Esperava um QR code na imagem, encontrei {len(codes)}")
    return codes[0].text


def authenticator_accounts(text="", image=b""):
    """The TOTP accounts in a pasted export/secret or a QR image."""
    raw = qr_value(image) if image else (text or "").strip()
    if not raw:
        return []
    try:
        return [account for account in parse_accounts(raw) if account.get("type") == "totp"]
    except (ValueError, TypeError):
        raise ValueError("O código do autenticador não foi reconhecido") from None


def account_label(account):
    return " · ".join(part for part in (account.get("issuer"), account.get("name")) if part) or "conta sem nome"


def single_account_uri(accounts, choice=None):
    """One otpauth://totp URI for the chosen account, so every login path reads it the same way."""
    if not accounts:
        raise ValueError("Informe o autenticador (texto ou imagem do QR code)")
    account = select_account(accounts, index=choice if len(accounts) > 1 else None, label="Autenticador")
    import hashlib

    otp = pyotp.TOTP(
        account["secret"],
        digits=account.get("digits", 6),
        interval=account.get("period", 30),
        digest=getattr(hashlib, account.get("algorithm", "sha1")),
    )
    return otp.provisioning_uri(name=account.get("name") or "PJe", issuer_name=account.get("issuer") or None)


# Portal state


@dataclass(frozen=True)
class PortalState:
    key: str
    label: str
    method: str | None
    username: str = ""
    has_authenticator: bool = False


INSTANCES = {"1g": "1º grau", "2g": "2º grau"}


def portal_label(config):
    if config.key.endswith("-tru"):
        return f"{config.tribunal} · Turma Regional de Uniformização"
    return f"{config.tribunal} · {INSTANCES.get(config.instance, config.instance)}"


def portal_state(config, values, files):
    def read(field):
        for prefix in (config.env_prefix, *config.legacy_env_prefixes):
            value = values.get(f"{prefix}_{field}", "").strip()
            if value:
                return value
        return ""

    certificate = A1_FILE in files and (config.auth == "certificate_totp" or read("CERT").lower() == "true")
    password = bool(read("USERNAME") and read("PASSWORD"))
    method = CERTIFICATE if certificate else PASSWORD if password else None
    return PortalState(
        config.key, portal_label(config), method, read("USERNAME") if method == PASSWORD else "", bool(read("2FA_DATA"))
    )


def with_portal(values, config, method, *, username="", password="", authenticator=""):
    """A copy of ``values`` with this portal set to ``method``; the other portals are untouched."""
    updated = {name: value for name, value in values.items() if name not in portal_names(config)}
    prefix = config.env_prefix
    if method == PASSWORD:
        if not username or not password:
            raise ValueError("Informe o usuário (CPF) e a senha do PJe")
        updated[f"{prefix}_USERNAME"] = username.strip()
        updated[f"{prefix}_PASSWORD"] = password
        if not authenticator:
            raise ValueError("O login com senha exige o autenticador (2FA) do PJe")
    elif method == CERTIFICATE:
        if config.auth != "certificate_totp":
            updated[f"{prefix}_CERT"] = "true"
    else:
        raise ValueError("Escolha certificado ou login com senha")
    if authenticator:
        updated[f"{prefix}_2FA_DATA"] = authenticator
    return updated


def without_portal(values, config):
    return {name: value for name, value in values.items() if name not in portal_names(config)}


# Login check


def check_login(portal):
    """Log in to the portal with the current credential scope and close the session. Raises on failure."""
    from jurisync.connectors.pje.auth.sessions import auth_method, is_authenticated

    config = portals.pje(portal)
    if auth_method(portal) == "certificate_totp":
        from jurisync.connectors.pje.auth.certificate import cert_login

        session = cert_login(portal)
    else:
        from jurisync.connectors.pje.auth.sso import login_session
        from jurisync.connectors.pje.process import credentials as password_credentials

        session, _ = login_session(*password_credentials(portal), config.login_url, config.host)
    try:
        if not is_authenticated(session, portal):
            raise RuntimeError("o login terminou mas a consulta de processos não abriu")
    finally:
        session.close()


# Everything an office can test from the web area or `jurisync-hosted portal check`.
SERVICES = ("advbox", "juridiq")


def check_targets():
    return (*portals.keys("pje"), *portals.keys("eproc"), *SERVICES)


def check(target):
    """Log in to a PJe or Eproc portal, ADVBOX or Juridiq with the current credential scope. Raises on failure.

    A fresh login is forced: a cached cookie or token would otherwise pass without proving the new password.
    """
    if target in portals.keys("pje"):
        return check_login(target)
    if target in portals.keys("eproc"):
        from jurisync.connectors.eproc.client import authenticate

        session = authenticate(target)[0]
        session.close()
        return None
    if target == "advbox":
        from jurisync.connectors.advbox.client import AdvboxClient
        from jurisync.connectors.advbox.session import session_file

        session_file().unlink(missing_ok=True)
        client = AdvboxClient()
        try:
            client.ensure_session()
        finally:
            client.close()
        return None
    if target == "juridiq":
        from jurisync.connectors.juridiq.api import JuridiqApi, config

        config()["sessionFile"].unlink(missing_ok=True)
        JuridiqApi().token()
        return None
    raise ValueError(f"Unknown access {target}")


def forget(target):
    """After a change: drop the office's open login and cached cookie or token for this access."""
    if target in portals.keys("pje"):
        return forget_sessions(target)
    if target in portals.keys("eproc"):
        try:
            from jurisync.connectors.eproc.server import sessions

            sessions.forget(target)
        except ImportError:
            pass
        return None
    if target == "advbox":
        from jurisync.connectors.advbox.server import client
        from jurisync.connectors.advbox.session import session_file

        client.forget()
        session_file().unlink(missing_ok=True)
        return None
    if target == "juridiq":
        from jurisync.connectors.juridiq.api import config

        config()["sessionFile"].unlink(missing_ok=True)
        return None
    raise ValueError(f"Unknown access {target}")


def forget_sessions(portal):
    """Drop the pooled login and the cookie cache of this portal for the current tenant."""
    from jurisync.connectors.pje.auth.sessions import cache_path

    try:
        from jurisync.connectors.pje.server import sessions

        sessions.forget(portal)
    except ImportError:
        pass
    path = cache_path(portal)
    if path is not None:
        path.unlink(missing_ok=True)


def tenant_scope(tenant, values, files):
    return credentials.scope(tenant, values, files)
