"""Guided credential setup: ask once for each portal's login, store it where every MCP client finds it,
check the logins, and register the servers with Claude.

    jurisync-setup --portals tjrs-1g,trf4-1g-rs,trf4-1g-sc --qr authenticator-qr.png
    jurisync-setup --customer elisandra          # portals taken from customers/elisandra/portals.toml
    jurisync-setup export --customer elisandra --out elisandra.jurisync   # operator: encrypted bundle
    jurisync-setup import elisandra.jurisync                              # customer machine: passphrase on a local page
    jurisync-setup skills --customer elisandra                            # install/update the customer's skills from GitHub

Secrets go to one file (``~/.config/jurisync/.env`` unless ``--env-file``), created with owner-only permissions,
and are never printed. Servers read that file at startup, so Claude Desktop and Claude Code share the same login.
"""

import argparse
import fnmatch
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from jurisync import customers, portals
from jurisync.core.totp import match_for_tribunal, parse_accounts, select_account, unique_match
from jurisync.install import credentials_bundle
from jurisync.paths import USER_ENV_FILE

SERVERS = {
    "esaj": "jurisync-esaj",
    "eproc": "jurisync-eproc",
    "pje": "jurisync-pje",
    "advbox": "jurisync-advbox",
    "juridiq": "jurisync-juridiq",
    "djen": "jurisync-djen",
}
FILE_SUFFIXES = ("_P12", "_PFX")  # variables that hold the path of a certificate file
# Settings of the computer a bundle is installed on: a bundle never sets them.
MACHINE_NAMES = ("JURISYNC_ENV_FILE", "JURISYNC_CHROME_PATH", "LIBREOFFICE_BIN", "*_LOGIN_URL")
# Variables outside the portal registry that a bundle may carry: the other systems' logins and per-office settings.
SYSTEM_NAMES = (
    "PJE_A1_P12", "PJE_A1_PIN", "PROJUDI_A1_P12", "PROJUDI_A1_PIN", "ESAJ_A1_P12", "ESAJ_A1_PIN",
    "PJE_ALLOW_DRAFT_WRITES", "ADVBOX_EMAIL", "ADVBOX_PASSWORD", "ADVBOX_DEVICE_ID", "ADVBOX_UPLOAD_FOLDER",
    "ADVBOX_ALLOW_WRITES", "JURIDIQ_EMAIL", "JURIDIQ_PASSWORD", "JURIDIQ_ALLOW_WRITES", "DJEN_SEARCHES",
    "JURISYNC_CUSTOMER",
)  # fmt: skip
VARIABLE_NAME = re.compile(r"[A-Z][A-Z0-9_]*\Z")
CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")


def is_file_variable(name):
    """Whether a variable holds a certificate file: ``PJE_A1_P12``, ``PROJUDI_A1_P12``, any ``<PREFIX>_A1_P12``."""
    return name.endswith(FILE_SUFFIXES)


def is_machine_name(name):
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in MACHINE_NAMES)


def portal_prefixes(keys=None):
    """``PREFIX_`` of each registered portal (current and earlier names), for every portal or the given keys."""
    found = []
    for key in keys if keys is not None else portals.PORTALS:
        portal = portals.get(key) if keys is not None else portals.PORTALS[key]
        found += [f"{prefix}_" for prefix in (portal.env_prefix, *portal.legacy_env_prefixes) if prefix]
    return tuple(found)


def bundle_names(customer):
    """Extra names a bundle for this customer may set: what its portals.toml declares, when this machine has it."""
    try:
        names, optional = declared_names(customer)
        return {*names, *optional, *customers.fixed_variables(customer)}
    except (ValueError, OSError):
        return set()


def accepted_values(values, customer, say):
    """The bundle variables that may be written: registry, system or declared names, with single-line values."""
    declared, prefixes, refused = bundle_names(customer), portal_prefixes(), []
    for name, value in values.items():
        known = name in SYSTEM_NAMES or name in declared or name.startswith(prefixes)
        if not VARIABLE_NAME.match(name) or is_machine_name(name) or not known:
            refused.append(name)
        elif CONTROL_CHARACTERS.search(str(value)):
            raise ValueError(f"O pacote tem um valor inválido (quebra de linha ou caractere de controle) em {name}")
    if refused:
        shown = ", ".join(sorted(repr(name) if not VARIABLE_NAME.match(name) else name for name in refused))
        say(f"Variáveis do pacote ignoradas (não são credenciais de portal deste cliente): {shown}")
    return {name: str(value) for name, value in values.items() if name not in refused}


def qr_text(path):
    """The text of the single QR code in an authenticator export image."""
    import zxingcpp
    from PIL import Image

    with Image.open(path) as image:
        codes = zxingcpp.read_barcodes(image, formats=zxingcpp.BarcodeFormat.QRCode)
    if len(codes) != 1:
        raise ValueError(f"Esperava um QR code na imagem, encontrei {len(codes)}")
    value = codes[0].text
    if not value.startswith(("otpauth-migration://", "otpauth://totp/")):
        raise ValueError("O QR code não é uma exportação do aplicativo autenticador")
    return value


def read_env(path):
    values = {}
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return values
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.removeprefix("export ").split("=", 1)
            values[key.strip()] = value.strip().strip("\"'")
    return values


def write_env(path, updates):
    """Merge ``updates`` into a KEY=value file, keeping other lines, with owner-only permissions."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = []
    remaining = dict(updates)
    for number, line in enumerate(lines):
        key = line.strip().removeprefix("export ").split("=", 1)[0].strip()
        if "=" in line and key in remaining:
            lines[number] = f"{key}={remaining.pop(key)}"
    lines.extend(f"{key}={value}" for key, value in remaining.items())
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write("\n".join(lines) + "\n")
    os.replace(temporary, path)
    os.chmod(path, 0o600)


def portal_keys(requested, customer):
    if requested:
        keys = [key.strip() for key in requested.split(",") if key.strip()]
    elif customer:
        keys = list(customers.load_setup(customer))
    else:
        keys = list(portals.keys("eproc"))
    return [portals.eproc(key).key for key in keys]


def stored(values, portal, name):
    """A portal variable from a parsed env file, under its canonical or earlier (Portuguese/legacy) name."""
    for prefix in (portal.env_prefix, *portal.legacy_env_prefixes):
        for candidate in (name, *portals.LEGACY_NAMES.get(name, ())):
            value = values.get(f"{prefix}_{candidate}")
            if value:
                return value
    return ""


def describe(accounts):
    return [f"{n}: {a['issuer'] or '-'} | {a['name'] or '-'}" for n, a in enumerate(accounts, 1)]


def collect(keys, current, *, qr, ask, ask_secret, say):
    """Return the variables to store for these portals, asking only for what is missing."""
    updates, accounts, chosen = {}, None, None
    for key in keys:
        portal = portals.eproc(key)
        say(f"\n== {key} ({portal.host}) ==")

        def have(name, portal=portal):
            return stored(current, portal, name) or portal.env(name)

        if not have("USERNAME"):
            updates[portal.env_name("USERNAME")] = ask("Usuário (CPF ou login): ").strip()
        if not have("PASSWORD"):
            updates[portal.env_name("PASSWORD")] = ask_secret("Senha: ").strip()
        if have("2FA_DATA"):
            continue
        if accounts is None:
            path = qr or ask("Imagem do QR do autenticador (arraste o arquivo aqui): ").strip().strip("\"'")
            text = qr_text(path)
            accounts = parse_accounts(text)
            say("Perfis no autenticador:\n  " + "\n  ".join(describe(accounts)))
        updates[portal.env_name("2FA_DATA")] = text
        if len(accounts) == 1:
            select_account(accounts, label=key)
            continue
        # Several profiles: decide now, once, and store the issuer; login only reads OTP_PROFILE_MATCH.
        match = match_for_tribunal(accounts, portal.tribunal)
        if match:
            say(f"Perfil do {key}: {match}")
        else:
            answer = ask(f"Qual perfil é do {key}? [número{', Enter = ' + str(chosen) if chosen else ''}]: ").strip()
            chosen = int(answer) if answer else chosen
            if not chosen or not 1 <= chosen <= len(accounts):
                raise ValueError("Escolha um número de perfil da lista")
            match = unique_match(accounts, select_account(accounts, index=chosen - 1, label=key))
            if not match:  # issuer and name repeat across profiles: only the position identifies it
                updates[portal.env_name("OTP_PROFILE_INDEX")] = str(chosen - 1)
                continue
        updates[portal.env_name("OTP_PROFILE_MATCH")] = match
    return updates


def check_login(key):
    """Log in once. Returns (ok, message); the message never contains a secret."""
    from jurisync.connectors.eproc.client import authenticate

    try:
        session, _, _ = authenticate(key)
    except Exception as error:  # noqa: BLE001 - report any login failure to the user
        return False, f"{type(error).__name__}: {str(error)[:140]}"
    try:
        session.close()
    except Exception:  # noqa: BLE001
        pass
    return True, "login ok"


def check_pje(key):
    """Authenticate a PJe portal (A1 certificate + authenticator, or password + authenticator)."""
    from jurisync.connectors.pje.process import login_portal

    try:
        session, _ = login_portal(key)
    except Exception as error:  # noqa: BLE001 - report any login failure to the user
        return False, f"{type(error).__name__}: {str(error)[:140]}"
    try:
        session.close()
    except Exception:  # noqa: BLE001
        pass
    return True, "login ok"


def check_advbox():
    """Open (or reuse) the ADVBOX session; a new device must be approved in ADVBOX first."""
    from jurisync.connectors.advbox.client import AdvboxClient

    try:
        AdvboxClient().ensure_session()
    except Exception as error:  # noqa: BLE001
        return False, f"{type(error).__name__}: {str(error)[:140]}"
    return True, "sessão ok"


def check_juridiq():
    """Log in to Juridiq with the stored e-mail and password."""
    from jurisync.connectors.juridiq.api import JuridiqApi

    try:
        JuridiqApi().token(force_refresh=True)
    except Exception as error:  # noqa: BLE001
        return False, f"{type(error).__name__}: {str(error)[:140]}"
    return True, "sessão ok"


def run_check(check):
    """``eproc:<portal>``, ``pje:<portal>``, ``advbox`` or ``juridiq`` → (ok, message)."""
    system, _, key = check.partition(":")
    if system == "eproc":
        return check_login(key)
    if system == "pje":
        return check_pje(key)
    if system == "advbox":
        return check_advbox()
    if system == "juridiq":
        return check_juridiq()
    return False, f"verificação desconhecida: {check}"


def wait_for_fresh_code(secret_text, say):
    """Portals refuse a TOTP code already used in this window; wait for the next one."""
    try:
        period = parse_accounts(secret_text)[0].get("period", 30)
    except (ValueError, IndexError):
        period = 30
    pause = period - time.time() % period + 1
    say(f"  aguardando {int(pause)} s para um código novo do autenticador...")
    time.sleep(pause)


def desktop_config_path():
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", home / "AppData" / "Roaming")) / "Claude" / "claude_desktop_config.json"
    return home / ".config" / "Claude" / "claude_desktop_config.json"


def register_desktop(names, say):
    path = desktop_config_path()
    if not path.parent.exists():
        say("Claude Desktop não encontrado; pulei o registro nele.")
        return False
    config = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    servers = config.setdefault("mcpServers", {})
    for name, command in names.items():
        servers[name] = {"command": command}
    path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    say(f"Registrado no Claude Desktop ({path.name}); reinicie o aplicativo.")
    return True


def register_claude_code(names, say):
    claude = shutil.which("claude")
    if not claude:
        say("Claude Code não encontrado; pulei o registro nele.")
        return False
    for name, command in names.items():
        result = subprocess.run(
            [claude, "mcp", "add", "--scope", "user", name, "--", command], capture_output=True, text=True, check=False
        )
        if result.returncode and "already exists" not in (result.stdout + result.stderr):
            say(f"  {name}: não consegui registrar ({(result.stderr or result.stdout).strip()[:100]})")
            return False
    say("Registrado no Claude Code.")
    return True


def server_commands(servers):
    return {name: shutil.which(command) or command for name, command in SERVERS.items() if name in servers}


def check_secret(check):
    """The authenticator export behind a check, to space out logins that share one TOTP window."""
    system, _, key = check.partition(":")
    try:
        if system == "eproc":
            return portals.eproc(key).env("2FA_DATA")
        if system == "pje":
            return portals.pje(key).env("2FA_DATA")
    except ValueError:
        return ""
    return ""


def verify_and_register(checks, args, say, servers=None):
    """Run each check, then register the servers. Returns the number of failed checks."""
    failures = 0
    if not args.no_login and checks:
        say("\nTestando o login de cada serviço...")
        previous_secret = None
        for check in checks:
            secret = check_secret(check)
            if previous_secret and previous_secret == secret:
                wait_for_fresh_code(secret, say)
            ok, message = run_check(check)
            if not ok and "Timed out" in message:
                # A slow first start (driver download, busy portal): one retry on a fresh code. Rejected
                # credentials are never retried, so a wrong password cannot trip the account lock.
                say(f"  {check}: demorou demais; tentando mais uma vez com um código novo...")
                wait_for_fresh_code(secret, say)
                ok, message = run_check(check)
            say(f"  {check}: {'pronto' if ok else 'FALHOU - ' + message}")
            failures += 0 if ok else 1
            previous_secret = secret
    if args.register != "none":
        wanted = set(servers or args.servers.split(","))
        names = server_commands(wanted)
        if args.register in ("auto", "claude-code"):
            register_claude_code(names, say)
        if args.register in ("auto", "desktop"):
            register_desktop(names, say)
    return failures


def add_finish_options(parser):
    parser.add_argument("--env-file", help=f"arquivo de credenciais (padrão: {USER_ENV_FILE})")
    parser.add_argument("--no-login", action="store_true", help="não testa o login")
    parser.add_argument("--register", choices=("auto", "claude-code", "desktop", "none"), default="auto")
    parser.add_argument("--servers", default="eproc,djen", help="servidores a registrar (o pacote já indica os dele)")


def target_env_file(args):
    return Path(args.env_file or os.environ.get("JURISYNC_ENV_FILE") or USER_ENV_FILE)


def setup_main(argv, *, ask, ask_secret, say):
    parser = argparse.ArgumentParser(description="Configura as credenciais dos portais e registra os servidores.")
    parser.add_argument("--customer", help="cliente em customers/<nome>/portals.toml (define os portais)")
    parser.add_argument("--portals", help="portais separados por vírgula (padrão: todos os eproc)")
    parser.add_argument("--qr", help="imagem do QR do autenticador")
    add_finish_options(parser)
    args = parser.parse_args(argv)

    env_file = target_env_file(args)
    keys = portal_keys(args.portals, args.customer)
    updates = collect(keys, read_env(env_file), qr=args.qr, ask=ask, ask_secret=ask_secret, say=say)
    if updates:
        write_env(env_file, updates)
        say(f"\nCredenciais gravadas em {env_file} (somente o seu usuário lê esse arquivo).")
    os.environ.update({k: v for k, v in read_env(env_file).items() if k not in os.environ})
    return 1 if verify_and_register([f"eproc:{key}" for key in keys], args, say) else 0


def declared_names(customer, keys=None):
    """Every variable a customer's portals.toml declares: portal secrets (all alternatives) and [env].

    ``keys`` limits the portal secrets to those portals; [env] is always included.
    """
    names, optional = [], []
    path = customers.customer_dir(customer) / "portals.toml"
    import tomllib

    data = tomllib.loads(path.read_text(encoding="utf-8"))
    for key, entry in data.items():
        if key == customers.RESERVED_TABLE or (keys is not None and key not in keys):
            continue
        for group in entry.get("secrets", []):
            names += [name.strip() for name in group.split("|")]
    required, extra_optional = customers.extra_variables(customer)
    names += required
    optional += extra_optional
    return names, optional


def missing_groups(setups, required, selected):
    """Required secret groups (``A|B``, as lists of names) with no member selected."""
    groups = [group for setup in setups.values() for group in setup.secrets] + list(required)
    names = [[name.strip() for name in group.split("|")] for group in groups]
    return [group for group in names if not any(name in selected for name in group)]


def customer_values(customer):
    """Variable values for a customer: its own .env first, then the shared repository .env."""
    shared = Path(os.environ.get("JURISYNC_ENV_FILE") or customers.REPO_ROOT / ".env")
    own = read_env(customers.customer_env_path(customer))
    fallback = read_env(shared)
    return own, fallback


def export_main(argv, *, ask_secret, say):
    parser = argparse.ArgumentParser(prog="jurisync-setup export", description="Gera o pacote criptografado.")
    parser.add_argument("--customer", required=True)
    parser.add_argument("--out", required=True, help="arquivo do pacote (.jurisync)")
    parser.add_argument("--ask-passphrase", action="store_true", help="digitar a senha em vez de gerar uma")
    parser.add_argument(
        "--allow-shared",
        action="append",
        default=[],
        metavar="NOME",
        help="aceitar esta variável do .env da raiz quando o .env do cliente não a tem (repita para cada uma)",
    )
    parser.add_argument(
        "--portals",
        help="somente estes portais do portals.toml, separados por vírgula (padrão: todos os do cliente)",
    )
    args = parser.parse_args(argv)

    setups = customers.load_setup(args.customer)
    if args.portals:
        keys = [key.strip() for key in args.portals.split(",") if key.strip()]
        unknown = [key for key in keys if key not in setups]
        if unknown:
            raise SystemExit(f"--portals: {', '.join(unknown)} não está no portals.toml de {args.customer}")
        setups = {key: setups[key] for key in keys}
    own, fallback = customer_values(args.customer)
    names, optional = declared_names(args.customer, list(setups) if args.portals else None)
    prefixes = portal_prefixes(list(setups))
    wanted = set(names) | set(optional)
    # The customer's own .env: every declared name plus anything under its portals' prefixes.
    selected = {name: value for name, value in own.items() if value and (name in wanted or name.startswith(prefixes))}
    # The shared .env serves several customers (it holds Jean's TRF1 password login under PJE_1G_*): a declared
    # name comes from it only when the operator names it, so one office's login never travels in another's bundle.
    in_shared = {name for name, value in fallback.items() if value and name in wanted and name not in selected}
    unknown = sorted(set(args.allow_shared) - in_shared)
    if unknown:
        raise SystemExit(
            f"--allow-shared: {', '.join(unknown)} não está no .env da raiz ou o cliente já a tem/não a declara"
        )
    from_shared = sorted(in_shared & set(args.allow_shared))
    selected.update({name: fallback[name] for name in from_shared})
    refused = sorted(in_shared - set(from_shared))
    fixed = customers.fixed_variables(args.customer)
    selected.update(fixed)
    lacking = missing_groups(setups, customers.extra_variables(args.customer)[0], selected)
    needed = sorted(name for name in refused if any(name in group for group in lacking))
    if needed:
        raise SystemExit(
            f"{args.customer}: {', '.join(needed)} só existe no .env da raiz, que serve vários clientes. Mova para "
            f"customers/{args.customer}/.env ou, se for mesmo deste cliente, repita o comando com "
            + " ".join(f"--allow-shared {name}" for name in needed)
        )
    if not selected:
        raise SystemExit(f"Nenhuma credencial de {args.customer} encontrada no .env do cliente")

    # File variables (A1 certificate): embed the bytes; authenticator QR images become their text export.
    files, file_vars = {}, {}
    for name in list(selected):
        path = Path(selected[name].strip().strip("\"'")).expanduser()
        if is_file_variable(name) and path.is_file():
            files[path.name] = path.read_bytes()
            file_vars[name] = path.name
            selected[name] = path.name
        elif name.endswith("_2FA_QR"):
            data_name = name.removesuffix("_2FA_QR") + "_2FA_DATA"
            if data_name not in selected and path.is_file():
                selected[data_name] = qr_text(path)  # the customer's own text export wins over an image
            del selected[name]
    checks = []
    for key in setups:
        system = portals.get(key).system
        if system in ("eproc", "pje"):
            checks.append(f"{system}:{key}")
    if any(name.startswith("ADVBOX_") for name in selected):
        checks.append("advbox")
    if any(name.startswith("JURIDIQ_") for name in selected):
        checks.append("juridiq")
    servers = {check.split(":")[0] for check in checks} | {"djen"}
    servers = sorted(servers)

    if args.ask_passphrase:
        passphrase = ask_secret("Senha do pacote: ")
        if passphrase != ask_secret("Repita a senha: "):
            raise SystemExit("As senhas não conferem")
    else:
        passphrase = credentials_bundle.generate_passphrase()
    text = credentials_bundle.seal(
        selected, passphrase, customer=args.customer, files=files, file_vars=file_vars, checks=checks, servers=servers
    )
    out = Path(args.out)
    descriptor = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(text + "\n")
    say(f"Pacote criado: {out}")
    say(f"  variáveis ({len(selected)}): {credentials_bundle.describe(selected)}")
    if files:
        say(f"  arquivos: {', '.join(sorted(files))}")
    if from_shared:
        say(f"  vindas do .env da raiz (--allow-shared): {', '.join(from_shared)}")
    if refused:
        say(f"  ignoradas do .env da raiz (use --allow-shared NOME se forem deste cliente): {', '.join(refused)}")
    if fixed:
        say(f"  valores fixos do cliente (portals.toml): {', '.join(f'{k}={v}' for k, v in sorted(fixed.items()))}")
    say(f"  verificações: {', '.join(checks) or '-'} | servidores: {', '.join(servers)}")
    if not args.ask_passphrase:
        say(f"Senha do pacote (envie por OUTRO canal, aparece só aqui): {passphrase}")
    return 0


def import_main(argv, *, ask_secret, say, stdin=None):
    parser = argparse.ArgumentParser(prog="jurisync-setup import", description="Instala as credenciais de um pacote.")
    parser.add_argument("bundle", help="arquivo .jurisync")
    parser.add_argument("--passphrase-stdin", action="store_true", help="ler a senha da entrada padrão")
    parser.add_argument("--terminal", action="store_true", help="pedir a senha no terminal em vez da página local")
    add_finish_options(parser)
    args = parser.parse_args(argv)

    text = Path(args.bundle).read_text(encoding="utf-8")
    if args.passphrase_stdin:
        passphrase = (stdin or sys.stdin).readline().strip()
    elif args.terminal:
        passphrase = ask_secret("Senha do pacote: ")
    else:
        passphrase = credentials_bundle.passphrase_from_page(say)
    opened = credentials_bundle.unseal(text, passphrase)
    env_file = target_env_file(args)
    directory = env_file.parent / "files"
    values = dict(opened["env"])
    for variable, name in opened["file_vars"].items():
        if not is_file_variable(variable) or name not in opened["files"]:
            raise ValueError(f"O pacote aponta {variable!r} para um arquivo que não é um certificado dele")
        values[variable] = str(directory / Path(name).name)  # never trust a path from the bundle
    values = accepted_values(values, opened["customer"], say)
    wanted_files = {Path(name).name for variable, name in opened["file_vars"].items() if variable in values}
    if wanted_files:
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        for name, raw in opened["files"].items():
            if Path(name).name not in wanted_files:
                continue
            target = directory / Path(name).name
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                output.write(raw)
    write_env(env_file, values)
    os.environ.update(values)
    say(f"Credenciais instaladas em {env_file} (somente o seu usuário lê esse arquivo).")
    checks = opened["checks"] or [f"eproc:{key}" for key in portals.keys("eproc") if portals.eproc(key).env("USERNAME")]
    return 1 if verify_and_register(checks, args, say, servers=set(opened["servers"]) or None) else 0


def main(argv=None, *, ask=input, ask_secret=getpass.getpass, say=print):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if argv[:1] == ["export"]:
            return export_main(argv[1:], ask_secret=ask_secret, say=say)
        if argv[:1] == ["import"]:
            return import_main(argv[1:], ask_secret=ask_secret, say=say)
        if argv[:1] == ["skills"]:
            from jurisync.install.skills_sync import skills_main

            return skills_main(argv[1:], say=say)
        return setup_main(argv, ask=ask, ask_secret=ask_secret, say=say)
    except (ValueError, TimeoutError, OSError) as error:
        say(f"Erro: {error}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
