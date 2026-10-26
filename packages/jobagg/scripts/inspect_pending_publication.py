#!/usr/bin/env python3
"""Bounded metadata observation; never publication verification or acknowledgement."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import time


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
    # A normal live WAL reader uses existing SQLite shared-memory reader slots.
    # Refuse a missing-sidecar setup rather than deliberately creating sidecars.
    with path.open("rb") as handle:
        wal_mode = handle.read(20)[18:20] == b"\x02\x02"
    if wal_mode:
        for suffix in ("-wal", "-shm"):
            with Path(str(path) + suffix).open("rb"):
                pass
    started = time.monotonic()
    deadline = started + max_seconds
    report = {
        "schema": "jobagg-pending-inspection-1", "started_at_utc": utc_now(),
        "database": str(path), "scope": "current pending publication metadata only",
        "publication_verified": False, "acknowledgement_authorized": False,
        "complete": False, "atomic_observation": False, "observed_pending_units": 0,
        "limits": {"max_units": max_units, "page_size": page_size,
                   "max_seconds": max_seconds, "sample": sample},
        "sample": [],
    }
    groups = Counter()
    cursor = None
    initial = None
    conn = None
    try:
        # Never immutable=1: a live WAL contains committed state absent from the DB file.
        conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0)
        conn.execute("PRAGMA query_only=ON")
        conn.set_authorizer(authorize)
        conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        while True:
            if time.monotonic() >= deadline:
                report["stop_reason"] = "time_limit"
                break
            conn.execute("BEGIN")
            current = boundary(conn)
            if initial is None:
                initial = current
                report["boundary"] = initial
            if current != initial:
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
                if len(report["sample"]) < sample:
                    report["sample"].append({
                        "unit": unit, "version": version, "source_id": source,
                        "kind": kind, "first_changed_at": changed,
                        "reason": reason, "operation": operation,
                    })
                if "oldest_pending_at_unix" not in report:
                    report["oldest_pending_at_unix"] = changed
                cursor = (changed, unit)
            # A commit between the last page snapshot and this check invalidates the census.
            if boundary(conn) != initial:
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
        print(json.dumps({"complete": False, "stop_reason": "input_error", "error": str(exc)}))
        return 2
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0 if report["complete"] else 2


if __name__ == "__main__":
    sys.exit(main())
