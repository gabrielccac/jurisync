"""Smoke test of the real logins: one login per configured portal and service, a check that the session is logged
in, then the session closed. Nothing else is read, nothing is written and no ciência is given.

    jurisync-smoke victor                      # every portal and service customers/victor configures
    jurisync-smoke                             # without a customer: the shared ~/.config/jurisync/.env
    jurisync-smoke victor --only trf1-1g,trf1-2g --save before.json
    jurisync-smoke victor --services           # ADVBOX and Juridiq too
    jurisync-smoke --compare before.json after.json

ADVBOX and Juridiq are left out unless asked for (``--services`` or by name in ``--only``): a new ADVBOX login can
make ADVBOX e-mail the lawyer a device code, and that run then stops, because only the lawyer can answer it. Their
login always starts from an empty session file in a temporary folder: a saved session or token never counts as ok, and
the file a running server uses is neither read nor overwritten.

Each portal gets one attempt and the run stops at the first refused login or CAPTCHA: the same password or
authenticator usually serves other portals, and a few refusals lock the account. Consecutive logins wait for a new
authenticator window, because portals refuse a code already used. Results carry codes and times, never values.
An A3 login refused by the token (a wrong PIN, a cancelled authorisation in the cloud A3's app) stops the run too:
a few wrong PINs lock the token. A cloud A3 is authorised once and that session serves every login of the run
(``core.a3.held_token``).
"""

import argparse
import fnmatch
import importlib
import json
import os
import re
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

from jurisync import customers, portals, registry
from jurisync.core import credentials
from jurisync.core.a3 import PinError
from jurisync.core.session_pool import EXPLANATIONS, REFUSALS, login_reason

TOTP_PERIOD = 30
STOP_REASONS = (*REFUSALS, "captcha")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
DOCUMENT = re.compile(r"(?<!\d)(?:\d{11}|\d{14}|\d{3}\.\d{3}\.\d{3}-\d{2}|\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})(?!\d)")
# Variables whose value identifies or unlocks an account: never shown, even inside a portal's error message.
SECRET_NAMES = (
    "*_PASSWORD", "*_SENHA", "*_PIN", "*_2FA_*", "*_TOTP*", "*_SECRET", "*_TOKEN", "*_KEY", "*_EMAIL", "*_USERNAME",
    "*_LOGIN", "*_CPF", "*_DEVICE_ID",
)  # fmt: skip
SESSION_FILES = {"advbox": "ADVBOX_SESSION_FILE", "juridiq": "JURIDIQ_SESSION_FILE"}


@dataclass(frozen=True)
class Target:
    system: str
    key: str
    method: str


@dataclass(frozen=True)
class Result:
    target: str
    method: str
    status: str  # ok, falha, nao_testado
    reason: str = ""
    seconds: float = 0.0
    detail: str = ""


def method_of(system, key):
    """The login method this install uses for the portal, as its connector decides it."""
    if system == "pje":
        from jurisync.connectors.pje.auth.sessions import auth_method

        return auth_method(key)
    if system == "eproc":
        from jurisync.connectors.eproc.client import auth_method

        return auth_method(key)
    portal = portals.get(key)
    return customers.declared_method(portal) or portal.auth


def targets(only=(), services=False):
    """Every portal whose login is configured, in registry order; then, with ``services`` or named in ``only``, every
    service whose account is."""
    found = []
    for connector in registry.CONNECTORS.values():
        if connector.login:
            if (services or connector.name in only) and all(credentials.env(name) for name in connector.login):
                found.append(Target(connector.name, connector.name, "password"))
            continue
        module = importlib.import_module(connector.module)
        if getattr(module, "sessions", None) is None or not hasattr(module, "configured_portals"):
            continue
        found += [Target(connector.name, key, method_of(connector.name, key)) for key in module.configured_portals()]
    return [target for target in found if target.key in only] if only else found


@contextmanager
def scratch_session_file(system):
    """The service's session file pointed at an empty temporary folder for the block, then restored."""
    name = SESSION_FILES[system]
    saved = os.environ.get(name)
    with tempfile.TemporaryDirectory(prefix="jurisync-smoke-") as folder:
        path = Path(folder) / f"{system}-session.json"
        os.environ[name] = str(path)
        try:
            yield path
        finally:
            if saved is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = saved


def login_service(target):
    """A new ADVBOX or Juridiq login from an empty session file: ok only when the login wrote a new session."""
    with scratch_session_file(target.system) as path:
        if target.system == "advbox":
            from jurisync.connectors.advbox.client import AdvboxClient

            client = AdvboxClient()
            try:
                client.ensure_session()
            finally:
                client.close()
        else:
            from jurisync.connectors.juridiq.api import JuridiqApi

            JuridiqApi().token(force_refresh=True)
        if not path.is_file():
            raise RuntimeError(f"{target.system}: no new session was written, so no login was proven")


def login(target):
    """One fresh login, the liveness check of its connector, then the session closed. Raises on failure."""
    if target.system in SESSION_FILES:
        login_service(target)
        return
    fresh = None
    if target.system == "pje":
        from jurisync.connectors.pje.process import authenticate

        def fresh(key):
            return authenticate(key)[0]  # the saved session would prove nothing about the login

    registry.get(target.system).pool().verify(target.key, fresh)


def wait_for_new_code(say, sleep=time.sleep, clock=time.time):
    pause = TOTP_PERIOD - clock() % TOTP_PERIOD + 1
    say(f"  aguardando {int(pause)} s por um código novo do autenticador...")
    sleep(pause)


def masked(text):
    """``text`` without e-mails, CPFs/CNPJs or the value of any secret variable set in this process."""
    for name, value in os.environ.items():
        if len(value.strip()) >= 4 and any(fnmatch.fnmatchcase(name.upper(), pattern) for pattern in SECRET_NAMES):
            text = text.replace(value.strip(), "[…]")
    return DOCUMENT.sub("[…]", EMAIL.sub("<e-mail>", text))


def needs_person(error):
    """A login that waits for something only a person gives: a CAPTCHA, or the code ADVBOX e-mails for a new device."""
    from jurisync.connectors.advbox.session import AdvboxDeviceVerification

    return isinstance(error, AdvboxDeviceVerification) or login_reason(error) == "captcha"


def run(found, *, say=print, attempt=login, wait=wait_for_new_code):
    """Log in to each target once, in order; after a refusal, a CAPTCHA or a device code the rest are not tried."""
    from jurisync.core.mcp import sanitize_diagnostics

    results, stopped, logged = [], None, False
    if any(target.method.startswith("a3") for target in found):
        say("A3: responda a janela do certificado quando abrir (desktopID: PIN e e-token); uma vale para toda a rodada")
    for target in found:
        if stopped:
            results.append(Result(target.key, target.method, "nao_testado", detail=f"parado após {stopped}"))
            continue
        if logged and target.system not in registry.SERVICES:
            wait(say)
        logged = True
        say(f"{target.key}: login ({target.method})...")
        began = time.monotonic()
        try:
            attempt(target)
        except Exception as error:  # noqa: BLE001 - every failure is a result of the smoke test
            reason = login_reason(error)
            detail = masked(sanitize_diagnostics(f"{type(error).__name__}: {str(error)[:200]}"))
            results.append(
                Result(target.key, target.method, "falha", reason, round(time.monotonic() - began, 1), detail)
            )
            say(f"  falha [{reason}] {EXPLANATIONS[reason]}")
            if reason in STOP_REASONS or needs_person(error) or isinstance(error, PinError):
                stopped = f"{target.key} [{reason}]"
                say("  parando: outra tentativa pode bloquear a conta ou exige uma pessoa")
            continue
        results.append(Result(target.key, target.method, "ok", seconds=round(time.monotonic() - began, 1)))
        say(f"  ok ({results[-1].seconds} s)")
    return results


def table(results):
    lines = [f"{'alvo':22} {'método':17} {'resultado':12} {'tempo':>7}  motivo"]
    for result in results:
        reason = f"[{result.reason}] {result.detail}" if result.reason else result.detail
        lines.append(f"{result.target:22} {result.method:17} {result.status:12} {result.seconds:6.1f}s  {reason}")
    return "\n".join(lines)


def compare(before, after):
    """Lines for the targets whose method, status or reason changed between two saved runs."""
    old = {item["target"]: item for item in before["results"]}
    new = {item["target"]: item for item in after["results"]}
    lines = []
    for key in sorted(old.keys() | new.keys()):
        a, b = old.get(key), new.get(key)
        if a is None or b is None:
            lines.append(f"{key}: {'só depois' if a is None else 'só antes'}")
        elif (a["method"], a["status"], a["reason"]) != (b["method"], b["status"], b["reason"]):
            lines.append(
                f"{key}: {a['method']} {a['status']} {a['reason']} -> {b['method']} {b['status']} {b['reason']}"
            )
    return lines or ["sem diferenças de método ou resultado"]


def load_env(customer):
    from jurisync.customers import load_env_file

    if customer:
        customers.load_setup(customer)  # an invalid name or a broken portals.toml fails before any login
        os.environ["JURISYNC_CUSTOMER"] = customer
    load_env_file()


def main(argv=None, *, say=print):
    parser = argparse.ArgumentParser(
        prog="jurisync-smoke",
        description="Faz um login por portal configurado, confirma a sessão e sai. Não lê nem grava nada no portal.",
    )
    parser.add_argument("customer", nargs="?", default="", help="escritório (customers/<nome>); vazio: o .env comum")
    parser.add_argument("--only", default="", help="só estes portais ou serviços, separados por vírgula")
    parser.add_argument(
        "--services", action="store_true", help="inclui ADVBOX e Juridiq (o ADVBOX pode mandar um código por e-mail)"
    )
    parser.add_argument("--save", type=Path, help="grava o resultado em JSON (sem valores de credenciais)")
    parser.add_argument("--compare", nargs=2, type=Path, metavar=("ANTES", "DEPOIS"), help="compara dois --save")
    args = parser.parse_args(argv)
    if args.compare:
        before, after = (json.loads(path.read_text(encoding="utf-8")) for path in args.compare)
        say("\n".join(compare(before, after)))
        return 0
    try:
        load_env(args.customer.strip())
    except ValueError as error:
        say(f"Erro: {error}")
        return 2
    found = targets({item.strip() for item in args.only.split(",") if item.strip()}, services=args.services)
    if not found:
        say("Nenhum portal ou serviço com login configurado.")
        return 2
    say(f"{len(found)} login(s): {', '.join(target.key for target in found)}")
    results = run(found, say=say)
    say("")
    say(table(results))
    if args.save:
        payload = {
            "customer": args.customer,
            "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "results": [asdict(result) for result in results],
        }
        args.save.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        say(f"Resultado gravado em {args.save}")
    return 0 if all(result.status == "ok" for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
