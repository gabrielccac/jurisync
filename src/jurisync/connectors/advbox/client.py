"""Named ADVBOX operations: listings, reports, workflows, and guarded task writes.

``AdvboxClient`` is assembled from one mixin per responsibility: ``reads`` (listings and reports), ``writes`` (office
lock, file staging, the task POST), ``tasks`` (task writes) and ``stages`` (lawsuit stage); the request builders live
in ``queries``. This module re-exports their names for the callers that import ``client``. A test that replaces a
name patches the module that uses it.
"""

from jurisync.connectors.advbox.parsers import BASE_URL
from jurisync.connectors.advbox.queries import (
    ACTIVITY_COLUMNS,
    ACTIVITY_SORTABLE,
    BRAZIL,
    COMPLETED_VALUES,
    DEFAULT_STEPS,
    PRIORITIES,
    PUBLICATION_COLUMNS,
    PUBLICATION_PAGE_SIZE_MAX,
    PUBLICATION_PERIODS,
    PUBLICATION_SORTABLE,
    TABLES,
    _js_bool,
    build_activities_body,
    build_publications_query,
    positive_integer,
    publication_dates,
    resolve_court,
    table_path,
)
from jurisync.connectors.advbox.reads import ReadsMixin, _compact_task
from jurisync.connectors.advbox.session import AdvboxSession
from jurisync.connectors.advbox.stages import LAWSUIT_READ_TRIES, LAWSUIT_READ_WAIT, StagesMixin
from jurisync.connectors.advbox.tasks import DEDUPE_KEY, MAX_DEDUPE_FORM_READS, TasksMixin, _with_marker, dedupe_marker
from jurisync.connectors.advbox.writes import UnconfirmedWriteError, WritesMixin, _plain

__all__ = [
    "AdvboxClient",
    "DEFAULT_STEPS",
    "TABLES",
    "ACTIVITY_COLUMNS",
    "ACTIVITY_SORTABLE",
    "_js_bool",
    "positive_integer",
    "table_path",
    "COMPLETED_VALUES",
    "PRIORITIES",
    "build_activities_body",
    "PUBLICATION_PERIODS",
    "PUBLICATION_PAGE_SIZE_MAX",
    "PUBLICATION_COLUMNS",
    "PUBLICATION_SORTABLE",
    "BRAZIL",
    "publication_dates",
    "build_publications_query",
    "resolve_court",
    "_compact_task",
    "UnconfirmedWriteError",
    "_plain",
    "DEDUPE_KEY",
    "MAX_DEDUPE_FORM_READS",
    "dedupe_marker",
    "_with_marker",
    "LAWSUIT_READ_TRIES",
    "LAWSUIT_READ_WAIT",
]


class AdvboxClient(ReadsMixin, WritesMixin, TasksMixin, StagesMixin):
    def __init__(self, session=None):
        self.session = session or AdvboxSession()
        self._courts = None

    def close(self):
        self.session.close()

    def ensure_session(self):
        self.session.using(lambda s: s.get_json(table_path("customers", page=1, pageSize=1)))
        return {"authenticated": True, "url": f"{BASE_URL}/"}
