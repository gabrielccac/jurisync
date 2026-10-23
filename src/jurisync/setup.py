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
import getpass
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from jurisync import credentials_bundle, customers, portals
from jurisync.core.totp import parse_accounts, select_account
from jurisync.paths import USER_ENV_FILE

SERVERS = {
    "eproc": "jurisync-eproc",
    "pje": "jurisync-pje",
    "advbox": "jurisync-advbox",
    "juridiq": "jurisync-juridiq",
    "djen": "jurisync-djen",
}
FILE_SUFFIXES = ("_P12", "_PFX")  # variables that hold the path of a certificate file


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
        if len(accounts) > 1:
            answer = ask(f"Qual perfil é do {key}? [número{', Enter = ' + str(chosen) if chosen else ''}]: ").strip()
            chosen = int(answer) if answer else chosen
            if not chosen or not 1 <= chosen <= len(accounts):
                raise ValueError("Escolha um número de perfil da lista")
            updates[portal.env_name("OTP_PROFILE_INDEX")] = str(chosen - 1)
        else:
            select_account(accounts, label=key)
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


def declared_names(customer):
    """Every variable a customer's portals.toml declares: portal secrets (all alternatives) and [env]."""
    names, optional = [], []
    path = customers.customer_dir(customer) / "portals.toml"
    import tomllib

    data = tomllib.loads(path.read_text(encoding="utf-8"))
    for key, entry in data.items():
        if key == customers.RESERVED_TABLE:
            continue
        for group in entry.get("secrets", []):
            names += [name.strip() for name in group.split("|")]
    required, extra_optional = customers.extra_variables(customer)
    names += required
    optional += extra_optional
    return names, optional


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
    args = parser.parse_args(argv)

    setups = customers.load_setup(args.customer)
    own, fallback = customer_values(args.customer)
    names, optional = declared_names(args.customer)
    prefixes = []
    for key in setups:
        portal = portals.get(key)
        prefixes += [f"{prefix}_" for prefix in (portal.env_prefix, *portal.legacy_env_prefixes) if prefix]
    wanted = set(names) | set(optional)
    selected, from_shared = {}, []
    for source, is_shared in ((own, False), (fallback, True)):
        for name, value in source.items():
            if not value or name in selected:
                continue
            # The shared .env serves several customers (it holds Jean's TRF1 password login under PJE_1G_*):
            # only names the customer declares come from it; a portal prefix sweeps the customer's own .env alone.
            if name in wanted or (not is_shared and name.startswith(tuple(prefixes))):
                selected[name] = value
                if is_shared:
                    from_shared.append(name)
    fixed = customers.fixed_variables(args.customer)
    selected.update(fixed)
    if not selected:
        raise SystemExit(f"Nenhuma credencial de {args.customer} encontrada (.env do cliente ou .env da raiz)")

    # File variables (A1 certificate): embed the bytes; authenticator QR images become their text export.
    files, file_vars = {}, {}
    for name in list(selected):
        path = Path(selected[name].strip().strip("\"'")).expanduser()
        if name.endswith(FILE_SUFFIXES) and path.is_file():
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
        say(f"  vindas do .env da raiz (confira se são deste cliente): {', '.join(sorted(from_shared))}")
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
    values = dict(opened["env"])
    if opened["files"]:
        directory = env_file.parent / "files"
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        for name, raw in opened["files"].items():
            target = directory / Path(name).name  # never trust a path from the bundle
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                output.write(raw)
        for variable, name in opened["file_vars"].items():
            values[variable] = str(directory / Path(name).name)
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
            from jurisync.skills_sync import skills_main

            return skills_main(argv[1:], say=say)
        return setup_main(argv, ask=ask, ask_secret=ask_secret, say=say)
    except (ValueError, TimeoutError, OSError) as error:
        say(f"Erro: {error}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
