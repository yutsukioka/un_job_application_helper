"""Cooperative inner verification budgets, below the process supervisor."""

from contextlib import contextmanager
import gzip
from pathlib import Path
import time

from jobagg.publication_deadline import entry_deadlines, expired


class VerificationBudgetExceeded(TimeoutError):
    pass


def check():
    if expired(None):
        raise VerificationBudgetExceeded("local verification deadline exhausted")


@contextmanager
def scope(deadline_at=None, deadline_epoch=None, *, check_after=True):
    if deadline_at is None:
        if deadline_epoch is None:
            check()
            yield
            if check_after:
                check()
            return
        deadline_at = time.monotonic() + max(0, deadline_epoch - time.time())
    wall = time.time() + max(0, deadline_at - time.monotonic())
    if deadline_epoch is not None:
        wall = min(wall, deadline_epoch)
    with entry_deadlines(deadline_at, wall):
        check()
        yield
        if check_after:
            check()


def read(path, *, compressed=False, maximum=None):
    """Chunk decompression/read; caller retains its original content/hash checks."""
    check()
    chunks = []
    size = 0
    with gzip.open(Path(path), "rb") if compressed else Path(path).open("rb") as stream:
        while True:
            block = stream.read(
                min(1024 * 1024, maximum + 1 - size) if maximum is not None else 1024 * 1024
            )
            check()
            if not block:
                break
            chunks.append(block)
            size += len(block)
            if maximum is not None and size > maximum:
                break
    check()
    return b"".join(chunks)
