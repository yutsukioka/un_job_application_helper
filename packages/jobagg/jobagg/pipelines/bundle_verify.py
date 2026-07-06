"""Pre-import validation for local jobagg bundle files."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

SQLITE_HEADER_MAGIC = b"SQLite format 3\x00"
DEFAULT_MAX_BUNDLE_BYTES = int(
    os.environ.get("JOBAGG_BUNDLE_MAX_BYTES", str(256 * 1024 * 1024))
)


@dataclass(frozen=True, slots=True)
class BundleVerifyResult:
    checked_files: tuple[Path, ...]
    total_bytes: int
    errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors


def verify_bundle_path(
    path: str | Path,
    *,
    max_bytes: int = DEFAULT_MAX_BUNDLE_BYTES,
) -> BundleVerifyResult:
    root = Path(path)
    errors: list[str] = []
    if max_bytes <= 0:
        return BundleVerifyResult((), 0, ("max_bytes must be positive",))

    try:
        db_paths = _db_paths(root)
        bundle_files = _candidate_bundle_files(root, db_paths)
        total_bytes = _total_size(bundle_files)
    except OSError as exc:
        return BundleVerifyResult((), 0, (f"bundle path cannot be inspected: {exc}",))
    except ValueError as exc:
        return BundleVerifyResult((), 0, (str(exc),))

    if total_bytes > max_bytes:
        return BundleVerifyResult(
            tuple(bundle_files),
            total_bytes,
            (f"bundle size {total_bytes} bytes exceeds cap {max_bytes} bytes",),
        )

    for db_path in db_paths:
        errors.extend(_validate_sqlite_database(db_path))
        if not any(str(db_path) in error for error in errors):
            errors.extend(_validate_json_sidecars(db_path))

    return BundleVerifyResult(tuple(bundle_files), total_bytes, tuple(errors))


def _db_paths(path: Path) -> list[Path]:
    if path.is_dir():
        db_paths = sorted(item for item in path.glob("*_jobs.sqlite3") if item.is_file())
        if not db_paths:
            raise ValueError(f"no *_jobs.sqlite3 files found in {path}")
        return db_paths
    if path.is_file():
        if path.name.endswith("_jobs.sqlite3"):
            return [path]
        raise ValueError(f"bundle database path must end with _jobs.sqlite3: {path}")
    raise ValueError(f"bundle path does not exist: {path}")


def _candidate_bundle_files(root: Path, db_paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    if root.is_dir():
        files.extend(item for item in root.iterdir() if item.is_file())
        return sorted(files)
    for db_path in db_paths:
        files.append(db_path)
        slug = db_path.name[: -len("_jobs.sqlite3")]
        for suffix in ("_jobs_current.json", "_jobs_history.json"):
            sidecar = db_path.with_name(f"{slug}{suffix}")
            if sidecar.exists():
                files.append(sidecar)
    return sorted(files)


def _total_size(paths: list[Path]) -> int:
    return sum(path.stat().st_size for path in paths)


def _validate_sqlite_database(path: Path) -> list[str]:
    errors: list[str] = []
    try:
        with path.open("rb") as handle:
            header = handle.read(len(SQLITE_HEADER_MAGIC))
    except OSError as exc:
        return [f"{path}: cannot read SQLite header: {exc}"]
    if header != SQLITE_HEADER_MAGIC:
        return [f"{path}: invalid SQLite header magic"]

    try:
        uri = "file:" + quote(str(path), safe="/") + "?mode=ro"
        with sqlite3.connect(uri, uri=True) as conn:
            row = conn.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.DatabaseError as exc:
        return [f"{path}: SQLite integrity_check failed: {exc}"]
    if row is None or row[0] != "ok":
        errors.append(f"{path}: SQLite integrity_check failed: {row[0] if row else 'no result'}")
    return errors


def _validate_json_sidecars(db_path: Path) -> list[str]:
    errors: list[str] = []
    slug = db_path.name[: -len("_jobs.sqlite3")]
    for suffix in ("_jobs_current.json", "_jobs_history.json"):
        path = db_path.with_name(f"{slug}{suffix}")
        if not path.is_file():
            errors.append(f"{path}: missing JSON sidecar")
            continue
        errors.extend(_validate_json_export(path))
    return errors


def _validate_json_export(path: Path) -> list[str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [f"{path}: invalid JSON export: {exc}"]
    if not isinstance(payload, list):
        return [f"{path}: JSON export must be a list"]
    for index, row in enumerate(payload):
        if not isinstance(row, dict):
            return [f"{path}: JSON export row {index} must be an object"]
        for field in ("source_id", "title", "status"):
            if field not in row:
                return [f"{path}: JSON export row {index} missing required field {field}"]
    return []
