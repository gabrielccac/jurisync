"""Guided credential setup: ask once for each portal's login, store it where every MCP client finds it,
check the logins, and register the servers with Claude.

    jurisync-setup --portals tjrs-1g,trf4-1g-rs,trf4-1g-sc --qr authenticator-qr.png
    jurisync-setup --customer elisandra          # portals taken from customers/elisandra/portals.toml
    jurisync-setup export --customer elisandra --out elisandra.jurisync   # operator: encrypted bundle
    jurisync-setup import elisandra.jurisync                              # customer machine: passphrase on a local page

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

SERVERS = {"eproc": "jurisync-eproc", "djen": "jurisync-djen"}


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


def describe(accounts):
    return [f"{n}: {a['issuer'] or '-'} | {a['name'] or '-'}" for n, a in enumerate(accounts, 1)]


def collect(keys, current, *, qr, ask, ask_secret, say):
    """Return the variables to store for these portals, asking only for what is missing."""
    updates, accounts, chosen = {}, None, None
    for key in keys:
        portal = portals.eproc(key)
        say(f"\n== {key} ({portal.host}) ==")

        def have(name, portal=portal):
            return current.get(portal.env_name(name)) or portal.env(name)

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


def verify_and_register(keys, args, say):
    """Test each portal's login, then register the servers. Returns the number of failed logins."""
    failures = 0
    if not args.no_login:
        say("\nTestando o login de cada portal...")
        previous_secret = None
        for key in keys:
            secret = portals.eproc(key).env("2FA_DATA")
            if previous_secret and previous_secret == secret:
                wait_for_fresh_code(secret, say)
            ok, message = check_login(key)
            say(f"  {key}: {'pronto' if ok else 'FALHOU - ' + message}")
            failures += 0 if ok else 1
            previous_secret = secret
    if args.register != "none":
        names = server_commands(set(args.servers.split(",")))
        if args.register in ("auto", "claude-code"):
            register_claude_code(names, say)
        if args.register in ("auto", "desktop"):
            register_desktop(names, say)
    return failures


def add_finish_options(parser):
    parser.add_argument("--env-file", help=f"arquivo de credenciais (padrão: {USER_ENV_FILE})")
    parser.add_argument("--no-login", action="store_true", help="não testa o login")
    parser.add_argument("--register", choices=("auto", "claude-code", "desktop", "none"), default="auto")
    parser.add_argument("--servers", default="eproc,djen", help="servidores a registrar")


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
    return 1 if verify_and_register(keys, args, say) else 0


def export_main(argv, *, ask_secret, say):
    parser = argparse.ArgumentParser(prog="jurisync-setup export", description="Gera o pacote criptografado.")
    parser.add_argument("--customer", required=True)
    parser.add_argument("--out", required=True, help="arquivo do pacote (.jurisync)")
    parser.add_argument("--ask-passphrase", action="store_true", help="digitar a senha em vez de gerar uma")
    args = parser.parse_args(argv)

    env_path = customers.customer_env_path(args.customer)
    values = read_env(env_path)
    prefixes = []
    for key in customers.load_setup(args.customer):
        portal = portals.get(key)
        prefixes += [f"{prefix}_" for prefix in (portal.env_prefix, *portal.legacy_env_prefixes) if prefix]
    selected = {name: value for name, value in values.items() if value and name.startswith(tuple(prefixes))}
    if not selected:
        raise SystemExit(f"Nenhuma credencial dos portais de {args.customer} encontrada em {env_path}")
    if args.ask_passphrase:
        passphrase = ask_secret("Senha do pacote: ")
        if passphrase != ask_secret("Repita a senha: "):
            raise SystemExit("As senhas não conferem")
    else:
        passphrase = credentials_bundle.generate_passphrase()
    text = credentials_bundle.seal(selected, passphrase, customer=args.customer)
    out = Path(args.out)
    descriptor = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(text + "\n")
    say(f"Pacote criado: {out} ({len(selected)} variáveis: {credentials_bundle.describe(selected)})")
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
    write_env(env_file, opened["env"])
    os.environ.update({k: v for k, v in opened["env"].items() if k not in os.environ})
    say(f"Credenciais instaladas em {env_file} (somente o seu usuário lê esse arquivo).")
    keys = [
        key
        for key in portals.keys("eproc")
        if any(
            name.startswith(f"{prefix}_")
            for name in opened["env"]
            for prefix in (portals.eproc(key).env_prefix, *portals.eproc(key).legacy_env_prefixes)
        )
    ]
    return 1 if verify_and_register(keys, args, say) else 0


def main(argv=None, *, ask=input, ask_secret=getpass.getpass, say=print):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if argv[:1] == ["export"]:
            return export_main(argv[1:], ask_secret=ask_secret, say=say)
        if argv[:1] == ["import"]:
            return import_main(argv[1:], ask_secret=ask_secret, say=say)
        return setup_main(argv, ask=ask, ask_secret=ask_secret, say=say)
    except (ValueError, TimeoutError, OSError) as error:
        say(f"Erro: {error}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
