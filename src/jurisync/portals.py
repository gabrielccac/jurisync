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

from jurisync.core import certificate, credentials

CNJ = re.compile(r"^\d{7}-\d{2}\.\d{4}\.(\d)\.(\d{2})\.(\d{4})$")


# Earlier (Portuguese) secret names still accepted; the canonical names match the PJe connector's.
LEGACY_NAMES = {"USERNAME": ("USUARIO",), "PASSWORD": ("SENHA",), "2FA_DATA": ("EXPORT_DATA",)}


@dataclass(frozen=True)
class Portal:
    key: str  # unique within its system: "trf1-1g", "trf4-1g-sc"
    system: str  # "pje" | "eproc" | "projudi" | "esaj" | "stj" | "tucujuris" | "jpe"
    tribunal: str  # TRF1, TRF4, TRF5, TJRS, TJAP, STJ
    instance: str  # "1g" | "2g"
    host: str
    root: str  # application path: "/pje/", "/eprocV2/" ("/" when not wired)
    auth: str  # "password_totp" | "certificate_totp" | "certificate_mtls" | "certificate_challenge" | "browser_totp" | "password" | "none"
    env_prefix: str = ""
    legacy_env_prefixes: tuple[str, ...] = ()
    region: str = ""  # CNJ "TR" segment, e.g. "01"
    inherited: tuple[str, ...] = field(default=())  # other "TR" segments of cases moved here (TRF6: TRF1's MG cases)
    justice: str = "4"  # CNJ "J" segment: "4" federal (default), "8" state courts
    origins: tuple[str, ...] = field(default=())  # CNJ "OOOO" prefixes served by this portal
    wired: bool = True  # False: recognized, but no connector yet
    guess_by_number: bool = True  # False: never suggested from a process number alone (only a link host picks it)
    login_path: str = ""  # Projudi: the page that starts the login when the flavor's default entry is not it
    flavor: str = ""  # Projudi/e-SAJ generation (adapter), Eproc "infra-captcha" (TJTO): "tjgo-servlet" | "legacy-keycloak" | "legacy-local" | "cnj-servlet" | "kz" (PJe-JT REST) | "pje1x" (PJe 1.x)
    impersonate: str = ""  # curl_cffi profile override; "" keeps the global default (see core.http.IMPERSONATE)

    @property
    def pje1x(self):
        """PJe 1.x (TRF5's pje.trf5.jus.br): other panel (Acervo/Intimações tabs) and search form (consultarProcessoForm)."""
        return self.system == "pje" and self.flavor == "pje1x"

    @property
    def kz(self):
        """PJe-JT 2.x: the lawyer panel and process pages are the Angular app (PJe-KZ) over REST, not JSF."""
        return self.system == "pje" and self.flavor == "kz"

    def owns_number(self, parts):
        """Whether a parsed CNJ number (``parse_process_number``) can be in this portal.

        A superior court (region ``00``, e.g. TST) keeps the number of the court of origin, so any region of its
        branch of justice is accepted there; every other portal needs its own region or one it inherited cases from.
        """
        if self.region == "00":
            return parts[3] == self.justice
        return parts[4] == self.region or parts[4] in self.inherited

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
            entry = {"tjgo-servlet": "LogOn", "legacy-keycloak": "autenticacao.do"}  # the Keycloak entry starts the SSO
            return self.url(self.login_path or entry.get(self.flavor, "usuario/logon.do?actionType=inicio"))
        if self.system == "tucujuris":
            return self.url("pages/login/login.html")  # a static page; the login itself is a JSON POST to /api/
        if self.system == "jpe":
            return self.url("portaljus/intranet/principal.rupe?contexto=jpe")  # public page holding all three logins
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
        """Whether this customer logs in here with the A1 certificate: PJE_A1_P12 and its PIN are set and <PREFIX>_CERT
        is on."""
        return self.env("CERT").lower() in {"1", "true", "yes", "on"} and certificate.is_configured()

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
        # pje2g.trf5.jus.br is the "Turmas Recursais" PJe (the JEF appeals), NOT the Tribunal: the login page says so.
        # Same Keycloak SSO (client "pje-trf5-2g") and the same A1 certificate + authenticator as the 1G. Its appeals
        # keep the 1G number, so a number alone cannot tell the two apart (as in TRF1): DJEN's link host can.
        # The key stays "trf5-2g" (customers' portals.toml and PJE_TRF5_2G_* secrets use it); the Tribunal's 2nd
        # instance is "trf5-tru" (pjett) and the old PJe 1.x host is "trf5-legacy".
        Portal(
            "trf5-2g", "pje", "TRF5", "2g", "pje2g.trf5.jus.br", "/pje/", "certificate_totp", "PJE_TRF5_2G",
            legacy_env_prefixes=("PJE_TRF5",), region="05",
        ),
        # TRF3 (SP/MS) serves the same PJe under /pje/; the password flow is the default, the A1 certificate opts in.
        Portal("trf3-1g", "pje", "TRF3", "1g", "pje1g.trf3.jus.br", "/pje/", "password_totp", "PJE_TRF3_1G", region="03"),
        Portal("trf3-2g", "pje", "TRF3", "2g", "pje2g.trf3.jus.br", "/pje/", "password_totp", "PJE_TRF3_2G", region="03"),
        # TRF6 (MG) keeps its older cases in PJe beside the Eproc (trf6-1g/2g). No SSO: the login page carries
        # ASSINATURA_MENSAGEM, which the A1 signs (certificate._legacy_login), and no code is asked; after it, the
        # panel is the PJe 2.x one. Cases moved from the TRF1 keep its 4.01 numbers, and region 06 is the Eproc's too,
        # so only a link host or the customer's setup picks these two.
        Portal("trf6-pje-1g", "pje", "TRF6", "1g", "pje1g.trf6.jus.br", "/pje/", "certificate_totp", "TRF6_PJE_1G",
               region="06", inherited=("01",), guess_by_number=False),
        Portal("trf6-pje-2g", "pje", "TRF6", "2g", "pje2g.trf6.jus.br", "/pje/", "certificate_totp", "TRF6_PJE_2G",
               region="06", inherited=("01",), guess_by_number=False),
        # pjett.trf5.jus.br is "2º Grau e TRU": the Tribunal's 2nd instance AND the Turma Regional de Uniformização
        # (the page header says so). Own PJe host, same SSO and A1 certificate. The key "trf5-tru" is kept for
        # compatibility (customers' portals.toml and PJE_TRF5_TRU_* secrets). Its cases keep the JEF number, so only
        # the link host (DJEN) or the panel it appears in points here.
        Portal(
            "trf5-tru", "pje", "TRF5", "2g", "pjett.trf5.jus.br", "/pje/", "certificate_totp", "PJE_TRF5_TRU",
            legacy_env_prefixes=("PJE_TRF5",), region="05", guess_by_number=False,
        ),
        # pje.trf5.jus.br is the old PJe 1.x of the Tribunal (PJeOffice): no SSO and no TOTP. The login page carries
        # ASSINATURA_MENSAGEM, which the A1 certificate signs (certificate._legacy_login); "certificate_totp" is only the
        # family name, no code is asked. Only the host picks it.
        Portal(
            "trf5-legacy", "pje", "TRF5", "2g", "pje.trf5.jus.br", "/pje/", "certificate_totp", "PJE_TRF5_LEGACY",
            legacy_env_prefixes=("PJE_TRF5",), region="05", guess_by_number=False, flavor="pje1x",
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
            region="07", justice="5", flavor="kz",
        ),
        Portal(
            "trt7-2g", "pje", "TRT7", "2g", "pje.trt7.jus.br", "/segundograu/", "certificate_totp", "TRT7_2G",
            region="07", justice="5", flavor="kz",
        ),
        Portal(
            "trt4-1g", "pje", "TRT4", "1g", "pje.trt4.jus.br", "/primeirograu/", "certificate_totp", "TRT4_1G",
            region="04", justice="5", flavor="kz",
        ),
        Portal(
            "trt9-1g", "pje", "TRT9", "1g", "pje.trt9.jus.br", "/primeirograu/", "certificate_totp", "TRT9_1G",
            region="09", justice="5", flavor="kz",
        ),
        Portal(
            "trt2-1g", "pje", "TRT2", "1g", "pje.trt2.jus.br", "/primeirograu/", "certificate_totp", "TRT2_1G",
            region="02", justice="5", flavor="kz",
        ),
        Portal(
            "trt2-2g", "pje", "TRT2", "2g", "pje.trt2.jus.br", "/segundograu/", "certificate_totp", "TRT2_2G",
            region="02", justice="5", flavor="kz",
        ),
        Portal(
            "trt18-1g", "pje", "TRT18", "1g", "pje.trt18.jus.br", "/primeirograu/", "certificate_totp", "TRT18_1G",
            region="18", justice="5", flavor="kz",
        ),
        Portal(
            "trt18-2g", "pje", "TRT18", "2g", "pje.trt18.jus.br", "/segundograu/", "certificate_totp", "TRT18_2G",
            region="18", justice="5", flavor="kz",
        ),
        Portal(
            "trt12-1g", "pje", "TRT12", "1g", "pje.trt12.jus.br", "/primeirograu/", "certificate_totp", "TRT12_1G",
            region="12", justice="5", flavor="kz",
        ),
        Portal(
            "trt12-2g", "pje", "TRT12", "2g", "pje.trt12.jus.br", "/segundograu/", "certificate_totp", "TRT12_2G",
            region="12", justice="5", flavor="kz",
        ),
        Portal(
            "trt15-1g", "pje", "TRT15", "1g", "pje.trt15.jus.br", "/primeirograu/", "certificate_totp", "TRT15_1G",
            region="15", justice="5", flavor="kz",
        ),
        Portal(
            "trt15-2g", "pje", "TRT15", "2g", "pje.trt15.jus.br", "/segundograu/", "certificate_totp", "TRT15_2G",
            region="15", justice="5", flavor="kz",
        ),
        Portal(
            "trt1-1g", "pje", "TRT1", "1g", "pje.trt1.jus.br", "/primeirograu/", "certificate_totp", "TRT1_1G",
            region="01", justice="5", flavor="kz",
        ),
        Portal(
            "trt1-2g", "pje", "TRT1", "2g", "pje.trt1.jus.br", "/segundograu/", "certificate_totp", "TRT1_2G",
            region="01", justice="5", flavor="kz",
        ),
        Portal(
            "trt5-1g", "pje", "TRT5", "1g", "pje.trt5.jus.br", "/primeirograu/", "certificate_totp", "TRT5_1G",
            region="05", justice="5", flavor="kz",
        ),
        Portal(
            "trt5-2g", "pje", "TRT5", "2g", "pje.trt5.jus.br", "/segundograu/", "certificate_totp", "TRT5_2G",
            region="05", justice="5", flavor="kz",
        ),
        Portal(
            "trt20-1g", "pje", "TRT20", "1g", "pje.trt20.jus.br", "/primeirograu/", "certificate_totp", "TRT20_1G",
            region="20", justice="5", flavor="kz",
        ),
        Portal(
            "trt20-2g", "pje", "TRT20", "2g", "pje.trt20.jus.br", "/segundograu/", "certificate_totp", "TRT20_2G",
            region="20", justice="5", flavor="kz",
        ),
        Portal(
            "trt6-1g", "pje", "TRT6", "1g", "pje.trt6.jus.br", "/primeirograu/", "certificate_totp", "TRT6_1G",
            region="06", justice="5", flavor="kz",
        ),
        Portal(
            "trt6-2g", "pje", "TRT6", "2g", "pje.trt6.jus.br", "/segundograu/", "certificate_totp", "TRT6_2G",
            region="06", justice="5", flavor="kz",
        ),
        Portal(
            "trt10-1g", "pje", "TRT10", "1g", "pje.trt10.jus.br", "/primeirograu/", "certificate_totp", "TRT10_1G",
            region="10", justice="5", flavor="kz",
        ),
        Portal(
            "trt10-2g", "pje", "TRT10", "2g", "pje.trt10.jus.br", "/segundograu/", "certificate_totp", "TRT10_2G",
            region="10", justice="5", flavor="kz",
        ),
        Portal(
            "trt17-1g", "pje", "TRT17", "1g", "pje.trt17.jus.br", "/primeirograu/", "certificate_totp", "TRT17_1G",
            region="17", justice="5", flavor="kz",
        ),
        Portal(
            "trt17-2g", "pje", "TRT17", "2g", "pje.trt17.jus.br", "/segundograu/", "certificate_totp", "TRT17_2G",
            region="17", justice="5", flavor="kz",
        ),
        Portal(
            "trt19-1g", "pje", "TRT19", "1g", "pje.trt19.jus.br", "/primeirograu/", "certificate_totp", "TRT19_1G",
            region="19", justice="5", flavor="kz",
        ),
        Portal(
            "trt19-2g", "pje", "TRT19", "2g", "pje.trt19.jus.br", "/segundograu/", "certificate_totp", "TRT19_2G",
            region="19", justice="5", flavor="kz",
        ),
        Portal(
            "trt23-1g", "pje", "TRT23", "1g", "pje.trt23.jus.br", "/primeirograu/", "certificate_totp", "TRT23_1G",
            region="23", justice="5", flavor="kz",
        ),
        Portal(
            "trt23-2g", "pje", "TRT23", "2g", "pje.trt23.jus.br", "/segundograu/", "certificate_totp", "TRT23_2G",
            region="23", justice="5", flavor="kz",
        ),
        Portal(
            "trt24-1g", "pje", "TRT24", "1g", "pje.trt24.jus.br", "/primeirograu/", "certificate_totp", "TRT24_1G",
            region="24", justice="5", flavor="kz",
        ),
        Portal(
            "trt24-2g", "pje", "TRT24", "2g", "pje.trt24.jus.br", "/segundograu/", "certificate_totp", "TRT24_2G",
            region="24", justice="5", flavor="kz",
        ),
        Portal(
            "trt3-1g", "pje", "TRT3", "1g", "pje.trt3.jus.br", "/primeirograu/", "certificate_totp", "TRT3_1G",
            region="03", justice="5", flavor="kz",
        ),
        Portal(
            "trt3-2g", "pje", "TRT3", "2g", "pje.trt3.jus.br", "/segundograu/", "certificate_totp", "TRT3_2G",
            region="03", justice="5", flavor="kz",
        ),
        Portal(
            "tst", "pje", "TST", "2g", "pje.tst.jus.br", "/tst/", "certificate_totp", "TST",
            region="00", justice="5", guess_by_number=False, flavor="kz",
        ),
        # State courts from one office's map (hosts as listed there; CNJ J=8): the three Eprocs first, then the
        # PJes. Single-host PJes serve /pje/; TJRJ/TJPI split instances by path like TJCE/TJPE.
        Portal("tjmg-1g", "eproc", "TJMG", "1g", "eproc1g.tjmg.jus.br", "/eproc/", "browser_totp", "TJMG_1G",
               region="13", justice="8"),
        Portal("tjmg-2g", "eproc", "TJMG", "2g", "eproc2g.tjmg.jus.br", "/eproc/", "certificate_totp", "TJMG_2G",
               region="13", justice="8"),
        # TJBA's Eproc (being deployed beside its PJe): the classic login form with the certificate button, like TJMG. Not
        # validated yet: on 13/11/2026 the portal knew no user for the A1's CPF. Region 05 is the PJe's too.
        Portal("tjba-eproc-1g", "eproc", "TJBA", "1g", "eproc1g.tjba.jus.br", "/eproc/", "browser_totp", "TJBA_EPROC_1G",
               region="05", justice="8", guess_by_number=False),
        # TJMS also has e-SAJ; a process number alone cannot identify its eproc cases.
        Portal("tjms-eproc-1g", "eproc", "TJMS", "1g", "eproc1g.tjms.jus.br", "/eproc/", "certificate_totp", "TJMS_1G",
               region="12", justice="8", guess_by_number=False),
        Portal("tjrj-eproc-1g", "eproc", "TJRJ", "1g", "eproc1g.tjrj.jus.br", "/eproc/", "browser_totp", "TJRJ_1G",
               region="19", justice="8"),
        Portal("tjrj-eproc-2g", "eproc", "TJRJ", "2g", "eproc2g.tjrj.jus.br", "/eproc/", "browser_totp", "TJRJ_2G",
               region="19", justice="8"),
        Portal("tjpr-1g", "eproc", "TJPR", "1g", "eproc1g.tjpr.jus.br", "/eproc/", "certificate_totp", "TJPR_1G",
               region="16", justice="8"),
        Portal("tjpr-2g", "eproc", "TJPR", "2g", "eproc2g.tjpr.jus.br", "/eproc/", "certificate_totp", "TJPR_2G",
               region="16", justice="8"),
        Portal("tjrj-1g", "pje", "TJRJ", "1g", "tjrj.pje.jus.br", "/1g/", "certificate_totp", "TJRJ_PJE_1G",
               region="19", justice="8"),
        Portal("tjrj-2g", "pje", "TJRJ", "2g", "tjrj.pje.jus.br", "/2g/", "certificate_totp", "TJRJ_PJE_2G",
               region="19", justice="8"),
        Portal("tjmt-1g", "pje", "TJMT", "1g", "pje.tjmt.jus.br", "/pje/", "certificate_totp", "TJMT_1G",
               region="11", justice="8"),
        # TJMT 2G runs on its own host under /pje2/ (SSO client "pje-tjmt-2g"); an appeal keeps its number.
        Portal("tjmt-2g", "pje", "TJMT", "2g", "pje2.tjmt.jus.br", "/pje2/", "certificate_totp", "TJMT_2G",
               region="11", justice="8", guess_by_number=False),
        Portal("tjdft-1g", "pje", "TJDFT", "1g", "pje.tjdft.jus.br", "/pje/", "certificate_totp", "TJDFT_1G",
               region="07", justice="8"),
        # TJDFT 2G: own host, same PDPJ SSO (client "pje-tjdft-2g"). An appeal keeps its number, so only the customer's
        # setup or a link host picks it.
        Portal("tjdft-2g", "pje", "TJDFT", "2g", "pje2i.tjdft.jus.br", "/pje/", "certificate_totp", "TJDFT_2G",
               region="07", justice="8", guess_by_number=False),
        Portal("tjpa-1g", "pje", "TJPA", "1g", "pje.tjpa.jus.br", "/pje/", "certificate_totp", "TJPA_1G",
               region="14", justice="8"),
        # TJMG runs PJe (juizados) beside its Eproc; same SSO (client "pje-tjmg-1g"). Region 13 is Eproc's too, so
        # only a link host or the customer's setup picks this one.
        Portal("tjmg-pje-1g", "pje", "TJMG", "1g", "pje.tjmg.jus.br", "/pje/", "password_totp", "TJMG_PJE_1G",
               region="13", justice="8", guess_by_number=False),
        # The Turmas Recursais of the TJMG juizados run their own PJe (SSO client "pje-tjmg-2g"); an appeal keeps the
        # 1G number, so only a link host or the customer's setup picks it.
        Portal("tjmg-pje-tr", "pje", "TJMG", "2g", "pjerecursal.tjmg.jus.br", "/pje/", "password_totp", "TJMG_PJE_TR",
               region="13", justice="8", guess_by_number=False),
        Portal("tjma-1g", "pje", "TJMA", "1g", "pje.tjma.jus.br", "/pje/", "certificate_totp", "TJMA_1G",
               region="10", justice="8"),
        # TJES 1G sits behind an AWS WAF image CAPTCHA (HTTP 405); the login solves it (core/awswaf.py).
        Portal("tjes-1g", "pje", "TJES", "1g", "pje.tjes.jus.br", "/pje/", "certificate_totp", "TJES_1G",
               region="08", justice="8"),
        Portal("tjes-2g", "pje", "TJES", "2g", "pje.tjes.jus.br", "/pje2g/", "certificate_totp", "TJES_2G",
               region="08", justice="8"),
        Portal("tjba-1g", "pje", "TJBA", "1g", "pje.tjba.jus.br", "/pje/", "certificate_totp", "TJBA_1G",
               region="05", justice="8"),
        # TJBA 2G: own host, same PDPJ SSO and login flow as the 1G. An appeal keeps its number, so only the
        # customer's setup or a link host picks it.
        Portal("tjba-2g", "pje", "TJBA", "2g", "pje2g.tjba.jus.br", "/pje/", "certificate_totp", "TJBA_2G",
               region="05", justice="8", guess_by_number=False),
        Portal("tjpb-1g", "pje", "TJPB", "1g", "pje.tjpb.jus.br", "/pje/", "certificate_totp", "TJPB_1G",
               region="15", justice="8"),
        Portal("tjpi-1g", "pje", "TJPI", "1g", "pje.tjpi.jus.br", "/1g/", "certificate_totp", "TJPI_1G",
               region="18", justice="8"),
        Portal("tjpi-2g", "pje", "TJPI", "2g", "pje.tjpi.jus.br", "/2g/", "certificate_totp", "TJPI_2G",
               region="18", justice="8"),
        Portal("tjrn-1g", "pje", "TJRN", "1g", "pje1g.tjrn.jus.br", "/pje/", "certificate_totp", "TJRN_1G",
               region="20", justice="8"),
        Portal("tjrn-2g", "pje", "TJRN", "2g", "pje2g.tjrn.jus.br", "/pje/", "certificate_totp", "TJRN_2G",
               region="20", justice="8"),
        # TJRO: pjepg = 1º grau (SSO client pje-tjro-1g), pjesg = 2º grau (pje-tjro-2g); pje.tjro.jus.br only redirects.
        Portal("tjro-1g", "pje", "TJRO", "1g", "pjepg.tjro.jus.br", "/pje/", "certificate_totp", "TJRO_1G",
               region="22", justice="8"),
        Portal("tjro-2g", "pje", "TJRO", "2g", "pjesg.tjro.jus.br", "/pje/", "certificate_totp", "TJRO_2G",
               region="22", justice="8"),
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
        # TJRS 2G: same Keycloak (keycloak-eks.tjrs.jus.br) and account as the 1G, reached through the national broker.
        Portal("tjrs-2g", "eproc", "TJRS", "2g", "eproc2g.tjrs.jus.br", "/eproc/", "browser_totp", "TJRS_2G",
               region="21", justice="8"),
        # State-court Eprocs below are registered for Denis (authenticator profiles
        # Eproc/TJSC, Eproc/TJTO, Eproc/TNU, Eproc/TJSP); the Eproc connector serves them.
        Portal("tjsc-1g", "eproc", "TJSC", "1g", "eproc1g.tjsc.jus.br", "/eproc/", "browser_totp", "TJSC_1G",
               region="24", justice="8"),
        Portal("tjsc-2g", "eproc", "TJSC", "2g", "eproc2g.tjsc.jus.br", "/eproc/", "browser_totp", "TJSC_2G",
               region="24", justice="8"),
        # TJAC's Eproc (CNJ J=8, TR=01): Keycloak SSO (sso.tjac.jus.br, realm eproc) with the certificate on
        # certificado-sso.tjac.jus.br, then the authenticator code. The 2FA profile is the one "Eproc/TJAC",
        # shared by both instances (TJAC_EPROC_*).
        Portal("tjac-1g", "eproc", "TJAC", "1g", "eproc1g.tjac.jus.br", "/eproc/", "browser_totp", "TJAC_1G",
               legacy_env_prefixes=("TJAC_EPROC",), region="01", justice="8"),
        Portal("tjac-2g", "eproc", "TJAC", "2g", "eproc2g.tjac.jus.br", "/eproc/", "browser_totp", "TJAC_2G",
               legacy_env_prefixes=("TJAC_EPROC",), region="01", justice="8"),
        # TJTO: local login form; the password is followed by an Infra image CAPTCHA (eproc.http_login answers it) and
        # the certificate button by a separate mtls.tjto.jus.br host (eproc.certificate).
        Portal("tjto-1g", "eproc", "TJTO", "1g", "eproc1.tjto.jus.br", "/eprocV2_prod_1grau/", "browser_totp", "TJTO_1G",
               region="27", justice="8", flavor="infra-captcha"),
        Portal("tjto-2g", "eproc", "TJTO", "2g", "eproc2.tjto.jus.br", "/eprocV2_prod_2grau/", "browser_totp", "TJTO_2G",
               region="27", justice="8", flavor="infra-captcha"),
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
               "TJPR_PROJUDI_1G", region="16", justice="8", guess_by_number=False,
               flavor="legacy-keycloak"),
        Portal("tjrr-1g", "projudi", "TJRR", "1g", "projudi.tjrr.jus.br", "/projudi/", "password_totp", "TJRR_1G",
               legacy_env_prefixes=("TJRR_PROJUDI",), region="23", justice="8", wired=False, guess_by_number=False,
               flavor="legacy-keycloak", login_path="tjpr/autenticacao.do"),
        # TJAM: validated live on 2026-11-16 (login + desk + search + process + files + one PDF).
        Portal("tjam-1g", "projudi", "TJAM", "1g", "projudi.tjam.jus.br", "/projudi/", "password", "TJAM_1G",
               legacy_env_prefixes=("TJAM_PROJUDI",), region="04", justice="8", guess_by_number=False,
               flavor="legacy-local"),
        # TJBA Projudi: CNJ 1.x servlets, local form behind a Tencent CAPTCHA that the login solves
        # (core/tencent_captcha.py). Login observed live on 2026-11-23, but no account got in (expired / refused
        # passwords), so the desk is unmapped and the portal stays unwired. Not TJBA's PJe (tjba-1g).
        Portal("tjba-projudi-1g", "projudi", "TJBA", "1g", "projudi.tjba.jus.br", "/projudi/", "password",
               "TJBA_PROJUDI", region="05", justice="8", wired=False, guess_by_number=False, flavor="cnj-servlet",
               login_path="PaginaPrincipal.jsp"),
        # e-SAJ (Softplan): the CAS (/sajcas) asks the A1 certificate to sign a challenge in the login form, with no
        # mutual TLS and no authenticator. Keys carry "esaj" because TJSP already has an Eproc portal (tjsp-1g).
        Portal("tjsp-esaj", "esaj", "TJSP", "1g", "esaj.tjsp.jus.br", "/", "certificate_challenge", "TJSP_ESAJ",
               region="26", justice="8", guess_by_number=False, flavor="esaj-cas"),
        Portal("tjms-esaj", "esaj", "TJMS", "1g", "esaj.tjms.jus.br", "/", "certificate_challenge", "TJMS_ESAJ",
               region="12", justice="8", flavor="esaj-cas"),
        # TJAL's e-SAJ lives on www2.tjal.jus.br (portal at /esaj/portal.do; CAS, consultation and mailbox at the root,
        # like TJSP); same certificate-challenge login with the A1.
        Portal("tjal-esaj", "esaj", "TJAL", "1g", "www2.tjal.jus.br", "/", "certificate_challenge", "TJAL_ESAJ",
               region="02", justice="8", flavor="esaj-cas"),
        # TJAM's e-SAJ (old case stock) lives on consultasaj.tjam.jus.br; the CAS ticket redirect also goes to http://
        # on that host. Same A1 certificate-challenge login. Current cases are in Projudi/PJe, so no guessing by number.
        Portal("tjam-esaj", "esaj", "TJAM", "1g", "consultasaj.tjam.jus.br", "/", "certificate_challenge", "TJAM_ESAJ",
               region="04", justice="8", guess_by_number=False, flavor="esaj-cas"),
        # Tucujuris: TJAP's own system (CNJ J=8, TR=03), a Knockout SPA over a JSON API at /api/ behind Cloudflare.
        # Login is user + password, then the authenticator code; advogados log in with OAB number + seccional
        # ("1234AP"), other profiles with CPF digits or e-mail. TJAP also runs a PJe (tjap-1g) whose numbers have the
        # same shape, so a number alone never points here; only a tucujuris.tjap.jus.br link does.
        Portal("tjap-tucujuris", "tucujuris", "TJAP", "1g", "tucujuris.tjap.jus.br", "/", "password_totp",
               "TJAP_TUCUJURIS", region="03", justice="8", guess_by_number=False),
        # JPe (Themis, "Portal do Processo Eletrônico"): the TJMG's own 2nd-instance system, JSF 1.2 + RichFaces 3.3
        # pages ending in .rupe under /rupe/. Two logins are wired, picked by configuration: the JPe's own Login (not the
        # CPF) + password at j_security_check, or the PDPJ SSO (client "jpe-tjmg-2g", CPF + password + TOTP), which
        # on 19/11/2026 the SSO itself refused (HTTP 400 "redirect_uri"). TJMG_THEMIS_2G is the name customers' .env
        # files used first. 2nd-instance numbers keep the origin's shape (CNJ origin 0000 for appeals filed there,
        # but TJMG's Eproc 2G holds the newer ones), so only a jpe.tjmg.jus.br link or the setup picks this portal.
        Portal("tjmg-jpe-2g", "jpe", "TJMG", "2g", "jpe.tjmg.jus.br", "/rupe/", "password_totp", "TJMG_JPE_2G",
               legacy_env_prefixes=("TJMG_THEMIS_2G",), region="13", justice="8", guess_by_number=False),
        # STJ: the lawyer's area is the CPE (cpe.web.stj.jus.br, REST under /api/). The A1 logs in over mutual TLS at
        # cpe-auth.web.stj.jus.br/api/auth/cert with no authenticator; password logins add TOTP/e-mail MFA and gov.br is
        # OAuth. The parent domain as host claims every STJ link (processo., ww2., cpe.web.). CNJ J=3, TR=00.
        # The connector (connectors/stj) calls the CPE hosts directly.
        Portal("stj", "stj", "STJ", "2g", "stj.jus.br", "/", "certificate_mtls", "STJ", region="00", justice="3"),
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


def stj(key):
    return get(key, "stj")


def tucujuris(key):
    return get(key, "tucujuris")


def jpe(key):
    return get(key, "jpe")


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
