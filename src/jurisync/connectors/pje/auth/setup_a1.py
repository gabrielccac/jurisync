#!/usr/bin/env python3
"""Configure one A1 certificate in PJeOffice and verify a TRF5 login."""

import argparse
import getpass
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

from jurisync import portals
from jurisync.core.totp import accounts_from_image

PJE_HOME = Path.home() / ".local/share/pjeoffice-pro"
CONFIG = Path.home() / ".pjeoffice-pro/pjeoffice-pro.config"


def configure_a1(config, certificate):
    """Set PJeOffice's A1 path while preserving its other settings."""
    text = config.read_text()
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    replacement = f"list.a1={certificate}"
    matches = [i for i, line in enumerate(lines) if line.rstrip("\r\n").startswith("list.a1=")]
    if matches:
        first = matches[0]
        had_newline = lines[first].endswith(("\n", "\r"))
        lines[first] = replacement + (newline if had_newline else "")
        for index in reversed(matches[1:]):
            del lines[index]
    else:
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += newline
        lines.append(replacement + newline)

    fd, temporary = tempfile.mkstemp(dir=config.parent)
    try:
        with os.fdopen(fd, "w") as output:
            output.write("".join(lines))
        os.chmod(temporary, config.stat().st_mode & 0o777)
        os.replace(temporary, config)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def pjeoffice_pids():
    pids = []
    for process in Path("/proc").iterdir():
        if not process.name.isdigit():
            continue
        try:
            command = (process / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if b"pjeoffice-pro.jar" in command:
            pids.append(int(process.name))
    return pids


def stop_pjeoffice():
    pids = pjeoffice_pids()
    for pid in pids:
        os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 15
    while pjeoffice_pids() and time.monotonic() < deadline:
        time.sleep(0.25)
    if pjeoffice_pids():
        raise RuntimeError("PJeOffice did not stop cleanly")


def start_pjeoffice(display):
    java = PJE_HOME / "jre/bin/java"
    if not java.is_file():
        raise RuntimeError(f"PJeOffice Java runtime not found: {java}")
    command = [
        str(java),
        "-XX:+UseG1GC",
        "-XX:MinHeapFreeRatio=3",
        "-XX:MaxHeapFreeRatio=3",
        "-Xms20m",
        "-Xmx2048m",
        f"-Dpjeoffice_home={PJE_HOME}",
        f"-Dffmpeg_home={PJE_HOME}",
        "-Dpjeoffice_looksandfeels=Metal",
        "-Dcutplayer4j_looksandfeels=Nimbus",
        "-Dsigner4j_a3auto=true",
        "-jar",
        "pjeoffice-pro.jar",
    ]
    env = dict(os.environ, DISPLAY=display)
    process = subprocess.Popen(
        command,
        cwd=PJE_HOME,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("PJeOffice exited while starting")
        try:
            with socket.create_connection(("127.0.0.1", 8800), timeout=0.3):
                time.sleep(1)
                return process
        except OSError:
            time.sleep(0.25)
    process.terminate()
    raise RuntimeError("PJeOffice did not start its local signing service")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("certificate", type=Path, help="path to a .p12/.pfx A1 certificate")
    parser.add_argument("--display", default=os.environ.get("DISPLAY", ":1"))
    qr_default = os.environ.get("PJE_TRF5_2FA_QR")
    parser.add_argument("--qr", type=Path, default=Path(qr_default) if qr_default else None)
    parser.add_argument("--account", type=int, default=1, help="1-based TOTP account in the QR")
    parser.add_argument(
        "--password-env", default="PJE_A1_PIN", help="environment variable holding the certificate password"
    )
    args = parser.parse_args()
    if args.qr is None:
        parser.error("pass --qr or set PJE_TRF5_2FA_QR")

    certificate = args.certificate.expanduser().resolve(strict=True)
    if not certificate.is_file():
        parser.error("certificate path must point to a file")
    password = os.environ.pop(args.password_env, None) or getpass.getpass("A1 certificate password: ")
    if not password:
        parser.error("certificate password cannot be empty")
    accounts = accounts_from_image(args.qr)
    if not 1 <= args.account <= len(accounts) or accounts[args.account - 1]["type"] != "totp":
        parser.error("selected QR account must be a TOTP account")

    stop_pjeoffice()
    configure_a1(CONFIG, certificate)
    print(f"Configured A1 certificate: {certificate}")
    start_pjeoffice(args.display)
    from jurisync.connectors.pje.auth.certificate import trf5_cert_login

    session = trf5_cert_login(twofa_data=accounts[args.account - 1]["secret"], pin=password)
    config = portals.pje("trf5-1g")
    page = session.get(config.search_url, impersonate="chrome", allow_redirects=True, timeout=30)
    authenticated = (
        page.status_code == 200
        and urllib.parse.urlsplit(page.url).hostname == config.host
        and "loginForm" not in page.text
    )
    if not authenticated:
        raise RuntimeError("TRF5 did not return an authenticated PJe page")
    print(f"A1 setup and TRF5 login validated: {urllib.parse.urlsplit(page.url).hostname}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError) as error:
        print(f"A1 setup failed: {error}", file=sys.stderr)
        sys.exit(1)
