"""Run with: python -m scripts.pje_2fa_from_qr IMAGE [--account NUMBER]."""

import argparse
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import pyotp
import zxingcpp
from PIL import Image

def accounts_from_image(path):
    with Image.open(path) as image:
        barcodes = zxingcpp.read_barcodes(image, formats=zxingcpp.BarcodeFormat.QRCode)
    if len(barcodes) != 1:
        raise ValueError(f"Expected one QR code, found {len(barcodes)}")

    value = barcodes[0].text
    if value.startswith("otpauth-migration://"):
        from integrations.pje.auth import migration_accounts
        return migration_accounts(value)
    if value.startswith("otpauth://totp/"):
        otp = pyotp.parse_uri(value)
        query = parse_qs(urlsplit(value).query)
        return [{
            "name": unquote(urlsplit(value).path.lstrip("/")),
            "issuer": query.get("issuer", [""])[0],
            "secret": otp.secret,
            "type": "totp",
        }]
    raise ValueError("QR code is not an authenticator TOTP export")


def main():
    parser = argparse.ArgumentParser(description="Extract TOTP data from an authenticator QR image")
    parser.add_argument("image", type=Path)
    parser.add_argument("--account", type=int, help="1-based account number to extract")
    args = parser.parse_args()

    try:
        accounts = accounts_from_image(args.image)
    except (OSError, ValueError) as error:
        parser.error(str(error))

    if args.account is None and len(accounts) > 1:
        for number, account in enumerate(accounts, 1):
            print(
                f"{number}: {account['issuer'] or '-'} | {account['name'] or '-'} | {account['type'].upper()}",
                flush=True,
            )
        parser.error(f"QR contains {len(accounts)} accounts; run again with --account NUMBER")

    number = args.account or 1
    if not 1 <= number <= len(accounts):
        parser.error(f"--account must be between 1 and {len(accounts)}")
    account = accounts[number - 1]
    if account["type"] != "totp":
        parser.error("selected account is HOTP, not the TOTP format used by PJe")
    print(account["secret"])


if __name__ == "__main__":
    main()
