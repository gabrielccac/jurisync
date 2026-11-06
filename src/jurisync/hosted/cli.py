"""jurisync-hosted: run the hosted server and manage its offices, users and vault.

    jurisync-hosted init                                         # new data dir; prints a vault key to keep safe
    jurisync-hosted tenant add escritorio-teste --name "Escritório Teste" --customer victor
    jurisync-hosted secrets import-bundle escritorio-teste victor.jurisync --portal trf5-1g --check
    jurisync-hosted secrets list escritorio-teste                # names only, never values
    jurisync-hosted user add pessoa@escritorio.com.br --tenant escritorio-teste
    jurisync-hosted serve --base-url https://mcp.exemplo.com.br --port 8080

State lives in JURISYNC_HOSTED_DIR (default ~/.local/share/jurisync/hosted): ``store.sqlite`` and ``vault/``. The vault
key is read from JURISYNC_VAULT_KEY and is never written there. Portal credentials come in only as an encrypted bundle
(``jurisync-setup export``) or, later, from the office's own page; none is typed here or printed.
"""

import argparse
import getpass
import io
import sys
from pathlib import Path

from jurisync import customers, portals
from jurisync.hosted.store import Store
from jurisync.hosted.vault import Vault, generate_key, load_key, require_tenant_id
from jurisync.install import credentials_bundle
from jurisync.paths import user_data

# Variables whose value is a file on the importing machine; their bytes go into the vault.
FILE_VARIABLES = {"PJE_A1_P12"}


def data_dir():
    return user_data("hosted", "JURISYNC_HOSTED_DIR")


def open_store():
    return Store(data_dir() / "store.sqlite")


def open_vault():
    return Vault(data_dir() / "vault", load_key())


def qr_text(raw):
    from jurisync.install.setup import qr_text as decode

    return decode(io.BytesIO(raw))


# Services outside the portal registry and the names their connectors read.
SERVICE_NAMES = {
    "advbox": {"ADVBOX_EMAIL", "ADVBOX_PASSWORD", "ADVBOX_DEVICE_ID", "ADVBOX_UPLOAD_FOLDER"},
    "juridiq": {"JURIDIQ_EMAIL", "JURIDIQ_PASSWORD"},
}


def wanted_names(portal_keys, customer):
    """The secret names the given portals read, from the customer's portals.toml, plus pinned settings."""
    names = set()
    for key in [key for key in portal_keys if key in SERVICE_NAMES]:
        names |= SERVICE_NAMES[key]
    portal_keys = [key for key in portal_keys if key not in SERVICE_NAMES]
    if not customer:
        # No customer profile: every variable the portal registry gives this portal, plus the office's A1.
        from jurisync.hosted import access

        for key in portal_keys:
            names |= access.portal_names(portals.get(key)) | {access.A1_FILE, access.A1_PIN}
        return names
    setups = customers.load_setup(customer)
    for key in portal_keys:
        setup = setups.get(key)
        if setup is None:
            raise ValueError(f"{key} is not declared in customers/{customer}/portals.toml")
        for group in setup.secrets:
            names.update(name.strip() for name in group.split("|"))
    return names


def normalize(values, files, wanted=None):
    """Turn a local env (values + files) into vault content: QR images become TOTP text, file paths become bytes."""
    out_values, out_files = {}, {}
    for name, value in values.items():
        if wanted is not None and name not in wanted and not name.endswith("_CERT"):
            continue
        if name.endswith("_2FA_QR"):
            raw = files.get(name)
            if raw is None:
                raw = Path(value).expanduser().read_bytes()
            out_values[name.removesuffix("_QR") + "_DATA"] = qr_text(raw)
        elif name in FILE_VARIABLES:
            out_files[name] = files[name] if name in files else Path(value.strip('"')).expanduser().read_bytes()
        else:
            out_values[name] = value
    return out_values, out_files


def missing_groups(portal_keys, customer, values, files):
    """Per portal, the secret groups (``A|B``) with no member stored; QR names count as their _DATA form."""
    if not portal_keys or not customer:
        return {}
    present = set(values) | set(files)
    setups = customers.load_setup(customer)
    missing = {}
    for key in [key for key in portal_keys if key not in SERVICE_NAMES]:
        for group in setups[key].secrets:
            names = [name.strip() for name in group.split("|")]
            if not any(name in present or name.removesuffix("_QR") + "_DATA" in present for name in names):
                missing.setdefault(key, []).append(group)
    return missing


def import_bundle(args, say):
    store, vault = open_store(), open_vault()
    tenant = require_existing(store, args.tenant)
    passphrase = getpass.getpass("Senha do pacote: ")
    opened = credentials_bundle.unseal(Path(args.bundle).read_text(encoding="utf-8"), passphrase)
    values = dict(opened["env"])
    files = {}
    for variable, name in opened["file_vars"].items():
        values[variable] = name
        files[variable] = opened["files"][name]
    wanted = wanted_names(args.portal, tenant["customer"]) if args.portal else None
    merge(
        vault,
        tenant["id"],
        *normalize(values, files, wanted),
        say,
        portal_keys=args.portal,
        customer=tenant["customer"],
        check=args.check,
        store=store,
    )


def merge(vault, tenant, values, files, say, *, portal_keys=None, customer="", check=False, store=None):
    current_values, current_files = vault.get(tenant)
    current_values.update(values)
    current_files.update(files)
    if check:
        from jurisync.hosted import access

        targets = [key for key in portal_keys or () if key in access.check_targets()]
        if not targets:
            raise SystemExit("--check precisa de --portal com ao menos um portal do PJe ou do Eproc, advbox ou juridiq")
        failed = [key for key in targets if not run_check(store, tenant, key, current_values, current_files, say)]
        if failed:
            raise SystemExit(f"Nada foi gravado: o login falhou em {', '.join(failed)}")
    vault.put(tenant, current_values, current_files)
    if store is not None:
        store.log(tenant, "operador", "credenciais importadas", ", ".join(sorted(values) + sorted(files)))
    say(f"{tenant}: {len(values)} valor(es) e {len(files)} arquivo(s) gravados no cofre.")
    say("Nomes no cofre: " + ", ".join(sorted(current_values) + sorted(current_files)))
    for portal, groups in missing_groups(portal_keys, customer, current_values, current_files).items():
        say(f"Atenção: {portal} ainda precisa de {', '.join(groups)}")


def run_check(store, tenant, portal, values, files, say):
    """Log in to one portal or service with these credentials; record and print the result."""
    from jurisync.core.mcp import sanitize_diagnostics
    from jurisync.hosted import access

    say(f"Testando o login em {portal}...")
    try:
        with access.tenant_scope(tenant, values, files):
            access.check(portal)
    except Exception as error:  # noqa: BLE001 - any portal failure is reported, never raised to the operator
        message = sanitize_diagnostics(error) or type(error).__name__
        say(f"{portal}: o login falhou ({message})")
        if store is not None:
            store.save_check(tenant, portal, False, message)
            store.log(tenant, "operador", "teste de login falhou", f"{portal}: {message}")
        return False
    say(f"{portal}: login OK")
    if store is not None:
        store.save_check(tenant, portal, True, "login OK")
        store.log(tenant, "operador", "teste de login OK", portal)
    return True


def forget(tenant, values, files, portal_keys):
    from jurisync.hosted import access

    with access.tenant_scope(tenant, values, files):
        for key in portal_keys:
            access.forget_sessions(key)


def certificate_remove(args, say):
    from jurisync.hosted import access

    store, vault = open_store(), open_vault()
    tenant = require_existing(store, args.tenant)["id"]
    values, files = vault.get(tenant)
    forget(tenant, values, files, portals.keys("pje"))
    values.pop(access.A1_PIN, None)
    files.pop(access.A1_FILE, None)
    vault.put(tenant, values, files)
    store.log(tenant, "operador", "certificado A1 removido")
    say(f"Certificado removido do cofre de {tenant}.")


def portal_list(args, say):
    from jurisync.hosted import access

    store, vault = open_store(), open_vault()
    office = require_existing(store, args.tenant)
    tenant = office["id"]
    values, files = vault.get(tenant)
    if office["customer"]:
        # The server applies the profile's pinned values on every call; show the same state.
        for key, value in customers.fixed_variables(office["customer"]).items():
            values.setdefault(key, value)
    info = access.certificate_info(values, files)
    say(f"Certificado A1: {f'{info.holder}, válido até {info.expires}' if info else 'não cadastrado'}")
    checks = store.checks(tenant)
    methods = {access.PASSWORD: "login com senha", access.CERTIFICATE: "certificado A1", None: "-"}
    for config in access.pje_portals():
        state = access.portal_state(config, values, files)
        check = checks.get(config.key)
        result = "não testado" if check is None else ("OK" if check["ok"] else f"falhou: {check['message']}")
        say(
            f"{config.key:<10} {methods[state.method]:<16} 2FA={'sim' if state.has_authenticator else 'não':<4} {result}"
        )


def portal_check(args, say):
    store, vault = open_store(), open_vault()
    tenant = require_existing(store, args.tenant)["id"]
    values, files = vault.get(tenant)
    if not run_check(store, tenant, args.portal, values, files, say):
        raise SystemExit(1)


def portal_remove(args, say):
    from jurisync.hosted import access

    store, vault = open_store(), open_vault()
    tenant = require_existing(store, args.tenant)["id"]
    config = portals.pje(args.portal)
    values, files = vault.get(tenant)
    forget(tenant, values, files, [config.key])
    vault.put(tenant, access.without_portal(values, config), files)
    store.forget_check(tenant, config.key)
    store.log(tenant, "operador", "acesso ao portal removido", config.key)
    say(f"{config.key}: acesso removido do cofre de {tenant}.")


def history(args, say):
    store = open_store()
    tenant = require_existing(store, args.tenant)["id"]
    import datetime

    for row in store.history(tenant, args.limit):
        moment = datetime.datetime.fromtimestamp(row["at"]).strftime("%d/%m/%Y %H:%M")
        say(f"{moment}  {row['actor']:<10} {row['action']}" + (f": {row['detail']}" if row["detail"] else ""))


def require_existing(store, tenant_id):
    tenant = store.tenant(tenant_id)
    if tenant is None:
        raise SystemExit(f"Escritório {tenant_id} não existe; crie com: jurisync-hosted tenant add {tenant_id}")
    return tenant


def serve(args):
    import uvicorn

    from jurisync.core.http import enable_request_logging
    from jurisync.hosted.app import build_app

    store, vault = open_store(), open_vault()
    enable_request_logging()
    if args.open_tenant:
        print(
            f"ATENÇÃO: modo de teste sem login. Quem tiver o endereço usa as credenciais de {args.open_tenant}.",
            file=sys.stderr,
        )
    app, closers = build_app(args.base_url, store, vault, args.connectors, open_tenant=args.open_tenant)
    try:
        uvicorn.run(app, host=args.host, port=args.port, proxy_headers=True, forwarded_allow_ips="127.0.0.1")
    finally:
        for close in closers:
            close()


def main(argv=None, *, say=print, ask_secret=getpass.getpass):
    parser = argparse.ArgumentParser(prog="jurisync-hosted", description="Servidor remoto do Jurisync.")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("init", help="cria a pasta de dados e mostra uma chave nova para o cofre")

    tenant = commands.add_parser("tenant", help="escritórios").add_subparsers(dest="action", required=True)
    add = tenant.add_parser("add")
    add.add_argument("tenant")
    add.add_argument("--name", required=True)
    add.add_argument("--customer", default="", help="perfil em customers/ (portals.toml, settings.toml)")
    add.add_argument("--connectors", nargs="+", default=["pje"])
    tenant.add_parser("list")
    remove = tenant.add_parser("remove")
    remove.add_argument("tenant")

    secrets = commands.add_parser("secrets", help="credenciais no cofre").add_subparsers(dest="action", required=True)
    from_bundle = secrets.add_parser("import-bundle", help="lê um pacote .jurisync (jurisync-setup export)")
    from_bundle.add_argument("tenant")
    from_bundle.add_argument("bundle")
    from_bundle.add_argument("--portal", nargs="*", choices=sorted(portals.PORTALS) + ["advbox", "juridiq"])
    from_bundle.add_argument("--check", action="store_true", help="só grava se o login nos portais do PJe entrar")
    listing = secrets.add_parser("list")
    listing.add_argument("tenant")

    cert = commands.add_parser("certificate", help="certificado A1 do escritório").add_subparsers(
        dest="action", required=True
    )
    cert.add_parser("remove").add_argument("tenant")

    portal = commands.add_parser("portal", help="acesso a cada portal do PJe").add_subparsers(
        dest="action", required=True
    )
    portal.add_parser("list").add_argument("tenant")
    from jurisync.hosted import access as _access

    portal_check_parser = portal.add_parser("check", help="testa o login de um portal, advbox ou juridiq")
    portal_check_parser.add_argument("tenant")
    portal_check_parser.add_argument("portal", choices=_access.check_targets())
    portal_remove_parser = portal.add_parser("remove")
    portal_remove_parser.add_argument("tenant")
    portal_remove_parser.add_argument("portal", choices=portals.keys("pje"))

    log = commands.add_parser("history", help="histórico de alterações do escritório")
    log.add_argument("tenant")
    log.add_argument("--limit", type=int, default=30)

    user = commands.add_parser("user", help="usuários que entram pelo OAuth").add_subparsers(
        dest="action", required=True
    )
    user_add = user.add_parser("add")
    user_add.add_argument("email")
    user_add.add_argument("--tenant", required=True)
    user.add_parser("password", help="troca a senha de um usuário").add_argument("email")
    user_list = user.add_parser("list")
    user_list.add_argument("--tenant")
    user_remove = user.add_parser("remove")
    user_remove.add_argument("email")

    run = commands.add_parser("serve")
    run.add_argument("--base-url", required=True, help="endereço público, ex. https://mcp.exemplo.com.br")
    run.add_argument("--host", default="127.0.0.1")
    run.add_argument("--port", type=int, default=8080)
    run.add_argument("--connectors", nargs="+", default=["pje"])
    run.add_argument(
        "--open-tenant", help="SÓ PARA TESTE: dispensa o login e atende como este escritório quem tiver o endereço"
    )

    args = parser.parse_args(argv)
    if args.command == "init":
        data_dir().mkdir(mode=0o700, parents=True, exist_ok=True)
        open_store()
        say(f"Pasta de dados: {data_dir()}")
        say("Guarde esta chave fora do servidor e exporte-a como JURISYNC_VAULT_KEY antes de usar o cofre:")
        say(generate_key())
    elif args.command == "tenant" and args.action == "add":
        if args.customer and not customers.customer_dir(args.customer).is_dir():
            raise SystemExit(f"Perfil customers/{args.customer} não existe")
        open_store().add_tenant(
            require_tenant_id(args.tenant), args.name, customer=args.customer, connectors=args.connectors
        )
        say(f"Escritório {args.tenant} criado ({', '.join(args.connectors)}).")
    elif args.command == "tenant" and args.action == "list":
        for row in open_store().tenants():
            say(f"{row['id']}\t{row['name']}\tperfil={row['customer'] or '-'}\t{','.join(row['connectors'])}")
    elif args.command == "tenant" and args.action == "remove":
        open_store().remove_tenant(args.tenant)
        open_vault().delete(args.tenant)
        say(f"Escritório {args.tenant}, usuários, tokens e cofre removidos.")
    elif args.command == "secrets" and args.action == "import-bundle":
        import_bundle(args, say)
    elif args.command == "secrets" and args.action == "list":
        values, files = open_vault().names(args.tenant)
        say("Valores: " + (", ".join(values) or "-"))
        say("Arquivos: " + (", ".join(files) or "-"))
    elif args.command == "user" and args.action == "add":
        password = ask_secret("Senha do usuário (mín. 12 caracteres): ")
        if password != ask_secret("Repita a senha: "):
            raise SystemExit("As senhas não conferem")
        open_store().add_user(args.email, args.tenant, password)
        say(f"Usuário {args.email.strip().lower()} pode entrar em {args.tenant}.")
    elif args.command == "user" and args.action == "password":
        password = ask_secret("Nova senha (mín. 12 caracteres): ")
        if password != ask_secret("Repita a senha: "):
            raise SystemExit("As senhas não conferem")
        open_store().set_password(args.email, password)
        say(f"Senha de {args.email.strip().lower()} trocada; sessões e tokens antigos foram encerrados.")
    elif args.command == "user" and args.action == "list":
        for row in open_store().users(args.tenant):
            say(f"{row['email']}\t{row['tenant']}")
    elif args.command == "user" and args.action == "remove":
        open_store().remove_user(args.email)
        say(f"Usuário {args.email} removido e tokens revogados.")
    elif args.command == "certificate" and args.action == "remove":
        certificate_remove(args, say)
    elif args.command == "portal" and args.action == "list":
        portal_list(args, say)
    elif args.command == "portal" and args.action == "check":
        portal_check(args, say)
    elif args.command == "portal" and args.action == "remove":
        portal_remove(args, say)
    elif args.command == "history":
        history(args, say)
    elif args.command == "serve":
        serve(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
