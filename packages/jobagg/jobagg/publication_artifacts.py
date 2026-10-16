"""Exact export bytes backed by shared immutable, bounded record chunks.

Only generation evidence is indirect. Consumer-facing live exports remain plain
files, on distinct inodes. A manifest is never returned as export content.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import stat
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

CHUNK_BYTES = 1024 * 1024
DIRECTORIES = {"exports-v2", "before_exports", "before_exports_verified"}


def sidecar(path):
    return Path(str(path) + ".publication.json")


def _direct(path):
    path = Path(path).absolute()
    if path.resolve() != path or (path.exists() and not stat.S_ISREG(path.lstat().st_mode)):
        raise ValueError("publication_artifact_requires_direct_regular_file")
    return path


def _store(path):
    path = _direct(path)
    for generation in path.parents:
        if generation.parent.name == "generations":
            relative = path.relative_to(generation)
            if len(relative.parts) > 1 and relative.parts[0] in DIRECTORIES:
                store = generation.parent.parent / "publication-objects"
                if store.resolve() != store:
                    raise ValueError("publication_object_store_is_indirect")
                return store
            break
    raise ValueError("publication_artifact_outside_generation")


def _sync(path):
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _manifest(path):
    path = _direct(path)
    store = _store(path)
    receipt = json.loads(_direct(sidecar(path)).read_text())
    if (
        receipt.get("schema_version") != 1
        or receipt.get("encoding") != "record-chunks-v1"
        or receipt.get("original_path") != str(path)
        or not re.fullmatch("[0-9a-f]{64}", str(receipt.get("sha256", "")))
        or type(receipt.get("size")) is not int or receipt["size"] < 0
        or not isinstance(receipt.get("chunks"), list)
    ):
        raise ValueError("invalid_publication_artifact_manifest")
    total = 0
    for chunk in receipt["chunks"]:
        if (
            not isinstance(chunk, dict) or set(chunk) != {"sha256", "size"}
            or not re.fullmatch("[0-9a-f]{64}", str(chunk["sha256"]))
            or type(chunk["size"]) is not int or not 0 < chunk["size"] <= CHUNK_BYTES
        ):
            raise ValueError("invalid_publication_artifact_chunk")
        total += chunk["size"]
    if total != receipt["size"]:
        raise ValueError("publication_artifact_size_mismatch")
    return receipt, store


def _chunk(store, chunk):
    path = _direct(store / chunk["sha256"])
    before = path.stat()
    with path.open("rb") as handle:
        opened = os.fstat(handle.fileno())
        data = handle.read(CHUNK_BYTES + 1)
    after = path.stat()
    identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
    if identity(before) != identity(opened) or identity(before) != identity(after):
        raise ValueError("publication_chunk_changed_during_read")
    if len(data) != chunk["size"] or _hash(data) != chunk["sha256"]:
        raise ValueError("publication_chunk_hash_changed")
    return data


def _verify(receipt, store, *, deadline_at=None):
    if deadline_at is not None and time.monotonic() >= deadline_at:
        raise TimeoutError("publication_artifact_deadline")
    digest = hashlib.sha256()
    for chunk in receipt["chunks"]:
        if deadline_at is not None and time.monotonic() >= deadline_at:
            raise TimeoutError("publication_artifact_deadline")
        digest.update(_chunk(store, chunk))
    if digest.hexdigest() != receipt["sha256"]:
        raise ValueError("publication_artifact_hash_changed")


def fingerprint(path, *, deadline_at=None):
    if not sidecar(path).exists():
        return None
    receipt, store = _manifest(path)
    _verify(receipt, store, deadline_at=deadline_at)
    return {key: receipt[key] for key in ("sha256", "size")}


class _Reader(io.RawIOBase):
    def __init__(self, receipt, store):
        self._chunks = iter(receipt["chunks"])
        self._store, self._pending = store, b""

    def readable(self):
        return True

    def readinto(self, buffer):
        if not self._pending:
            chunk = next(self._chunks, None)
            if chunk is None:
                return 0
            self._pending = _chunk(self._store, chunk)
        length = min(len(buffer), len(self._pending))
        buffer[:length] = self._pending[:length]
        self._pending = self._pending[length:]
        return length


@contextmanager
def open_artifact(path):
    receipt, store = _manifest(path)
    _verify(receipt, store)  # Validate before a recovery writer sees any bytes.
    with io.BufferedReader(_Reader(receipt, store)) as stream:
        yield stream


def referenced_objects(path, *, deadline_at=None):
    if deadline_at is not None and time.monotonic() >= deadline_at:
        raise TimeoutError("publication_artifact_deadline")
    receipt, store = _manifest(path)
    objects = set()
    for chunk in receipt["chunks"]:
        if deadline_at is not None and time.monotonic() >= deadline_at:
            raise TimeoutError("publication_artifact_deadline")
        objects.add(_direct(store / chunk["sha256"]))
    return sorted(objects)


def artifact_inventory(logical_path, expected_sha256, expected_size, *, deadline_at=None):
    expected = {"sha256": expected_sha256, "size": expected_size}
    if fingerprint(logical_path, deadline_at=deadline_at) != expected:
        raise ValueError("publication_artifact_binding_changed")
    receipt, store = _manifest(logical_path)
    manifest_path = _direct(sidecar(logical_path))
    payload = manifest_path.read_bytes()
    result = [{"path": str(manifest_path), "sha256": _hash(payload), "size": len(payload)}]
    seen = set()
    for chunk in receipt["chunks"]:
        if deadline_at is not None and time.monotonic() >= deadline_at:
            raise TimeoutError("publication_artifact_deadline")
        if chunk["sha256"] not in seen:
            result.append({"path": str(store / chunk["sha256"]), **chunk})
            seen.add(chunk["sha256"])
    return result


def _mkdirs(directory, *, consume=None):
    missing = []
    parent = Path(directory)
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for parent in reversed(missing):
        if consume:
            consume(parent, 4096)
        parent.mkdir()


class RecordWriter:
    """Text sink preserving exporter bytes; flush records without a full copy."""
    def __init__(self, path, *, translate_newlines=False, consume=None):
        self.path = _direct(path)
        self.store = _store(path)
        self.consume = consume or (lambda path, size: None)
        for directory in (self.store, self.path.parent):
            _mkdirs(directory, consume=self.consume)
        self.translate_newlines = translate_newlines
        self.pending = bytearray()
        self.digest, self.size, self.chunks = hashlib.sha256(), 0, []

    def write(self, value):
        text = value.replace("\n", os.linesep) if self.translate_newlines else value
        self.write_bytes(text.encode("utf-8"))
        return len(value)

    def write_bytes(self, data):
        self.digest.update(data)
        self.size += len(data)
        view = memoryview(data)
        while view:
            length = min(CHUNK_BYTES - len(self.pending), len(view))
            self.pending.extend(view[:length])
            view = view[length:]
            if len(self.pending) == CHUNK_BYTES:
                self.record_boundary()

    def flush(self):
        pass  # A flush is not necessarily a record boundary.

    def record_boundary(self):
        if not self.pending:
            return
        data = bytes(self.pending)
        self.pending.clear()
        chunk = {"sha256": _hash(data), "size": len(data)}
        target = _direct(self.store / chunk["sha256"])
        if target.exists():
            _chunk(self.store, chunk)
        else:
            self.consume(target, len(data) + 4096)  # Reserve before allocating any new object.
            fd, temporary = tempfile.mkstemp(prefix=".chunk-", dir=self.store)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                # Shared objects are immutable: never overwrite an existing name.
                try:
                    os.link(temporary, target)
                except FileExistsError:
                    _chunk(self.store, chunk)
                _sync(self.store)
            finally:
                os.unlink(temporary)
            _chunk(self.store, chunk)
        self.chunks.append(chunk)

    def seal(self):
        self.record_boundary()
        receipt = {
            "schema_version": 1, "encoding": "record-chunks-v1",
            "original_path": str(self.path), "sha256": self.digest.hexdigest(),
            "size": self.size, "chunks": self.chunks,
        }
        _seal(self.path, receipt, consume=self.consume)
        return {key: receipt[key] for key in ("sha256", "size")}


def _seal(path, receipt, *, consume=None):
    from jobagg.atomic_files import atomic_write_text

    if Path(path).exists() or Path(path).is_symlink():
        raise ValueError("publication_artifact_conflicts_with_materialized_file")
    _verify(receipt, _store(path))
    payload = json.dumps(receipt, sort_keys=True) + "\n"
    if consume:
        consume(sidecar(path), len(payload.encode()) + 4096)
    atomic_write_text(sidecar(path), payload)
    _sync(Path(path).parent)
    if fingerprint(path) != {key: receipt[key] for key in ("sha256", "size")}:
        raise ValueError("publication_artifact_readback_failed")


def capture(source, target, expected, *, candidates=(), consume=None):
    """Bind the exact preimage, reusing a verified prior record manifest if present."""
    for candidate in candidates:
        if sidecar(candidate).exists() and fingerprint(candidate) == expected:
            receipt, store = _manifest(candidate)
            if store != _store(target):
                continue
            receipt["original_path"] = str(Path(target).absolute())
            _mkdirs(Path(target).parent, consume=consume)
            _seal(target, receipt, consume=consume)
            return
    from jobagg.retained_artifacts import open_artifact as open_original

    writer = RecordWriter(target, consume=consume)
    with open_original(source) as stream:
        for block in iter(lambda: stream.read(CHUNK_BYTES), b""):
            writer.write_bytes(block)
    if {"sha256": writer.digest.hexdigest(), "size": writer.size} != expected:
        raise ValueError("publication_capture_source_changed")
    writer.seal()
