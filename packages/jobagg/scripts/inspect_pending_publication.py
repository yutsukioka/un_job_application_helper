#!/usr/bin/env python3
"""Bounded metadata observation; never publication verification or acknowledgement."""

import argparse
import ctypes
import errno
import fcntl
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time


REPORT_ROOT = Path("/private/tmp" if sys.platform == "darwin" else "/tmp")


TABLES = {
    "jobagg_runtime_state", "jobagg_dirty_state",
    "jobagg_dirty_current", "jobagg_dirty_versions",
}
PAGE_SQL = """
SELECT c.unit,c.version,c.source_id,c.kind,c.first_changed_at,c.reason,v.operation
FROM jobagg_dirty_current AS c INDEXED BY jobagg_dirty_pending
LEFT JOIN jobagg_dirty_versions AS v ON v.version=c.version AND v.unit=c.unit
WHERE c.disposition='pending' AND (c.first_changed_at,c.unit) > (?,?)
ORDER BY c.first_changed_at,c.unit LIMIT ?
"""


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def authorize(action, first, second, database, trigger):
    """Deny mutations, attachments, and reads outside the four metadata tables."""
    if action == sqlite3.SQLITE_READ:
        return sqlite3.SQLITE_OK if first in TABLES else sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_PRAGMA:
        return sqlite3.SQLITE_OK if first == "data_version" else sqlite3.SQLITE_DENY
    if action in {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_TRANSACTION}:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def boundary(conn):
    runtime = conn.execute(
        "SELECT schema,epoch,revision,phase,config_sha256,source_transaction_id "
        "FROM jobagg_runtime_state WHERE id=1"
    ).fetchone()
    dirty = conn.execute(
        "SELECT schema,epoch,contract_sha256,bootstrap_complete "
        "FROM jobagg_dirty_state WHERE id=1"
    ).fetchone()
    if not runtime or not dirty:
        raise ValueError("Missing native authority metadata")
    if runtime[0] != "jobagg-runtime-authority-3" or dirty[0] != "publication-work-3":
        raise ValueError("Unsupported native authority/publication schema")
    return {
        "runtime_schema": runtime[0], "epoch": runtime[1], "revision": runtime[2],
        "phase": runtime[3], "config_sha256": runtime[4],
        "source_transaction_id": runtime[5], "publication_schema": dirty[0],
        "publication_epoch": dirty[1], "contract_sha256": dirty[2],
        "bootstrap_complete": bool(dirty[3]),
        "connection_data_version": conn.execute("PRAGMA data_version").fetchone()[0],
    }


def inspect(database, *, max_units=10000, page_size=100, max_seconds=5, sample=0):
    if not (1 <= max_units <= 100000 and 1 <= page_size <= 500
            and 0 < max_seconds <= 30 and 0 <= sample <= 100):
        raise ValueError("Limits: units 1..100000, page 1..500, seconds (0,30], sample 0..100")
    path = Path(database).resolve(strict=True)
    started = time.monotonic()
    deadline = started + max_seconds
    report = {
        "schema": "jobagg-pending-inspection-2", "started_at_utc": utc_now(),
        "database": str(path), "scope": "current pending publication metadata only",
        "publication_verified": False, "acknowledgement_authorized": False,
        "complete": False, "atomic_observation": False, "observed_pending_units": 0,
        "limits": {"max_units": max_units, "page_size": page_size,
                   "max_seconds": max_seconds, "sample": sample},
        "sample": [], "units": [],
        "database_identity": file_identity(path),
    }
    groups = Counter()
    cursor = None
    initial = None
    conn = None
    try:
        conn = open_reader(path, deadline)
        report["reader_mode"] = "private_filesystem_clone"
        while True:
            if time.monotonic() >= deadline:
                report["stop_reason"] = "time_limit"
                break
            conn.execute("BEGIN")
            current = boundary(conn)
            if initial is None:
                initial = current
                report["boundary"] = initial
            if current != initial or not reader_unchanged(conn):
                conn.rollback()
                report["stop_reason"] = "database_changed"
                break
            size = min(page_size, max_units - report["observed_pending_units"])
            sql = PAGE_SQL
            params = (size + 1,)
            if cursor is None:
                sql = sql.replace(" AND (c.first_changed_at,c.unit) > (?,?)", "")
            else:
                params = (*cursor, size + 1)
            rows = conn.execute(sql, params).fetchall()
            conn.rollback()  # Release the snapshot before processing or sleeping.
            for unit, version, source, kind, changed, reason, operation in rows[:size]:
                if changed is None or unit is None or version is None or operation is None:
                    raise ValueError("Pending metadata is missing its ordering/version provenance")
                groups[(source, kind, reason, operation)] += 1
                report["observed_pending_units"] += 1
                record = {
                    "unit": unit, "version": version, "source_id": source,
                    "kind": kind, "first_changed_at": changed,
                    "reason": reason, "operation": operation,
                }
                report["units"].append(record)
                if len(report["sample"]) < sample:
                    report["sample"].append(record)
                if "oldest_pending_at_unix" not in report:
                    report["oldest_pending_at_unix"] = changed
                cursor = (changed, unit)
            # A commit between the last page snapshot and this check invalidates the census.
            if (boundary(conn) != initial or not reader_unchanged(conn)
                    or file_identity(path) != report["database_identity"]):
                report["stop_reason"] = "database_changed"
                break
            if time.monotonic() >= deadline:
                report["stop_reason"] = "time_limit"
                break
            if len(rows) <= size:
                report.update(complete=True, atomic_observation=True, stop_reason="exhausted")
                break
            if report["observed_pending_units"] >= max_units:
                report["stop_reason"] = "unit_limit"
                break
            time.sleep(min(0.025, max(0, deadline - time.monotonic())))
    except sqlite3.Error as exc:
        report["stop_reason"] = (
            "time_limit" if time.monotonic() >= deadline else "database_error"
        )
        report["error"] = str(exc)
    except ValueError as exc:
        report.update(stop_reason="invalid_metadata", error=str(exc))
    finally:
        if conn is not None:
            conn.close()
    report["groups"] = [
        {"source_id": source, "kind": kind, "reason": reason,
         "operation": operation, "observed_units": count}
        for (source, kind, reason, operation), count in sorted(
            groups.items(), key=lambda item: json.dumps(item[0])
        )
    ]
    report["finished_at_utc"] = utc_now()
    report["elapsed_seconds"] = round(time.monotonic() - started, 6)
    return report


def file_identity(path):
    value = Path(path).stat()
    return {"device": value.st_dev, "inode": value.st_ino}


def source_signature(path):
    result = {}
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            value = Path(str(path) + suffix).stat()
            result[suffix] = (value.st_dev, value.st_ino, value.st_size,
                              value.st_mtime_ns, value.st_ctime_ns)
        except FileNotFoundError:
            result[suffix] = None
    return result


def clone_file(source, target):
    """Require filesystem CoW; never silently fall back to a multi-GB byte copy."""
    if sys.platform == "darwin":
        library = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        clone = library.clonefile
        clone.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
        clone.restype = ctypes.c_int
        if clone(os.fsencode(source), os.fsencode(target), 0):
            code = ctypes.get_errno()
            raise OSError(code, "Filesystem clone failed: " + os.strerror(code))
    elif sys.platform.startswith("linux"):
        with source.open("rb") as original, target.open("xb") as copied:
            fcntl.ioctl(copied.fileno(), 0x40049409, original.fileno())  # Linux FICLONE
    else:
        raise OSError(errno.ENOTSUP, "Read-only snapshots require filesystem cloning")
    target.chmod(0o600)


def snapshot_parent(path):
    device = path.stat().st_dev
    if REPORT_ROOT.stat().st_dev == device:
        return REPORT_ROOT
    # The OS temp root is on another volume. Use a private directory at the
    # source volume root, outside the application's production data subtree.
    root = path.parent
    while root.parent != root and root.parent.stat().st_dev == device:
        root = root.parent
    return root


class SnapshotReader(sqlite3.Connection):
    snapshot_directory = None

    def close(self):
        try:
            super().close()
        finally:
            if self.snapshot_directory is not None:
                self.snapshot_directory.cleanup()
                self.snapshot_directory = None


def reader_unchanged(conn):
    return source_signature(conn.source_path) == conn.source_signature


def open_reader(path, deadline):
    # Clone only while all source file clocks are stable. No SQLite connection
    # ever opens production, so missing WAL/SHM files are never created there.
    before = source_signature(path)
    if before[""] is None or before["-journal"] is not None:
        raise ValueError("Missing database or rollback journal requires separate review")
    directory = tempfile.TemporaryDirectory(prefix=".jobagg-readonly-", dir=snapshot_parent(path))
    conn = None
    try:
        copied = Path(directory.name) / "jobs.sqlite3"
        for suffix in ("", "-wal"):
            if time.monotonic() >= deadline:
                raise sqlite3.OperationalError("Snapshot capture time limit")
            if before[suffix] is not None:
                clone_file(Path(str(path) + suffix), Path(str(copied) + suffix))
        if source_signature(path) != before:
            raise ValueError("Database changed during snapshot capture; retry later")
        # A real WAL, when present, was cloned too. Normal SQLite WAL handling
        # runs entirely in the private directory; immutable=1 is never used.
        conn = sqlite3.connect(copied.as_uri() + "?mode=ro", uri=True,
                               timeout=0, factory=SnapshotReader)
        conn.snapshot_directory = directory
        conn.source_path = path
        conn.source_signature = before
        conn.execute("PRAGMA query_only=ON")
        conn.set_authorizer(authorize)
        conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        return conn
    except BaseException:
        if conn is not None:
            conn.close()
        directory.cleanup()
        raise


def save_report(report, prefix):
    """Create a private report in the OS temp root, independent of a live TMPDIR."""
    directory = Path(tempfile.mkdtemp(prefix=prefix, dir=REPORT_ROOT))
    target = directory / "report.json"
    with target.open("x", encoding="utf-8") as handle:
        os.chmod(target, 0o600)
        json.dump(report, handle, indent=2, allow_nan=False)
        handle.write("\n")
    return target


def print_saved_report(report, prefix):
    target = save_report(report, prefix)
    summary = {key: value for key, value in report.items() if key not in {"units", "comparisons"}}
    print(json.dumps({**summary, "report_file": str(target)}, indent=2, allow_nan=False), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--database", type=Path)
    source.add_argument("--config", type=Path, help="Read publication_worker_database only")
    parser.add_argument("--max-units", type=int, default=10000)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--max-seconds", type=float, default=5)
    parser.add_argument("--sample", type=int, default=0, help="Optional private identity sample (0..100)")
    args = parser.parse_args(argv)
    try:
        database = args.database
        if args.config:
            config = args.config.resolve(strict=True)
            database = Path(json.loads(config.read_text())["publication_worker_database"])
            if not database.is_absolute():
                database = config.parent / database
        report = inspect(database, max_units=args.max_units, page_size=args.page_size,
                         max_seconds=args.max_seconds, sample=args.sample)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        report = {"schema": "jobagg-pending-inspection-2", "complete": False,
                  "finished_at_utc": utc_now(), "units": [],
                  "stop_reason": "input_error", "error": str(exc)}
    try:
        print_saved_report(report, "jobagg-pending-inspection-")
    except OSError as exc:
        print(json.dumps({"complete": False, "stop_reason": "report_write_error",
                          "error": str(exc)}), flush=True)
        return 2
    return 0 if report["complete"] else 2


if __name__ == "__main__":
    sys.exit(main())
