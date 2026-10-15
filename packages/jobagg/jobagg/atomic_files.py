"""Small cross-platform helpers for locked atomic file updates."""

from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from pathlib import Path
from typing import BinaryIO, Iterator


_FILE_WRITE_GUARD = ContextVar("jobagg_file_write_guard", default=None)


@contextmanager
def admitted_file_writes(guard):
    """Scope a pre-write admission callback to this thread/context.

    The callback receives (absolute target path, encoded byte length) and returns
    a context manager. Its grant covers the replacement temporary until handles
    close. Callers must explicitly propagate this scope into worker threads.
    Direct binary/SQLite writes need their own admitted boundaries.
    """
    token = _FILE_WRITE_GUARD.set(guard)
    try:
        yield
    finally:
        _FILE_WRITE_GUARD.reset(token)


@contextmanager
def locked_path(path: str | Path) -> Iterator[None]:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.with_name(f"{target.name}.lock")
    with lock_path.open("a+b") as lock_file:
        _lock_file(lock_file)
        try:
            yield
        finally:
            _unlock_file(lock_file)


def atomic_write_text(path: str | Path, text: str, *, encoding: str = "utf-8") -> None:
    atomic_write_bytes(path, text.encode(encoding))


def atomic_write_bytes(path: str | Path, payload: bytes) -> None:
    """Admit exact bytes before any target/temp creation; retain exact newlines."""
    if not isinstance(payload, bytes):
        raise TypeError("atomic payload must be bytes")
    target = Path(path)
    guard = _FILE_WRITE_GUARD.get()
    with (guard(target.absolute(), len(payload)) if guard else nullcontext()):
        _atomic_write_bytes(target, payload)


def _atomic_write_bytes(target: Path, payload: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = ""
    try:
        fd, temp_path = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, target)
        _fsync_directory(target.parent)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


def _lock_file(lock_file: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        lock_file.seek(0)
        if not lock_file.read(1):
            lock_file.write(b"\0")
            lock_file.flush()
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
        return

    import fcntl

    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)


def _unlock_file(lock_file: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
