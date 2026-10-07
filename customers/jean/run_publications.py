"""Run Jean's Juridiq publication pipeline with a caller-supplied agent.

State lives under data/customers/jean/, isolated by Juridiq account and profile.
"""

import hashlib
import os
from pathlib import Path

from jurisync.workflows.publications.pipeline import run_publication_pipeline

from .profile import PROFILE

STATE_DIR = Path(__file__).resolve().parents[2] / "data" / "customers" / "jean"


def run_jean_publications(agent, *, state_dir=STATE_DIR, **options):
    account = (os.environ.get("JURIDIQ_EMAIL") or "").strip().lower()
    if not account:
        raise RuntimeError("JURIDIQ_EMAIL is required to isolate workflow state")
    account_id = hashlib.sha256(account.encode()).hexdigest()[:16]
    state_file = Path(state_dir).resolve() / f"juridiq-{account_id}-{PROFILE['id']}.json"
    return run_publication_pipeline(agent=agent, profile=PROFILE, state_file=state_file, **options)
