"""Versioned lossless artifact representations for immutable JobAgg evidence.

This module is the Phase 1 storage boundary from the storage-reduction handover.
It adds compressed physical representations without changing logical artifact
paths, hashes or parsers.  A reader resolves all of the following through one
API:

* an ordinary legacy file (for example ``plan.json``);
* a representation recorded in this module's additive manifest;
* a legacy ``storage-cold-archive.json`` object already written by the
  dispatcher's existing gzip cold archive.

The logical SHA-256 always covers the exact reconstructed bytes.  Compression
never rewrites JSON, SQLite pages or response bodies, so null-versus-absent
values, ordering and unchanged request evidence remain distinct.

``preview_artifact`` is read-only apart from optional measurement scratch under
the system temporary directory.  ``compress_artifact`` defaults to preview and
requires the shared owner lock before any install or retirement.  Retirement is
opt-in and requires an explicit reader-compatibility assertion plus a durable
journal intent.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
import gzip
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
import time
import uuid
import zlib
from typing import Any, BinaryIO, Iterator, Mapping


SCHEMA_VERSION = 1
FORMAT = "jobagg-storage-representations"
JOURNAL_FORMAT = "jobagg-storage-journal"
STORAGE_DIRECTORY = ".jobagg-storage"
MANIFEST_NAME = "representations.json"
JOURNAL_NAME = "representations.journal.jsonl"
OBJECTS_DIRECTORY = "objects"
TEMPORARY_DIRECTORY = "tmp"
DICTIONARIES_DIRECTORY = "dictionaries"

CODEC_IDENTITY = "identity"
CODEC_GZIP = "gzip"
CODEC_ZSTD = "zstd"
DEFAULT_CODEC = "auto"
DEFAULT_ZSTD_LEVEL = 3
DEFAULT_GZIP_LEVEL = 6
DEFAULT_MIN_COMPRESS_BYTES = 4096
DEFAULT_MAX_OBJECT_BYTES = 8 * 1024 * 1024 * 1024
DEFAULT_MAX_IN_MEMORY_BYTES = 512 * 1024 * 1024
DEFAULT_MAX_HASH_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_MEASURE_BYTES = 1024 * 1024 * 1024
DEFAULT_RESTORE_RESERVE_BYTES = 64 * 1024 * 1024
_CHUNK = 1024 * 1024
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_GENERATION = re.compile(r"[0-9a-f]{32}\Z")
_ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
_GZIP_MAGIC = b"\x1f\x8b"
_SCHEMAS = {
    ".json": ("json", "utf-8"),
    ".jsonl": ("jsonl", "utf-8"),
    ".sqlite3": ("sqlite3", "binary"),
    ".sqlite": ("sqlite3", "binary"),
    ".html": ("html", "utf-8"),
    ".htm": ("html", "utf-8"),
    ".xml": ("xml", "utf-8"),
    ".csv": ("csv", "utf-8"),
    ".txt": ("text", "utf-8"),
}
_KNOWN_CODECS = {CODEC_IDENTITY, CODEC_GZIP, CODEC_ZSTD}


class ArtifactStorageError(ValueError):
    """Base class for storage-representation failures."""


class ArtifactMissingError(ArtifactStorageError):
    """The logical artifact and all of its representations are absent."""


class ArtifactIntegrityError(ArtifactStorageError):
    """Reconstructed bytes, physical bytes or declared lengths differ."""


class ArtifactContractError(ArtifactStorageError):
    """A manifest, codec, dictionary or field contract is invalid."""


class ArtifactTooLargeError(ArtifactStorageError):
    """A declared or observed size exceeds the caller's resource bound."""


class CompressionUnavailableError(ArtifactStorageError):
    """The requested codec runtime is unavailable."""


class ArtifactReadIncompleteError(ArtifactStorageError):
    """A streaming reader closed before the full logical artifact was verified."""


@dataclass(frozen=True)
class CodecSpec:
    name: str
    level: int | None
    dictionary_sha256: str | None = None

    def as_manifest(self) -> dict[str, Any]:
        return {
            "codec": self.name,
            "codec_params": {
                "level": self.level,
                "dictionary_sha256": self.dictionary_sha256,
            },
        }

    @property
    def object_suffix(self) -> str:
        parts = [self.name]
        if self.level is not None:
            parts.append(f"l{self.level}")
        if self.dictionary_sha256:
            parts.append(f"d{self.dictionary_sha256[:16]}")
        return "-".join(parts)


@dataclass(frozen=True)
class SourceIdentity:
    device: int
    inode: int
    size: int
    mtime_ns: int

    def as_manifest(self) -> dict[str, int]:
        return {
            "device": self.device,
            "inode": self.inode,
            "size": self.size,
            "mtime_ns": self.mtime_ns,
        }

    @classmethod
    def from_stat(cls, value: os.stat_result) -> "SourceIdentity":
        return cls(value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)


@dataclass(frozen=True)
class ObjectDescriptor:
    """The complete logical/physical binding required by the handover."""

    logical_path: str
    logical_sha256: str
    logical_bytes: int
    content_schema: str
    content_encoding: str
    codec: str
    codec_level: int | None
    dictionary_sha256: str | None
    physical_path: str
    physical_sha256: str
    physical_bytes: int
    legacy: bool
    installed_utc: str | None = None
    verified_utc: str | None = None

    def as_manifest(self) -> dict[str, Any]:
        return {
            "logical_path": self.logical_path,
            "logical_sha256": self.logical_sha256,
            "logical_bytes": self.logical_bytes,
            "content_schema": self.content_schema,
            "content_encoding": self.content_encoding,
            **CodecSpec(self.codec, self.codec_level, self.dictionary_sha256).as_manifest(),
            "physical_path": self.physical_path,
            "physical_sha256": self.physical_sha256,
            "physical_bytes": self.physical_bytes,
            "installed_utc": self.installed_utc,
            "verified_utc": self.verified_utc,
            "legacy": self.legacy,
        }


@dataclass(frozen=True)
class ResolvedArtifact:
    descriptor: ObjectDescriptor
    data: bytes

    @property
    def sha256(self) -> str:
        return self.descriptor.logical_sha256

    @property
    def size(self) -> int:
        return self.descriptor.logical_bytes


@dataclass
class RestoredSnapshot:
    """A verified sealed SQLite copy in private scratch storage."""

    path: Path
    scratch_root: Path
    sha256: str
    size: int

    def open_readonly(self) -> sqlite3.Connection:
        # ``as_uri`` percent-encodes URI delimiters (for example ``#`` and ``?``)
        # so SQLite opens the verified snapshot rather than a truncated prefix.
        uri = f"{self.path.absolute().as_uri()}?mode=ro&immutable=1"
        connection = sqlite3.connect(uri, uri=True)
        connection.execute("PRAGMA query_only = ON")
        return connection

    def cleanup(self) -> None:
        shutil.rmtree(self.scratch_root, ignore_errors=True)

    def __enter__(self) -> "RestoredSnapshot":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.cleanup()


def zstd_available() -> bool:
    try:
        import zstandard  # noqa: F401
    except ImportError:
        return False
    return True


def available_codecs() -> tuple[str, ...]:
    codecs = [CODEC_IDENTITY, CODEC_GZIP]
    if zstd_available():
        codecs.append(CODEC_ZSTD)
    return tuple(codecs)


def _require_zstd():
    try:
        import zstandard
    except ImportError as exc:
        raise CompressionUnavailableError(
            "Zstandard runtime is not packaged for this interpreter"
        ) from exc
    return zstandard


def _utc() -> str:
    return datetime.now(UTC).isoformat()


def _check_deadline(deadline_at: float | None) -> None:
    if deadline_at is not None and time.monotonic() >= deadline_at:
        raise TimeoutError("Artifact storage time budget exhausted")


def _deadline(max_seconds: float | None) -> float | None:
    if max_seconds is None:
        return None
    if type(max_seconds) not in (int, float) or not math.isfinite(max_seconds) or max_seconds <= 0:
        raise ArtifactContractError("max_seconds must be finite and positive")
    return time.monotonic() + max_seconds


def _operation_deadline(max_seconds, deadline_at=None):
    relative = _deadline(max_seconds)
    if deadline_at is not None:
        if type(deadline_at) not in (int, float) or not math.isfinite(deadline_at):
            raise ArtifactContractError("Absolute storage deadline must be finite")
        relative = min(relative, deadline_at) if relative is not None else deadline_at
    _check_deadline(relative)
    return relative


def normalize_codec(codec: str = DEFAULT_CODEC, level: int | None = None) -> CodecSpec:
    selected = (codec or DEFAULT_CODEC).lower()
    if selected == DEFAULT_CODEC:
        selected = CODEC_ZSTD if zstd_available() else CODEC_GZIP
    if selected not in _KNOWN_CODECS - {CODEC_IDENTITY}:
        raise ArtifactContractError(f"Unsupported codec: {codec}")
    if selected == CODEC_ZSTD:
        _require_zstd()
        value = DEFAULT_ZSTD_LEVEL if level is None else level
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 19:
            raise ArtifactContractError("Zstandard level must be 1-19")
        return CodecSpec(CODEC_ZSTD, value)
    value = DEFAULT_GZIP_LEVEL if level is None else level
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 9:
        raise ArtifactContractError("gzip level must be 1-9")
    return CodecSpec(CODEC_GZIP, value)


def _content_metadata(relative: str, schema: str | None, encoding: str | None):
    suffix = Path(relative).suffix.lower()
    inferred_schema, inferred_encoding = _SCHEMAS.get(suffix, ("binary", "binary"))
    return schema or inferred_schema, encoding or inferred_encoding


def _sha256(value: str) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise ArtifactContractError("Expected SHA-256 is invalid")
    return value


def _positive(value: int | None, label: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ArtifactContractError(f"{label} must be a non-negative integer")
    return value


def _regular(path: Path) -> os.stat_result:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise ArtifactMissingError(f"Artifact is missing: {path}") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ArtifactContractError(f"Artifact is linked, hardlinked or not regular: {path}")
    return info


def _directory(path: Path) -> os.stat_result:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise ArtifactMissingError(f"Directory is missing: {path}") from exc
    if not stat.S_ISDIR(info.st_mode):
        raise ArtifactContractError(f"Directory is linked or invalid: {path}")
    return info


def _sync_dir(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _inside_root(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ArtifactContractError("Artifact path must be a non-empty relative path")
    candidate = Path(relative)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise ArtifactContractError(f"Artifact path escapes the root: {relative}")
    path = root.joinpath(*candidate.parts)
    current = root
    for part in candidate.parts[:-1]:
        current = current / part
        if current.exists() or current.is_symlink():
            _directory(current)
    return path


def _relative(root: Path, path: str | Path) -> str:
    candidate = Path(path)
    if candidate.is_absolute():
        try:
            candidate = candidate.relative_to(root)
        except ValueError as exc:
            raise ArtifactContractError(f"Artifact is outside the data root: {path}") from exc
    return candidate.as_posix()


def _safe_open(path: Path) -> tuple[BinaryIO, SourceIdentity]:
    before = _regular(path)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    opened = os.fstat(descriptor)
    if SourceIdentity.from_stat(before) != SourceIdentity.from_stat(opened):
        os.close(descriptor)
        raise ArtifactIntegrityError(f"File changed while opening: {path}")
    return os.fdopen(descriptor, "rb"), SourceIdentity.from_stat(opened)


def _digest(
    path: Path,
    *,
    deadline_at: float | None = None,
    max_bytes: int | None = None,
) -> tuple[str, int, SourceIdentity]:
    _check_deadline(deadline_at)
    stream, before = _safe_open(path)
    digest, size = hashlib.sha256(), 0
    try:
        for block in iter(lambda: stream.read(_CHUNK), b""):
            _check_deadline(deadline_at)
            size += len(block)
            if max_bytes is not None and size > max_bytes:
                raise ArtifactTooLargeError(f"Artifact exceeds {max_bytes} bytes: {path}")
            digest.update(block)
        after = os.fstat(stream.fileno())
    finally:
        stream.close()
    latest = _regular(path)
    expected = SourceIdentity.from_stat(after)
    if SourceIdentity.from_stat(latest) != expected or expected != before:
        raise ArtifactIntegrityError(f"File changed while hashing: {path}")
    return digest.hexdigest(), size, before


def _read_prefix(path: Path, length: int = 8) -> bytes:
    stream, _ = _safe_open(path)
    try:
        return stream.read(length)
    finally:
        stream.close()


def _already_compressed(path: Path) -> str | None:
    prefix = _read_prefix(path)
    if prefix.startswith(_GZIP_MAGIC):
        return CODEC_GZIP
    if prefix.startswith(_ZSTD_MAGIC):
        return CODEC_ZSTD
    return None


def _sqlite_sidecars(path: Path) -> list[Path]:
    return [
        candidate
        for candidate in (
            Path(str(path) + "-wal"),
            Path(str(path) + "-shm"),
            Path(str(path) + "-journal"),
        )
        if candidate.exists() or candidate.is_symlink()
    ]


def _check_sqlite_candidate(path: Path, *, sealed: bool) -> None:
    if _sqlite_sidecars(path):
        raise ArtifactContractError(
            "SQLite candidate has mutable WAL/shm sidecars; use a sealed snapshot"
        )
    if not sealed:
        raise ArtifactContractError(
            "SQLite compression requires an explicit sealed-snapshot assertion"
        )


def _load_dictionary(
    sha256: str | None,
    *,
    dictionaries: Mapping[str, bytes] | None = None,
    dictionary_path: str | Path | None = None,
    deadline_at: float | None = None,
) -> bytes | None:
    if not sha256:
        return None
    _sha256(sha256)
    if dictionaries and sha256 in dictionaries:
        data = dictionaries[sha256]
    elif dictionary_path is not None:
        path = Path(dictionary_path)
        stream, _ = _safe_open(path)
        try:
            data = stream.read(DEFAULT_MAX_IN_MEMORY_BYTES + 1)
        finally:
            stream.close()
        if len(data) > DEFAULT_MAX_IN_MEMORY_BYTES:
            raise ArtifactTooLargeError("Zstandard dictionary exceeds the memory bound")
        actual = hashlib.sha256(data).hexdigest()
        if actual != sha256:
            raise ArtifactContractError("Zstandard dictionary hash differs from manifest")
    else:
        raise ArtifactContractError(
            "Representation requires a Zstandard dictionary that was not supplied"
        )
    if hashlib.sha256(data).hexdigest() != sha256:
        raise ArtifactContractError("Zstandard dictionary hash differs from manifest")
    _check_deadline(deadline_at)
    return data


def _zstd_compression_dict(data: bytes | None):
    if data is None:
        return None
    return _require_zstd().ZstdCompressionDict(data)


def _write_compressed(
    source: Path,
    destination: Path,
    spec: CodecSpec,
    dictionary_data: bytes | None,
    *,
    deadline_at: float | None = None,
    max_source_bytes: int = DEFAULT_MAX_OBJECT_BYTES,
    max_object_bytes: int = DEFAULT_MAX_OBJECT_BYTES,
) -> dict[str, Any]:
    _check_deadline(deadline_at)
    if max_source_bytes <= 0:
        raise ArtifactContractError("Compression source bound must be positive")
    information = _regular(source)
    if information.st_size > max_source_bytes:
        raise ArtifactTooLargeError("Compression source exceeds its declared size")
    descriptor = os.open(
        destination,
        os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    physical_size = 0
    source_size = 0
    try:
        with os.fdopen(descriptor, "wb") as output, _safe_open(source)[0] as incoming:
            if spec.name == CODEC_GZIP:
                with gzip.GzipFile(
                    filename="",
                    mode="wb",
                    fileobj=output,
                    mtime=0,
                    compresslevel=spec.level or DEFAULT_GZIP_LEVEL,
                ) as compressed:
                    for block in iter(lambda: incoming.read(_CHUNK), b""):
                        _check_deadline(deadline_at)
                        source_size += len(block)
                        if source_size > max_source_bytes:
                            raise ArtifactTooLargeError("Compression source exceeds its bound")
                        compressed.write(block)
                        physical_size = output.tell()
                        if physical_size > max_object_bytes:
                            raise ArtifactTooLargeError("Compressed object exceeds its bound")
                output.flush()
                os.fsync(output.fileno())
            elif spec.name == CODEC_ZSTD:
                zstandard = _require_zstd()
                compressor = zstandard.ZstdCompressor(
                    level=spec.level or DEFAULT_ZSTD_LEVEL,
                    dict_data=_zstd_compression_dict(dictionary_data),
                ).compressobj()
                for block in iter(lambda: incoming.read(_CHUNK), b""):
                    _check_deadline(deadline_at)
                    source_size += len(block)
                    if source_size > max_source_bytes:
                        raise ArtifactTooLargeError("Compression source exceeds its bound")
                    compressed_block = compressor.compress(block)
                    if compressed_block:
                        physical_size += len(compressed_block)
                        if physical_size > max_object_bytes:
                            raise ArtifactTooLargeError("Compressed object exceeds its bound")
                        output.write(compressed_block)
                tail = compressor.flush()
                if tail:
                    physical_size += len(tail)
                    if physical_size > max_object_bytes:
                        raise ArtifactTooLargeError("Compressed object exceeds its bound")
                    output.write(tail)
                output.flush()
                os.fsync(output.fileno())
            else:
                raise ArtifactContractError(f"Unsupported compression codec: {spec.name}")
        physical_size = destination.stat().st_size
        if physical_size > max_object_bytes:
            raise ArtifactTooLargeError("Compressed object exceeds its bound")
        if source_size != information.st_size:
            raise ArtifactIntegrityError("Compression source length changed while reading")
        digest, size, _ = _digest(destination, deadline_at=deadline_at, max_bytes=max_object_bytes)
        if size != physical_size:
            raise ArtifactIntegrityError("Compressed object length changed before sealing")
        return {"sha256": digest, "size": size}
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def _verify_physical(
    path: Path,
    spec: CodecSpec,
    *,
    expected_sha256: str,
    expected_bytes: int,
    deadline_at: float | None = None,
    max_bytes: int = DEFAULT_MAX_OBJECT_BYTES,
) -> SourceIdentity:
    _check_deadline(deadline_at)
    prefix = _read_prefix(path)
    if spec.name == CODEC_ZSTD and not prefix.startswith(_ZSTD_MAGIC):
        raise ArtifactContractError(
            "Manifest declares Zstandard but the object is not a Zstandard frame"
        )
    if spec.name == CODEC_GZIP and not prefix.startswith(_GZIP_MAGIC):
        raise ArtifactContractError("Manifest declares gzip but the object is not a gzip stream")
    digest, size, identity = _digest(path, deadline_at=deadline_at, max_bytes=max_bytes)
    if size != expected_bytes:
        raise ArtifactIntegrityError("Compressed object length differs from manifest")
    if digest != _sha256(expected_sha256):
        raise ArtifactIntegrityError("Compressed object hash differs from manifest")
    return identity


def _iter_decompressed(
    path: Path,
    spec: CodecSpec,
    dictionary_data: bytes | None,
    *,
    expected_sha256: str,
    expected_bytes: int,
    physical_sha256: str,
    physical_bytes: int,
    deadline_at: float | None = None,
    max_output_bytes: int = DEFAULT_MAX_OBJECT_BYTES,
) -> Iterator[bytes]:
    if expected_bytes > max_output_bytes:
        raise ArtifactTooLargeError("Declared logical size exceeds the restore bound")
    identity = _verify_physical(
        path,
        spec,
        expected_sha256=physical_sha256,
        expected_bytes=physical_bytes,
        deadline_at=deadline_at,
        max_bytes=physical_bytes,
    )
    _check_deadline(deadline_at)
    digest, total = hashlib.sha256(), 0
    stream, opened = _safe_open(path)
    decompressed: BinaryIO | None = None
    try:
        if spec.name == CODEC_GZIP:
            decompressed = gzip.GzipFile(fileobj=stream, mode="rb")
        elif spec.name == CODEC_ZSTD:
            zstandard = _require_zstd()
            decompressed = zstandard.ZstdDecompressor(
                dict_data=_zstd_compression_dict(dictionary_data)
            ).stream_reader(stream, read_across_frames=False)
        else:
            raise ArtifactContractError(f"Unsupported compression codec: {spec.name}")
        try:
            for block in iter(lambda: decompressed.read(_CHUNK), b""):
                _check_deadline(deadline_at)
                total += len(block)
                if total > expected_bytes or total > max_output_bytes:
                    raise ArtifactTooLargeError(
                        "Reconstructed artifact exceeds its declared size"
                    )
                digest.update(block)
                yield block
        except (EOFError, gzip.BadGzipFile, zlib.error) as exc:
            raise ArtifactIntegrityError("Compressed object cannot be decoded") from exc
        except Exception as exc:
            if exc.__class__.__module__.startswith("zstandard"):
                raise ArtifactIntegrityError("Compressed object cannot be decoded") from exc
            raise
    finally:
        stream.close()
    _check_deadline(deadline_at)
    latest = _regular(path)
    if SourceIdentity.from_stat(latest) != opened or opened != identity:
        raise ArtifactIntegrityError("Compressed object changed while it was being read")
    if total != expected_bytes:
        raise ArtifactIntegrityError("Reconstructed artifact length differs from manifest")
    if digest.hexdigest() != _sha256(expected_sha256):
        raise ArtifactIntegrityError("Reconstructed artifact hash differs from manifest")


def _empty_manifest() -> dict[str, Any]:
    return {
        "format": FORMAT,
        "schema_version": SCHEMA_VERSION,
        "updated_utc": _utc(),
        "artifacts": {},
    }


def _default_manifest_path(root: Path) -> Path:
    return root / STORAGE_DIRECTORY / MANIFEST_NAME


def _default_journal_path(root: Path) -> Path:
    return root / STORAGE_DIRECTORY / JOURNAL_NAME


def _store_layout(root: Path, manifest_path: Path) -> dict[str, Path]:
    return {
        "root": root,
        "storage": root / STORAGE_DIRECTORY,
        "objects": root / STORAGE_DIRECTORY / OBJECTS_DIRECTORY,
        "temporary": root / STORAGE_DIRECTORY / TEMPORARY_DIRECTORY,
        "dictionaries": root / STORAGE_DIRECTORY / DICTIONARIES_DIRECTORY,
        "manifest": manifest_path,
        "journal": _default_journal_path(root),
    }


def _ensure_store(root: Path, manifest_path: Path) -> dict[str, Path]:
    _directory(root)
    layout = _store_layout(root, manifest_path)
    for key in ("storage", "objects", "temporary"):
        if layout[key].exists() or layout[key].is_symlink():
            _directory(layout[key])
        else:
            layout[key].mkdir(parents=True, exist_ok=True)
            layout[key].chmod(0o700)
            _sync_dir(layout[key].parent)
    return layout


def _representation_path(root: Path, relative: str) -> Path:
    return _inside_root(root, relative)


def _validate_relative_manifest_path(relative: str) -> str:
    if not isinstance(relative, str) or not relative:
        raise ArtifactContractError("Manifest path must be a non-empty relative string")
    candidate = Path(relative)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise ArtifactContractError(f"Manifest path escapes the root: {relative}")
    return candidate.as_posix()


def _reject_store_relative(relative: str) -> None:
    """Physical objects are never logical artifacts; resolve through their path."""
    if Path(relative).parts and Path(relative).parts[0] == STORAGE_DIRECTORY:
        raise ArtifactContractError(
            "A storage object or manifest cannot be compressed as a logical artifact"
        )


def _validate_codec_params(value: Any) -> tuple[str, int | None, str | None]:
    if not isinstance(value, dict):
        raise ArtifactContractError("Representation codec params must be an object")
    codec = value.get("codec")
    if codec not in _KNOWN_CODECS:
        raise ArtifactContractError(f"Unknown representation codec: {codec}")
    params = value.get("codec_params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ArtifactContractError("Representation codec_params must be an object")
    if set(params) - {"level", "dictionary_sha256"}:
        raise ArtifactContractError("Representation codec_params contains unknown keys")
    level = params.get("level")
    if level is not None and (
        isinstance(level, bool) or not isinstance(level, int) or level < 0
    ):
        raise ArtifactContractError("Representation codec level is invalid")
    dictionary = params.get("dictionary_sha256")
    if dictionary is not None:
        _sha256(dictionary)
    if codec == CODEC_IDENTITY and (level is not None or dictionary is not None):
        raise ArtifactContractError("Identity representation cannot carry codec params")
    if dictionary is not None and codec != CODEC_ZSTD:
        raise ArtifactContractError(
            "Zstandard dictionaries cannot be attached to a non-Zstandard representation"
        )
    return codec, level, dictionary


def _validate_representation(root: Path, value: Any, logical_path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ArtifactContractError("Manifest representation must be an object")
    codec, level, dictionary = _validate_codec_params(value)
    physical_path = _validate_relative_manifest_path(value.get("physical_path"))
    if codec == CODEC_IDENTITY:
        if physical_path != logical_path:
            raise ArtifactContractError("Identity representation must point at the logical path")
    elif not physical_path.startswith(f"{STORAGE_DIRECTORY}/{OBJECTS_DIRECTORY}/"):
        raise ArtifactContractError("Compressed representation is outside the object store")
    physical_sha = _sha256(value.get("physical_sha256"))
    physical_bytes = _positive(value.get("physical_bytes"), "physical_bytes")
    if physical_bytes is None or physical_bytes <= 0:
        raise ArtifactContractError("Representation physical byte length is missing")
    installed_utc = value.get("installed_utc")
    verified_utc = value.get("verified_utc")
    for timestamp in (installed_utc, verified_utc):
        if timestamp is not None and not isinstance(timestamp, str):
            raise ArtifactContractError("Representation timestamp is invalid")
    return {
        "codec": codec,
        "codec_params": {"level": level, "dictionary_sha256": dictionary},
        "physical_path": physical_path,
        "physical_sha256": physical_sha,
        "physical_bytes": physical_bytes,
        "installed_utc": installed_utc,
        "verified_utc": verified_utc,
    }


def _validate_entry(root: Path, logical_path: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ArtifactContractError("Manifest artifact entry must be an object")
    if value.get("logical_path") != logical_path:
        raise ArtifactContractError("Manifest logical path differs from its key")
    logical_sha = _sha256(value.get("logical_sha256"))
    logical_bytes = _positive(value.get("logical_bytes"), "logical_bytes")
    if logical_bytes is None:
        raise ArtifactContractError("Manifest logical byte length is missing")
    schema = value.get("content_schema")
    encoding = value.get("content_encoding")
    if not isinstance(schema, str) or not isinstance(encoding, str):
        raise ArtifactContractError("Manifest encoding/schema labels are missing")
    representations = value.get("representations")
    if not isinstance(representations, list) or not representations:
        raise ArtifactContractError("Manifest entry has no representations")
    validated = [
        _validate_representation(root, representation, logical_path)
        for representation in representations
    ]
    physical_paths = [item["physical_path"] for item in validated]
    if len(set(physical_paths)) != len(physical_paths):
        raise ArtifactContractError("Manifest entry repeats a representation path")
    for item in validated:
        if item["codec"] == CODEC_IDENTITY and (
            item["physical_sha256"] != logical_sha or item["physical_bytes"] != logical_bytes
        ):
            raise ArtifactContractError(
                "Identity representation must match the logical hash and byte length"
            )
    legacy = value.get("legacy")
    if not isinstance(legacy, dict):
        raise ArtifactContractError("Manifest legacy binding is missing")
    if legacy.get("path") != logical_path:
        raise ArtifactContractError("Manifest legacy path differs from logical path")
    if not isinstance(legacy.get("retired"), bool):
        raise ArtifactContractError("Manifest legacy retired flag is invalid")
    retired_utc = legacy.get("retired_utc")
    if retired_utc is not None and not isinstance(retired_utc, str):
        raise ArtifactContractError("Manifest legacy retirement time is invalid")
    removed_bytes = _positive(legacy.get("removed_bytes"), "removed_bytes")
    retired = legacy["retired"]
    if retired and retired_utc is None:
        raise ArtifactContractError("Retired legacy binding lacks a retirement time")
    if retired and any(item["codec"] == CODEC_IDENTITY for item in validated):
        raise ArtifactContractError(
            "Retired legacy binding still lists an identity representation"
        )
    return {
        "logical_path": logical_path,
        "logical_sha256": logical_sha,
        "logical_bytes": logical_bytes,
        "content_schema": schema,
        "content_encoding": encoding,
        "representations": validated,
        "legacy": {
            "path": logical_path,
            "retired": retired,
            "retired_utc": retired_utc,
            "removed_bytes": removed_bytes or 0,
        },
        "created_utc": value.get("created_utc"),
        "updated_utc": value.get("updated_utc"),
    }


def _validate_manifest(root: Path, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ArtifactContractError("Manifest must be a JSON object")
    if value.get("format") != FORMAT or value.get("schema_version") != SCHEMA_VERSION:
        raise ArtifactContractError("Unsupported storage representation manifest")
    artifacts = value.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ArtifactContractError("Manifest artifacts must be an object")
    validated = {}
    for key, entry in artifacts.items():
        relative = _validate_relative_manifest_path(key)
        if relative in validated:
            raise ArtifactContractError("Manifest repeats an artifact path")
        validated[relative] = _validate_entry(root, relative, entry)
    return {**value, "artifacts": validated}


def _read_manifest(path: Path, root: Path) -> dict[str, Any]:
    _inside_root(root, _relative(root, path))
    if not path.exists() and not path.is_symlink():
        return _empty_manifest()
    _regular(path)
    try:
        with path.open("rb") as stream:
            payload = stream.read(32 * 1024**2 + 1)
        if len(payload) > 32 * 1024**2:
            raise ArtifactContractError("Storage representation manifest exceeds byte bound")
        value = json.loads(payload)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactContractError("Storage representation manifest is unreadable") from exc
    return _validate_manifest(root, value)


def _manifest_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _write_manifest(path: Path, value: dict[str, Any], *, deadline_at: float | None = None) -> str:
    _check_deadline(deadline_at)
    _directory(path.parent)
    if path.exists() or path.is_symlink():
        _regular(path)
    if "updated_utc" in value:
        value["updated_utc"] = _utc()
    descriptor, temporary_name = tempfile.mkstemp(prefix=".representations-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        payload = _manifest_bytes(value)
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        _sync_dir(path.parent)
        return hashlib.sha256(payload).hexdigest()
    finally:
        temporary.unlink(missing_ok=True)


def _representation_descriptor(
    relative: str,
    entry: dict[str, Any],
    representation: dict[str, Any],
    *,
    legacy: bool,
) -> ObjectDescriptor:
    return ObjectDescriptor(
        logical_path=relative,
        logical_sha256=entry["logical_sha256"],
        logical_bytes=entry["logical_bytes"],
        content_schema=entry["content_schema"],
        content_encoding=entry["content_encoding"],
        codec=representation["codec"],
        codec_level=representation["codec_params"]["level"],
        dictionary_sha256=representation["codec_params"]["dictionary_sha256"],
        physical_path=representation["physical_path"],
        physical_sha256=representation["physical_sha256"],
        physical_bytes=representation["physical_bytes"],
        legacy=legacy,
        installed_utc=representation.get("installed_utc"),
        verified_utc=representation.get("verified_utc"),
    )


def _merge_entry(
    manifest: dict[str, Any],
    relative: str,
    descriptor: ObjectDescriptor,
) -> dict[str, Any]:
    artifacts = manifest["artifacts"]
    existing = artifacts.get(relative)
    representation = {
        "codec": descriptor.codec,
        "codec_params": {
            "level": descriptor.codec_level,
            "dictionary_sha256": descriptor.dictionary_sha256,
        },
        "physical_path": descriptor.physical_path,
        "physical_sha256": descriptor.physical_sha256,
        "physical_bytes": descriptor.physical_bytes,
        "installed_utc": descriptor.installed_utc,
        "verified_utc": descriptor.verified_utc,
    }
    now = _utc()
    if existing is None:
        artifacts[relative] = {
            "logical_path": relative,
            "logical_sha256": descriptor.logical_sha256,
            "logical_bytes": descriptor.logical_bytes,
            "content_schema": descriptor.content_schema,
            "content_encoding": descriptor.content_encoding,
            "representations": [representation],
            "legacy": {
                "path": relative,
                "retired": False,
                "retired_utc": None,
                "removed_bytes": 0,
            },
            "created_utc": now,
            "updated_utc": now,
        }
        return manifest
    if (
        existing["logical_sha256"] != descriptor.logical_sha256
        or existing["logical_bytes"] != descriptor.logical_bytes
    ):
        raise ArtifactContractError(
            "Immutable logical artifact already has a different recorded identity"
        )
    physical_paths = {item["physical_path"] for item in existing["representations"]}
    if descriptor.physical_path not in physical_paths:
        existing["representations"].append(representation)
    existing["updated_utc"] = now
    return manifest


def _append_journal(path: Path, record: dict[str, Any], *, deadline_at: float | None = None) -> None:
    _check_deadline(deadline_at)
    if not isinstance(record, dict):
        raise ArtifactContractError("Journal record must be an object")
    payload = dict(record, format=JOURNAL_FORMAT, schema_version=SCHEMA_VERSION)
    line = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    descriptor = os.open(
        path,
        os.O_CREAT | os.O_WRONLY | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        offset = 0
        while offset < len(line):
            written = os.write(descriptor, line[offset:])
            if written <= 0:
                raise OSError("Journal append made no progress")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _sync_dir(path.parent)


def _read_journal(path: Path) -> list[dict[str, Any]]:
    if not path.exists() and not path.is_symlink():
        return []
    _regular(path)
    records = []
    try:
        with path.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ArtifactContractError("Journal record must be an object")
                if value.get("format") != JOURNAL_FORMAT:
                    raise ArtifactContractError("Journal record format is invalid")
                if value.get("schema_version") != SCHEMA_VERSION:
                    raise ArtifactContractError("Journal record schema version is invalid")
                value["_line"] = line_number
                records.append(value)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactContractError("Storage journal is unreadable") from exc
    return records


def _journal_event_logged(
    records: list[dict[str, Any]],
    *,
    kind: str,
    logical_path: str | None = None,
    intent_id: str | None = None,
) -> bool:
    for record in records:
        if record.get("kind") != kind:
            continue
        if logical_path is not None and record.get("logical_path") != logical_path:
            continue
        if intent_id is not None and record.get("intent_id") != intent_id:
            continue
        return True
    return False


@dataclass(frozen=True)
class JournalIntent:
    intent_id: str
    logical_path: str
    logical_sha256: str
    logical_bytes: int
    physical_path: str
    physical_sha256: str
    physical_bytes: int
    codec: str
    retired_legacy: bool
    codec_level: int | None = None
    dictionary_sha256: str | None = None


def _open_intents(records: list[dict[str, Any]]) -> dict[str, JournalIntent]:
    intents: dict[str, JournalIntent] = {}
    closed: set[str] = set()
    for record in records:
        intent_id = record.get("intent_id")
        if not isinstance(intent_id, str) or not intent_id:
            continue
        kind = record.get("kind")
        if kind == "intent":
            try:
                codec = record["codec"]
                codec_level = record.get("codec_level")
                dictionary = record.get("dictionary_sha256")
                retired_legacy = record["retired_legacy"]
            except KeyError as exc:
                raise ArtifactContractError(
                    "Journal intent is missing a required field"
                ) from exc
            if codec not in {CODEC_GZIP, CODEC_ZSTD}:
                raise ArtifactContractError("Journal intent codec is invalid")
            if codec_level is not None and (
                not isinstance(codec_level, int) or isinstance(codec_level, bool)
            ):
                raise ArtifactContractError("Journal intent codec level is invalid")
            if dictionary is not None:
                _sha256(dictionary)
                if codec != CODEC_ZSTD:
                    raise ArtifactContractError(
                        "Journal intent carries a dictionary for a non-Zstandard codec"
                    )
            if not isinstance(retired_legacy, bool):
                raise ArtifactContractError("Journal retirement flag is invalid")
            logical_bytes = _positive(record.get("logical_bytes"), "logical_bytes")
            physical_bytes = _positive(record.get("physical_bytes"), "physical_bytes")
            if logical_bytes is None or physical_bytes is None:
                raise ArtifactContractError("Journal intent byte length is missing")
            intents[intent_id] = JournalIntent(
                intent_id=intent_id,
                logical_path=_validate_relative_manifest_path(record.get("logical_path")),
                logical_sha256=_sha256(record.get("logical_sha256")),
                logical_bytes=logical_bytes,
                physical_path=_validate_relative_manifest_path(record.get("physical_path")),
                physical_sha256=_sha256(record.get("physical_sha256")),
                physical_bytes=physical_bytes,
                codec=codec,
                retired_legacy=retired_legacy,
                codec_level=codec_level,
                dictionary_sha256=dictionary,
            )
        elif kind in {"complete", "failed", "abandoned"}:
            closed.add(intent_id)
    return {key: value for key, value in intents.items() if key not in closed}


def _intent_record(intent: JournalIntent, *, run_id: str) -> dict[str, Any]:
    return {
        "kind": "intent",
        "run_id": run_id,
        "intent_id": intent.intent_id,
        "at_utc": _utc(),
        "logical_path": intent.logical_path,
        "logical_sha256": intent.logical_sha256,
        "logical_bytes": intent.logical_bytes,
        "physical_path": intent.physical_path,
        "physical_sha256": intent.physical_sha256,
        "physical_bytes": intent.physical_bytes,
        "codec": intent.codec,
        "retired_legacy": intent.retired_legacy,
        "codec_level": intent.codec_level,
        "dictionary_sha256": intent.dictionary_sha256,
    }


COLD_ARCHIVE_MANIFEST = "storage-cold-archive.json"
COLD_ARCHIVE_SCHEMA_VERSION = 1


def _cold_archive_entry(generation: Path, relative: str) -> dict[str, Any] | None:
    """Read one binding from an existing dispatcher cold-archive receipt.

    The receipt contract is owned by ``storage_cold_archive`` (schema 1).  This
    focused reader validates only the requested entry so resolving one artifact
    never decompresses an entire generation; ``verify_archived_generation``
    remains the whole-generation verifier.
    """
    receipt_path = generation / COLD_ARCHIVE_MANIFEST
    if not receipt_path.exists() and not receipt_path.is_symlink():
        return None
    _regular(receipt_path)
    if receipt_path.stat().st_size > DEFAULT_MAX_IN_MEMORY_BYTES:
        raise ArtifactTooLargeError("Cold archive receipt is too large")
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactContractError("Cold archive receipt is unreadable") from exc
    if not isinstance(receipt, dict):
        raise ArtifactContractError("Cold archive receipt must be an object")
    archive_root = generation.parent.parent / "cold_archive"
    expected_root = str(archive_root)
    if (
        receipt.get("schema_version") != COLD_ARCHIVE_SCHEMA_VERSION
        or receipt.get("generation_id") != generation.name
        or not _GENERATION.fullmatch(generation.name)
        or receipt.get("archive_root") != expected_root
        or not isinstance(receipt.get("objects"), dict)
        or relative not in receipt["objects"]
    ):
        return None
    entry = receipt["objects"][relative]
    if not isinstance(entry, dict):
        raise ArtifactContractError("Cold archive entry must be an object")
    logical_sha = _sha256(entry.get("sha256"))
    object_sha = _sha256(entry.get("object_sha256"))
    logical_bytes = _positive(entry.get("size_bytes"), "size_bytes")
    object_bytes = _positive(entry.get("object_size_bytes"), "object_size_bytes")
    if not logical_bytes or not object_bytes:
        raise ArtifactContractError("Cold archive entry has a zero or missing size")
    object_path = archive_root / "objects" / logical_sha[:2] / f"{logical_sha}.json.gz"
    if entry.get("object_path") != str(object_path):
        raise ArtifactContractError("Cold archive object path differs from the receipt contract")
    return {
        "logical_path": relative,
        "logical_sha256": logical_sha,
        "logical_bytes": logical_bytes,
        "object_sha256": object_sha,
        "object_size_bytes": object_bytes,
        "object_path": object_path,
    }


def _cold_archive_binding(
    root: Path, logical: Path
) -> dict[str, Any] | None:
    """Find the generation receipt that owns a cold-archived logical path."""
    for ancestor in logical.parents:
        if ancestor == root:
            return None
        if _GENERATION.fullmatch(ancestor.name):
            key = logical.relative_to(ancestor).as_posix()
            cold = _cold_archive_entry(ancestor, key)
            if cold is not None:
                _inside_root(root, _relative(root, cold["object_path"]))
            return cold
    return None


def _cold_archive_stream(
    cold: dict[str, Any],
    relative: str,
    *,
    deadline_at: float | None,
    max_output_bytes: int,
) -> tuple[ObjectDescriptor, Iterator[bytes]]:
    schema, encoding = _content_metadata(relative, None, None)
    descriptor = ObjectDescriptor(
        logical_path=relative,
        logical_sha256=cold["logical_sha256"],
        logical_bytes=cold["logical_bytes"],
        content_schema=schema,
        content_encoding=encoding,
        codec=CODEC_GZIP,
        codec_level=DEFAULT_GZIP_LEVEL,
        dictionary_sha256=None,
        physical_path=str(cold["object_path"]),
        physical_sha256=cold["object_sha256"],
        physical_bytes=cold["object_size_bytes"],
        legacy=True,
        installed_utc=None,
        verified_utc=None,
    )
    return descriptor, _iter_cold_archive(
        cold, deadline_at=deadline_at, max_output_bytes=max_output_bytes
    )


def _iter_plain(path: Path, *, deadline_at, max_bytes) -> Iterator[bytes]:
    _check_deadline(deadline_at)
    stream, before = _safe_open(path)
    total = 0
    try:
        for block in iter(lambda: stream.read(_CHUNK), b""):
            _check_deadline(deadline_at)
            total += len(block)
            if total > max_bytes:
                raise ArtifactTooLargeError(f"Artifact exceeds {max_bytes} bytes: {path}")
            yield block
        after = os.fstat(stream.fileno())
    finally:
        stream.close()
    if SourceIdentity.from_stat(_regular(path)) != SourceIdentity.from_stat(after) or SourceIdentity.from_stat(after) != before:
        raise ArtifactIntegrityError(f"File changed while it was being read: {path}")


def _iter_legacy_file(
    root: Path,
    legacy_path: Path,
    *,
    expected_sha256: str | None,
    expected_bytes: int | None,
    deadline_at: float | None,
    max_output_bytes: int,
) -> Iterator[bytes]:
    """Stream an uncompressed legacy artifact with exact verification."""
    relative = _relative(root, legacy_path)
    impostor = _already_compressed(legacy_path)
    if impostor and Path(relative).suffix.lower() in _SCHEMAS:
        raise ArtifactContractError(
            f"Artifact {relative} is a {impostor} stream without a representation manifest"
        )
    digest, total = hashlib.sha256(), 0
    try:
        for block in _iter_plain(
            legacy_path, deadline_at=deadline_at, max_bytes=max_output_bytes
        ):
            total += len(block)
            if total > max_output_bytes:
                raise ArtifactTooLargeError("Legacy artifact exceeds the read bound")
            digest.update(block)
            yield block
    except GeneratorExit:
        raise
    actual_sha = digest.hexdigest()
    if expected_bytes is not None and total != expected_bytes:
        raise ArtifactIntegrityError("Legacy artifact length differs from its binding")
    if expected_sha256 is not None and actual_sha != _sha256(expected_sha256):
        raise ArtifactIntegrityError("Legacy artifact hash differs from its binding")


def _representation_sort_key(representation: dict[str, Any]) -> tuple[int, str]:
    order = {CODEC_IDENTITY: 0, CODEC_ZSTD: 1, CODEC_GZIP: 2}
    return order.get(representation["codec"], 3), representation["physical_path"]


def _iter_manifest_representation(
    root: Path,
    descriptor: ObjectDescriptor,
    *,
    dictionaries: Mapping[str, bytes] | None,
    dictionary_path: str | Path | None,
    deadline_at: float | None,
    max_output_bytes: int,
) -> Iterator[bytes]:
    if descriptor.codec == CODEC_IDENTITY:
        yield from _iter_legacy_file(
            root,
            _inside_root(root, descriptor.physical_path),
            expected_sha256=descriptor.logical_sha256,
            expected_bytes=descriptor.logical_bytes,
            deadline_at=deadline_at,
            max_output_bytes=max_output_bytes,
        )
        return
    spec = CodecSpec(descriptor.codec, descriptor.codec_level, descriptor.dictionary_sha256)
    dictionary_data = _load_dictionary(
        descriptor.dictionary_sha256,
        dictionaries=dictionaries,
        dictionary_path=dictionary_path,
        deadline_at=deadline_at,
    )
    yield from _iter_decompressed(
        _inside_root(root, descriptor.physical_path),
        spec,
        dictionary_data,
        expected_sha256=descriptor.logical_sha256,
        expected_bytes=descriptor.logical_bytes,
        physical_sha256=descriptor.physical_sha256,
        physical_bytes=descriptor.physical_bytes,
        deadline_at=deadline_at,
        max_output_bytes=max_output_bytes,
    )


def _iter_cold_archive(
    entry: dict[str, Any],
    *,
    deadline_at: float | None,
    max_output_bytes: int,
) -> Iterator[bytes]:
    spec = CodecSpec(CODEC_GZIP, DEFAULT_GZIP_LEVEL)
    yield from _iter_decompressed(
        Path(entry["object_path"]),
        spec,
        None,
        expected_sha256=entry["logical_sha256"],
        expected_bytes=entry["logical_bytes"],
        physical_sha256=entry["object_sha256"],
        physical_bytes=entry["object_size_bytes"],
        deadline_at=deadline_at,
        max_output_bytes=max_output_bytes,
    )


def _open_logical_artifact(
    root: Path,
    path: str | Path,
    *,
    expected_sha256: str | None,
    expected_bytes: int | None,
    dictionaries: Mapping[str, bytes] | None,
    dictionary_path: str | Path | None,
    deadline_at: float | None,
    max_output_bytes: int,
    manifest_path: Path,
) -> tuple[ObjectDescriptor, Iterator[bytes], list[str]]:
    root = Path(root).absolute()
    relative = _relative(root, path)
    logical = _inside_root(root, relative)
    manifest = _read_manifest(manifest_path, root)
    entry = manifest["artifacts"].get(relative)
    attempts: list[str] = []

    def confirmed(descriptor: ObjectDescriptor) -> None:
        if expected_sha256 is not None and descriptor.logical_sha256 != _sha256(expected_sha256):
            raise ArtifactIntegrityError(
                f"Resolved artifact hash differs from the requested binding: {relative}"
            )
        if expected_bytes is not None and descriptor.logical_bytes != expected_bytes:
            raise ArtifactIntegrityError(
                f"Resolved artifact length differs from the requested binding: {relative}"
            )

    if entry is not None:
        legacy_digest: str | None = None
        legacy_size: int | None = None
        if logical.exists() or logical.is_symlink():
            _regular(logical)
            legacy_digest, legacy_size, _ = _digest(logical, deadline_at=deadline_at)
            if (
                legacy_digest != entry["logical_sha256"]
                or legacy_size != entry["logical_bytes"]
            ):
                raise ArtifactIntegrityError(
                    f"Legacy artifact differs from its manifest binding: {relative}"
                )
        deferred_errors: list[ArtifactStorageError] = []
        for representation in sorted(entry["representations"], key=_representation_sort_key):
            descriptor = _representation_descriptor(relative, entry, representation, legacy=False)
            confirmed(descriptor)
            physical = _inside_root(root, descriptor.physical_path)
            if not physical.exists() and not physical.is_symlink():
                attempts.append(f"{descriptor.codec}:missing")
                continue
            effective_dictionaries = dictionaries
            if descriptor.codec == CODEC_ZSTD:
                if not zstd_available():
                    attempts.append("zstd:unavailable")
                    deferred_errors.append(
                        CompressionUnavailableError(
                            "Zstandard runtime is not packaged for this interpreter"
                        )
                    )
                    continue
                dictionary_sha = descriptor.dictionary_sha256
                if dictionary_sha:
                    supplied = dictionaries is not None and dictionary_sha in dictionaries
                    if not supplied and dictionary_path is None:
                        attempts.append("zstd:dictionary_missing")
                        deferred_errors.append(
                            ArtifactContractError(
                                "Representation requires a Zstandard dictionary that was not supplied"
                            )
                        )
                        continue
                    if not supplied:
                        try:
                            dictionary_data = _load_dictionary(
                                dictionary_sha,
                                dictionaries=dictionaries,
                                dictionary_path=dictionary_path,
                                deadline_at=deadline_at,
                            )
                        except ArtifactStorageError as exc:
                            attempts.append("zstd:dictionary_missing")
                            deferred_errors.append(exc)
                            continue
                        effective_dictionaries = dict(dictionaries or {})
                        effective_dictionaries[dictionary_sha] = dictionary_data
            attempts.append(f"{descriptor.codec}:selected")
            return (
                descriptor,
                _iter_manifest_representation(
                    root,
                    descriptor,
                    dictionaries=effective_dictionaries,
                    dictionary_path=dictionary_path,
                    deadline_at=deadline_at,
                    max_output_bytes=max_output_bytes,
                ),
                attempts,
            )
        if legacy_digest is not None and legacy_size is not None:
            descriptor = ObjectDescriptor(
                logical_path=relative,
                logical_sha256=entry["logical_sha256"],
                logical_bytes=entry["logical_bytes"],
                content_schema=entry["content_schema"],
                content_encoding=entry["content_encoding"],
                codec=CODEC_IDENTITY,
                codec_level=None,
                dictionary_sha256=None,
                physical_path=relative,
                physical_sha256=legacy_digest,
                physical_bytes=legacy_size,
                legacy=True,
            )
            confirmed(descriptor)
            attempts.append("legacy_hot:selected")
            return (
                descriptor,
                _iter_legacy_file(
                    root,
                    logical,
                    expected_sha256=descriptor.logical_sha256,
                    expected_bytes=descriptor.logical_bytes,
                    deadline_at=deadline_at,
                    max_output_bytes=max_output_bytes,
                ),
                attempts,
            )
        cold = _cold_archive_binding(root, logical)
        if cold is not None:
            descriptor, iterator = _cold_archive_stream(
                cold,
                relative,
                deadline_at=deadline_at,
                max_output_bytes=max_output_bytes,
            )
            if (
                descriptor.logical_sha256 != entry["logical_sha256"]
                or descriptor.logical_bytes != entry["logical_bytes"]
            ):
                raise ArtifactIntegrityError(
                    f"Cold archive receipt differs from the manifest binding: {relative}"
                )
            confirmed(descriptor)
            attempts.append("legacy_cold_archive:selected")
            return descriptor, iterator, attempts
        if deferred_errors:
            raise deferred_errors[0]
        raise ArtifactMissingError(
            f"No installed representation can serve {relative}: {', '.join(attempts)}"
        )

    if logical.exists() or logical.is_symlink():
        _regular(logical)
        impostor = _already_compressed(logical)
        if impostor and Path(relative).suffix.lower() in _SCHEMAS:
            raise ArtifactContractError(
                f"Artifact {relative} is a {impostor} stream without a representation manifest"
            )
        digest, size, _ = _digest(logical, deadline_at=deadline_at)
        schema, encoding = _content_metadata(relative, None, None)
        descriptor = ObjectDescriptor(
            logical_path=relative,
            logical_sha256=digest,
            logical_bytes=size,
            content_schema=schema,
            content_encoding=encoding,
            codec=CODEC_IDENTITY,
            codec_level=None,
            dictionary_sha256=None,
            physical_path=relative,
            physical_sha256=digest,
            physical_bytes=size,
            legacy=True,
        )
        confirmed(descriptor)
        attempts.append("legacy_hot:selected")
        return (
            descriptor,
            _iter_legacy_file(
                root,
                logical,
                expected_sha256=descriptor.logical_sha256,
                expected_bytes=descriptor.logical_bytes,
                deadline_at=deadline_at,
                max_output_bytes=max_output_bytes,
            ),
            attempts,
        )

    cold = _cold_archive_binding(root, logical)
    if cold is not None:
        descriptor, iterator = _cold_archive_stream(
            cold,
            relative,
            deadline_at=deadline_at,
            max_output_bytes=max_output_bytes,
        )
        confirmed(descriptor)
        attempts.append("legacy_cold_archive:selected")
        return descriptor, iterator, attempts
    raise ArtifactMissingError(f"Artifact is not hot, manifested or cold-archived: {relative}")


class ArtifactStream:
    """A verified streaming read that refuses to finish partially.

    ``complete`` becomes true only when the underlying iterator raised
    ``StopIteration`` *after* the final length and SHA-256 checks passed.  Using
    the object as a context manager raises ``ArtifactReadIncompleteError`` when
    the caller stops early, so a truncated copy can never be mistaken for the
    artifact.
    """

    def __init__(
        self,
        descriptor: ObjectDescriptor,
        iterator: Iterator[bytes],
        attempts: list[str],
    ) -> None:
        self.descriptor = descriptor
        self.attempts = tuple(attempts)
        self.complete = False
        self.consumed = 0
        self._iterator = iterator

    def __iter__(self) -> "ArtifactStream":
        return self

    def __next__(self) -> bytes:
        try:
            block = next(self._iterator)
        except StopIteration:
            self.complete = True
            raise
        self.consumed += len(block)
        return block

    def abandon(self) -> None:
        """Close without consuming any logical byte; no completeness is claimed."""
        if self.consumed:
            raise ArtifactReadIncompleteError(
                f"Artifact read already consumed bytes: {self.descriptor.logical_path}"
            )
        self.close()
        self.complete = True

    def close(self) -> None:
        if not self.complete:
            try:
                self._iterator.close()
            except GeneratorExit:
                pass

    def __enter__(self) -> "ArtifactStream":
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        if not self.complete:
            self.close()
            if exc_type is None:
                raise ArtifactReadIncompleteError(
                    f"Artifact read stopped before verification: {self.descriptor.logical_path}"
                )
        return False


def stream_artifact(
    root: str | Path,
    path: str | Path,
    *,
    expected_sha256: str | None = None,
    expected_bytes: int | None = None,
    dictionaries: Mapping[str, bytes] | None = None,
    dictionary_path: str | Path | None = None,
    max_output_bytes: int = DEFAULT_MAX_OBJECT_BYTES,
    max_seconds: float | None = None,
    deadline_at: float | None = None,
    manifest_path: str | Path | None = None,
) -> ArtifactStream:
    """Open one logical artifact through legacy, manifested or cold storage."""
    root_path = Path(root).absolute()
    manifest = (
        Path(manifest_path).absolute() if manifest_path is not None else _default_manifest_path(root_path)
    )
    deadline_at = _operation_deadline(max_seconds, deadline_at)
    descriptor, iterator, attempts = _open_logical_artifact(
        root_path,
        path,
        expected_sha256=expected_sha256,
        expected_bytes=expected_bytes,
        dictionaries=dictionaries,
        dictionary_path=dictionary_path,
        deadline_at=deadline_at,
        max_output_bytes=max_output_bytes,
        manifest_path=manifest,
    )
    return ArtifactStream(descriptor, iterator, attempts)


def resolve_artifact(
    root: str | Path,
    path: str | Path,
    *,
    expected_sha256: str | None = None,
    expected_bytes: int | None = None,
    dictionaries: Mapping[str, bytes] | None = None,
    dictionary_path: str | Path | None = None,
    max_bytes: int = DEFAULT_MAX_IN_MEMORY_BYTES,
    max_seconds: float | None = None,
    manifest_path: str | Path | None = None,
) -> ResolvedArtifact:
    """Return exact logical bytes; raise instead of returning partial evidence."""
    if max_bytes <= 0:
        raise ArtifactContractError("max_bytes must be positive")
    with stream_artifact(
        root,
        path,
        expected_sha256=expected_sha256,
        expected_bytes=expected_bytes,
        dictionaries=dictionaries,
        dictionary_path=dictionary_path,
        max_output_bytes=max_bytes,
        max_seconds=max_seconds,
        manifest_path=manifest_path,
    ) as stream:
        if stream.descriptor.logical_bytes > max_bytes:
            raise ArtifactTooLargeError(
                f"Artifact declares {stream.descriptor.logical_bytes} bytes, above the "
                f"{max_bytes}-byte in-memory bound; stream or restore it instead"
            )
        buffer = io.BytesIO()
        for block in stream:
            buffer.write(block)
        data = buffer.getvalue()
    return ResolvedArtifact(descriptor=stream.descriptor, data=data)


def copy_artifact(
    root: str | Path,
    path: str | Path,
    destination: str | Path,
    *,
    expected_sha256: str | None = None,
    expected_bytes: int | None = None,
    dictionaries: Mapping[str, bytes] | None = None,
    dictionary_path: str | Path | None = None,
    max_output_bytes: int = DEFAULT_MAX_OBJECT_BYTES,
    max_seconds: float | None = None,
    deadline_at: float | None = None,
    manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    """Stream a verified artifact to a destination without installing partial bytes."""
    deadline_at = _operation_deadline(max_seconds, deadline_at)
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor: ObjectDescriptor | None = None
    temporary: Path | None = None
    try:
        with stream_artifact(
            root,
            path,
            expected_sha256=expected_sha256,
            expected_bytes=expected_bytes,
            dictionaries=dictionaries,
            dictionary_path=dictionary_path,
            max_output_bytes=max_output_bytes,
            max_seconds=max_seconds,
            deadline_at=deadline_at,
            manifest_path=manifest_path,
        ) as stream:
            descriptor = stream.descriptor
            if target.exists() and not target.is_symlink() and target.stat().st_size == descriptor.logical_bytes:
                if _digest(target)[0] == descriptor.logical_sha256:
                    stream.abandon()
                    return {
                        "logical_path": descriptor.logical_path,
                        "sha256": descriptor.logical_sha256,
                        "size_bytes": descriptor.logical_bytes,
                        "attempts": list(stream.attempts),
                        "reused": True,
                    }
            descriptor_fd, temporary_name = tempfile.mkstemp(prefix=".restore-", dir=target.parent)
            temporary = Path(temporary_name)
            written = 0
            with os.fdopen(descriptor_fd, "wb") as output:
                for block in stream:
                    written += len(block)
                    output.write(block)
                output.flush()
                os.fsync(output.fileno())
            if written != descriptor.logical_bytes:
                raise ArtifactIntegrityError("Copied artifact length differs from its descriptor")
            os.replace(temporary, target)
            temporary = None
            _sync_dir(target.parent)
        return {
            "logical_path": descriptor.logical_path,
            "sha256": descriptor.logical_sha256,
            "size_bytes": descriptor.logical_bytes,
            "attempts": list(stream.attempts),
            "reused": False,
        }
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@contextmanager
def _shared_owner(shared_lock: str | Path | None, *, owner_fd: int | None = None) -> Iterator[None]:
    """Acquire the existing dispatcher/worker owner before touching storage."""
    if shared_lock is None:
        raise ArtifactContractError("Execution requires the actual shared owner lock path")
    path = Path(shared_lock).absolute()
    before = _regular(path)
    if owner_fd is not None:
        from jobagg.runtime_source_head import owner
        if path.resolve() != path:
            raise ArtifactContractError("Shared owner path is aliased")
        owner(owner_fd, path)
        opened = os.fstat(owner_fd)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ArtifactIntegrityError("Borrowed shared owner differs")
        yield
        owner(owner_fd, path)
        return
    descriptor = os.open(path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "r+b") as handle:
        opened = os.fstat(handle.fileno())
        identity = (before.st_dev, before.st_ino)
        if identity != (opened.st_dev, opened.st_ino):
            raise ArtifactIntegrityError("Shared owner lock changed while opening")
        try:
            _lock_nonblocking(handle)
        except BlockingIOError as exc:
            raise ArtifactStorageError("Shared owner lock is already held") from exc
        try:
            latest = _regular(path)
            if identity != (latest.st_dev, latest.st_ino):
                raise ArtifactIntegrityError("Shared owner lock changed after acquisition")
            yield
        finally:
            _unlock_store(handle)


@contextmanager
def _store_lock(manifest_path: Path, *, wait_seconds: float | None = 30.0) -> Iterator[None]:
    """Serialize writers of this manifest in addition to the shared owner lock.

    A bounded wait uses a non-blocking flock/ftime loop so a concurrent writer is
    rejected with a deterministic error instead of hanging the caller.  A
    ``None`` wait retains the normal blocking behaviour.
    """
    lock_path = manifest_path.with_name(f"{manifest_path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + wait_seconds if wait_seconds is not None else None
    # This writable metadata open must not follow a locally planted symlink: an
    # fchmod through one would change an unrelated file outside the store.
    before = _regular(lock_path) if lock_path.exists() or lock_path.is_symlink() else None
    descriptor = os.open(
        lock_path,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise ArtifactContractError(f"Storage lock is not a regular file: {lock_path}")
        latest = lock_path.lstat()
        if (latest.st_dev, latest.st_ino) != (opened.st_dev, opened.st_ino):
            raise ArtifactIntegrityError("Storage lock changed while opening")
        if before is not None and (before.st_dev, before.st_ino) != (
            opened.st_dev,
            opened.st_ino,
        ):
            raise ArtifactIntegrityError("Storage lock changed while opening")
        handle = os.fdopen(descriptor, "r+b")
    except Exception:
        os.close(descriptor)
        raise
    with handle:
        if os.name != "nt":
            os.fchmod(handle.fileno(), 0o600)
        while True:
            try:
                _lock_nonblocking(handle)
                break
            except BlockingIOError as exc:
                if deadline is not None and time.monotonic() >= deadline:
                    raise ArtifactStorageError(
                        f"Another process holds the storage owner lock: {lock_path}"
                    ) from exc
                time.sleep(0.05)
        try:
            yield
        finally:
            _unlock_store(handle)


def _lock_nonblocking(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        if not handle.read(1):
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_store(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _codec_for(codec: str, level: int | None) -> CodecSpec:
    spec = normalize_codec(codec, level)
    if spec.name == CODEC_ZSTD:
        return spec
    return spec


def _is_sqlite(relative: str) -> bool:
    return Path(relative).suffix.lower() in {".sqlite3", ".sqlite"}


def preview_artifact(
    root: str | Path,
    path: str | Path,
    *,
    codec: str = DEFAULT_CODEC,
    level: int | None = None,
    measure: bool = True,
    dictionary_sha256: str | None = None,
    dictionaries: Mapping[str, bytes] | None = None,
    dictionary_path: str | Path | None = None,
    sealed_sqlite: bool = False,
    min_bytes: int = DEFAULT_MIN_COMPRESS_BYTES,
    max_object_bytes: int = DEFAULT_MAX_OBJECT_BYTES,
    max_measure_bytes: int = DEFAULT_MAX_MEASURE_BYTES,
    max_seconds: float | None = None,
    deadline_at: float | None = None,
    manifest_path: str | Path | None = None,
    scratch_parent: str | Path | None = None,
) -> dict[str, Any]:
    """Measure a prospective representation without writing into the store.

    Nothing is created under ``root``.  When ``measure`` is true the logical
    bytes are copied to a private system-temporary directory, compressed there,
    decompressed again and compared with the pinned hash.  The scratch directory
    is always removed before returning.
    """
    root_path = Path(root).absolute()
    _directory(root_path)
    _reject_store_relative(_relative(root_path, path))
    if max_object_bytes <= 0 or max_measure_bytes <= 0:
        raise ArtifactContractError("Preview bounds must be positive")
    spec = normalize_codec(codec, level)
    if dictionary_sha256 is not None:
        _sha256(dictionary_sha256)
        if spec.name != CODEC_ZSTD:
            raise ArtifactContractError(
                "Zstandard dictionaries cannot be used with gzip compression"
            )
        spec = CodecSpec(spec.name, spec.level, dictionary_sha256)
    deadline_at = _operation_deadline(max_seconds, deadline_at)
    manifest = (
        Path(manifest_path).absolute()
        if manifest_path is not None
        else _default_manifest_path(root_path)
    )
    report: dict[str, Any] = {
        "logical_path": None,
        "logical_sha256": None,
        "logical_bytes": None,
        "content_schema": None,
        "content_encoding": None,
        "codec": spec.name,
        "codec_level": spec.level,
        "dictionary_sha256": spec.dictionary_sha256,
        "measure": bool(measure),
        "eligible": False,
        "blocked_reason": None,
        "compressed_bytes": None,
        "compressed_sha256": None,
        "physical_path": None,
        "savings_bytes": None,
        "savings_ratio": None,
        "scratch_bytes": 0,
        "attempts": [],
        "verified": False,
        "measured_utc": None,
    }
    with stream_artifact(
        root_path,
        path,
        dictionaries=dictionaries,
        dictionary_path=dictionary_path,
        max_output_bytes=max_measure_bytes,
        max_seconds=max_seconds,
        deadline_at=deadline_at,
        manifest_path=manifest,
    ) as stream:
        descriptor = stream.descriptor
        report.update(
            {
                "logical_path": descriptor.logical_path,
                "logical_sha256": descriptor.logical_sha256,
                "logical_bytes": descriptor.logical_bytes,
                "content_schema": descriptor.content_schema,
                "content_encoding": descriptor.content_encoding,
                "attempts": list(stream.attempts),
                "physical_path": _relative(
                    root_path,
                    _object_path_for(root_path, spec, descriptor.logical_sha256),
                ),
            }
        )
        if descriptor.logical_bytes > max_measure_bytes:
            raise ArtifactTooLargeError(
                f"Artifact exceeds the {max_measure_bytes}-byte preview bound"
            )
        if _is_sqlite(descriptor.logical_path) and not sealed_sqlite:
            report["blocked_reason"] = "sqlite_requires_sealed_snapshot"
            stream.abandon()
            return report
        if _is_sqlite(descriptor.logical_path):
            _check_sqlite_candidate(
                _inside_root(root_path, descriptor.logical_path), sealed=True
            )
        if descriptor.logical_bytes < max(0, min_bytes):
            report["blocked_reason"] = "below_min_bytes"
            stream.abandon()
            return report
        if not measure:
            report["eligible"] = True
            report["blocked_reason"] = "not_measured"
            stream.abandon()
            return report
        block = _measure_representation(
            root_path,
            stream,
            spec,
            dictionaries=dictionaries,
            dictionary_path=dictionary_path,
            deadline_at=deadline_at,
            max_object_bytes=max_object_bytes,
            scratch_parent=scratch_parent,
        )
    report["scratch_bytes"] = block["scratch_bytes"]
    report["compressed_bytes"] = block["physical_bytes"]
    report["compressed_sha256"] = block["physical_sha256"]
    report["verified"] = block["verified"]
    report["measured_utc"] = _utc()
    savings = descriptor.logical_bytes - block["physical_bytes"]
    report["savings_bytes"] = savings
    report["savings_ratio"] = (
        round(block["physical_bytes"] / descriptor.logical_bytes, 6)
        if descriptor.logical_bytes
        else None
    )
    if savings <= 0:
        report["blocked_reason"] = "no_measured_saving"
    else:
        report["eligible"] = True
    return report


def _ensure_object_directory(path: Path) -> None:
    """Create the content-addressed shard with owner-only permissions."""
    parent = path.parent
    if parent.exists() or parent.is_symlink():
        _directory(parent)
        return
    parent.mkdir(parents=True, exist_ok=True)
    parent.chmod(0o700)
    _sync_dir(parent.parent)


def _object_path_for(root: Path, spec: CodecSpec, logical_sha256: str) -> Path:
    suffix = ".zst" if spec.name == CODEC_ZSTD else ".gz"
    name = f"{logical_sha256}.{spec.object_suffix}{suffix}"
    return root / STORAGE_DIRECTORY / OBJECTS_DIRECTORY / logical_sha256[:2] / name


def _measure_representation(
    root: Path,
    stream: ArtifactStream,
    spec: CodecSpec,
    *,
    dictionaries: Mapping[str, bytes] | None,
    dictionary_path: str | Path | None,
    deadline_at: float | None,
    max_object_bytes: int,
    scratch_parent: str | Path | None = None,
) -> dict[str, Any]:
    """Round-trip a prospective object in scratch and prove it reconstructs."""
    descriptor = stream.descriptor
    dictionary_data = _load_dictionary(
        spec.dictionary_sha256,
        dictionaries=dictionaries,
        dictionary_path=dictionary_path,
        deadline_at=deadline_at,
    )
    with tempfile.TemporaryDirectory(prefix="jobagg-preview-", dir=scratch_parent) as scratch_name:
        scratch = Path(scratch_name)
        source = scratch / "source.bin"
        written = 0
        with source.open("wb") as output:
            for block in stream:
                written += len(block)
                output.write(block)
            output.flush()
            os.fsync(output.fileno())
        if written != descriptor.logical_bytes:
            raise ArtifactIntegrityError("Preview source copy length differs from descriptor")
        candidate = scratch / "candidate.bin"
        physical = _write_compressed(
            source,
            candidate,
            spec,
            dictionary_data,
            deadline_at=deadline_at,
            max_source_bytes=descriptor.logical_bytes,
            max_object_bytes=max_object_bytes,
        )
        digest, total = hashlib.sha256(), 0
        for block in _iter_decompressed(
            candidate,
            spec,
            dictionary_data,
            expected_sha256=descriptor.logical_sha256,
            expected_bytes=descriptor.logical_bytes,
            physical_sha256=physical["sha256"],
            physical_bytes=physical["size"],
            deadline_at=deadline_at,
            max_output_bytes=descriptor.logical_bytes,
        ):
            total += len(block)
            digest.update(block)
    return {
        "physical_bytes": physical["size"],
        "physical_sha256": physical["sha256"],
        "verified": digest.hexdigest() == descriptor.logical_sha256
        and total == descriptor.logical_bytes,
        "scratch_bytes": written + physical["size"],
    }


def compress_artifact(
    root: str | Path,
    path: str | Path,
    *,
    codec: str = DEFAULT_CODEC,
    level: int | None = None,
    execute: bool = False,
    retire_legacy: bool = False,
    readers_support_manifest: bool = False,
    retirement_approved: bool = False,
    sealed_sqlite: bool = False,
    dictionary_sha256: str | None = None,
    dictionaries: Mapping[str, bytes] | None = None,
    dictionary_path: str | Path | None = None,
    min_bytes: int = DEFAULT_MIN_COMPRESS_BYTES,
    max_object_bytes: int = DEFAULT_MAX_OBJECT_BYTES,
    max_measure_bytes: int = DEFAULT_MAX_MEASURE_BYTES,
    max_seconds: float | None = None,
    deadline_at: float | None = None,
    manifest_path: str | Path | None = None,
    scratch_parent: str | Path | None = None,
    owner_lock_seconds: float = 30.0,
    shared_lock: str | Path | None = None,
    owner_fd: int | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Preview by default; install a representation only with ``execute=True``.

    Retirement of the legacy logical file is opt-in and requires both
    ``readers_support_manifest`` and ``retirement_approved``.  The caller asserts
    that every reader understands the manifest and that no unresolved dependency
    still needs the original bytes; this module enforces the mechanical gates and
    records the retirement request in the durable journal intent. The caller must
    supply the actual existing dispatcher/worker ``shared_lock`` path; the
    separate manifest lock only serializes representation writers.
    """
    deadline_at = _operation_deadline(max_seconds, deadline_at)
    if retire_legacy and not (readers_support_manifest and retirement_approved):
        raise ArtifactContractError(
            "Legacy retirement requires reader-compatibility and dependency approval"
        )
    root_path = Path(root).absolute()
    manifest = (
        Path(manifest_path).absolute()
        if manifest_path is not None
        else _default_manifest_path(root_path)
    )
    preview = preview_artifact(
        root_path,
        path,
        codec=codec,
        level=level,
        measure=True,
        dictionary_sha256=dictionary_sha256,
        dictionaries=dictionaries,
        dictionary_path=dictionary_path,
        sealed_sqlite=sealed_sqlite,
        min_bytes=min_bytes,
        max_object_bytes=max_object_bytes,
        max_measure_bytes=max_measure_bytes,
        max_seconds=max_seconds,
        deadline_at=deadline_at,
        scratch_parent=scratch_parent,
        manifest_path=manifest,
    )
    report = {
        **preview,
        "executed": False,
        "retired": False,
        "removed_bytes": 0,
        "installed_utc": None,
        "manifest_sha256": None,
        "intent_id": None,
    }
    if not execute:
        return report
    if not preview["eligible"] or not preview["verified"]:
        raise ArtifactContractError(
            f"Artifact cannot be installed: {preview['blocked_reason'] or 'measurement failed'}"
        )
    spec = CodecSpec(preview["codec"], preview["codec_level"], preview["dictionary_sha256"])
    _check_deadline(deadline_at)
    owner_scope = (_shared_owner(shared_lock) if owner_fd is None
                   else _shared_owner(shared_lock, owner_fd=owner_fd))
    with owner_scope:
        layout = _ensure_store(root_path, manifest)
        with _store_lock(manifest, wait_seconds=min(owner_lock_seconds, max(0, deadline_at-time.monotonic())) if deadline_at is not None else owner_lock_seconds):
            installed = _install_representation(
                root_path, layout, preview, spec,
                retire_legacy=retire_legacy, readers_support_manifest=readers_support_manifest,
                retirement_approved=retirement_approved, dictionaries=dictionaries,
                dictionary_path=dictionary_path, deadline_at=deadline_at,
                max_object_bytes=max_object_bytes, max_measure_bytes=max_measure_bytes, run_id=run_id,
            )
    report.update(installed)
    report["executed"] = True
    return report


def _stage_source(
    root: Path,
    layout: dict[str, Path],
    relative: str,
    descriptor: ObjectDescriptor,
    *,
    dictionaries: Mapping[str, bytes] | None,
    dictionary_path: str | Path | None,
    deadline_at: float | None,
    max_measure_bytes: int,
) -> Path:
    """Copy the logical bytes to a scratch source when no hot file remains."""
    scratch = layout["temporary"] / f"source-{uuid.uuid4().hex}"
    written = 0
    try:
        with scratch.open("xb") as output:
            with stream_artifact(
                root,
                relative,
                dictionaries=dictionaries,
                dictionary_path=dictionary_path,
                max_output_bytes=max_measure_bytes,
                manifest_path=layout["manifest"],
            ) as stream:
                for block in stream:
                    written += len(block)
                    output.write(block)
            output.flush()
            os.fsync(output.fileno())
        if written != descriptor.logical_bytes:
            raise ArtifactIntegrityError("Staged source length differs from its descriptor")
        return scratch
    except Exception:
        scratch.unlink(missing_ok=True)
        raise


def _verify_round_trip(
    candidate: Path,
    spec: CodecSpec,
    dictionary_data: bytes | None,
    descriptor: ObjectDescriptor,
    physical: dict[str, Any],
    *,
    deadline_at: float | None,
) -> None:
    digest, total = hashlib.sha256(), 0
    for block in _iter_decompressed(
        candidate,
        spec,
        dictionary_data,
        expected_sha256=descriptor.logical_sha256,
        expected_bytes=descriptor.logical_bytes,
        physical_sha256=physical["sha256"],
        physical_bytes=physical["size"],
        deadline_at=deadline_at,
        max_output_bytes=descriptor.logical_bytes,
    ):
        total += len(block)
        digest.update(block)
    if total != descriptor.logical_bytes or digest.hexdigest() != descriptor.logical_sha256:
        raise ArtifactIntegrityError(
            "Candidate object does not reconstruct the pinned logical artifact"
        )


def _install_representation(
    root: Path,
    layout: dict[str, Path],
    preview: dict[str, Any],
    spec: CodecSpec,
    *,
    retire_legacy: bool,
    readers_support_manifest: bool,
    retirement_approved: bool,
    dictionaries: Mapping[str, bytes] | None,
    dictionary_path: str | Path | None,
    deadline_at: float | None,
    max_object_bytes: int,
    max_measure_bytes: int,
    run_id: str | None,
) -> dict[str, Any]:
    relative = preview["logical_path"]
    logical = _inside_root(root, relative)
    manifest = _read_manifest(layout["manifest"], root)
    existing = manifest["artifacts"].get(relative)
    if existing is not None and existing["logical_sha256"] != preview["logical_sha256"]:
        raise ArtifactContractError(
            "Immutable logical artifact already has a different recorded identity"
        )
    target = _object_path_for(root, spec, preview["logical_sha256"])
    target_relative = _relative(root, target)
    _inside_root(root, target_relative)
    if _is_sqlite(relative):
        _check_sqlite_candidate(logical, sealed=True)
    dictionary_data = _load_dictionary(
        spec.dictionary_sha256,
        dictionaries=dictionaries,
        dictionary_path=dictionary_path,
        deadline_at=deadline_at,
    )
    installed_utc = _utc()
    placeholder = ObjectDescriptor(
        logical_path=relative,
        logical_sha256=preview["logical_sha256"],
        logical_bytes=preview["logical_bytes"],
        content_schema=preview["content_schema"],
        content_encoding=preview["content_encoding"],
        codec=spec.name,
        codec_level=spec.level,
        dictionary_sha256=spec.dictionary_sha256,
        physical_path=target_relative,
        physical_sha256="",
        physical_bytes=0,
        legacy=False,
        installed_utc=installed_utc,
        verified_utc=installed_utc,
    )
    temporary = layout["temporary"] / f"object-{uuid.uuid4().hex}"
    reused = target.exists() or target.is_symlink()
    if reused:
        physical_digest, physical_bytes, _ = _digest(target, deadline_at=deadline_at)
        if (physical_digest, physical_bytes) != (
            preview["compressed_sha256"],
            preview["compressed_bytes"],
        ):
            raise ArtifactIntegrityError("Existing object differs from the measured candidate")
        _verify_physical(
            target,
            spec,
            expected_sha256=physical_digest,
            expected_bytes=physical_bytes,
            deadline_at=deadline_at,
            max_bytes=physical_bytes,
        )
        # Deduplication must not skip the current-evidence check: the hot file may
        # have changed after preview while the measured object already existed.
        if logical.exists() or logical.is_symlink():
            source_digest, source_bytes, _ = _digest(logical, deadline_at=deadline_at)
            if (
                source_digest != preview["logical_sha256"]
                or source_bytes != preview["logical_bytes"]
            ):
                raise ArtifactIntegrityError("Source changed after preview")
        physical = {"sha256": physical_digest, "size": physical_bytes}
    else:
        staged: Path | None = None
        source = logical
        try:
            if not logical.exists() and not logical.is_symlink():
                staged = _stage_source(
                    root,
                    layout,
                    relative,
                    placeholder,
                    dictionaries=dictionaries,
                    dictionary_path=dictionary_path,
                    deadline_at=deadline_at,
                    max_measure_bytes=max_measure_bytes,
                )
                source = staged
            before = _digest(source, deadline_at=deadline_at)
            if before[0] != preview["logical_sha256"] or before[1] != preview["logical_bytes"]:
                raise ArtifactIntegrityError("Source changed after preview")
            physical = _write_compressed(
                source,
                temporary,
                spec,
                dictionary_data,
                deadline_at=deadline_at,
                max_source_bytes=preview["logical_bytes"],
                max_object_bytes=max_object_bytes,
            )
            if physical["sha256"] != preview["compressed_sha256"] or physical["size"] != preview["compressed_bytes"]:
                raise ArtifactIntegrityError(
                    "Installed candidate differs from the measured representation"
                )
            after = _digest(source, deadline_at=deadline_at)
            if after[2] != before[2]:
                raise ArtifactIntegrityError("Source changed while it was being compressed")
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        finally:
            if staged is not None:
                staged.unlink(missing_ok=True)
    descriptor = ObjectDescriptor(
        logical_path=relative,
        logical_sha256=preview["logical_sha256"],
        logical_bytes=preview["logical_bytes"],
        content_schema=preview["content_schema"],
        content_encoding=preview["content_encoding"],
        codec=spec.name,
        codec_level=spec.level,
        dictionary_sha256=spec.dictionary_sha256,
        physical_path=target_relative,
        physical_sha256=physical["sha256"],
        physical_bytes=physical["size"],
        legacy=False,
        installed_utc=installed_utc,
        verified_utc=installed_utc,
    )
    if not reused:
        _verify_round_trip(
            temporary, spec, dictionary_data, descriptor, physical, deadline_at=deadline_at
        )
    return _commit_representation(
        root,
        layout,
        manifest,
        descriptor,
        temporary=None if reused else temporary,
        retire_legacy=retire_legacy,
        readers_support_manifest=readers_support_manifest,
        retirement_approved=retirement_approved,
        run_id=run_id,
        deadline_at=deadline_at,
    )


def _journal_event(intent_id: str, kind: str, **fields: Any) -> dict[str, Any]:
    return {"kind": kind, "intent_id": intent_id, "at_utc": _utc(), **fields}


def _commit_representation(
    root: Path,
    layout: dict[str, Path],
    manifest: dict[str, Any],
    descriptor: ObjectDescriptor,
    *,
    temporary: Path | None,
    retire_legacy: bool,
    readers_support_manifest: bool,
    retirement_approved: bool,
    run_id: str | None,
    deadline_at: float | None,
) -> dict[str, Any]:
    relative = descriptor.logical_path
    target = root / descriptor.physical_path
    intent_id = uuid.uuid4().hex
    intent = JournalIntent(
        intent_id=intent_id,
        logical_path=relative,
        logical_sha256=descriptor.logical_sha256,
        logical_bytes=descriptor.logical_bytes,
        physical_path=descriptor.physical_path,
        physical_sha256=descriptor.physical_sha256,
        physical_bytes=descriptor.physical_bytes,
        codec=descriptor.codec,
        retired_legacy=retire_legacy,
        codec_level=descriptor.codec_level,
        dictionary_sha256=descriptor.dictionary_sha256,
    )
    _append_journal(
        layout["journal"], _intent_record(intent, run_id=run_id or intent_id), deadline_at=deadline_at
    )
    spec = CodecSpec(descriptor.codec, descriptor.codec_level, descriptor.dictionary_sha256)
    created = False
    manifest_sha = ""
    removed_bytes = 0
    try:
        if temporary is not None:
            _ensure_object_directory(target)
            try:
                if target.exists() or target.is_symlink():
                    _verify_physical(
                        target,
                        spec,
                        expected_sha256=descriptor.physical_sha256,
                        expected_bytes=descriptor.physical_bytes,
                        deadline_at=deadline_at,
                        max_bytes=descriptor.physical_bytes,
                    )
                else:
                    os.replace(temporary, target)
                    created = True
                    _sync_dir(target.parent)
            finally:
                temporary.unlink(missing_ok=True)
        _verify_physical(
            target,
            spec,
            expected_sha256=descriptor.physical_sha256,
            expected_bytes=descriptor.physical_bytes,
            deadline_at=deadline_at,
            max_bytes=descriptor.physical_bytes,
        )
        updated = _merge_entry(manifest, relative, descriptor)
        manifest_sha = _write_manifest(layout["manifest"], updated, deadline_at=deadline_at)
        _append_journal(
            layout["journal"],
            _journal_event(
                intent_id,
                "installed",
                logical_path=relative,
                physical_path=descriptor.physical_path,
                physical_sha256=descriptor.physical_sha256,
                physical_bytes=descriptor.physical_bytes,
                manifest_sha256=manifest_sha,
                retired_legacy=retire_legacy,
            ),
            deadline_at=deadline_at,
        )
        if retire_legacy:
            if not (readers_support_manifest and retirement_approved):
                raise ArtifactContractError(
                    "Legacy retirement requires reader-compatibility and dependency approval"
                )
            removed_bytes, manifest_sha = _retire_legacy(
                root,
                layout,
                descriptor,
                manifest_sha=manifest_sha,
                deadline_at=deadline_at,
            )
            if removed_bytes:
                _append_journal(
                    layout["journal"],
                    _journal_event(
                        intent_id,
                        "retired",
                        logical_path=relative,
                        removed_bytes=removed_bytes,
                        manifest_sha256=manifest_sha,
                    ),
                    deadline_at=deadline_at,
                )
        _append_journal(
            layout["journal"],
            _journal_event(
                intent_id,
                "complete",
                logical_path=relative,
                physical_path=descriptor.physical_path,
                physical_sha256=descriptor.physical_sha256,
                physical_bytes=descriptor.physical_bytes,
                removed_bytes=removed_bytes,
                manifest_sha256=manifest_sha,
            ),
            deadline_at=deadline_at,
        )
    except Exception as exc:
        if not manifest_sha:
            # ``_write_manifest`` replaces the manifest before its directory fsync.
            # A failure after that point leaves a committed manifest even though the
            # return assignment never completed, so an empty ``manifest_sha`` alone
            # is not proof that nothing was written.  Preserve the object and keep
            # the intent open for journal reconciliation instead of deleting evidence.
            committed = False
            recovered_sha = ""
            reread_unknown = False
            try:
                current = _read_manifest(layout["manifest"], root)
                item = current["artifacts"].get(relative)
                if (
                    item is not None
                    and item["logical_sha256"] == descriptor.logical_sha256
                    and item["logical_bytes"] == descriptor.logical_bytes
                ):
                    committed = any(
                        representation["physical_path"] == descriptor.physical_path
                        and representation["physical_sha256"] == descriptor.physical_sha256
                        and representation["physical_bytes"] == descriptor.physical_bytes
                        for representation in item["representations"]
                    )
                if committed:
                    recovered_sha = hashlib.sha256(
                        layout["manifest"].read_bytes()
                    ).hexdigest()
                    manifest_sha = recovered_sha
            except Exception:
                reread_unknown = True
            if committed or reread_unknown:
                try:
                    _append_journal(
                        layout["journal"],
                        _journal_event(
                            intent_id,
                            "commit_uncertain",
                            logical_path=relative,
                            physical_path=descriptor.physical_path,
                            manifest_sha256=recovered_sha,
                            error=f"{type(exc).__name__}: {exc}",
                        ),
                        deadline_at=deadline_at,
                    )
                except Exception:
                    pass
            else:
                if created:
                    target.unlink(missing_ok=True)
                _append_journal(
                    layout["journal"],
                    _journal_event(intent_id, "failed", error=f"{type(exc).__name__}: {exc}"),
                    deadline_at=deadline_at,
                )
        raise
    return {
        "retired": removed_bytes > 0,
        "removed_bytes": removed_bytes,
        "installed_utc": descriptor.installed_utc,
        "manifest_sha256": manifest_sha,
        "intent_id": intent_id,
        "physical_path": descriptor.physical_path,
        "physical_sha256": descriptor.physical_sha256,
        "physical_bytes": descriptor.physical_bytes,
    }


def _retire_legacy(
    root: Path,
    layout: dict[str, Path],
    descriptor: ObjectDescriptor,
    *,
    manifest_sha: str,
    deadline_at: float | None,
) -> tuple[int, str]:
    """Remove the hot logical file after the representation is durable."""
    relative = descriptor.logical_path
    logical = _inside_root(root, relative)
    current = _read_manifest(layout["manifest"], root)
    entry = current["artifacts"].get(relative)
    if entry is None:
        raise ArtifactContractError("Retirement requires a committed manifest entry")
    removed_bytes = 0
    if logical.exists() or logical.is_symlink():
        digest, size, _ = _digest(logical, deadline_at=deadline_at)
        if digest != descriptor.logical_sha256 or size != descriptor.logical_bytes:
            raise ArtifactIntegrityError("Legacy file changed before retirement")
        logical.unlink()
        _sync_dir(logical.parent)
        removed_bytes = size
        current = _read_manifest(layout["manifest"], root)
        entry = current["artifacts"][relative]
        entry["legacy"] = {
            "path": relative,
            "retired": True,
            "retired_utc": _utc(),
            "removed_bytes": removed_bytes,
        }
        entry["representations"] = [
            representation
            for representation in entry["representations"]
            if representation["codec"] != CODEC_IDENTITY
        ]
        entry["updated_utc"] = _utc()
        manifest_sha = _write_manifest(layout["manifest"], current, deadline_at=deadline_at)
    elif not entry["legacy"]["retired"]:
        # The hot file is already absent.  Record the observed state without
        # claiming historical bytes as newly removed by this operation.
        entry["legacy"]["retired"] = True
        entry["legacy"]["retired_utc"] = _utc()
        entry["legacy"]["removed_bytes"] = 0
        entry["representations"] = [
            representation
            for representation in entry["representations"]
            if representation["codec"] != CODEC_IDENTITY
        ]
        entry["updated_utc"] = _utc()
        manifest_sha = _write_manifest(layout["manifest"], current, deadline_at=deadline_at)
    return removed_bytes, manifest_sha


def _descriptor_from_intent(intent: JournalIntent) -> ObjectDescriptor:
    schema, encoding = _content_metadata(intent.logical_path, None, None)
    return ObjectDescriptor(
        logical_path=intent.logical_path,
        logical_sha256=intent.logical_sha256,
        logical_bytes=intent.logical_bytes,
        content_schema=schema,
        content_encoding=encoding,
        codec=intent.codec,
        codec_level=intent.codec_level,
        dictionary_sha256=intent.dictionary_sha256,
        physical_path=intent.physical_path,
        physical_sha256=intent.physical_sha256,
        physical_bytes=intent.physical_bytes,
        legacy=False,
        installed_utc=_utc(),
        verified_utc=_utc(),
    )


def _cleanup_temporary(layout: dict[str, Path]) -> tuple[int, int]:
    """Remove staged files left by a writer that stopped before cleanup."""
    removed_files = 0
    removed_bytes = 0
    for entry in sorted(layout["temporary"].iterdir()):
        info = entry.lstat()
        if stat.S_ISDIR(info.st_mode):
            continue
        entry.unlink(missing_ok=True)
        removed_files += 1
        removed_bytes += info.st_size
    if removed_files:
        _sync_dir(layout["temporary"])
    return removed_files, removed_bytes


def reconcile_artifacts(
    root: str | Path,
    *,
    manifest_path: str | Path | None = None,
    owner_lock_seconds: float = 30.0,
    shared_lock: str | Path | None = None,
    max_objects: int = 1000,
    dictionaries: Mapping[str, bytes] | None = None,
    dictionary_path: str | Path | None = None,
    max_seconds: float | None = None,
) -> dict[str, Any]:
    """Resume durable install/retirement intents left open by an interruption."""
    root_path = Path(root).absolute()
    _directory(root_path)
    manifest = (
        Path(manifest_path).absolute()
        if manifest_path is not None
        else _default_manifest_path(root_path)
    )
    deadline_at = _deadline(max_seconds)
    if max_objects <= 0:
        raise ArtifactContractError("Reconcile object bound must be positive")
    summary: dict[str, Any] = {
        "open_intents": 0,
        "processed_intents": 0,
        "completed": 0,
        "retired": 0,
        "removed_bytes": 0,
        "abandoned": 0,
        "held": 0,
        "temporary_files_removed": 0,
        "temporary_bytes_removed": 0,
        "objects": [],
    }
    with _shared_owner(shared_lock):
        layout = _ensure_store(root_path, manifest)
        with _store_lock(manifest, wait_seconds=owner_lock_seconds):
            records = _read_journal(layout["journal"])
            intents = _open_intents(records)
            summary["open_intents"] = len(intents)
            current = _read_manifest(layout["manifest"], root_path)
            for intent_id, intent in sorted(intents.items())[:max_objects]:
                _check_deadline(deadline_at)
                outcome = _reconcile_intent(
                    root_path,
                    layout,
                    current,
                    intent,
                    deadline_at=deadline_at,
                    dictionaries=dictionaries, dictionary_path=dictionary_path,
                    journal_records=records,
                )
                current = outcome["manifest"]
                summary[outcome["state"]] = summary.get(outcome["state"], 0) + 1
                summary["processed_intents"] += 1
                removed = outcome.get("removed_bytes", 0)
                if removed:
                    summary["retired"] += 1
                    summary["removed_bytes"] += removed
                summary["objects"].append(
                    {
                        "intent_id": intent_id,
                        "logical_path": intent.logical_path,
                        "state": outcome["state"],
                        "removed_bytes": outcome.get("removed_bytes", 0),
                    }
                )
            (
                summary["temporary_files_removed"],
                summary["temporary_bytes_removed"],
            ) = _cleanup_temporary(layout)
    summary["completed"] = sum(
        1 for item in summary["objects"] if item["state"] == "completed"
    )
    return summary


def _reconcile_intent(
    root: Path,
    layout: dict[str, Path],
    manifest: dict[str, Any],
    intent: JournalIntent,
    *,
    dictionaries: Mapping[str, bytes] | None = None,
    dictionary_path: str | Path | None = None,
    deadline_at: float | None,
    journal_records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    relative = intent.logical_path
    object_path = _inside_root(root, intent.physical_path)
    if not object_path.exists() and not object_path.is_symlink():
        _append_journal(
            layout["journal"],
            _journal_event(intent.intent_id, "abandoned", reason="object_missing"),
            deadline_at=deadline_at,
        )
        return {"state": "abandoned", "manifest": manifest}
    spec = CodecSpec(intent.codec, intent.codec_level, intent.dictionary_sha256)
    dictionary_data = _load_dictionary(
        intent.dictionary_sha256, dictionaries=dictionaries,
        dictionary_path=dictionary_path, deadline_at=deadline_at,
    )
    # A physical hash alone does not prove this object reconstructs the intent.
    # Verify logical bytes before publishing a manifest or retiring the original.
    _verify_round_trip(
        object_path, spec, dictionary_data, _descriptor_from_intent(intent),
        {"sha256": intent.physical_sha256, "size": intent.physical_bytes},
        deadline_at=deadline_at,
    )
    entry = manifest["artifacts"].get(relative)
    dirty = False
    if entry is None:
        descriptor = _descriptor_from_intent(intent)
        manifest = _merge_entry(manifest, relative, descriptor)
        dirty = True
    elif entry["logical_sha256"] != intent.logical_sha256 or entry["logical_bytes"] != intent.logical_bytes:
        _append_journal(
            layout["journal"],
            _journal_event(intent.intent_id, "held", reason="logical_identity_conflict"),
            deadline_at=deadline_at,
        )
        return {"state": "held", "manifest": manifest}
    elif intent.physical_path not in {
        representation["physical_path"] for representation in entry["representations"]
    }:
        descriptor = _descriptor_from_intent(intent)
        manifest = _merge_entry(manifest, relative, descriptor)
        dirty = True
    if dirty:
        _write_manifest(layout["manifest"], manifest, deadline_at=deadline_at)
    removed = 0
    if intent.retired_legacy:
        retired_logged = _journal_event_logged(
            journal_records or [], kind="retired", logical_path=relative
        )
        logical = _inside_root(root, relative)
        if logical.exists() or logical.is_symlink():
            digest, size, _ = _digest(logical, deadline_at=deadline_at)
            if digest != intent.logical_sha256 or size != intent.logical_bytes:
                _append_journal(
                    layout["journal"],
                    _journal_event(intent.intent_id, "held", reason="legacy_file_changed"),
                    deadline_at=deadline_at,
                )
                return {"state": "held", "manifest": manifest}
            descriptor = _descriptor_from_intent(intent)
            removed, _ = _retire_legacy(
                root,
                layout,
                descriptor,
                manifest_sha="",
                deadline_at=deadline_at,
            )
        else:
            entry = manifest["artifacts"].get(relative)
            if entry is not None and not retired_logged:
                if entry["legacy"]["retired"]:
                    # The manifest already recorded this retirement, including the
                    # bytes that were removed.  Attribute them once to the recovery
                    # summary instead of reporting zero merely because the hot file
                    # is gone by the time reconciliation observes it.
                    removed = entry["legacy"]["removed_bytes"]
                else:
                    removed = intent.logical_bytes
                    entry["legacy"]["retired"] = True
                    entry["legacy"]["retired_utc"] = _utc()
                    entry["legacy"]["removed_bytes"] = removed
                    entry["representations"] = [
                        representation
                        for representation in entry["representations"]
                        if representation["codec"] != CODEC_IDENTITY
                    ]
                    entry["updated_utc"] = _utc()
                    _write_manifest(layout["manifest"], manifest, deadline_at=deadline_at)
    final = _read_manifest(layout["manifest"], root)
    manifest_sha = hashlib.sha256(_manifest_bytes(final)).hexdigest()
    if removed and not _journal_event_logged(
        journal_records or [], kind="retired", logical_path=relative
    ):
        retired_event = _journal_event(
            intent.intent_id,
            "retired",
            logical_path=relative,
            removed_bytes=removed,
            manifest_sha256=manifest_sha,
        )
        _append_journal(
            layout["journal"],
            retired_event,
            deadline_at=deadline_at,
        )
        if journal_records is not None:
            journal_records.append(retired_event)
    _append_journal(
        layout["journal"],
        _journal_event(
            intent.intent_id,
            "complete",
            logical_path=relative,
            physical_path=intent.physical_path,
            physical_sha256=intent.physical_sha256,
            physical_bytes=intent.physical_bytes,
            removed_bytes=removed,
            manifest_sha256=manifest_sha,
        ),
        deadline_at=deadline_at,
    )
    return {"state": "completed", "manifest": final, "removed_bytes": removed}


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--max-seconds", type=float)


def _add_measurement_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--codec",
        choices=(
            "auto",
            *(codec for codec in available_codecs() if codec != CODEC_IDENTITY),
        ),
        default=DEFAULT_CODEC,
    )
    parser.add_argument("--level", type=int)
    parser.add_argument("--dictionary-sha256")
    parser.add_argument("--dictionary-file", type=Path)
    parser.add_argument("--min-bytes", type=int, default=DEFAULT_MIN_COMPRESS_BYTES)
    parser.add_argument(
        "--max-object-bytes", type=int, default=DEFAULT_MAX_OBJECT_BYTES
    )
    parser.add_argument(
        "--max-measure-bytes", type=int, default=DEFAULT_MAX_MEASURE_BYTES
    )
    parser.add_argument("--sealed-sqlite", action="store_true")


def _artifact_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--path", type=Path, required=True, dest="artifact_path")


def _measurement_arguments(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "codec": args.codec,
        "level": args.level,
        "dictionary_sha256": args.dictionary_sha256,
        "dictionary_path": args.dictionary_file,
        "min_bytes": args.min_bytes,
        "max_object_bytes": args.max_object_bytes,
        "max_measure_bytes": args.max_measure_bytes,
        "sealed_sqlite": args.sealed_sqlite,
        "max_seconds": args.max_seconds,
        "manifest_path": args.manifest,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    codecs = commands.add_parser("codecs", help="list compiled compression codecs")
    codecs.set_defaults(handler=lambda _args: {"codecs": list(available_codecs())})

    preview = commands.add_parser("preview", help="measure without writing the store")
    _add_common_options(preview)
    _add_measurement_options(preview)
    _artifact_arguments(preview)
    preview.add_argument("--no-measure", action="store_false", dest="measure")

    compress = commands.add_parser(
        "compress", help="preview by default; install with --execute"
    )
    _add_common_options(compress)
    _add_measurement_options(compress)
    _artifact_arguments(compress)
    compress.add_argument("--shared-lock", type=Path)
    compress.add_argument("--execute", action="store_true")
    compress.add_argument("--retire-legacy", action="store_true")
    compress.add_argument("--readers-support-manifest", action="store_true")
    compress.add_argument("--retirement-approved", action="store_true")
    compress.add_argument("--owner-lock-seconds", type=float, default=30.0)
    compress.add_argument("--run-id")

    reconcile = commands.add_parser(
        "reconcile", help="resume durable install/retirement intents"
    )
    _add_common_options(reconcile)
    reconcile.add_argument("--shared-lock", type=Path, required=True)
    reconcile.add_argument("--dictionary-file", type=Path)
    reconcile.add_argument("--owner-lock-seconds", type=float, default=30.0)
    reconcile.add_argument("--max-objects", type=int, default=1000)

    restore = commands.add_parser(
        "restore-sqlite", help="restore and verify a sealed SQLite snapshot"
    )
    _add_common_options(restore)
    _artifact_arguments(restore)
    restore.add_argument("--scratch-parent", type=Path)
    restore.add_argument("--dictionary-file", type=Path)
    restore.add_argument("--reserve-bytes", type=int, default=DEFAULT_RESTORE_RESERVE_BYTES)
    restore.add_argument(
        "--keep", action="store_true", help="retain verified scratch for the operator"
    )

    copy = commands.add_parser("copy", help="stream a verified representation to a file")
    _add_common_options(copy)
    _artifact_arguments(copy)
    copy.add_argument("--destination", type=Path, required=True)
    copy.add_argument("--dictionary-file", type=Path)
    copy.add_argument("--max-output-bytes", type=int, default=DEFAULT_MAX_OBJECT_BYTES)
    return parser


def restore_sqlite(
    root: str | Path,
    path: str | Path,
    *,
    scratch_parent: str | Path | None = None,
    expected_sha256: str | None = None,
    expected_bytes: int | None = None,
    max_output_bytes: int = DEFAULT_MAX_OBJECT_BYTES,
    reserve_bytes: int = DEFAULT_RESTORE_RESERVE_BYTES,
    max_seconds: float | None = None,
    deadline_at: float | None = None,
    manifest_path: str | Path | None = None,
    dictionaries: Mapping[str, bytes] | None = None,
    dictionary_path: str | Path | None = None,
) -> RestoredSnapshot:
    """Restore a sealed SQLite artifact to private scratch and verify it opens.

    The caller must clean up the returned snapshot (or use it as a context
    manager).  The scratch directory is created with owner-only permissions and
    the restored file is opened ``mode=ro&immutable=1``.
    """
    deadline_at = _operation_deadline(max_seconds, deadline_at)
    root_path = Path(root).absolute()
    relative = _relative(root_path, path)
    if not _is_sqlite(relative):
        raise ArtifactContractError("SQLite restore requires a .sqlite3 or .sqlite path")
    _check_sqlite_candidate(_inside_root(root_path, relative), sealed=True)
    if reserve_bytes < 0:
        raise ArtifactContractError("Restore reserve must be non-negative")
    manifest = (
        Path(manifest_path).absolute()
        if manifest_path is not None
        else _default_manifest_path(root_path)
    )
    with stream_artifact(
        root_path,
        path,
        expected_sha256=expected_sha256,
        expected_bytes=expected_bytes,
        max_output_bytes=max_output_bytes,
        dictionaries=dictionaries,
        dictionary_path=dictionary_path,
        max_seconds=max_seconds,
        deadline_at=deadline_at,
        manifest_path=manifest,
    ) as stream:
        descriptor = stream.descriptor
        stream.abandon()
    parent = Path(scratch_parent) if scratch_parent is not None else None
    free = shutil.disk_usage(parent if parent is not None else tempfile.gettempdir()).free
    required = descriptor.logical_bytes + reserve_bytes
    if free < required:
        raise ArtifactStorageError(
            f"Restore needs {required} free bytes but only {free} are available"
        )
    scratch_root = Path(tempfile.mkdtemp(prefix="jobagg-sqlite-", dir=parent))
    scratch_root.chmod(0o700)
    target = scratch_root / "snapshot.sqlite3"
    try:
        result = copy_artifact(
            root_path,
            path,
            target,
            expected_sha256=expected_sha256,
            expected_bytes=expected_bytes,
            dictionaries=dictionaries,
            dictionary_path=dictionary_path,
            max_output_bytes=descriptor.logical_bytes,
            max_seconds=max_seconds,
            deadline_at=deadline_at,
            manifest_path=manifest,
        )
        if result["sha256"] != descriptor.logical_sha256:
            raise ArtifactIntegrityError("Restored SQLite hash differs from its descriptor")
        with target.open("rb") as handle:
            header = handle.read(16)
        if header != b"SQLite format 3\x00":
            raise ArtifactIntegrityError("Restored file is not a SQLite database")
        target.chmod(0o400)
        snapshot = RestoredSnapshot(
            path=target,
            scratch_root=scratch_root,
            sha256=result["sha256"],
            size=result["size_bytes"],
        )
        _check_deadline(deadline_at)
        connection = snapshot.open_readonly()
        connection.set_progress_handler(lambda: int(deadline_at is not None and time.monotonic() >= deadline_at), 256)
        try:
            row = connection.execute("PRAGMA quick_check").fetchone()
        finally:
            connection.close()
        _check_deadline(deadline_at)
        if row is None or row[0] != "ok":
            raise ArtifactIntegrityError("Restored SQLite database failed PRAGMA quick_check")
        return snapshot
    except Exception:
        shutil.rmtree(scratch_root, ignore_errors=True)
        raise


def _run_command(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "preview":
        return preview_artifact(
            args.root,
            args.artifact_path,
            measure=args.measure,
            **_measurement_arguments(args),
        )
    if args.command == "compress":
        return compress_artifact(
            args.root,
            args.artifact_path,
            execute=args.execute,
            retire_legacy=args.retire_legacy,
            readers_support_manifest=args.readers_support_manifest,
            retirement_approved=args.retirement_approved,
            owner_lock_seconds=args.owner_lock_seconds,
            shared_lock=args.shared_lock,
            run_id=args.run_id,
            **_measurement_arguments(args),
        )
    if args.command == "reconcile":
        return reconcile_artifacts(
            args.root,
            manifest_path=args.manifest,
            owner_lock_seconds=args.owner_lock_seconds,
            shared_lock=args.shared_lock,
            max_objects=args.max_objects,
            dictionary_path=args.dictionary_file,
            max_seconds=args.max_seconds,
        )
    if args.command == "restore-sqlite":
        snapshot = restore_sqlite(
            args.root,
            args.artifact_path,
            scratch_parent=args.scratch_parent,
            reserve_bytes=args.reserve_bytes,
            max_seconds=args.max_seconds,
            manifest_path=args.manifest,
            dictionary_path=args.dictionary_file,
        )
        report = {
            "logical_path": _relative(Path(args.root).absolute(), args.artifact_path),
            "sha256": snapshot.sha256,
            "size_bytes": snapshot.size,
            "verified": True,
            "scratch_retained": args.keep,
            "path": str(snapshot.path) if args.keep else None,
            "scratch_root": str(snapshot.scratch_root) if args.keep else None,
        }
        if not args.keep:
            snapshot.cleanup()
        return report
    if args.command == "copy":
        return copy_artifact(
            args.root,
            args.artifact_path,
            args.destination,
            dictionaries=None,
            dictionary_path=args.dictionary_file,
            max_output_bytes=args.max_output_bytes,
            max_seconds=args.max_seconds,
            manifest_path=args.manifest,
        )
    raise ArtifactContractError(f"Unrecognized command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "codecs":
            result = args.handler(args)
        else:
            result = _run_command(args)
    except (ArtifactStorageError, OSError, sqlite3.Error, TimeoutError) as exc:
        print(
            json.dumps(
                {
                    "command": args.command,
                    "error": type(exc).__name__,
                    "message": str(exc),
                },
                indent=2,
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
