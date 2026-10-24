"""Resolve accepted detail evidence independently of a mutable refresh task.

The successful attempt and its immutable artifact establish acceptance. A task
may already be pending, blocked or failed for a later refresh. This helper does
not certify current public availability, freshness, or whole-page fidelity.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time

from jobagg.publication_deadline import expired

from jobagg.remediation_scheduling import task_key


DETAIL_ATTEMPTS_QUERY = (
    "SELECT a.* FROM remediation_tasks t CROSS JOIN remediation_attempts a "
    "WHERE t.task_id=a.task_id AND t.source_id=? AND t.kind='detail' "
    "AND t.external_id=? AND a.source_id=t.source_id AND a.kind=t.kind "
    "AND a.status='done' AND a.finished_at IS NOT NULL "
    "ORDER BY a.finished_at DESC,a.attempt_id"
)


def ensure_detail_lineage_indexes(conn):
    """Idempotent migration for both new and existing worker databases."""
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_remediation_tasks_detail_identity "
        "ON remediation_tasks(source_id,kind,external_id,task_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_remediation_attempts_detail_lineage "
        "ON remediation_attempts(task_id,source_id,kind,status,finished_at DESC,attempt_id)"
    )


class _LineageDeadlineExceeded(TimeoutError):
    """The configured lineage budget expired, distinct from artifact I/O failures."""


def _check(deadline_at):
    if (deadline_at is not None and time.monotonic() >= deadline_at) or expired(None):
        raise _LineageDeadlineExceeded("Accepted detail lineage deadline exhausted")


def _add(timings, key, started):
    if timings is not None:
        timings[key] = timings.get(key, 0.0) + time.monotonic() - started


def _sha(path, *, deadline_at=None, timings=None):
    digest = hashlib.sha256()
    started = time.monotonic()
    stream = path.open("rb")
    _add(timings, "artifact_open_seconds", started)
    with stream:
        while True:
            started = time.monotonic()
            block = stream.read(1024 * 1024)
            _add(timings, "artifact_hash_read_seconds", started)
            _check(deadline_at)
            if not block:
                break
            started = time.monotonic()
            digest.update(block)
            _add(timings, "artifact_hash_cpu_seconds", started)
    return digest.hexdigest()


def matching_detail_attempts(conn, row, proof, *, deadline_at=None, timings=None, legacy_only=False):
    """Return only exact successful lineage, never evidence from a failed attempt.

    Receipt/artifact failures are non-matches; the caller rejects acceptance when no
    matching lineage exists. Keeping only these rows in a publication projection
    therefore preserves both successful binding and the missing-lineage rejection.
    """
    _check(deadline_at)
    cursor = conn.cursor()
    cursor.row_factory = sqlite3.Row
    sql_started = time.monotonic()
    from jobagg.accepted_evidence import selected_attempts
    bound = None if legacy_only else selected_attempts(conn, row, proof)
    if bound is not None:
        attempts = bound
    else:
        canonical = task_key(row["source_id"], "detail", str(row["external_id"]))
        # Legacy IDs remain eligible only through the same exact immutable identity
        # crosswalk. Orphan attempts cannot invent a task or borrow another source.
        candidates = [item[0] for item in cursor.execute(
            "SELECT task_id FROM remediation_tasks WHERE source_id=? AND kind='detail' AND external_id=?",
            (row["source_id"], str(row["external_id"])))]
        candidates.sort(key=lambda identity: (identity != canonical, identity))
        matching = []
        attempts = []
        for identity in candidates:
            _check(deadline_at)
            attempts.extend(cursor.execute(
                "SELECT * FROM remediation_attempts WHERE task_id=? AND source_id=? AND kind='detail' "
                "AND status='done' AND finished_at IS NOT NULL ORDER BY finished_at DESC,attempt_id",
                (identity, row["source_id"])).fetchall())
    attempts.sort(key=lambda attempt: (-attempt["finished_at"], attempt["attempt_id"]))
    _add(timings, "sql_seconds", sql_started)
    matching = []
    for attempt in attempts:
        _check(deadline_at)
        try:
            receipt = json.loads(attempt["evidence"] or "{}")
            from jobagg import artifact_locations, runtime_authority
            path = artifact_locations.resolve(conn, receipt["detail_path"], receipt["detail_sha256"],
                                              strict=runtime_authority.state(conn) is not None)
            started = time.monotonic()
            is_file = path.is_file()
            _add(timings, "artifact_metadata_seconds", started)
            if not is_file or _sha(path, deadline_at=deadline_at, timings=timings) != receipt.get("detail_sha256"):
                continue
            started = time.monotonic()
            text = path.read_text()
            _add(timings, "artifact_text_read_seconds", started)
            started = time.monotonic()
            artifact = json.loads(text)
            _add(timings, "artifact_parse_seconds", started)
            _check(deadline_at)
            started = time.monotonic()
            parsed = artifact["job"]
            if (
                artifact.get("proof") == proof
                and parsed.get("source_id") == row["source_id"]
                and str(parsed.get("external_id")) == str(row["external_id"])
                and parsed.get("description") == row["description"]
            ):
                matching.append({"attempt": dict(attempt), "artifact": artifact})
            _add(timings, "proof_check_seconds", started)
        except _LineageDeadlineExceeded:
            raise
        except (KeyError, TypeError, ValueError, AttributeError, OSError):
            continue
    _check(deadline_at)
    return matching
