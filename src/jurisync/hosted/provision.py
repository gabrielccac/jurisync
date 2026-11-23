"""Provision offices on the hosted server from ``customers/<name>/``: one plan, one sealed bundle per office.

Two steps on two machines, so no secret is typed on the server and none is printed anywhere:

    # Our machine, where customers/<name>/.env, the A1 files and the QR images are:
    jurisync-hosted provision export --out ~/rollout --passphrase-file ~/.config/jurisync/rollout.passphrase
    # Copy ~/rollout to the server (scp) and the passphrase file through another channel, then on the server:
    jurisync-hosted provision apply ~/rollout --passphrase-file /root/rollout.passphrase

``export`` reads each customer's ``portals.toml`` and own ``.env`` (never the shared root ``.env``, which holds other
offices' accounts) and decides, per portal, whether the server can log in there:

* A3 portals (``*_A3_PIN``: a physical token) are skipped: the token has to be on the machine that logs in.
* Portals whose declared secrets are missing are skipped, so the server never tries a login that cannot work.
* Legacy names (``*_USUARIO``/``*_SENHA``/``*_EXPORT_DATA``, older prefixes) become the canonical ones in the bundle;
  the customer's ``.env`` is not changed.
* QR images become ``*_2FA_DATA`` text, the A1 file becomes bytes, the office's own
  ``ADVBOX_ALLOW_WRITES``/``JURIDIQ_ALLOW_WRITES`` travel with it. A3 switches and PJe draft writes never do, nor do the
  profile's pinned values (``[env].set``): the server reads them from the installed profile on every call.

It writes ``plan.json`` (names, portals, connectors, reasons; no values), one ``<office>.jurisync`` bundle per office
sealed with the rollout passphrase, and ``profiles/<customer>/`` (``portals.toml``, ``settings.toml``) for the server's
``JURISYNC_CUSTOMERS_DIR``: five customer folders are not in the repository. Users listed in an earlier ``plan.json``
are kept, so the plan can be edited and exported again.

``apply`` creates or updates each tenant, replaces its vault content with its bundle (without a login:
the first login of each portal is ``jurisync-hosted portal check``, run one at a time), installs the profiles and adds
the plan's users with generated passwords written to a 0600 file.
"""

import json
import os
import re
import secrets
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from jurisync import customers, portals, registry
from jurisync.install import credentials_bundle

PLAN = "plan.json"
PROFILE_FILES = ("portals.toml", "settings.toml")
# Systems the hosted server serves, in the order a tenant lists them.
SYSTEM_ORDER = tuple(registry.CONNECTORS)
SERVICES = {name: registry.get(name).login for name in registry.SERVICES}
WRITE_GRANTS = tuple(connector.write_grant for connector in registry.CONNECTORS.values() if connector.write_grant)
# Portals whose login meets a CAPTCHA the server answers with JURISYNC_CAPTCHA_API_KEY (or the captcha-ocr extra).
CAPTCHA_PORTALS = {"tjes-1g": "AWS WAF"}


@dataclass
class OfficePlan:
    tenant: str
    name: str
    customer: str
    connectors: list = field(default_factory=list)
    portals: list = field(default_factory=list)  # portal keys stored in the vault, plus advbox/juridiq
    checks: list = field(default_factory=list)  # one `jurisync-hosted portal check` login each: every portal, service
    skipped: dict = field(default_factory=dict)  # portal -> reason
    notes: list = field(default_factory=list)
    renamed: dict = field(default_factory=dict)  # legacy name -> canonical name
    users: list = field(default_factory=list)
    mode: str = "hosted"  # "local": settings.toml [hosted] mode; remembered on the server, nothing served
    reason: str = ""


def is_a3(setup):
    return any(name.strip().endswith("_A3_PIN") for group in setup.secrets for name in group.split("|"))


def a3_switch(config, values):
    """The variable that sends this portal to the A3 token in a local install, if any is on."""
    system = {"pje": "PJE_AUTH", "eproc": "EPROC_AUTH"}.get(config.system)
    for name in (f"{config.env_prefix}_AUTH", system):
        if name and values.get(name, "").strip().lower() == "a3":
            return name
    return None


def canonical_names(values, keys):
    """``(values, renamed)``: each portal's legacy field names (``_USUARIO``, ``_SENHA``, ``_EXPORT_DATA``) as the
    standard ones under the same prefix. A standard name already set wins; the legacy one is dropped either way.

    Prefixes are left alone: an older prefix (``PJE_TRF5``, ``JFSC``) can be shared by several portals, and the
    connectors still read it.
    """
    values, renamed = dict(values), {}
    for key in keys:
        config = portals.get(key)
        for prefix in (config.env_prefix, *config.legacy_env_prefixes):
            if not prefix:
                continue
            for good, olds in portals.LEGACY_NAMES.items():
                for old in olds:
                    name = f"{prefix}_{old}"
                    if name in values:
                        value = values.pop(name)
                        values.setdefault(f"{prefix}_{good}", value)
                        renamed[name] = f"{prefix}_{good}"
    return values, renamed


def group_present(group, values):
    """Whether any name of an ``A|B`` group has a value (a QR image counts for its ``_2FA_DATA`` text)."""
    names = [name.strip() for name in group.split("|")]
    return any(
        values.get(name, "").strip() or values.get(name.removesuffix("_DATA") + "_QR", "").strip() for name in names
    )


def resolve_files(values, directory):
    """File variables and QR images as absolute paths (a relative one is relative to the customer folder)."""
    from jurisync.install.setup import is_file_variable

    out = dict(values)
    for name, value in values.items():
        if not (is_file_variable(name) or name.endswith("_2FA_QR")):
            continue
        text = value.strip().strip("\"'")
        if not text:
            out.pop(name)
            continue
        path = Path(text).expanduser()
        if not path.is_absolute() and (directory / path).is_file():
            path = directory / path
        if not path.is_file():
            raise SystemExit(f"{directory.name}: o arquivo de {name} não existe")
        out[name] = str(path)
    return out


def plan_office(customer, values, *, name=None, ready=None):
    """The office's plan from its profile and the names in its own .env (values are only checked for presence)."""
    from jurisync.hosted.app import TENANT_READY

    ready = TENANT_READY if ready is None else ready
    setups = customers.load_setup(customer)
    mode, reason = customers.hosted_mode(customer)
    if mode == "local":
        # The office keeps its own install: no portal, credential, connector or user on the server.
        reason = reason or "instalação local"
        plan = OfficePlan(tenant=customer, name=name or customer.title(), customer=customer, mode=mode, reason=reason)
        plan.skipped = {key: reason for key in setups}
        return plan
    # The profile's pinned values ([env].set, e.g. TJSP_1G_CERT=true) count as set, as they do on every call.
    values = {**values, **customers.fixed_variables(customer)}
    values, renamed = canonical_names(values, list(setups))
    plan = OfficePlan(tenant=customer, name=name or customer.title(), customer=customer, renamed=renamed)
    browsers, switched = 0, []
    for key, setup in setups.items():
        config = portals.get(key)
        if is_a3(setup):
            plan.skipped[key] = "A3: o login precisa do token físico na máquina que entra (decidir A1, ponte ou local)"
            continue
        if config.system not in ready:
            plan.skipped[key] = f"{config.system} ainda não atende vários escritórios no servidor"
            continue
        missing = [group for group in setup.secrets if not group_present(group, values)]
        if missing:
            plan.skipped[key] = "faltam " + ", ".join(missing)
            continue
        plan.portals.append(key)
        plan.checks.append(key)
        if setup.method == "browser_totp":
            browsers += 1
        switch = a3_switch(config, values)
        if switch:
            switched.append(f"{key} ({switch}=a3)")
        if key in CAPTCHA_PORTALS or config.flavor == "infra-captcha":
            kind = CAPTCHA_PORTALS.get(key, "imagem")
            plan.notes.append(f"{key}: CAPTCHA ({kind}) resolvido com JURISYNC_CAPTCHA_API_KEY no servidor")
    for service, required in SERVICES.items():
        if all(values.get(name, "").strip() for name in required):
            plan.portals.append(service)
            plan.checks.append(service)
    if switched:
        plan.notes.append(
            "no computador do escritório estes portais entram pelo A3; no servidor usam o login declarado "
            "(senha + TOTP), a provar com portal check: " + ", ".join(switched)
        )
    if browsers:
        plan.notes.append(f"{browsers} portal(is) Eproc entram por Chrome headless (browser_totp)")
    served = {portals.get(key).system for key in plan.portals if key not in SERVICES}
    served |= {key for key in plan.portals if key in SERVICES} | {"djen"}
    plan.connectors = [system for system in SYSTEM_ORDER if system in served]
    known = {name for setup in setups.values() for group in setup.secrets for name in group.split("|")}
    known |= set(customers.extra_variables(customer)[0]) | set(customers.extra_variables(customer)[1])
    unserved = sorted(
        name
        for name in values
        if name.endswith("_API_KEY") and name not in known and not name.startswith(("JURISYNC_", "OPENROUTER_"))
    )
    if unserved:
        plan.notes.append("não servidos pelo servidor (ficam fora do cofre): " + ", ".join(unserved))
    return plan


def vault_content(plan, values):
    """``(values, files)`` for the office's vault: only what its planned portals and services read."""
    from jurisync.hosted import cli

    values, _ = canonical_names(values, [key for key in plan.portals if key not in SERVICES] + list(plan.skipped))
    values = resolve_files(values, customers.customer_dir(plan.customer))
    fixed = customers.fixed_variables(plan.customer)
    wanted = cli.wanted_names(plan.portals, plan.customer) if plan.portals else set()
    wanted |= {name for name in WRITE_GRANTS if values.get(name)}
    if plan.portals or fixed:
        optional = customers.extra_variables(plan.customer)[1]
        wanted |= {name for name in optional if values.get(name)}
        wanted |= {"DJEN_SEARCHES"} & set(values)
    prefixes = tuple(
        f"{prefix}_"
        for key in plan.portals
        if key not in SERVICES
        for prefix in (portals.get(key).env_prefix, *portals.get(key).legacy_env_prefixes)
        if prefix
    )
    # Settings of a planned portal (method, TOTP profile, cache identity) or a service; nothing of a skipped portal,
    # nothing the profile pins (it wins on the server, so a copy would only go stale).
    picked = {
        name: value
        for name, value in values.items()
        if value
        and name not in fixed
        and (name in wanted or (name.startswith(prefixes) and name.endswith(cli.SETTING_SUFFIXES)))
    }
    return cli.normalize(picked, {}, wanted | set(picked))


def read_customer_env(customer):
    from jurisync.install.setup import read_env

    return read_env(customers.customer_env_path(customer))


def all_customers():
    root = customers.customers_dir()
    return sorted(path.name for path in root.iterdir() if (path / "portals.toml").is_file())


def write_private(path, text):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(text)


def rollout_passphrase(path, *, create):
    """The passphrase that seals every bundle of a rollout, from a 0600 file; created on export, never printed."""
    path = Path(path).expanduser()
    if path.is_file():
        text = path.read_text(encoding="utf-8").strip()
        if len(text) < credentials_bundle.MIN_PASSPHRASE:
            raise SystemExit(
                f"{path}: a senha do pacote precisa ter ao menos {credentials_bundle.MIN_PASSPHRASE} caracteres"
            )
        return text
    if not create:
        raise SystemExit(f"{path} não existe: copie para o servidor o arquivo de senha usado no export")
    text = credentials_bundle.generate_passphrase()
    write_private(path, text + "\n")
    return text


def export(out, passphrase_file, *, only=None, names=None, say=print):
    out = Path(out).expanduser()
    if out.resolve() in Path(passphrase_file).expanduser().resolve().parents:
        raise SystemExit(
            "O arquivo de senha não pode ficar dentro da pasta do pacote: eles viajam por canais diferentes"
        )
    out.mkdir(mode=0o700, parents=True, exist_ok=True)
    passphrase = rollout_passphrase(passphrase_file, create=True)
    previous = {}
    if (out / PLAN).is_file():
        previous = {office["tenant"]: office for office in json.loads((out / PLAN).read_text())["offices"]}
    offices = []
    for customer in only or all_customers():
        values = read_customer_env(customer)
        try:
            plan = plan_office(customer, values, name=(names or {}).get(customer))
        except ValueError as error:
            if only:
                raise SystemExit(f"{customer}: {error}") from None
            say(f"{customer}: perfil inválido, fora do plano ({error})")
            continue
        if customer in previous:
            plan.users = list(previous[customer].get("users", [])) if plan.mode == "hosted" else []
            plan.name = (names or {}).get(customer) or previous[customer].get("name", plan.name)
        profile = out / "profiles" / customer
        profile.mkdir(parents=True, exist_ok=True)
        for file_name in PROFILE_FILES:
            source = customers.customer_dir(customer) / file_name
            if source.is_file():
                shutil.copyfile(source, profile / file_name)
        offices.append(plan)
        if plan.mode == "local":
            (out / f"{plan.tenant}.jurisync").unlink(missing_ok=True)  # a bundle from before the switch
            say(summary(plan))
            continue
        content_values, content_files = vault_content(plan, values)
        bundle = credentials_bundle.seal(
            content_values,
            passphrase,
            customer=customer,
            files={name: raw for name, raw in content_files.items()},
            file_vars={name: name for name in content_files},
            checks=plan.checks,
            servers=plan.connectors,
        )
        write_private(out / f"{plan.tenant}.jurisync", bundle + "\n")
        say(summary(plan, len(content_values), len(content_files)))
    # Offices not exported this time stay in the plan as they were.
    merged = {**previous, **{plan.tenant: asdict(plan) for plan in offices}}
    plan_text = json.dumps({"created": int(time.time()), "offices": [merged[key] for key in sorted(merged)]}, indent=2)
    write_private(out / PLAN, plan_text + "\n")
    say(f"Plano: {out / PLAN} ({len(merged)} escritório(s)); senha dos pacotes em {passphrase_file} (não impressa)")
    return offices


def summary(plan, values=None, files=None):
    if plan.mode == "local":
        return (
            f"{plan.tenant}: só local ({plan.reason}): {len(plan.skipped)} portal(is) ficam na instalação do escritório"
        )
    pieces = [f"{plan.tenant}: {len([key for key in plan.portals if key not in SERVICES])} portal(is)"]
    services = [key for key in plan.portals if key in SERVICES]
    if services:
        pieces.append("+ " + ", ".join(services))
    pieces.append(f"| conectores {','.join(plan.connectors)}")
    if values is not None:
        pieces.append(f"| cofre: {values} nome(s), {files} arquivo(s)")
    lines = [" ".join(pieces)]
    for key, reason in plan.skipped.items():
        lines.append(f"    pulado {key}: {reason}")
    for note in plan.notes:
        lines.append(f"    nota: {note}")
    if plan.renamed:
        lines.append(f"    renomeados: {len(plan.renamed)} nome(s) antigo(s) -> padrão")
    return "\n".join(lines)


def install_profile(source, customer):
    """Copy a customer's portals.toml/settings.toml into JURISYNC_CUSTOMERS_DIR on the server."""
    if not os.environ.get("JURISYNC_CUSTOMERS_DIR"):
        if (customers.customer_dir(customer) / "portals.toml").is_file():
            return False
        raise SystemExit(
            f"Perfil customers/{customer} não existe aqui: defina JURISYNC_CUSTOMERS_DIR (os perfis vão para lá)"
        )
    target = customers.customer_dir(customer)
    target.mkdir(parents=True, exist_ok=True)
    for file_name in PROFILE_FILES:
        if (source / file_name).is_file():
            shutil.copyfile(source / file_name, target / file_name)
    return True


def apply(directory, passphrase_file, *, only=None, user_passwords=None, replace_renewed=False, say=print):
    from jurisync.hosted import cli

    directory = Path(directory).expanduser()
    plan = json.loads((directory / PLAN).read_text(encoding="utf-8"))
    passphrase = rollout_passphrase(passphrase_file, create=False)
    store, vault = cli.open_store(), cli.open_vault()
    user_passwords = Path(user_passwords or cli.data_dir() / "new-user-passwords.txt")
    created_users = []
    for office in plan["offices"]:
        if only and office["tenant"] not in only:
            continue
        tenant, customer = office["tenant"], office["customer"]
        if install_profile(directory / "profiles" / customer, customer):
            say(f"{tenant}: perfil instalado em {customers.customer_dir(customer)}")
        if office.get("mode", "hosted") == "local":
            apply_local(store, vault, office, say)
            continue
        if store.tenant(tenant) is None:
            store.add_tenant(tenant, office["name"], customer=customer, connectors=office["connectors"])
            say(f"{tenant}: escritório criado ({', '.join(office['connectors'])})")
        else:
            store.update_tenant(tenant, name=office["name"], connectors=office["connectors"], mode="hosted", reason="")
            say(f"{tenant}: conectores atualizados ({', '.join(office['connectors'])})")
        opened = credentials_bundle.unseal((directory / f"{tenant}.jurisync").read_text(encoding="utf-8"), passphrase)
        if opened["customer"] != customer:
            raise SystemExit(f"{tenant}.jurisync é de {opened['customer']}, não de {customer}")
        values, files = dict(opened["env"]), {}
        for variable, name in opened["file_vars"].items():
            values.pop(variable, None)
            files[variable] = opened["files"][name]
        replace_vault(store, vault, tenant, values, files, office["portals"], customer, say, replace_renewed)
        existing = {row["email"] for row in store.users()}
        for email in office.get("users", []):
            email = email.strip().lower()
            if email in existing:
                continue
            password = secrets.token_urlsafe(15)
            store.add_user(email, tenant, password)
            store.log(tenant, "operador", "usuário criado", email)
            created_users.append(f"{email}\t{tenant}\t{password}")
            say(f"{tenant}: usuário {email} criado")
        for key, reason in office.get("skipped", {}).items():
            say(f"{tenant}: {key} fora do servidor ({reason})")
    if created_users:
        previous = user_passwords.read_text(encoding="utf-8") if user_passwords.is_file() else ""
        write_private(user_passwords, previous + "\n".join(created_users) + "\n")
        say(f"Senhas iniciais dos {len(created_users)} usuário(s) novos em {user_passwords} (0600, não impressas)")
    say("Próximo passo, um login por portal: jurisync-hosted portal check <escritório> <portal> (lista em plan.json)")


def replace_vault(store, vault, tenant, values, files, portal_keys, customer, say, replace_renewed=False):
    """The office's vault becomes the bundle: names the plan dropped go, the values the server renews stay (the
    bundle's copy is the office machine's, older; ``replace_renewed`` takes it anyway)."""
    from jurisync.hosted import cli

    current_values, current_files = vault.get(tenant)
    values = cli.keep_renewed(current_values, values, replace_renewed)
    removed = sorted((set(current_values) - set(values)) | (set(current_files) - set(files)))
    vault.put(tenant, values, files)
    store.log(tenant, "operador", "credenciais importadas", ", ".join(sorted(values) + sorted(files)))
    say(f"{tenant}: {len(values)} valor(es) e {len(files)} arquivo(s) gravados no cofre.")
    if removed:
        store.log(tenant, "operador", "credenciais removidas", ", ".join(removed))
        say(f"{tenant}: removidos do cofre: {', '.join(removed)}")
    for portal in sorted(set(store.checks(tenant)) - set(portal_keys)):
        store.forget_check(tenant, portal)
        say(f"{tenant}: teste de login de {portal} apagado (portal fora do plano)")
    for portal, groups in cli.missing_groups(portal_keys, customer, values, files).items():
        say(f"Atenção: {portal} ainda precisa de {', '.join(groups)}")


def apply_local(store, vault, office, say):
    """A local-only office: the tenant is kept as a record, with no connectors, credentials or users."""
    tenant, reason = office["tenant"], office.get("reason", "")
    if store.tenant(tenant) is None:
        store.add_tenant(
            tenant, office["name"], customer=office["customer"], connectors=(), mode="local", reason=reason
        )
        say(f"{tenant}: registrado como só local ({reason}); nada é servido")
    else:
        store.update_tenant(tenant, name=office["name"], connectors=(), mode="local", reason=reason)
        say(f"{tenant}: agora só local ({reason}); conectores removidos")
    if vault.names(tenant) != ([], []):
        vault.delete(tenant)
        say(f"{tenant}: credenciais apagadas do cofre")
    for portal in sorted(store.checks(tenant)):
        store.forget_check(tenant, portal)
    for row in store.users(tenant):
        store.remove_user(row["email"])
        say(f"{tenant}: usuário {row['email']} removido")
    store.log(tenant, "operador", "escritório só local", reason)


# Tools an office plugin may name that the hosted server does not serve: filing runs on the office's machine, and so do
# each connector's local-only tools (PJe drafts).
LOCAL_ONLY_TOOLS = re.compile(
    r"\b(filing_[a-z_]+|"
    + "|".join(sorted(tool for connector in registry.CONNECTORS.values() for tool in connector.local_only_tools))
    + r")\b"
)


def remote_plugin(source, url, out, *, say=print):
    """Copy an office plugin with its local MCP servers replaced by the hosted ``/mcp`` URL.

    Skills are copied as files (the plugin's symlinks point into customers/), customer profiles are left out: the
    server holds them. Returns the skills that name tools the server does not serve.
    """
    source, out = Path(source), Path(out)
    manifest = json.loads((source / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    if not url.startswith("https://") or not url.rstrip("/").endswith("/mcp"):
        raise SystemExit("--url deve ser o endereço público do servidor, https://<host>/mcp")
    manifest["mcpServers"] = {"jurisync": {"type": "http", "url": url.rstrip("/")}}
    manifest["description"] = manifest.get("description", "") + " (servidor remoto)"
    if out.exists():
        shutil.rmtree(out)
    (out / ".claude-plugin").mkdir(parents=True)
    local_only = {}
    for entry in manifest.get("skills", []):
        skill = (source / entry).resolve()
        shutil.copytree(skill, out / entry, symlinks=False)
        found = sorted(
            {
                match.group(1)
                for path in skill.rglob("*.md")
                for match in LOCAL_ONLY_TOOLS.finditer(path.read_text(encoding="utf-8"))
            }
        )
        if found:
            local_only[Path(entry).name] = found
    (out / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    say(f"Plugin remoto em {out}: servidor jurisync -> {url}")
    for skill, tools in local_only.items():
        say(f"    {skill} usa ferramentas que o servidor não serve ({', '.join(tools)}): continua na instalação local")
    return local_only
