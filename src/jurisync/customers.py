"""Per-customer portal setup: which login method each portal uses and where its secrets live.

A customer is a directory under ``customers/``. Two files describe its access:

* ``portals.toml`` (committed, no values): for each portal key, the login ``method`` and
  the environment variables that must be set. Alternatives are joined with ``|``. The method decides
  the login: a PJe or Eproc portal declared with a certificate method gets ``<PREFIX>_CERT=true``
  without repeating it in ``[env].set``.
* ``.env`` (gitignored, the secrets): the values of those variables.

An optional ``settings.toml`` (committed) holds the customer's choices, e.g.
``[pje] ciencia_on_read = "allow"`` to read pending communications without asking.

``JURISYNC_CUSTOMER`` selects the customer for a server process. Its ``.env`` is loaded
before the shared one, and variables already in the environment always win.
"""

import argparse
import functools
import os
import tomllib
from dataclasses import dataclass

from jurisync import portals
from jurisync.core.credentials import customer_dir, customer_env_path, customer_name, customers_dir
from jurisync.paths import REPO_ROOT

# Methods a customer may declare. Connectors decide how each one logs in.
METHODS = ("password_totp", "certificate_totp", "certificate_mtls", "certificate_challenge", "browser_totp")
# Systems whose portals switch to the A1 login with <PREFIX>_CERT (Portal.uses_certificate), and the methods that mean
# it. The switch only acts where an A1 is configured: an A3 login (PJE_AUTH/EPROC_AUTH=a3) is chosen before it.
CERTIFICATE_SYSTEMS = ("pje", "eproc")
CERTIFICATE_METHODS = ("certificate_totp", "certificate_mtls")
TRUE = ("1", "true", "yes", "on")


@dataclass(frozen=True)
class PortalSetup:
    key: str
    method: str
    secrets: tuple[str, ...]

    def missing(self):
        """Secret groups with no variable set (a group is ``A|B``: any one is enough)."""
        return tuple(
            group for group in self.secrets if not any(os.environ.get(name.strip()) for name in group.split("|"))
        )


def load_setup(name):
    """Read ``portals.toml`` and validate it against the portal registry."""
    path = customer_dir(name) / "portals.toml"
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"customer {name!r} has no portals.toml at {path}") from None
    setups = {}
    for key, entry in data.items():
        if key == RESERVED_TABLE:
            continue
        portals.get(key)  # unknown or unwired portals fail loudly
        method = entry.get("method")
        if method not in METHODS:
            raise ValueError(f"{name}/{key}: method must be one of {', '.join(METHODS)}")
        secrets = entry.get("secrets", [])
        if not isinstance(secrets, list) or not all(isinstance(item, str) and item for item in secrets):
            raise ValueError(f"{name}/{key}: secrets must be a list of variable names")
        setups[key] = PortalSetup(key, method, tuple(portals.get(key).variable_names(group) for group in secrets))
    pinned = data.get(RESERVED_TABLE, {}).get("set") or {}
    switches = certificate_switches(setups)
    for key in setups:
        variable = portals.get(key).env_name("CERT")
        # Only the pin the portal reads first counts (the first non-blank, as Portal.env): one it shadows has no effect.
        names = portals.get(key).variable_names(variable).split("|") if variable in switches else []
        pin = next((name for name in names if str(pinned.get(name, "")).strip()), None)
        if pin and (str(pinned[pin]).strip().lower() in TRUE) != switches[variable]:
            raise ValueError(
                f"{name}: [env].set {pin} = {pinned[pin]!r} contradicts the method declared for its portal"
            )
    return setups


def certificate_switches(setups):
    """``<PREFIX>_CERT`` of each declared PJe/Eproc portal: on when its method is a certificate login."""
    switches = {}
    for key, setup in setups.items():
        portal = portals.get(key)
        if portal.system not in CERTIFICATE_SYSTEMS or not portal.env_prefix:
            continue
        variable = portal.env_name("CERT")
        switches[variable] = switches.get(variable, False) or setup.method in CERTIFICATE_METHODS
    return switches


RESERVED_TABLE = "env"  # [env] lists variables that belong to no portal (ADVBOX login, CPF, API keys)


def profile_portals(keys):
    """``keys`` narrowed to the portals the current customer's ``portals.toml`` declares (local process or hosted
    office); all of them when there is no profile. Chosen at setup: a login that also opens another portal does not
    offer it."""
    name = customer_name()
    if not name or not (customer_dir(name) / "portals.toml").is_file():
        return tuple(keys)
    declared = load_setup(name)
    return tuple(key for key in keys if key in declared)


def declared_method(portal):
    """The login method the current customer's ``portals.toml`` declares for ``portal`` (a ``Portal``); ``None`` when
    there is no customer, no profile, or no entry for it. A declared method decides the login; connectors fall back to
    the registry's ``Portal.auth`` only without one."""
    name = customer_name()
    if not name:
        return None
    path = customer_dir(name) / "portals.toml"
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return _declared_methods(name, str(path), stat.st_mtime_ns, stat.st_size).get(portal.id)


@functools.lru_cache(maxsize=32)
def _declared_methods(name, path, mtime, size):
    return {portals.get(key).id: setup.method for key, setup in load_setup(name).items()}


def extra_variables(name):
    """Variables declared in the customer's ``[env]`` table: ``(required, optional)`` name lists."""
    path = customer_dir(name) / "portals.toml"
    try:
        table = tomllib.loads(path.read_text(encoding="utf-8")).get(RESERVED_TABLE, {})
    except FileNotFoundError:
        return [], []
    return list(table.get("secrets", [])), list(table.get("optional", []))


def fixed_variables(name):
    """Variables the profile pins to a value: ``<PREFIX>_CERT=true`` of each portal declared with a certificate method,
    then the ``[env].set`` table (e.g. ADVBOX_ALLOW_WRITES = "true")."""
    path = customer_dir(name) / "portals.toml"
    try:
        table = tomllib.loads(path.read_text(encoding="utf-8")).get(RESERVED_TABLE, {})
    except FileNotFoundError:
        return {}
    switches = {variable: "true" for variable, on in certificate_switches(load_setup(name)).items() if on}
    return {**switches, **{str(key): str(value) for key, value in (table.get("set") or {}).items()}}


CIENCIA_POLICIES = ("ask", "allow")


def settings(name=None):
    """The customer's committed ``settings.toml`` (empty when it has none)."""
    name = name or customer_name()
    if not name:
        return {}
    try:
        return tomllib.loads((customer_dir(name) / "settings.toml").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def ciencia_on_read(name=None):
    """PJe: ``ask`` (default) refuses to read a communication whose ciência is pending until the user agrees;
    ``allow`` reads it and reports that the ciência may have been registered."""
    value = settings(name).get("pje", {}).get("ciencia_on_read", "ask")
    if value not in CIENCIA_POLICIES:
        raise ValueError(f"settings.toml [pje] ciencia_on_read must be one of {', '.join(CIENCIA_POLICIES)}")
    return value


HOSTED_MODES = ("hosted", "local")


def hosted_mode(name):
    """``(mode, reason)`` from the customer's ``settings.toml`` ``[hosted]`` table.

    ``mode = "local"`` keeps the office on its own computer (an A3 token, say): the hosted server only remembers it,
    with no credentials, no users and no tools. Default ``hosted``.
    """
    table = settings(name).get("hosted", {})
    mode = table.get("mode", "hosted")
    if mode not in HOSTED_MODES:
        raise ValueError(f"settings.toml [hosted] mode must be one of {', '.join(HOSTED_MODES)}")
    return mode, str(table.get("reason", "")).strip()


def load_env_file(path=None):
    """Load KEY=VALUE lines like `node --env-file`: existing variables win.

    The path comes from JURISYNC_ENV_FILE (set per server in .mcp.json); without it the
    user's ``~/.config/jurisync/.env`` is used when it exists. When JURISYNC_CUSTOMER names a customer, its
    gitignored ``customers/<name>/.env`` is loaded first, so a customer's values
    take precedence over the shared file. The values its portals.toml ``[env].set`` pins (e.g. ``TRF1_1G_CERT=true``)
    come next, so a checkout behaves like an installed bundle, which writes them into the customer's file.
    With a customer selected the shared file then provides machine settings only (``core.mcp.MACHINE_PATTERNS``);
    without one it is the single account's file and loads whole.
    """
    from jurisync.core.mcp import load_env_lines
    from jurisync.paths import USER_ENV_FILE

    shared = path or os.environ.get("JURISYNC_ENV_FILE") or (USER_ENV_FILE if USER_ENV_FILE.exists() else None)
    if shared and not customer_name():
        # A client that registers the server without JURISYNC_CUSTOMER (Claude Desktop) can name it in the shared file.
        load_env_lines(shared, only={"JURISYNC_CUSTOMER"})
    customer = customer_name()
    own = customer_env_path()
    if own:
        load_env_lines(own)
    if customer:
        for key, value in fixed_variables(customer).items():
            os.environ.setdefault(key, value)
    if shared:
        # Only a checkout that keeps the office's own customers/<name>/.env treats the shared file as other offices'
        # secrets. An installed office (plugin or bundle) has no such file: its ~/.config/jurisync/.env is its own.
        load_env_lines(shared, machine_only=bool(own and own.exists()))


def names():
    """Every customer folder (``_template`` and other ``_``/``.`` folders are not customers)."""
    root = customers_dir()
    if not root.is_dir():
        return []
    return sorted(path.name for path in root.iterdir() if path.is_dir() and not path.name.startswith(("_", ".")))


def validate(name):
    """What keeps a customer folder from working, one line each; empty when it is ready. Reads no secret values."""
    folder = customer_dir(name)
    try:
        setups = load_setup(name)
        required, optional = extra_variables(name)
    except (ValueError, tomllib.TOMLDecodeError) as error:
        return [str(error)]
    problems = []
    if not setups and not required:
        problems.append(f"{name}: portals.toml declares no portal and no [env] secrets")
    for read in (ciencia_on_read, hosted_mode):
        try:
            read(name)
        except (ValueError, tomllib.TOMLDecodeError) as error:
            problems.append(f"{name}: {error}")
    example = folder / ".env.example"
    if example.is_file():
        listed = {
            line.lstrip("# ").split("=", 1)[0].strip()
            for line in example.read_text(encoding="utf-8").splitlines()
            if "=" in line
        }
        groups = [group for setup in setups.values() for group in setup.secrets] + list(required)
        for group in dict.fromkeys(groups):
            if not any(variable.strip() in listed for variable in group.split("|")):
                problems.append(f"{name}: .env.example lacks {group}")
    return problems


def env_example(name):
    """The customer's ``.env.example``, made from its portals.toml: one line per declared variable, the alternatives of
    an ``A|B`` group and the optional ones commented out, each variable once. No values."""
    data = tomllib.loads((customer_dir(name) / "portals.toml").read_text(encoding="utf-8"))
    lines, seen = (
        [f"# Gerado de portals.toml por: jurisync-setup env-example {name}. Copie para customers/{name}/.env."],
        set(),
    )
    for key, entry in data.items():
        lines.append(f"# {key}" + (f" ({entry['method']})" if "method" in entry else ""))
        for group in entry.get("secrets", []):
            first, *others = (variable.strip() for variable in group.split("|"))
            lines += [
                f"{first}=" if first not in seen else "",
                *(f"# {other}=" for other in others if other not in seen),
            ]
            seen.update((first, *others))
        lines += [f"# {variable}=" for variable in entry.get("optional", []) if variable not in seen]
        seen.update(entry.get("optional", []))
    return "\n".join(line for line in lines if line) + "\n"


def check(name):
    """Report, per portal, the method and which secrets are still missing. Never prints values."""
    return {key: {"method": setup.method, "missing": list(setup.missing())} for key, setup in load_setup(name).items()}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check a customer's portal credentials without printing them.")
    parser.add_argument("name", nargs="?", default=customer_name())
    args = parser.parse_args(argv)
    if not args.name:
        parser.error("pass a customer name or set JURISYNC_CUSTOMER")
    os.environ["JURISYNC_CUSTOMER"] = args.name
    load_env_file(os.environ.get("JURISYNC_ENV_FILE") or REPO_ROOT / ".env")
    for key, status in check(args.name).items():
        state = "ready" if not status["missing"] else "missing " + ", ".join(status["missing"])
        print(f"{key:12} {status['method']:17} {state}")


if __name__ == "__main__":
    main()
