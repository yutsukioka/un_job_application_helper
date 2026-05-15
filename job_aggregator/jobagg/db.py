"""SQLite persistence for job records and change events."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jobagg.hashing import ensure_job_hash
from jobagg.models import ChangeEvent, JobRecord, SyncResult


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _is_past_closing_date(value: str | None) -> bool:
    parsed = _parse_dt(value)
    if parsed is None:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.date() < datetime.now(tz=UTC).date()


class JobDatabase:
    def __init__(self, path: str | Path = "jobagg.sqlite3") -> None:
        self.path = Path(path)

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_key TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    org_id TEXT NOT NULL,
                    ats_family TEXT NOT NULL,
                    external_id TEXT,
                    title TEXT NOT NULL,
                    location TEXT,
                    department TEXT,
                    employment_type TEXT,
                    posted_at TEXT,
                    closes_at TEXT,
                    apply_url TEXT NOT NULL,
                    source_url TEXT,
                    description TEXT,
                    status TEXT NOT NULL,
                    normalized_hash TEXT NOT NULL,
                    raw_json TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    missing_run_count INTEGER NOT NULL DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_jobs_source_status
                    ON jobs (source_id, status);

                CREATE TABLE IF NOT EXISTS change_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT NOT NULL,
                    job_key TEXT NOT NULL,
                    change_type TEXT NOT NULL,
                    old_hash TEXT,
                    new_hash TEXT,
                    observed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS vacancy_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT NOT NULL,
                    job_key TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    observed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS source_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT NOT NULL,
                    fetched INTEGER NOT NULL,
                    inserted INTEGER NOT NULL,
                    updated INTEGER NOT NULL,
                    unchanged INTEGER NOT NULL,
                    missing INTEGER NOT NULL,
                    closed INTEGER NOT NULL,
                    errors_json TEXT NOT NULL,
                    observed_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_source_runs_source_observed
                    ON source_runs (source_id, observed_at);
                """
            )
            self._ensure_column(
                conn,
                "jobs",
                "missing_run_count",
                "INTEGER NOT NULL DEFAULT 0",
            )

    def upsert_job(self, job: JobRecord) -> str:
        job_key = job.identity_key()
        with self.connect() as conn:
            current = conn.execute(
                "SELECT * FROM jobs WHERE job_key = ?",
                (job_key,),
            ).fetchone()
            if current is not None:
                self._merge_existing_detail_fields(job, current)

            ensure_job_hash(job)
            if current is None:
                change_type = "inserted"
                event_type = "created"
                first_seen_at = _dt(job.first_seen_at)
                old_hash = None
            elif current["status"] in {"closed", "missing"} and job.status == "open":
                change_type = "updated"
                event_type = "reopened"
                first_seen_at = current["first_seen_at"]
                old_hash = current["normalized_hash"]
            elif current["normalized_hash"] != job.normalized_hash:
                change_type = "updated"
                event_type = "updated"
                first_seen_at = current["first_seen_at"]
                old_hash = current["normalized_hash"]
            else:
                change_type = "unchanged"
                event_type = "unchanged"
                first_seen_at = current["first_seen_at"]
                old_hash = current["normalized_hash"]

            conn.execute(
                """
                INSERT INTO jobs (
                    job_key, source_id, org_id, ats_family, external_id, title,
                    location, department, employment_type, posted_at, closes_at,
                    apply_url, source_url, description, status, normalized_hash,
                    raw_json, first_seen_at, last_seen_at, missing_run_count
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_key) DO UPDATE SET
                    org_id = excluded.org_id,
                    title = excluded.title,
                    location = excluded.location,
                    department = excluded.department,
                    employment_type = excluded.employment_type,
                    posted_at = excluded.posted_at,
                    closes_at = excluded.closes_at,
                    apply_url = excluded.apply_url,
                    source_url = excluded.source_url,
                    description = excluded.description,
                    status = excluded.status,
                    normalized_hash = excluded.normalized_hash,
                    raw_json = excluded.raw_json,
                    last_seen_at = excluded.last_seen_at,
                    missing_run_count = 0
                """,
                (
                    job_key,
                    job.source_id,
                    job.org_id,
                    job.ats_family,
                    job.external_id,
                    job.title,
                    job.location,
                    job.department,
                    job.employment_type,
                    _dt(job.posted_at),
                    _dt(job.closes_at),
                    job.apply_url,
                    job.source_url,
                    job.description,
                    job.status,
                    job.normalized_hash,
                    json.dumps(job.raw, sort_keys=True, ensure_ascii=True),
                    first_seen_at,
                    _dt(job.last_seen_at),
                    0,
                ),
            )

            if event_type != "unchanged":
                self.add_change_event(
                    ChangeEvent(
                        source_id=job.source_id,
                        job_key=job_key,
                        change_type=event_type,
                        old_hash=old_hash,
                        new_hash=job.normalized_hash,
                    ),
                    conn=conn,
                )
                self.add_vacancy_snapshot(job, conn=conn)
            return change_type

    def _ensure_column(
        self,
        conn: sqlite3.Connection,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _merge_existing_detail_fields(self, job: JobRecord, current: sqlite3.Row) -> None:
        """Preserve detail-only fields when a listing-only sync omits them."""

        if job.department is None:
            job.department = current["department"]
        if job.employment_type is None:
            job.employment_type = current["employment_type"]
        if job.posted_at is None:
            job.posted_at = _parse_dt(current["posted_at"])
        if job.closes_at is None:
            job.closes_at = _parse_dt(current["closes_at"])
        if job.description is None:
            job.description = current["description"]

    def get_job(self, job_key: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_key = ?", (job_key,)).fetchone()
            if row is None:
                return None
            data = dict(row)
            data["raw"] = json.loads(data.pop("raw_json") or "{}")
            return data

    def has_active_jobs(self, source_id: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM jobs
                WHERE source_id = ? AND status IN ('open', 'missing')
                LIMIT 1
                """,
                (source_id,),
            ).fetchone()
            return row is not None

    def upsert_jobs(self, jobs: Iterable[JobRecord]) -> dict[str, int]:
        counts = {"inserted": 0, "updated": 0, "unchanged": 0}
        for job in jobs:
            counts[self.upsert_job(job)] += 1
        return counts

    def add_change_event(self, event: ChangeEvent, conn: sqlite3.Connection | None = None) -> None:
        def write(connection: sqlite3.Connection) -> None:
            connection.execute(
                """
                INSERT INTO change_events (
                    source_id, job_key, change_type, old_hash, new_hash, observed_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event.source_id,
                    event.job_key,
                    event.change_type,
                    event.old_hash,
                    event.new_hash,
                    _dt(event.observed_at),
                ),
            )

        if conn is not None:
            write(conn)
            return
        with self.connect() as owned_conn:
            write(owned_conn)

    def add_vacancy_snapshot(self, job: JobRecord, conn: sqlite3.Connection | None = None) -> None:
        def write(connection: sqlite3.Connection) -> None:
            connection.execute(
                """
                INSERT INTO vacancy_snapshots (
                    source_id, job_key, content_hash, snapshot_json, observed_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    job.source_id,
                    job.identity_key(),
                    job.normalized_hash or "",
                    json.dumps(job.hash_payload(), sort_keys=True, ensure_ascii=True),
                    _dt(job.last_seen_at),
                ),
            )

        if conn is not None:
            write(conn)
            return
        with self.connect() as owned_conn:
            write(owned_conn)

    def add_source_run(
        self,
        result: SyncResult,
        *,
        observed_at: datetime | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        observed_at = observed_at or datetime.now(tz=UTC)

        def write(connection: sqlite3.Connection) -> None:
            connection.execute(
                """
                INSERT INTO source_runs (
                    source_id, fetched, inserted, updated, unchanged, missing,
                    closed, errors_json, observed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.source_id,
                    result.fetched,
                    result.inserted,
                    result.updated,
                    result.unchanged,
                    result.missing,
                    result.closed,
                    json.dumps(result.errors, ensure_ascii=True),
                    _dt(observed_at),
                ),
            )

        if conn is not None:
            write(conn)
            return
        with self.connect() as owned_conn:
            write(owned_conn)

    def iter_source_runs(self, source_id: str | None = None) -> Iterable[dict[str, Any]]:
        query = "SELECT * FROM source_runs"
        params = []
        if source_id:
            query += " WHERE source_id = ?"
            params.append(source_id)
        query += " ORDER BY observed_at, id"
        with self.connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
            for row in rows:
                data = dict(row)
                data["errors"] = json.loads(data.pop("errors_json") or "[]")
                yield data

    def mark_missing(
        self,
        source_id: str,
        seen_job_keys: set[str],
        *,
        missing_run_threshold: int = 3,
    ) -> dict[str, int]:
        with self.connect() as conn:
            open_rows = conn.execute(
                """
                SELECT job_key, normalized_hash, status, closes_at, missing_run_count
                FROM jobs
                WHERE source_id = ? AND status IN ('open', 'missing')
                """,
                (source_id,),
            ).fetchall()
            counts = {"missing": 0, "closed": 0}
            for row in open_rows:
                if row["job_key"] in seen_job_keys:
                    if row["missing_run_count"]:
                        conn.execute(
                            "UPDATE jobs SET missing_run_count = 0 WHERE job_key = ?",
                            (row["job_key"],),
                        )
                    continue
                missing_run_count = int(row["missing_run_count"] or 0) + 1
                if _is_past_closing_date(row["closes_at"]):
                    conn.execute(
                        """
                        UPDATE jobs
                        SET status = 'closed', missing_run_count = ?
                        WHERE job_key = ?
                        """,
                        (missing_run_count, row["job_key"]),
                    )
                    self.add_change_event(
                        ChangeEvent(
                            source_id=source_id,
                            job_key=row["job_key"],
                            change_type="closed",
                            old_hash=row["normalized_hash"],
                            new_hash=None,
                        ),
                        conn=conn,
                    )
                    counts["closed"] += 1
                    continue

                if missing_run_count >= missing_run_threshold:
                    conn.execute(
                        """
                        UPDATE jobs
                        SET status = 'missing', missing_run_count = ?
                        WHERE job_key = ?
                        """,
                        (missing_run_count, row["job_key"]),
                    )
                    if row["status"] != "missing":
                        self.add_change_event(
                            ChangeEvent(
                                source_id=source_id,
                                job_key=row["job_key"],
                                change_type="missing",
                                old_hash=row["normalized_hash"],
                                new_hash=row["normalized_hash"],
                            ),
                            conn=conn,
                        )
                        counts["missing"] += 1
                    continue

                conn.execute(
                    "UPDATE jobs SET missing_run_count = ? WHERE job_key = ?",
                    (missing_run_count, row["job_key"]),
                )
            return counts

    def mark_missing_closed(self, source_id: str, seen_job_keys: set[str]) -> int:
        return self.mark_missing(
            source_id,
            seen_job_keys,
            missing_run_threshold=1,
        )["closed"]

    def iter_jobs(
        self,
        source_id: str | None = None,
        *,
        status: str | None = None,
    ) -> Iterable[dict[str, Any]]:
        query = "SELECT * FROM jobs"
        clauses = []
        params = []
        if source_id:
            clauses.append("source_id = ?")
            params.append(source_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY source_id, title"
        with self.connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
            for row in rows:
                data = dict(row)
                data["raw"] = json.loads(data.pop("raw_json") or "{}")
                yield data
