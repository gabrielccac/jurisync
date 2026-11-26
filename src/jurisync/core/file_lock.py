"""An exclusive lock between processes on a state file, for read-modify-write cycles (polls, pipelines)."""

import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path


class LockBusy(TimeoutError):
    """Another process kept the lock for longer than the caller waits."""


if sys.platform == "win32":
    import msvcrt

    def _try_lock(handle):
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(handle):
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _try_lock(handle):
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(handle):
        fcntl.flock(handle, fcntl.LOCK_UN)


@contextmanager
def locked(path, *, wait=0.0, poll=0.2, clock=time.monotonic, sleep=time.sleep):
    """Hold ``<path>.lock`` exclusively; wait up to ``wait`` seconds for another holder, then raise ``LockBusy``."""
    lock = Path(f"{path}.lock")
    lock.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    deadline = clock() + wait
    with open(lock, "a+b") as handle:
        while True:
            try:
                _try_lock(handle)
                break
            except OSError:
                if clock() >= deadline:
                    raise LockBusy(f"another process is using {os.path.basename(path)}") from None
                sleep(poll)
        try:
            yield
        finally:
            _unlock(handle)
