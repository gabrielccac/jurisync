"""The per-session process memo (search and KZ) forgets expired processes and keeps a bounded number."""

from jurisync.connectors.pje import kz


def test_expired_and_oldest_processes_are_dropped():
    entries = {}
    kz.memo_store(entries, "old", (0.0, "a"))
    kz.memo_store(entries, "fresh", (kz.MEMO_SECONDS, "b"))
    kz.memo_store(entries, "new", (kz.MEMO_SECONDS + 1, "c"))
    assert list(entries) == ["fresh", "new"]
    for n in range(kz.MEMO_PROCESSES + 5):
        kz.memo_store(entries, str(n), (kz.MEMO_SECONDS + 2, n))
    assert len(entries) == kz.MEMO_PROCESSES
    assert list(entries)[-1] == str(kz.MEMO_PROCESSES + 4)
    kz.memo_store(entries, "5", (kz.MEMO_SECONDS + 3, "again"))  # a process read again becomes the newest
    assert list(entries)[-1] == "5" and entries["5"][1] == "again"
