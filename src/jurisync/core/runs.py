"""Where a filing run lives. A run is shared by the filing workflow and the portal connector that files it."""

from jurisync.core import credentials
from jurisync.paths import user_data


def run_directory(run_id):
    """The folder of one filing run. Every server and tool that touches a run must use this same root.

    On the hosted server runs live under the office's own subfolder.
    """
    root = user_data("filings", "JURISYNC_FILING_DIR").resolve()
    tenant = credentials.tenant()
    return (root / "tenants" / tenant if tenant else root) / run_id
