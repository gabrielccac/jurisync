"""jurisync-hosted: run the hosted server and manage its offices, users and vault.

    jurisync-hosted init                                         # new data dir; prints a vault key to keep safe
    jurisync-hosted tenant add escritorio-teste --name "Escritório Teste" --customer victor
    jurisync-hosted secrets import-bundle escritorio-teste victor.jurisync --portal trf5-1g --check
    jurisync-hosted secrets list escritorio-teste                # names only, never values
    jurisync-hosted user add pessoa@escritorio.com.br --tenant escritorio-teste
    jurisync-hosted serve --base-url https://mcp.exemplo.com.br --port 8080
    jurisync-hosted calls escritorio-teste --limit 50           # tool calls: who, which tool, result; no arguments
    jurisync-hosted provision export --out ~/rollout --passphrase-file ~/.config/jurisync/rollout.passphrase
    jurisync-hosted provision apply ~/rollout --passphrase-file /root/rollout.passphrase   # on the server

State lives in JURISYNC_HOSTED_DIR (default ~/.local/share/jurisync/hosted): ``store.sqlite`` and ``vault/``. The vault
key is read from JURISYNC_VAULT_KEY and is never written there. Portal credentials come in only as an encrypted bundle
(``jurisync-setup export``) or, later, from the office's own page; none is typed here or printed.
"""

import argparse
import getpass
import io
import os
import sys
from pathlib import Path

from jurisync import customers, portals, registry
from jurisync.hosted.store import Store
from jurisync.hosted.vault import Vault, generate_key, load_key, require_tenant_id
from jurisync.install import credentials_bundle
from jurisync.install.setup import is_file_variable  # a file on the importing machine; its bytes go into the vault
from jurisync.paths import user_data


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
    name: {*registry.get(name).login, *registry.get(name).settings, registry.get(name).write_grant} - {""}
    for name in registry.SERVICES
}
# Never stored for an office: PJe draft writes stay off on the server, and an A3 token (its PIN, the switch that sends
# a portal to it) only works on the machine where it is plugged in.
NEVER_HOSTED = ("PJE_ALLOW_DRAFT_WRITES",)
NEVER_HOSTED_SUFFIXES = ("_A3_PIN", "_AUTH")


def never_hosted(name):
    return name in NEVER_HOSTED or name.endswith(NEVER_HOSTED_SUFFIXES)


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
    from jurisync.hosted import access

    setups = customers.load_setup(customer)
    for key in portal_keys:
        setup = setups.get(key)
        if setup is None:
            raise ValueError(f"{key} is not declared in customers/{customer}/portals.toml")
        for group in setup.secrets:
            names.update(name.strip() for name in group.split("|"))
        # Fields a profile may leave undeclared but the login reads (wesley's TRF1 takes PJE_1G_2FA_DATA).
        names |= access.portal_names(portals.get(key))
    if portal_keys:
        # The office-wide names of [env] (the A1 certificate, a CPF): a service's own names only with that service.
        required, optional = customers.extra_variables(customer)
        names |= {
            name
            for name in [*required, *optional]
            if not any(name in service_names for service_names in SERVICE_NAMES.values())
        }
    return names


# Per-portal settings that go with the secrets: login method, the TOTP profile chosen at setup, the cache identity,
# the eproc user profile (OAB registration) picked after login.
SETTING_SUFFIXES = ("_CERT", "_OTP_PROFILE_MATCH", "_OTP_PROFILE_INDEX", "_SESSION_ACCOUNT", "_USER_PROFILE")


def normalize(values, files, wanted=None):
    """Turn a local env (values + files) into vault content: QR images become TOTP text, file paths become bytes."""
    out_values, out_files = {}, {}
    for name, value in values.items():
        if never_hosted(name):
            continue
        if wanted is not None and name not in wanted and not name.endswith(SETTING_SUFFIXES):
            continue
        if name.endswith("_2FA_QR"):
            raw = files.get(name)
            if raw is None:
                raw = Path(value).expanduser().read_bytes()
            out_values[name.removesuffix("_QR") + "_DATA"] = qr_text(raw)
        elif is_file_variable(name):
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
    if tenant["mode"] == "local":
        raise SystemExit(f"{tenant['id']} usa a instalação local ({tenant['reason']}): nada vai para o cofre")
    if args.passphrase_file:
        passphrase = Path(args.passphrase_file).expanduser().read_text(encoding="utf-8").strip()
    else:
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
            raise SystemExit(
                f"--check precisa de --portal com ao menos um portal ou serviço ({', '.join(SERVICE_NAMES)})"
            )
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
    from jurisync.core.session_pool import EXPLANATIONS, login_reason
    from jurisync.hosted import access

    say(f"Testando o login em {portal}...")
    try:
        with access.tenant_scope(tenant, values, files):
            access.check(portal)
    except Exception as error:  # noqa: BLE001 - any portal failure is reported, never raised to the operator
        reason = login_reason(error)
        message = f"[{reason}] {sanitize_diagnostics(error) or type(error).__name__}"
        say(f"{portal}: o login falhou: {EXPLANATIONS[reason]} ({message})")
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
            access.forget(key)


def certificate_remove(args, say):
    from jurisync.hosted import access

    store, vault = open_store(), open_vault()
    tenant = require_existing(store, args.tenant)["id"]
    values, files = vault.get(tenant)
    # The A1 also logs in to Eproc, e-SAJ, Projudi, STJ: every portal's login and cookie cache goes.
    forget(tenant, values, files, access.served_portals())
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
    if office["mode"] == "local":
        say(f"{tenant}: só local ({office['reason'] or 'instalação local'}); nenhum portal no servidor")
        return
    values, files = vault.get(tenant)
    if office["customer"]:
        # The server applies the profile's pinned values on every call; show the same state.
        for key, value in customers.fixed_variables(office["customer"]).items():
            values.setdefault(key, value)
    info = access.certificate_info(values, files)
    say(f"Certificado A1: {f'{info.holder}, válido até {info.expires}' if info else 'não cadastrado'}")
    checks = store.checks(tenant)
    methods = {access.PASSWORD: "login com senha", access.CERTIFICATE: "certificado A1", None: "-"}

    def result(key):
        check = checks.get(key)
        return "não testado" if check is None else ("OK" if check["ok"] else f"falhou: {check['message']}")

    for key in office_portals(office, values, checks):
        config = portals.get(key)
        state = access.portal_state(config, values, files)
        authenticator = "sim" if state.has_authenticator else "não"
        say(
            f"{key:<16} {registry.label(config.system):<9} {methods[state.method]:<16} 2FA={authenticator:<4} "
            + result(key)
        )
    for service in registry.SERVICES:
        if all(values.get(name) for name in registry.get(service).login):
            say(f"{service:<16} {registry.label(service):<9} {methods[access.PASSWORD]:<16} {'':<9}{result(service)}")


def office_portals(office, values, checks=()):
    """The office's portals: its customer profile's, else those with a value in its vault or a login check."""
    from jurisync.hosted import access

    served = access.served_portals()
    if office["customer"] and (customers.customer_dir(office["customer"]) / "portals.toml").is_file():
        return [key for key in customers.load_setup(office["customer"]) if key in served]
    present = {name for name, value in values.items() if value}
    return [key for key in served if key in checks or access.portal_names(portals.get(key)) & present]


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
    config = portals.get(args.portal)
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


def calls(args, say):
    store = open_store()
    tenant = require_existing(store, args.tenant)["id"]
    import datetime

    for row in store.calls(tenant, args.limit):
        moment = datetime.datetime.fromtimestamp(row["at"]).strftime("%d/%m/%Y %H:%M:%S")
        result = "ok" if row["ok"] else f"erro {row['error']}"
        say(f"{moment}  {row['user'] or '-'}  {row['tool']}  {result}  {row['duration_ms']} ms")


def require_existing(store, tenant_id):
    tenant = store.tenant(tenant_id)
    if tenant is None:
        raise SystemExit(f"Escritório {tenant_id} não existe; crie com: jurisync-hosted tenant add {tenant_id}")
    return tenant


def missing_profiles(store):
    """Offices whose customer profile is not on this machine (JURISYNC_CUSTOMERS_DIR)."""
    return [
        row["id"]
        for row in store.tenants()
        if row["customer"]
        and row["mode"] == "hosted"
        and not (customers.customer_dir(row["customer"]) / "portals.toml").is_file()
    ]


def serve(args):
    import uvicorn

    from jurisync.core.http import enable_request_logging
    from jurisync.hosted.app import TENANT_READY, build_app

    store, vault = open_store(), open_vault()
    enable_request_logging()
    lacking = missing_profiles(store)
    if lacking:
        # The profile pins per-portal methods (A1 on a password portal) and the office's settings: without it the
        # server would try another login. Install it (provision apply, JURISYNC_CUSTOMERS_DIR) before serving.
        raise SystemExit(f"Perfil customers/<nome>/portals.toml ausente para: {', '.join(lacking)}")
    if args.open_tenant:
        print(
            f"ATENÇÃO: modo de teste sem login. Quem tiver o endereço usa as credenciais de {args.open_tenant}.",
            file=sys.stderr,
        )
    app, closers = build_app(args.base_url, store, vault, args.connectors or TENANT_READY, open_tenant=args.open_tenant)
    try:
        uvicorn.run(app, host=args.host, port=args.port, proxy_headers=True, forwarded_allow_ips="127.0.0.1")
    finally:
        for close in closers:
            close()


def main(argv=None, *, say=print, ask_secret=getpass.getpass):
    parser = argparse.ArgumentParser(prog="jurisync-hosted", description="Servidor remoto do Jurisync.")
    commands = parser.add_subparsers(dest="command", required=True)
    from jurisync.hosted import access as _access

    commands.add_parser("init", help="cria a pasta de dados e mostra uma chave nova para o cofre")

    tenant = commands.add_parser("tenant", help="escritórios").add_subparsers(dest="action", required=True)
    add = tenant.add_parser("add")
    add.add_argument("tenant")
    add.add_argument("--name", required=True)
    add.add_argument("--customer", default="", help="perfil em customers/ (portals.toml, settings.toml)")
    add.add_argument("--connectors", nargs="+", default=["pje"], choices=list(registry.CONNECTORS))
    add.add_argument("--local", metavar="MOTIVO", help="escritório só local (ex. A3): registrado, sem nada servido")
    tenant.add_parser("list")
    set_connectors = tenant.add_parser("connectors", help="troca os conectores do escritório")
    set_connectors.add_argument("tenant")
    set_connectors.add_argument("connectors", nargs="+", choices=list(registry.CONNECTORS))
    remove = tenant.add_parser("remove")
    remove.add_argument("tenant")

    secrets = commands.add_parser("secrets", help="credenciais no cofre").add_subparsers(dest="action", required=True)
    from_bundle = secrets.add_parser("import-bundle", help="lê um pacote .jurisync (jurisync-setup export)")
    from_bundle.add_argument("tenant")
    from_bundle.add_argument("bundle")
    from_bundle.add_argument("--portal", nargs="*", choices=_access.check_targets())
    from_bundle.add_argument("--check", action="store_true", help="só grava se o login nos portais e serviços entrar")
    from_bundle.add_argument("--passphrase-file", help="arquivo (0600) com a senha do pacote, em vez de digitá-la")
    listing = secrets.add_parser("list")
    listing.add_argument("tenant")

    cert = commands.add_parser("certificate", help="certificado A1 do escritório").add_subparsers(
        dest="action", required=True
    )
    cert.add_parser("remove").add_argument("tenant")

    portal = commands.add_parser("portal", help="acesso do escritório a cada portal").add_subparsers(
        dest="action", required=True
    )
    portal.add_parser("list").add_argument("tenant")
    portal_check_parser = portal.add_parser("check", help="testa o login de um portal, advbox ou juridiq")
    portal_check_parser.add_argument("tenant")
    portal_check_parser.add_argument("portal", choices=_access.check_targets())
    portal_remove_parser = portal.add_parser("remove")
    portal_remove_parser.add_argument("tenant")
    portal_remove_parser.add_argument("portal", choices=_access.served_portals())

    log = commands.add_parser("history", help="histórico de alterações do escritório")
    log.add_argument("tenant")
    log.add_argument("--limit", type=int, default=30)

    calls_parser = commands.add_parser("calls", help="chamadas de ferramentas do escritório, sem argumentos")
    calls_parser.add_argument("tenant")
    calls_parser.add_argument("--limit", type=int, default=30)

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

    provision = commands.add_parser("provision", help="escritórios a partir de customers/").add_subparsers(
        dest="action", required=True
    )
    export_parser = provision.add_parser("export", help="na nossa máquina: plano + um pacote cifrado por escritório")
    export_parser.add_argument("--out", required=True, help="pasta do plano e dos pacotes")
    export_parser.add_argument("--passphrase-file", required=True, help="senha dos pacotes (criada se não existir)")
    export_parser.add_argument("--customers-dir", help="pasta customers/ (padrão: JURISYNC_CUSTOMERS_DIR ou a do repo)")
    export_parser.add_argument("--customer", nargs="+", help="só estes clientes (padrão: todos com portals.toml)")
    apply_parser = provision.add_parser("apply", help="no servidor: cria escritórios, cofre e usuários do plano")
    apply_parser.add_argument("plan", help="pasta gerada pelo export")
    apply_parser.add_argument("--passphrase-file", required=True)
    apply_parser.add_argument("--office", nargs="+", help="só estes escritórios")
    apply_parser.add_argument("--user-passwords", help="arquivo 0600 das senhas iniciais (padrão: na pasta de dados)")

    plugin_parser = provision.add_parser("plugin", help="cópia de um plugin do escritório apontando para o servidor")
    plugin_parser.add_argument("source", help="pasta do plugin, ex. plugins/victor")
    plugin_parser.add_argument("--url", required=True, help="https://<host>/mcp")
    plugin_parser.add_argument("--out", required=True)

    run = commands.add_parser("serve")
    run.add_argument("--base-url", required=True, help="endereço público, ex. https://mcp.exemplo.com.br")
    run.add_argument("--host", default="127.0.0.1")
    run.add_argument("--port", type=int, default=8080)
    run.add_argument("--connectors", nargs="+", help="default: every connector ready for several offices")
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
            require_tenant_id(args.tenant),
            args.name,
            customer=args.customer,
            connectors=args.connectors,
            mode="local" if args.local else "hosted",
            reason=args.local or "",
        )
        served = f"só local: {args.local}" if args.local else ", ".join(args.connectors)
        say(f"Escritório {args.tenant} criado ({served}).")
    elif args.command == "tenant" and args.action == "connectors":
        store = open_store()
        if require_existing(store, args.tenant)["mode"] == "local":
            raise SystemExit(f"{args.tenant} usa a instalação local: não recebe conectores")
        store.update_tenant(args.tenant, connectors=args.connectors)
        say(f"Escritório {args.tenant}: {', '.join(args.connectors)}.")
    elif args.command == "tenant" and args.action == "list":
        for row in open_store().tenants():
            served = ",".join(row["connectors"]) if row["mode"] == "hosted" else f"só local: {row['reason'] or '-'}"
            say(f"{row['id']}\t{row['name']}\tperfil={row['customer'] or '-'}\t{served}")
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
    elif args.command == "calls":
        calls(args, say)
    elif args.command == "provision" and args.action == "export":
        from jurisync.hosted import provision as provisioning

        if args.customers_dir:
            os.environ["JURISYNC_CUSTOMERS_DIR"] = str(Path(args.customers_dir).expanduser().resolve())
        provisioning.export(args.out, args.passphrase_file, only=args.customer, say=say)
    elif args.command == "provision" and args.action == "apply":
        from jurisync.hosted import provision as provisioning

        provisioning.apply(
            args.plan, args.passphrase_file, only=args.office, user_passwords=args.user_passwords, say=say
        )
    elif args.command == "provision" and args.action == "plugin":
        from jurisync.hosted import provision as provisioning

        provisioning.remote_plugin(args.source, args.url, args.out, say=say)
    elif args.command == "serve":
        serve(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
