"""Run Jean's Juridiq publication pipeline with a caller-supplied agent.

State lives under <state dir>/data/customers/jean/ (``jurisync.paths.STATE_DIR``), isolated by Juridiq account and
profile. The account is the one in the current credential scope: Jean's .env locally, the office's vault hosted.
"""

import hashlib
from pathlib import Path

from jurisync.core import credentials
from jurisync.paths import STATE_DIR as JURISYNC_STATE_DIR
from jurisync.workflows.publications.pipeline import run_publication_pipeline

from .profile import PROFILE

STATE_DIR = JURISYNC_STATE_DIR / "data" / "customers" / "jean"


def run_jean_publications(agent, *, state_dir=STATE_DIR, **options):
    account = credentials.env("JURIDIQ_EMAIL").strip().lower()
    if not account:
        raise RuntimeError("JURIDIQ_EMAIL is required to isolate workflow state")
    account_id = hashlib.sha256(account.encode()).hexdigest()[:16]
    state_file = Path(state_dir).resolve() / f"juridiq-{account_id}-{PROFILE['id']}.json"
    return run_publication_pipeline(agent=agent, profile=PROFILE, state_file=state_file, **options)
