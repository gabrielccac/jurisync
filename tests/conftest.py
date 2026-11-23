import pytest

from jurisync.hosted import store
from jurisync.install import credentials_bundle


@pytest.fixture(autouse=True, scope="session")
def cheap_scrypt():
    """Production scrypt costs 40-80 ms per hash; tests only need the same code path, not the same cost."""
    saved = store.SCRYPT["n"], credentials_bundle.SCRYPT_N
    store.SCRYPT["n"] = credentials_bundle.SCRYPT_N = 2**4
    yield
    store.SCRYPT["n"], credentials_bundle.SCRYPT_N = saved
