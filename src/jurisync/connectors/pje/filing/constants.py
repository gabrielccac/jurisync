"""Which PJe portals the draft filing knows, and what each one allows.

The draft flow (classification, parties, characteristics, documents, Não Protocolados) is PJe's own and the same code
drives it on every portal; a portal's entry here says where it lives and whether drafts may be written there. A portal
is only switched to `writes=True` once its flow has been checked against the live site.
"""

from dataclasses import dataclass

from jurisync import portals


@dataclass(frozen=True)
class FilingPortal:
    key: str
    writes: bool
    note: str = ""

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
        writes=False,
        note="read-only: the classification form was surveyed (docs/connectors/pje-trf1-filing-recon.md); "
        "draft writes wait for a test draft",
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
