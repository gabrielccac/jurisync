"""The PJe draft client: one object composed from the per-section mixins."""

from jurisync.connectors.pje.filing.author import AuthorMixin
from jurisync.connectors.pje.filing.characteristics import CharacteristicsMixin
from jurisync.connectors.pje.filing.classification import ClassificationMixin
from jurisync.connectors.pje.filing.documents import DocumentsMixin
from jurisync.connectors.pje.filing.fill import FillMixin
from jurisync.connectors.pje.filing.parties import PartiesMixin
from jurisync.connectors.pje.filing.pending import PendingMixin
from jurisync.connectors.pje.filing.representative import RepresentativeMixin
from jurisync.connectors.pje.filing.subjects import SubjectsMixin
from jurisync.connectors.pje.filing.transport import Transport


class DraftClient(
    FillMixin,
    DocumentsMixin,
    CharacteristicsMixin,
    PartiesMixin,
    AuthorMixin,
    RepresentativeMixin,
    SubjectsMixin,
    PendingMixin,
    ClassificationMixin,
    Transport,
):
    """Create and fill a PJe 1st-degree draft. It has no protocol submission action."""
