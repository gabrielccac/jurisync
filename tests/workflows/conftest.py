import pytest


@pytest.fixture(autouse=True)
def no_selected_customer(monkeypatch):
    """Tests choose their own customer; a JURISYNC_CUSTOMER in the developer's shell must not narrow the profiles."""
    monkeypatch.delenv("JURISYNC_CUSTOMER", raising=False)
