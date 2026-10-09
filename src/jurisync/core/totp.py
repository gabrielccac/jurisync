"""Authenticator data → TOTP codes, shared by every portal login.

Accepted inputs: ``otpauth://totp/...`` URIs, Google Authenticator exports
(``otpauth-migration://...`` or their base64 ``data``), raw base32 secrets, and
QR images of any of these. Accounts are plain dicts:
``{"secret", "name", "issuer", "algorithm", "digits", "period", "type"}``.

    python -m jurisync.core.totp IMAGE [--account NUMBER]   # print one secret from a QR
"""

import argparse
import base64
import hashlib
import re
from urllib.parse import parse_qs, unquote, urlsplit

import pyotp

BASE32 = re.compile(r"[A-Za-z2-7 ]+={0,6}")


def _varint(data, offset):
    value = shift = 0
    while offset < len(data):
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, offset
        shift += 7
        if shift > 63:
            break
    raise ValueError("Invalid protobuf varint")


def _proto_fields(data):
    """Yield (field_number, wire_type, value) for a protobuf message."""
    offset = 0
    while offset < len(data):
        key, offset = _varint(data, offset)
        field_number, wire_type = key >> 3, key & 7
        if wire_type == 0:
            value, offset = _varint(data, offset)
        elif wire_type == 1:
            value, offset = data[offset : offset + 8], offset + 8
        elif wire_type == 2:
            length, offset = _varint(data, offset)
            value, offset = data[offset : offset + length], offset + length
        elif wire_type == 5:
            value, offset = data[offset : offset + 4], offset + 4
        else:
            raise ValueError(f"Unsupported protobuf wire type: {wire_type}")
        if offset > len(data):
            raise ValueError("Truncated protobuf message")
        yield field_number, wire_type, value


def migration_accounts(encoded):
    """Decode a Google Authenticator export (URI or its base64 data)."""
    if encoded.startswith("otpauth-migration://"):
        encoded = parse_qs(urlsplit(encoded).query).get("data", [""])[0]
    raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    accounts = []
    for field, wire, payload in _proto_fields(raw):
        if field != 1 or wire != 2:
            continue
        account = {"name": "", "issuer": "", "algorithm": "sha1", "digits": 6, "period": 30, "type": "totp"}
        for nested_field, nested_wire, value in _proto_fields(payload):
            if nested_wire == 2 and nested_field == 1:
                account["secret"] = base64.b32encode(value).decode()
            elif nested_wire == 2 and nested_field in (2, 3):
                account["name" if nested_field == 2 else "issuer"] = value.decode("utf-8", errors="replace")
            elif nested_wire == 0 and nested_field == 4:
                account["algorithm"] = {2: "sha256", 3: "sha512"}.get(value, "sha1")
            elif nested_wire == 0 and nested_field == 5:
                account["digits"] = 8 if value == 2 else 6
            elif nested_wire == 0 and nested_field == 6:
                account["type"] = "hotp" if value == 1 else "totp"
        if account.get("secret"):
            accounts.append(account)
    if not accounts:
        raise ValueError("No TOTP account found in migration data")
    return accounts


def parse_accounts(value):
    """Return every account in an otpauth URI, migration export, or raw secret."""
    value = value.strip()
    # Query parsers decode escapes themselves; decoding %2B early loses Base64 '+' characters.
    if not value.startswith(("otpauth://", "otpauth-migration://")) and "data=" not in value:
        value = unquote(value)
    if value.startswith("otpauth://totp/"):
        otp = pyotp.parse_uri(value)
        return [
            {
                "secret": otp.secret,
                "name": otp.name or "",
                "issuer": parse_qs(urlsplit(value).query).get("issuer", [""])[0],
                "algorithm": otp.digest().name.lower(),
                "digits": otp.digits,
                "period": otp.interval,
                "type": "totp",
            }
        ]
    if "data=" in value and not value.startswith("otpauth-migration://"):
        value = parse_qs(value).get("data", [value])[0]
    if len(value) <= 64 and BASE32.fullmatch(value):
        return [
            {
                "secret": value.replace(" ", "").upper(),
                "name": "",
                "issuer": "",
                "algorithm": "sha1",
                "digits": 6,
                "period": 30,
                "type": "totp",
            }
        ]
    return migration_accounts(value)


def accounts_from_image(path):
    """Read the single authenticator QR code in an image."""
    import zxingcpp
    from PIL import Image

    with Image.open(path) as image:
        barcodes = zxingcpp.read_barcodes(image, formats=zxingcpp.BarcodeFormat.QRCode)
    if len(barcodes) != 1:
        raise ValueError(f"Expected one QR code, found {len(barcodes)}")
    value = barcodes[0].text
    if not value.startswith(("otpauth-migration://", "otpauth://totp/")):
        raise ValueError("QR code is not an authenticator TOTP export")
    return parse_accounts(value)


def select_account(accounts, *, match="", index=None, label="OTP"):
    """Pick one TOTP account by name/issuer substring or zero-based index."""
    selected = None
    if match:
        needle = match.strip().casefold()
        found = [
            account
            for account in accounts
            if needle in account.get("name", "").casefold() or needle in account.get("issuer", "").casefold()
        ]
        if len(found) > 1:
            raise ValueError(f"{label} profile match is ambiguous")
        selected = found[0] if found else None
    if selected is None and index is not None:
        if not 0 <= index < len(accounts):
            raise ValueError(f"{label} profile index is outside the available profiles")
        selected = accounts[index]
    if selected is None:
        if len(accounts) != 1:
            raise ValueError(f"Set the {label} profile match or zero-based index")
        selected = accounts[0]
    if selected["type"] != "totp":
        raise ValueError(f"{label} selected authenticator profile is not TOTP")
    return selected


def parse_index(value, name):
    """Parse an optional zero-based index from configuration."""
    if value in (None, ""):
        return None
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"{name} must be an integer") from None


def totp(account):
    return pyotp.TOTP(
        account["secret"],
        digits=account.get("digits", 6),
        interval=account.get("period", 30),
        digest=getattr(hashlib, account.get("algorithm", "sha1")),
    )


def next_code(account, *, sleep=None, now=None):
    """Wait for the next TOTP window and return its code.

    Portals reject a code that was already used in the current window, for
    example by a login a few seconds earlier; the next window's code is fresh.
    """
    import time

    sleep = sleep or time.sleep
    period = account.get("period", 30)
    current = time.time() if now is None else now
    sleep(period - current % period + 1)
    return totp(account).now()


def main():
    parser = argparse.ArgumentParser(description="Print a TOTP secret from an authenticator QR image")
    parser.add_argument("image")
    parser.add_argument("--account", type=int, help="1-based account number when the QR holds several")
    args = parser.parse_args()
    try:
        accounts = accounts_from_image(args.image)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    if args.account is None and len(accounts) > 1:
        for number, account in enumerate(accounts, 1):
            print(f"{number}: {account['issuer'] or '-'} | {account['name'] or '-'} | {account['type'].upper()}")
        parser.error(f"QR contains {len(accounts)} accounts; run again with --account NUMBER")
    number = args.account or 1
    if not 1 <= number <= len(accounts):
        parser.error(f"--account must be between 1 and {len(accounts)}")
    if accounts[number - 1]["type"] != "totp":
        parser.error("selected account is HOTP, not TOTP")
    print(accounts[number - 1]["secret"])


if __name__ == "__main__":
    main()
