"""Which PJe portals the draft filing knows, and what each one allows.

The draft flow (classification, parties, characteristics, documents, Não Protocolados) is PJe's own and the same code
drives it on every portal; a portal's entry here says where it lives and whether drafts may be written there. A portal
may write before every step has been seen live, but then `unverified` names those steps: each one still reads the
draft back after acting and fails with PJe's own message when the result is not there, and runs report the list so
the first real filing is watched step by step.
"""

from dataclasses import dataclass

from jurisync import portals


@dataclass(frozen=True)
class FilingPortal:
    key: str
    writes: bool
    note: str = ""
    unverified: tuple = ()

    @property
    def portal(self):
        return portals.pje(self.key)

    @property
    def register_url(self):
        return self.portal.base_url + "Processo/cadastrar.seam"

    @property
    def update_url(self):
        return self.portal.base_url + "Processo/update.seam"

    @property
    def pending_url(self):
        return self.portal.base_url + "Processo/ConsultaProcessoNaoProtocolado/listView.seam"


FILING_PORTALS = {
    "trf5-1g": FilingPortal("trf5-1g", writes=True),
    "trf1-1g": FilingPortal(
        "trf1-1g",
        writes=True,
        note="surveyed read-only (docs/connectors/pje-trf1-filing-recon.md); the steps after Incluir follow the TRF5 "
        "flow and have not run on TRF1 yet",
        unverified=(
            "Incluir (create the draft) and the fields PJe 2.13 may ask for after the class",
            "subject association (selecting a search result)",
            "author lookup and insertion",
            "extra attorney association",
            "INSS in the passive pole",
            "characteristics and priority save",
            "main petition upload in PDF mode",
            "supporting uploads, their type list and size limit",
        ),
    ),
}
DEFAULT_PORTAL = "trf5-1g"


def filing_portal(key):
    if key not in FILING_PORTALS:
        raise ValueError(f"Draft filing does not support {key}; supported: {', '.join(sorted(FILING_PORTALS))}")
    return FILING_PORTALS[key]


def require_writes(config):
    if not config.writes:
        raise ValueError(f"Draft writes are not enabled for {config.key} yet ({config.note})")
