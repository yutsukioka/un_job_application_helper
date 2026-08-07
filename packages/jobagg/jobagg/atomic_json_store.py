"""Cross-platform locked and atomic JSON store transactions."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO, Generic, TypeVar

if os.name == "nt":
    import msvcrt
else:
    import fcntl


MAX_JSON_STORE_BYTES = 16 * 1024 * 1024

Document = TypeVar("Document")
Outcome = TypeVar("Outcome")


class AtomicJsonStoreError(ValueError):
    """Fixed, private-free JSON store failure."""


class AtomicJsonStore(Generic[Document]):
    """Serialize one JSON target's reads and read-modify-write transactions."""

    def __init__(
        self,
        path: str | Path,
        *,
        default_factory: Callable[[], Document],
        validator: Callable[[Any], None],
        encoder: Callable[[Document], bytes],
        maximum_bytes: int = MAX_JSON_STORE_BYTES,
    ) -> None:
        self.path = Path(path)
        self._default_factory = default_factory
        self._validator = validator
        self._encoder = encoder
        self._maximum_bytes = maximum_bytes

    def read(self) -> Document:
        with _exclusive_json_lock(self.path):
            return self._read_unlocked()

    def mutate(
        self,
        mutation: Callable[[Document], tuple[Outcome, bool]],
    ) -> Outcome:
        with _exclusive_json_lock(self.path):
            document = self._read_unlocked()
            outcome, changed = mutation(document)
            if changed:
                self._write_unlocked(document)
            return outcome

    def _read_unlocked(self) -> Document:
        if not self.path.exists():
            return self._default_factory()
        if not self.path.is_file():
            raise AtomicJsonStoreError("JSON store is invalid.")
        try:
            if self.path.stat().st_size > self._maximum_bytes:
                raise AtomicJsonStoreError("JSON store exceeds the size limit.")
            payload = self.path.read_bytes()
        except AtomicJsonStoreError:
            raise
        except OSError:
            raise AtomicJsonStoreError("JSON store read failed.") from None
        if not payload or len(payload) > self._maximum_bytes:
            raise AtomicJsonStoreError("JSON store is invalid.")
        try:
            document = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise AtomicJsonStoreError("JSON store is invalid.") from None
        try:
            self._validator(document)
        except AtomicJsonStoreError:
            raise
        except (TypeError, ValueError):
            raise AtomicJsonStoreError("JSON store is invalid.") from None
        return document

    def _write_unlocked(self, document: Document) -> None:
        try:
            self._validator(document)
            payload = self._encoder(document)
        except AtomicJsonStoreError:
            raise
        except (TypeError, ValueError):
            raise AtomicJsonStoreError("JSON store is invalid.") from None
        if not isinstance(payload, bytes) or not payload or len(payload) > self._maximum_bytes:
            raise AtomicJsonStoreError("JSON store exceeds the size limit.")

        temporary_path: Path | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, raw_temporary_path = tempfile.mkstemp(
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
            )
            temporary_path = Path(raw_temporary_path)
            with os.fdopen(descriptor, "wb") as temporary_file:
                remaining = memoryview(payload)
                while remaining:
                    written = temporary_file.write(remaining)
                    if written is None or written <= 0:
                        raise OSError("incomplete write")
                    remaining = remaining[written:]
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, self.path)
            temporary_path = None
            if self.path.read_bytes() != payload:
                raise OSError("read-back mismatch")
            _fsync_parent_directory(self.path.parent)
        except (OSError, ValueError):
            raise AtomicJsonStoreError("JSON store write failed.") from None
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass


@contextmanager
def _exclusive_json_lock(target: Path) -> Any:
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        lock_path = target.with_name(f"{target.name}.mutation.lock")
        with lock_path.open("a+b") as lock_file:
            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()
                os.fsync(lock_file.fileno())
            lock_file.seek(0)
            _acquire_platform_lock(lock_file)
            try:
                yield
            finally:
                _release_platform_lock(lock_file)
    except AtomicJsonStoreError:
        raise
    except OSError:
        raise AtomicJsonStoreError("JSON store lock failed.") from None


def _acquire_platform_lock(lock_file: BinaryIO) -> None:
    lock_file.seek(0)
    if os.name == "nt":
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
    else:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)


def _release_platform_lock(lock_file: BinaryIO) -> None:
    lock_file.seek(0)
    if os.name == "nt":
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _fsync_parent_directory(parent: Path) -> None:
    if os.name != "posix":
        return
    descriptor: int | None = None
    try:
        descriptor = os.open(parent, os.O_RDONLY)
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
