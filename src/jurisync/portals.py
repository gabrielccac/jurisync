"""Every court portal Jurisync knows, in one registry.

A portal is one deployment of a court system: tribunal + instance (``1g``/``2g``,
the "grau de jurisdição") + section when a tribunal splits its first instance
by state, as TRF4 does. Keys are unique within a system (``trf1-1g``,
``trf4-1g-sc``) and are what MCP tools, CLIs, and results use; ``Portal.id``
prefixes the system (``pje-trf1-1g``) where systems mix. Code passes ``Portal``
objects internally, so a missing portal fails loudly instead of defaulting.

PJe deployments keep their paths under ``root``: federal courts serve ``/pje/``
while some state courts (TJAP) serve ``/1g/``; every connector path derives from
it instead of assuming ``/pje/``.
"""

import re
from dataclasses import dataclass, field

from jurisync.core import credentials

CNJ = re.compile(r"^\d{7}-\d{2}\.\d{4}\.(\d)\.(\d{2})\.(\d{4})$")


# Earlier (Portuguese) secret names still accepted; the canonical names match the PJe connector's.
LEGACY_NAMES = {"USERNAME": ("USUARIO",), "PASSWORD": ("SENHA",), "2FA_DATA": ("EXPORT_DATA",)}


@dataclass(frozen=True)
class Portal:
    key: str  # unique within its system: "trf1-1g", "trf4-1g-sc"
    system: str  # "pje" | "eproc" | "projudi" | "esaj" | "stj"
    tribunal: str  # TRF1, TRF4, TRF5, TJRS, TJAP, STJ
    instance: str  # "1g" | "2g"
    host: str
    root: str  # application path: "/pje/", "/eprocV2/" ("/" when not wired)
    auth: str  # "password_totp" | "certificate_totp" | "certificate_mtls" | "certificate_challenge" | "browser_totp" | "password" | "none"
    env_prefix: str = ""
    legacy_env_prefixes: tuple[str, ...] = ()
    region: str = ""  # CNJ "TR" segment, e.g. "01"
    justice: str = "4"  # CNJ "J" segment: "4" federal (default), "8" state courts
    origins: tuple[str, ...] = field(default=())  # CNJ "OOOO" prefixes served by this portal
    wired: bool = True  # False: recognized, but no connector yet
    guess_by_number: bool = True  # False: never suggested from a process number alone (only a link host picks it)
    flavor: str = (
        ""  # Projudi/e-SAJ generation (adapter): "tjgo-servlet" | "legacy-keycloak" | "legacy-local" | "tjpi-publico"
    )
    impersonate: str = ""  # curl_cffi profile override; "" keeps the global default (see core.http.IMPERSONATE)

    @property
    def id(self):
        return f"{self.system}-{self.key}"

    @property
    def base_url(self):
        return f"https://{self.host}{self.root}"

    @property
    def login_url(self):
        if self.system == "pje":
            return self.url("login.seam")
        if self.system == "esaj":
            return self.url("sajcas/login")
        if self.system == "projudi":
            return self.url("LogOn" if self.flavor == "tjgo-servlet" else "usuario/logon.do?actionType=inicio")
        if self.system == "eproc":
            # The Eproc root redirects to the SSO login. The externo_controlador.php
            # path is 403-blocked on some deployments (e.g. TJRS behind Cloudflare).
            return self.base_url
        return self.url("externo_controlador.php")

    @property
    def search_url(self):
        return self.url("Processo/ConsultaProcesso/listView.seam")

    def url(self, path=""):
        return self.base_url + path.lstrip("/")

    def env(self, name, default=""):
        """Read ``<PREFIX>_<name>``, falling back to legacy prefixes and legacy variable names."""
        for prefix in (self.env_prefix, *self.legacy_env_prefixes):
            for candidate in (name, *LEGACY_NAMES.get(name, ())):
                value = credentials.env(f"{prefix}_{candidate}").strip()
                if value:
                    return value
        return default

    def uses_certificate(self):
        """Whether this customer logs in here with the A1 certificate: PJE_A1_P12 is set and <PREFIX>_CERT is on."""
        return credentials.has("PJE_A1_P12") and self.env("CERT").lower() in {"1", "true", "yes", "on"}

    def env_name(self, name):
        return f"{self.env_prefix}_{name}"


PORTALS = {
    portal.key: portal
    for portal in (
        Portal("trf1-1g", "pje", "TRF1", "1g", "pje1g.trf1.jus.br", "/pje/", "password_totp", "PJE_1G", region="01"),
        Portal("trf1-2g", "pje", "TRF1", "2g", "pje2g.trf1.jus.br", "/pje/", "password_totp", "PJE_2G", region="01"),
        Portal(
            "trf5-1g", "pje", "TRF5", "1g", "pje1g.trf5.jus.br", "/pje/", "certificate_totp", "PJE_TRF5_1G",
            legacy_env_prefixes=("PJE_TRF5",), region="05",
        ),
        # Same Keycloak SSO (client "pje-trf5-2g") and the same A1 certificate + authenticator as the 1G. It also
        # serves the Turmas Recursais, whose appeals keep the 1G number, so a number alone cannot tell the two
        # apart (as in TRF1): DJEN's link host can.
        Portal(
            "trf5-2g", "pje", "TRF5", "2g", "pje2g.trf5.jus.br", "/pje/", "certificate_totp", "PJE_TRF5_2G",
            legacy_env_prefixes=("PJE_TRF5",), region="05",
        ),
        # TRF3 (SP/MS) serves the same PJe under /pje/; the password flow is the default, the A1 certificate opts in.
        Portal("trf3-1g", "pje", "TRF3", "1g", "pje1g.trf3.jus.br", "/pje/", "password_totp", "PJE_TRF3_1G", region="03"),
        Portal("trf3-2g", "pje", "TRF3", "2g", "pje2g.trf3.jus.br", "/pje/", "password_totp", "PJE_TRF3_2G", region="03"),
        # Turma Regional de Uniformização da 5ª Região: its own PJe host, same SSO and A1 certificate. Its cases keep
        # the JEF number, so only the link host (DJEN) or the panel it appears in points here.
        Portal(
            "trf5-tru", "pje", "TRF5", "2g", "pjett.trf5.jus.br", "/pje/", "certificate_totp", "PJE_TRF5_TRU",
            legacy_env_prefixes=("PJE_TRF5",), region="05", guess_by_number=False,
        ),
        # TJAP is a state court (CNJ J=8, TR=03) served by the PDPJ PJe under /1g/; login goes through the
        # same Keycloak SSO as the federal courts (realm "pje", client "pje-tjap-1g").
        Portal(
            "tjap-1g", "pje", "TJAP", "1g", "pje.tjap.jus.br", "/1g/", "password_totp", "TJAP_1G",
            region="03", justice="8",
        ),
        # TJPE (CNJ J=8, TR=17) serves both instances on one cloud host, told apart by the path (/1g/, /2g/), with
        # the same Keycloak SSO (clients "pje-tjpe-1g-cloud" and "pje-tjpe-2g-cloud"). An appeal keeps its number.
        Portal(
            "tjpe-1g", "pje", "TJPE", "1g", "pje.cloud.tjpe.jus.br", "/1g/", "password_totp", "TJPE_1G",
            region="17", justice="8",
        ),
        Portal(
            "tjpe-2g", "pje", "TJPE", "2g", "pje.cloud.tjpe.jus.br", "/2g/", "password_totp", "TJPE_2G",
            region="17", justice="8",
        ),
        # TJCE (CNJ J=8, TR=06) serves PJe 1G and 2G on one host, told apart by the path
        # (/pje1grau/, /pje2grau/); Turmas Recursais share the 2G environment. Registered for Denis.
        Portal(
            "tjce-1g", "pje", "TJCE", "1g", "pje.tjce.jus.br", "/pje1grau/", "certificate_totp", "TJCE_1G",
            region="06", justice="8",
        ),
        Portal(
            "tjce-2g", "pje", "TJCE", "2g", "pje.tjce.jus.br", "/pje2grau/", "certificate_totp", "TJCE_2G",
            region="06", justice="8",
        ),
        # Labor courts (CNJ J=5): TRT7/TRT18 1G/2G share one host told apart by the path; TST is its own
        # PJe-JT host (its cases keep the TRT number, so only its host points there). Same national
        # SSO/certificate mechanics as the other PJes.
        Portal(
            "trt7-1g", "pje", "TRT7", "1g", "pje.trt7.jus.br", "/primeirograu/", "certificate_totp", "TRT7_1G",
            region="07", justice="5",
        ),
        Portal(
            "trt7-2g", "pje", "TRT7", "2g", "pje.trt7.jus.br", "/segundograu/", "certificate_totp", "TRT7_2G",
            region="07", justice="5",
        ),
        Portal(
            "trt18-1g", "pje", "TRT18", "1g", "pje.trt18.jus.br", "/primeirograu/", "certificate_totp", "TRT18_1G",
            region="18", justice="5",
        ),
        Portal(
            "trt18-2g", "pje", "TRT18", "2g", "pje.trt18.jus.br", "/segundograu/", "certificate_totp", "TRT18_2G",
            region="18", justice="5",
        ),
        Portal(
            "tst", "pje", "TST", "2g", "pje.tst.jus.br", "/tst/", "certificate_totp", "TST",
            region="00", justice="5", guess_by_number=False,
        ),
        # State courts from one office's map (hosts as listed there; CNJ J=8): the three Eprocs first, then the
        # PJes. Single-host PJes serve /pje/; TJRJ/TJPI split instances by path like TJCE/TJPE.
        Portal("tjmg-1g", "eproc", "TJMG", "1g", "eproc1g.tjmg.jus.br", "/eproc/", "browser_totp", "TJMG_1G",
               region="13", justice="8"),
        Portal("tjrj-eproc-1g", "eproc", "TJRJ", "1g", "eproc1g.tjrj.jus.br", "/eproc/", "browser_totp", "TJRJ_1G",
               region="19", justice="8"),
        Portal("tjpr-1g", "eproc", "TJPR", "1g", "eproc1g.tjpr.jus.br", "/eproc/", "browser_totp", "TJPR_1G",
               region="16", justice="8"),
        Portal("tjrj-1g", "pje", "TJRJ", "1g", "tjrj.pje.jus.br", "/1g/", "certificate_totp", "TJRJ_PJE_1G",
               region="19", justice="8"),
        Portal("tjrj-2g", "pje", "TJRJ", "2g", "tjrj.pje.jus.br", "/2g/", "certificate_totp", "TJRJ_PJE_2G",
               region="19", justice="8"),
        Portal("tjmt-1g", "pje", "TJMT", "1g", "pje.tjmt.jus.br", "/pje/", "certificate_totp", "TJMT_1G",
               region="11", justice="8"),
        Portal("tjdft-1g", "pje", "TJDFT", "1g", "pje.tjdft.jus.br", "/pje/", "certificate_totp", "TJDFT_1G",
               region="07", justice="8"),
        Portal("tjpa-1g", "pje", "TJPA", "1g", "pje.tjpa.jus.br", "/pje/", "certificate_totp", "TJPA_1G",
               region="14", justice="8"),
        Portal("tjma-1g", "pje", "TJMA", "1g", "pje.tjma.jus.br", "/pje/", "certificate_totp", "TJMA_1G",
               region="10", justice="8"),
        Portal("tjes-1g", "pje", "TJES", "1g", "pje.tjes.jus.br", "/pje/", "certificate_totp", "TJES_1G",
               region="08", justice="8"),
        Portal("tjba-1g", "pje", "TJBA", "1g", "pje.tjba.jus.br", "/pje/", "certificate_totp", "TJBA_1G",
               region="05", justice="8"),
        Portal("tjpb-1g", "pje", "TJPB", "1g", "pje.tjpb.jus.br", "/pje/", "certificate_totp", "TJPB_1G",
               region="15", justice="8"),
        Portal("tjpi-1g", "pje", "TJPI", "1g", "pje.tjpi.jus.br", "/1g/", "certificate_totp", "TJPI_1G",
               region="18", justice="8"),
        Portal("tjpi-2g", "pje", "TJPI", "2g", "pje.tjpi.jus.br", "/2g/", "certificate_totp", "TJPI_2G",
               region="18", justice="8"),
        Portal(
            "trf4-1g-sc", "eproc", "TRF4", "1g", "eproc.jfsc.jus.br", "/eprocV2/", "browser_totp", "TRF4_1G_SC",
            legacy_env_prefixes=("JFSC",), region="04", origins=("72",),
        ),
        Portal(
            "trf4-1g-rs", "eproc", "TRF4", "1g", "eproc.jfrs.jus.br", "/eprocV2/", "browser_totp", "TRF4_1G_RS",
            legacy_env_prefixes=("JFRS", "JRFS"), region="04", origins=("71",),
        ),
        # TRF6 serves its Eproc under /eproc/ (not /eprocV2/); both instances share one account (TRF6_*),
        # so the prefix stays shared (intentional exception to prefix == key).
        Portal("trf6-1g", "eproc", "TRF6", "1g", "eproc1g.trf6.jus.br", "/eproc/", "browser_totp", "TRF6", region="06"),
        Portal("trf6-2g", "eproc", "TRF6", "2g", "eproc2g.trf6.jus.br", "/eproc/", "browser_totp", "TRF6", region="06"),
        Portal("trf4-1g-pr", "eproc", "TRF4", "1g", "eproc.jfpr.jus.br", "/eprocV2/", "browser_totp", "TRF4_1G_PR",
               legacy_env_prefixes=("JFPR",), region="04", origins=("70",)),
        # TRF4 itself (2G) serves its Eproc under /eproc2trf4/; its SSO also accepts the A1 certificate over mutual TLS.
        Portal("trf4-2g", "eproc", "TRF4", "2g", "eproc.trf4.jus.br", "/eproc2trf4/", "browser_totp", "TRF4_2G",
               region="04"),
        # TRF2 (RJ/ES) serves its Eproc under /eproc/: the tribunal (2G) plus one base
        # per section (JFRJ for RJ, JFES for ES). Browser login (OAB + password) + TOTP,
        # same shape as TRF6; the 1G and 2G environments share the password.
        Portal("trf2-1g-rj", "eproc", "TRF2", "1g", "eproc.jfrj.jus.br", "/eproc/", "browser_totp", "TRF2_1G_RJ",
               legacy_env_prefixes=("JFRJ",), region="02"),
        Portal("trf2-1g-es", "eproc", "TRF2", "1g", "eproc.jfes.jus.br", "/eproc/", "browser_totp", "TRF2_1G_ES",
               legacy_env_prefixes=("JFES",), region="02"),
        Portal("trf2-2g", "eproc", "TRF2", "2g", "eproc.trf2.jus.br", "/eproc/", "browser_totp", "TRF2_2G",
               region="02"),
        # TJRS serves its Eproc under /eproc/ (public document links use it); state justice is CNJ J=8, TR=21.
        Portal("tjrs-1g", "eproc", "TJRS", "1g", "eproc1g.tjrs.jus.br", "/eproc/", "browser_totp", "TJRS_1G",
               legacy_env_prefixes=("TJRS",), region="21", justice="8", wired=True),
        # State-court Eprocs below are registered for Denis (authenticator profiles
        # Eproc/TJSC, Eproc/TJTO, Eproc/TNU, Eproc/TJSP); no connector reads them yet.
        Portal("tjsc-1g", "eproc", "TJSC", "1g", "eproc1g.tjsc.jus.br", "/eproc/", "browser_totp", "TJSC_1G",
               region="24", justice="8"),
        Portal("tjsc-2g", "eproc", "TJSC", "2g", "eproc2g.tjsc.jus.br", "/eproc/", "browser_totp", "TJSC_2G",
               region="24", justice="8"),
        Portal("tjto-1g", "eproc", "TJTO", "1g", "eproc1.tjto.jus.br", "/eprocV2_prod_1grau/", "browser_totp", "TJTO_1G",
               region="27", justice="8"),
        Portal("tjto-2g", "eproc", "TJTO", "2g", "eproc2.tjto.jus.br", "/eprocV2_prod_2grau/", "browser_totp", "TJTO_2G",
               region="27", justice="8"),
        Portal("tjsp-1g", "eproc", "TJSP", "1g", "eproc1g.tjsp.jus.br", "/eproc/", "browser_totp", "TJSP_1G",
               region="26", justice="8"),
        Portal("tjsp-2g", "eproc", "TJSP", "2g", "eproc2g.tjsp.jus.br", "/eproc/", "browser_totp", "TJSP_2G",
               region="26", justice="8"),
        # TNU (Turma Nacional de Uniformização, CJF): single eproc for JEF uniformization cases,
        # which keep their origin number, so a number alone never points here.
        Portal("tnu", "eproc", "TNU", "2g", "eproctnu.cjf.jus.br", "/eproc/", "browser_totp", "TNU",
               justice="4", guess_by_number=False),
        # Projudi is one CNJ system in several generations. TJGO's servlet front end is the only one validated: the A1
        # certificate logs in over mutual TLS with no authenticator. The others are recognized but not wired until a
        # live smoke proves login + process + one document; they never come from a process number alone, and their keys
        # carry "projudi" where the same tribunal already has a PJe or Eproc portal. They do not share TJGO's login or routes
        # (TJPR/TJRR use a Keycloak SSO with user and password, TJAM/TJPI a local form).
        Portal("tjgo-1g", "projudi", "TJGO", "1g", "projudi.tjgo.jus.br", "/", "certificate_mtls", "TJGO_1G",
               region="09", justice="8", flavor="tjgo-servlet", impersonate="chrome120"),
        # TJPR/TJRR: Keycloak SSO (TJPR realm projudi at sso.projudi.tjpr.jus.br, TJRR realm projudi at
        # keycloak.tjrr.jus.br) with CPF + password + TOTP. TJAM: local form, certificate internal to Projudi.
        # TJPI: Projudi 1.9.8, local form, CAPTCHA. Credentials, when wired, use <PREFIX>_USERNAME/_PASSWORD/_2FA_DATA.
        Portal("tjpr-projudi-1g", "projudi", "TJPR", "1g", "projudi.tjpr.jus.br", "/projudi/", "password_totp",
               "TJPR_PROJUDI_1G", region="16", justice="8", wired=False, guess_by_number=False,
               flavor="legacy-keycloak"),
        Portal("tjrr-1g", "projudi", "TJRR", "1g", "projudi.tjrr.jus.br", "/projudi/", "password_totp", "TJRR_1G",
               region="23", justice="8", wired=False, guess_by_number=False, flavor="legacy-keycloak"),
        Portal("tjam-1g", "projudi", "TJAM", "1g", "projudi.tjam.jus.br", "/projudi/", "password", "TJAM_1G",
               region="04", justice="8", wired=False, guess_by_number=False, flavor="legacy-local"),
        Portal("tjpi-projudi-1g", "projudi", "TJPI", "1g", "projudi.tjpi.jus.br", "/projudi/", "password",
               "TJPI_PROJUDI_1G", region="18", justice="8", wired=False, guess_by_number=False,
               flavor="tjpi-publico"),
        # e-SAJ (Softplan): the CAS (/sajcas) asks the A1 certificate to sign a challenge in the login form, with no
        # mutual TLS and no authenticator. Keys carry "esaj" because TJSP already has an Eproc portal (tjsp-1g).
        Portal("tjsp-esaj", "esaj", "TJSP", "1g", "esaj.tjsp.jus.br", "/", "certificate_challenge", "TJSP_ESAJ",
               region="26", justice="8", guess_by_number=False, flavor="esaj-cas"),
        Portal("tjms-esaj", "esaj", "TJMS", "1g", "esaj.tjms.jus.br", "/", "certificate_challenge", "TJMS_ESAJ",
               region="12", justice="8", flavor="esaj-cas"),
        Portal("stj", "stj", "STJ", "2g", "processo.stj.jus.br", "/", "none", wired=False),
    )
}  # fmt: skip

# Earlier names, still accepted as input. Outputs always use the current key.
ALIASES = {
    "jfsc": "trf4-1g-sc",
    "jfrs": "trf4-1g-rs",
    "jfpr": "trf4-1g-pr",
    "tjrs": "tjrs-1g",
    "jfrj": "trf2-1g-rj",
    "jfes": "trf2-1g-es",
}


def pje(key):
    return get(key, "pje")


def esaj(key):
    return get(key, "esaj")


def eproc(key):
    return get(key, "eproc")


def projudi(key):
    return get(key, "projudi")


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


def for_host(host, path=""):
    """The portal serving this host (or a subdomain of it), if any.

    Portals that share a host (TJPE's 1G and 2G) are told apart by the application path of the link.
    """
    matches = [portal for portal in PORTALS.values() if host == portal.host or host.endswith("." + portal.host)]
    if len(matches) > 1:
        path = path or ""
        for portal in matches:
            if portal.root != "/" and path.startswith(portal.root):
                return portal
    return matches[0] if matches else None


def for_process(number):
    """Wired-or-not portals that may hold a CNJ process number, most specific first."""
    match = CNJ.match(number or "")
    if not match:
        return []
    justice, region, origin = match.groups()
    candidates = [
        portal
        for portal in PORTALS.values()
        if portal.guess_by_number and portal.justice == justice and portal.region == region
    ]
    by_origin = [portal for portal in candidates if any(origin.startswith(prefix) for prefix in portal.origins)]
    if by_origin:
        return by_origin
    return [portal for portal in candidates if not portal.origins]
