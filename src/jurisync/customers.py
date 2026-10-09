"""Per-customer portal setup: which login method each portal uses and where its secrets live.

A customer is a directory under ``customers/``. Two files describe its access:

* ``portals.toml`` (committed, no values): for each portal key, the login ``method`` and
  the environment variables that must be set. Alternatives are joined with ``|``.
* ``.env`` (gitignored, the secrets): the values of those variables.

``JURISYNC_CUSTOMER`` selects the customer for a server process. Its ``.env`` is loaded
before the shared one, and variables already in the environment always win.
"""

import argparse
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from jurisync import portals

# Methods a customer may declare. Connectors decide how each one logs in.
METHODS = ("password_totp", "certificate_totp", "browser_totp")

REPO_ROOT = Path(__file__).resolve().parents[2]


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


def customers_dir():
    return Path(os.environ.get("JURISYNC_CUSTOMERS_DIR") or REPO_ROOT / "customers")


def customer_name():
    return os.environ.get("JURISYNC_CUSTOMER", "").strip()


def customer_dir(name):
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise ValueError(f"invalid customer name: {name!r}")
    return customers_dir() / name


def load_setup(name):
    """Read ``portals.toml`` and validate it against the portal registry."""
    path = customer_dir(name) / "portals.toml"
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"customer {name!r} has no portals.toml at {path}") from None
    setups = {}
    for key, entry in data.items():
        portals.get(key)  # unknown or unwired portals fail loudly
        method = entry.get("method")
        if method not in METHODS:
            raise ValueError(f"{name}/{key}: method must be one of {', '.join(METHODS)}")
        secrets = entry.get("secrets", [])
        if not isinstance(secrets, list) or not all(isinstance(item, str) and item for item in secrets):
            raise ValueError(f"{name}/{key}: secrets must be a list of variable names")
        setups[key] = PortalSetup(key, method, tuple(secrets))
    return setups


def customer_env_path(name=None):
    name = name or customer_name()
    return customer_dir(name) / ".env" if name else None


def check(name):
    """Report, per portal, the method and which secrets are still missing. Never prints values."""
    return {key: {"method": setup.method, "missing": list(setup.missing())} for key, setup in load_setup(name).items()}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check a customer's portal credentials without printing them.")
    parser.add_argument("name", nargs="?", default=customer_name())
    args = parser.parse_args(argv)
    if not args.name:
        parser.error("pass a customer name or set JURISYNC_CUSTOMER")
    from jurisync.core.mcp import load_env_file

    os.environ["JURISYNC_CUSTOMER"] = args.name
    load_env_file(os.environ.get("JURISYNC_ENV_FILE") or REPO_ROOT / ".env")
    for key, status in check(args.name).items():
        state = "ready" if not status["missing"] else "missing " + ", ".join(status["missing"])
        print(f"{key:12} {status['method']:17} {state}")


if __name__ == "__main__":
    main()
