import os
import shutil
import tempfile

import pytest


@pytest.fixture(autouse=True, scope="package")
def temp_files_in_memory():
    """The hosted Store is SQLite with an fsync per statement; on tmpfs that costs nothing, so tests stay fast."""
    if not (os.path.isdir("/dev/shm") and os.access("/dev/shm", os.W_OK)):
        yield
        return
    saved = tempfile.tempdir
    tempfile.tempdir = tempfile.mkdtemp(prefix="jurisync-tests-", dir="/dev/shm")
    try:
        yield
    finally:
        shutil.rmtree(tempfile.tempdir, ignore_errors=True)
        tempfile.tempdir = saved
