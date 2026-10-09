"""Every court portal Jurisync knows, in one registry.

A portal is one deployment of a court system: tribunal + instance (``1g``/``2g``,
the "grau de jurisdição") + section when a tribunal splits its first instance
by state, as TRF4 does. Keys are unique within a system (``trf1-1g``,
``trf4-1g-sc``) and are what MCP tools, CLIs, and results use; ``Portal.id``
prefixes the system (``pje-trf1-1g``) where systems mix. Code passes ``Portal``
objects internally, so a missing portal fails loudly instead of defaulting.
"""

import os
import re
from dataclasses import dataclass, field

CNJ = re.compile(r"^\d{7}-\d{2}\.\d{4}\.(\d)\.(\d{2})\.(\d{4})$")


@dataclass(frozen=True)
class Portal:
    key: str  # unique within its system: "trf1-1g", "trf4-1g-sc"
    system: str  # "pje" | "eproc" | "stj"
    tribunal: str  # TRF1, TRF4, TRF5, TJRS, STJ
    instance: str  # "1g" | "2g"
    host: str
    root: str  # application path: "/pje/", "/eprocV2/" ("/" when not wired)
    auth: str  # "password_totp" | "certificate_totp" | "browser_totp" | "none"
    env_prefix: str = ""
    legacy_env_prefixes: tuple[str, ...] = ()
    region: str = ""  # CNJ "TR" segment, e.g. "01"
    origins: tuple[str, ...] = field(default=())  # CNJ "OOOO" prefixes served by this portal
    wired: bool = True  # False: recognized, but no connector yet

    @property
    def id(self):
        return f"{self.system}-{self.key}"

    @property
    def base_url(self):
        return f"https://{self.host}{self.root}"

    @property
    def login_url(self):
        return self.url("login.seam" if self.system == "pje" else "externo_controlador.php")

    @property
    def search_url(self):
        return self.url("Processo/ConsultaProcesso/listView.seam")

    def url(self, path=""):
        return self.base_url + path.lstrip("/")

    def env(self, name, default=""):
        """Read ``<PREFIX>_<name>``, falling back to legacy prefixes."""
        for prefix in (self.env_prefix, *self.legacy_env_prefixes):
            value = os.environ.get(f"{prefix}_{name}", "").strip()
            if value:
                return value
        return default

    def env_name(self, name):
        return f"{self.env_prefix}_{name}"


PORTALS = {
    portal.key: portal
    for portal in (
        Portal("trf1-1g", "pje", "TRF1", "1g", "pje1g.trf1.jus.br", "/pje/", "password_totp", "PJE_1G", region="01"),
        Portal("trf1-2g", "pje", "TRF1", "2g", "pje2g.trf1.jus.br", "/pje/", "password_totp", "PJE_2G", region="01"),
        Portal(
            "trf5-1g", "pje", "TRF5", "1g", "pje1g.trf5.jus.br", "/pje/", "certificate_totp", "PJE_TRF5_1G", region="05"
        ),
        Portal(
            "trf4-1g-sc", "eproc", "TRF4", "1g", "eproc.jfsc.jus.br", "/eprocV2/", "browser_totp", "JFSC",
            region="04", origins=("72",),
        ),
        Portal(
            "trf4-1g-rs", "eproc", "TRF4", "1g", "eproc.jfrs.jus.br", "/eprocV2/", "browser_totp", "JFRS",
            legacy_env_prefixes=("JRFS",), region="04", origins=("71",),
        ),
        # TRF6 serves its Eproc under /eproc/ (not /eprocV2/); both instances share one account (TRF6_*).
        Portal("trf6-1g", "eproc", "TRF6", "1g", "eproc1g.trf6.jus.br", "/eproc/", "browser_totp", "TRF6", region="06"),
        Portal("trf6-2g", "eproc", "TRF6", "2g", "eproc2g.trf6.jus.br", "/eproc/", "browser_totp", "TRF6", region="06"),
        Portal("trf4-1g-pr", "eproc", "TRF4", "1g", "eproc.jfpr.jus.br", "/", "browser_totp", region="04",
               origins=("70",), wired=False),
        Portal("trf4-2g", "eproc", "TRF4", "2g", "eproc.trf4.jus.br", "/", "browser_totp", region="04",
               wired=False),
        Portal("tjrs-1g", "eproc", "TJRS", "1g", "eproc1g.tjrs.jus.br", "/", "browser_totp", wired=False),
        Portal("stj", "stj", "STJ", "2g", "processo.stj.jus.br", "/", "none", wired=False),
    )
}  # fmt: skip

# Earlier names, still accepted as input. Outputs always use the current key.
ALIASES = {"jfsc": "trf4-1g-sc", "jfrs": "trf4-1g-rs", "jfpr": "trf4-1g-pr", "tjrs": "tjrs-1g"}


def pje(key):
    return get(key, "pje")


def eproc(key):
    return get(key, "eproc")


def get(key, system=None):
    """Resolve a key (or pass through a Portal) for a wired portal of this system."""
    portal = key if isinstance(key, Portal) else PORTALS.get(ALIASES.get(key, key))
    if portal is None or not portal.wired or (system and portal.system != system):
        choices = ", ".join(keys(system))
        raise ValueError(f"portal must be one of {choices}")
    return portal


def keys(system):
    return tuple(key for key, portal in PORTALS.items() if portal.wired and portal.system == system)


def hosts(system):
    return {PORTALS[key].host for key in keys(system)}


def for_host(host):
    """The portal serving this host (or a subdomain of it), if any."""
    for portal in PORTALS.values():
        if host == portal.host or host.endswith("." + portal.host):
            return portal
    return None


def for_process(number):
    """Wired-or-not portals that may hold a CNJ process number, most specific first."""
    match = CNJ.match(number or "")
    if not match:
        return []
    justice, region, origin = match.groups()
    if justice != "4":  # federal justice only, for now
        return []
    federal = [portal for portal in PORTALS.values() if portal.region == region]
    by_origin = [portal for portal in federal if any(origin.startswith(prefix) for prefix in portal.origins)]
    if by_origin:
        return by_origin
    return [portal for portal in federal if not portal.origins]
