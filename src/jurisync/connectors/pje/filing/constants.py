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
    # PJe 2.13 (TRF1) opens "Associar parte ao processo" on a "Tipo da Parte" select before the pre-registration form;
    # these are its labels for the author and for the defendant (seen live on 2026-10-18).
    party_roles: dict | None = None
    # Party details PJe must have filled from the Receita lookup before the author is inserted. TRF1 (PJe 2.13) shows
    # mother's name and birth date only as labels there and its Receita table came back almost empty (2026-10-18), so
    # identity rests on the looked-up name and, after insertion, the name and CPF in the active pole.
    author_required_details: tuple = (
        "nomeGenitoraDecoration:nomeGenitora",
        "dataNascimentoDecoration:dataNascimentoInputDate",
    )

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
        note="two supervised drafts completed every phase (15508678 and 15508705, October 2026); the paths below have "
        "not run on TRF1 yet",
        unverified=(
            "registering a new address for the author (both drafts reused an address PJe already had)",
            "extra attorney association",
        ),
        party_roles={"active": "AUTOR", "passive": "REU"},
        author_required_details=(),
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
