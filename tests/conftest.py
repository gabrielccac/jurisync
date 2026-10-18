"""Keep every test away from the user's real filing data: runs and inputs go to a throwaway folder by default."""

import os
import tempfile

_FILING = tempfile.TemporaryDirectory(prefix="jurisync-test-filing-")
os.environ.setdefault("JURISYNC_FILING_DIR", _FILING.name)
