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
        self._persistent_conn: sqlite3.Connection | None = None

    @staticmethod
    def _apply_pragmas(conn: sqlite3.Connection) -> None:
        # journal_mode=WAL improves concurrent read/write throughput; the other
        # pragmas are safe defaults for an embedded single-writer workload.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA temp_store=MEMORY")

    @contextmanager
    def connect(self):
        if self._persistent_conn is not None:
            # Reuse the connection opened by ``connection_scope`` so a batch
            # operation does not pay the open/close + WAL checkpoint cost per
            # row and so all writes share one transaction.
            yield self._persistent_conn
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            self._apply_pragmas(conn)
            yield conn
            conn.commit()
        finally:
            conn.close()

    @contextmanager
    def connection_scope(self):
        """Open one connection and reuse it for nested ``connect()`` calls.

        Useful for batch operations such as ``upsert_jobs`` where the previous
        per-call open/close cycle dominated runtime. Commits on success and
        rolls back on exception.
        """

        if self._persistent_conn is not None:
            # Already inside a scope; just yield the existing connection.
            yield self._persistent_conn
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        self._apply_pragmas(conn)
        self._persistent_conn = conn
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._persistent_conn = None
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

                CREATE INDEX IF NOT EXISTS idx_jobs_closes_at
                    ON jobs (closes_at);

                CREATE INDEX IF NOT EXISTS idx_jobs_posted_at
                    ON jobs (posted_at);

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

                CREATE TABLE IF NOT EXISTS vacancy_source_features (
                    vacancy_id TEXT PRIMARY KEY REFERENCES jobs(job_key),
                    source_id TEXT NOT NULL,
                    ats_family TEXT NOT NULL,
                    raw_title TEXT,
                    raw_description TEXT,
                    raw_location TEXT,
                    raw_department TEXT,
                    raw_employment_type TEXT,
                    source_grade TEXT,
                    source_grade_field TEXT,
                    source_contract_type TEXT,
                    source_contract_field TEXT,
                    source_job_family_code TEXT,
                    source_job_family_label TEXT,
                    source_job_network_code TEXT,
                    source_job_network_label TEXT,
                    source_recruitment_type TEXT,
                    source_staff_category TEXT,
                    source_seniority TEXT,
                    source_country_code TEXT,
                    source_city TEXT,
                    source_region TEXT,
                    source_work_modality TEXT,
                    source_unv_category_code TEXT,
                    source_unv_category_label TEXT,
                    source_unv_volunteer_type TEXT,
                    source_unv_work_location TEXT,
                    source_unv_work_arrangement TEXT,
                    source_unv_assignment_duration TEXT,
                    source_unv_hours_week TEXT,
                    source_unv_host_entity TEXT,
                    source_unv_sdg TEXT,
                    source_unv_expertise_areas TEXT,
                    evidence TEXT,
                    extracted_at TEXT NOT NULL,
                    extractor_version TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS vacancy_classifications (
                    vacancy_id TEXT PRIMARY KEY REFERENCES jobs(job_key),
                    ccog_primary_code TEXT,
                    ccog_primary_label TEXT,
                    ccog_family_code TEXT,
                    ccog_family_label TEXT,
                    ccog_part TEXT,
                    ccog_confidence REAL,
                    ccog_method TEXT,
                    contract_category TEXT,
                    contract_subtype TEXT,
                    contract_confidence REAL,
                    national_international TEXT,
                    national_international_confidence REAL,
                    grade_system TEXT,
                    grade_family TEXT,
                    grade_code TEXT,
                    grade_level TEXT,
                    staff_category TEXT,
                    min_years_experience INTEGER,
                    grade_confidence REAL,
                    country TEXT,
                    country_iso2 TEXT,
                    country_iso3 TEXT,
                    city TEXT,
                    region TEXT,
                    subregion TEXT,
                    location_confidence REAL,
                    work_modality TEXT,
                    work_modality_confidence REAL,
                    unv_category TEXT,
                    unv_raw_category TEXT,
                    unv_volunteer_type TEXT,
                    unv_assignment_duration TEXT,
                    unv_work_arrangement TEXT,
                    unv_hours_per_week TEXT,
                    unv_host_entity TEXT,
                    unv_sdg TEXT,
                    unv_expertise_areas TEXT,
                    needs_review INTEGER NOT NULL DEFAULT 0,
                    classification_version TEXT NOT NULL,
                    evidence TEXT,
                    classified_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_class_ccog_code
                    ON vacancy_classifications (ccog_primary_code);
                CREATE INDEX IF NOT EXISTS idx_class_ccog_family
                    ON vacancy_classifications (ccog_family_code);
                CREATE INDEX IF NOT EXISTS idx_class_contract
                    ON vacancy_classifications (contract_category);
                CREATE INDEX IF NOT EXISTS idx_class_grade
                    ON vacancy_classifications (grade_family, grade_code);
                CREATE INDEX IF NOT EXISTS idx_class_country
                    ON vacancy_classifications (country_iso3);
                CREATE INDEX IF NOT EXISTS idx_class_city
                    ON vacancy_classifications (city);
                CREATE INDEX IF NOT EXISTS idx_class_region
                    ON vacancy_classifications (region);
                CREATE INDEX IF NOT EXISTS idx_class_modality
                    ON vacancy_classifications (work_modality);
                CREATE INDEX IF NOT EXISTS idx_class_scope
                    ON vacancy_classifications (national_international);
                CREATE INDEX IF NOT EXISTS idx_class_unv_category
                    ON vacancy_classifications (unv_category);
                CREATE INDEX IF NOT EXISTS idx_class_review
                    ON vacancy_classifications (needs_review);

                CREATE TABLE IF NOT EXISTS vacancy_locations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    vacancy_id TEXT NOT NULL REFERENCES jobs(job_key),
                    city TEXT,
                    city_key TEXT,
                    country TEXT,
                    country_iso2 TEXT,
                    country_iso3 TEXT,
                    region TEXT,
                    subregion TEXT,
                    location_type TEXT NOT NULL,
                    is_primary INTEGER NOT NULL DEFAULT 0,
                    is_remote INTEGER NOT NULL DEFAULT 0,
                    confidence REAL NOT NULL DEFAULT 0.0,
                    source_field TEXT,
                    evidence TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_vacloc_city_key
                    ON vacancy_locations (city_key);
                CREATE INDEX IF NOT EXISTS idx_vacloc_country_iso3
                    ON vacancy_locations (country_iso3);
                CREATE INDEX IF NOT EXISTS idx_vacloc_city_country
                    ON vacancy_locations (city_key, country_iso3);
                CREATE INDEX IF NOT EXISTS idx_vacloc_type
                    ON vacancy_locations (location_type);
                CREATE INDEX IF NOT EXISTS idx_vacloc_vacancy
                    ON vacancy_locations (vacancy_id);

                CREATE TABLE IF NOT EXISTS classification_overrides (
                    vacancy_id TEXT NOT NULL REFERENCES jobs(job_key),
                    field_name TEXT NOT NULL,
                    override_value TEXT NOT NULL,
                    reason TEXT,
                    created_at TEXT NOT NULL,
                    created_by TEXT,
                    PRIMARY KEY (vacancy_id, field_name)
                );
                """
            )
            self._ensure_column(
                conn,
                "jobs",
                "missing_run_count",
                "INTEGER NOT NULL DEFAULT 0",
            )
            # Hash of the underlying job at the time of classification, so a
            # re-run can skip rows whose source content has not changed.
            self._ensure_column(
                conn,
                "vacancy_classifications",
                "source_hash",
                "TEXT",
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
            elif current["status"] != job.status:
                change_type = "updated"
                event_type = job.status if job.status in {"closed", "missing"} else "status_changed"
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
        with self.connection_scope():
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

    def iter_jobs_with_classification(
        self,
        source_id: str | None = None,
        *,
        status: str | None = None,
    ) -> Iterable[dict[str, Any]]:
        query = """
            SELECT
                j.*,
                c.ccog_primary_code,
                c.ccog_primary_label,
                c.ccog_family_code,
                c.ccog_family_label,
                c.ccog_part,
                c.ccog_confidence,
                c.ccog_method,
                c.contract_category,
                c.contract_subtype,
                c.contract_confidence,
                c.national_international,
                c.national_international_confidence,
                c.grade_system,
                c.grade_family,
                c.grade_code,
                c.grade_level,
                c.staff_category,
                c.min_years_experience,
                c.grade_confidence,
                c.country,
                c.country_iso2,
                c.country_iso3,
                c.city,
                c.region,
                c.subregion,
                c.location_confidence,
                c.work_modality,
                c.work_modality_confidence,
                c.unv_category,
                c.unv_raw_category,
                c.unv_volunteer_type,
                c.unv_assignment_duration,
                c.unv_work_arrangement,
                c.unv_hours_per_week,
                c.unv_host_entity,
                c.unv_sdg,
                c.unv_expertise_areas,
                c.needs_review,
                c.classification_version,
                c.classified_at
            FROM jobs j
            LEFT JOIN vacancy_classifications c ON c.vacancy_id = j.job_key
        """
        clauses = []
        params = []
        if source_id:
            clauses.append("j.source_id = ?")
            params.append(source_id)
        if status:
            clauses.append("j.status = ?")
            params.append(status)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY j.source_id, j.title"
        try:
            with self.connect() as conn:
                rows = conn.execute(query, tuple(params)).fetchall()
        except sqlite3.OperationalError:
            yield from self.iter_jobs(source_id=source_id, status=status)
            return
        for row in rows:
            data = dict(row)
            data["raw"] = json.loads(data.pop("raw_json") or "{}")
            if data.get("unv_expertise_areas"):
                data["unv_expertise_areas"] = json.loads(data["unv_expertise_areas"])
            data["needs_review"] = bool(data["needs_review"]) if data.get("needs_review") is not None else None
            yield data

    def upsert_vacancy_source_features(self, features: Any) -> None:
        from jobagg.classification.pipeline import feature_to_row

        row = feature_to_row(features)
        columns = list(row)
        placeholders = ", ".join("?" for _ in columns)
        updates = ", ".join(f"{column} = excluded.{column}" for column in columns if column != "vacancy_id")
        with self.connect() as conn:
            conn.execute(
                f"""
                INSERT INTO vacancy_source_features ({", ".join(columns)})
                VALUES ({placeholders})
                ON CONFLICT(vacancy_id) DO UPDATE SET {updates}
                """,
                tuple(row[column] for column in columns),
            )

    def upsert_vacancy_classification(
        self,
        classification: Any,
        *,
        source_hash: str | None = None,
    ) -> None:
        from jobagg.classification.pipeline import classification_to_row

        row = classification_to_row(classification)
        row["source_hash"] = source_hash
        columns = list(row)
        placeholders = ", ".join("?" for _ in columns)
        updates = ", ".join(f"{column} = excluded.{column}" for column in columns if column != "vacancy_id")
        with self.connect() as conn:
            conn.execute(
                f"""
                INSERT INTO vacancy_classifications ({", ".join(columns)})
                VALUES ({placeholders})
                ON CONFLICT(vacancy_id) DO UPDATE SET {updates}
                """,
                tuple(row[column] for column in columns),
            )

    def classification_state(self, version: str) -> dict[str, str | None]:
        """Return ``{vacancy_id: source_hash}`` for rows at ``version``.

        Used by ``classify_database`` to skip vacancies whose source content
        has not changed since they were last classified.
        """

        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT vacancy_id, source_hash
                FROM vacancy_classifications
                WHERE classification_version = ?
                """,
                (version,),
            ).fetchall()
        return {row["vacancy_id"]: row["source_hash"] for row in rows}

    def replace_vacancy_locations(self, vacancy_id: str, locations: Iterable[Any]) -> None:
        from jobagg.classification.pipeline import location_to_row

        rows = [location_to_row(location) for location in locations]
        columns = [
            "vacancy_id",
            "city",
            "city_key",
            "country",
            "country_iso2",
            "country_iso3",
            "region",
            "subregion",
            "location_type",
            "is_primary",
            "is_remote",
            "confidence",
            "source_field",
            "evidence",
        ]
        with self.connect() as conn:
            conn.execute("DELETE FROM vacancy_locations WHERE vacancy_id = ?", (vacancy_id,))
            if not rows:
                return
            placeholders = ", ".join("?" for _ in columns)
            conn.executemany(
                f"""
                INSERT INTO vacancy_locations ({", ".join(columns)})
                VALUES ({placeholders})
                """,
                [tuple(row[column] for column in columns) for row in rows],
            )

    def iter_vacancy_locations(self, vacancy_id: str) -> Iterable[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM vacancy_locations
                WHERE vacancy_id = ?
                ORDER BY is_primary DESC, confidence DESC, id
                """,
                (vacancy_id,),
            ).fetchall()
        for row in rows:
            data = dict(row)
            data["is_primary"] = bool(data["is_primary"])
            data["is_remote"] = bool(data["is_remote"])
            data["evidence"] = json.loads(data["evidence"] or "{}")
            yield data

    def vacancy_locations_by_vacancy(
        self,
        vacancy_ids: Iterable[str],
    ) -> dict[str, list[dict[str, Any]]]:
        ids = list(dict.fromkeys(vacancy_ids))
        locations = {vacancy_id: [] for vacancy_id in ids}
        if not ids:
            return locations
        with self.connect() as conn:
            for index in range(0, len(ids), 900):
                chunk = ids[index : index + 900]
                placeholders = ", ".join("?" for _ in chunk)
                rows = conn.execute(
                    f"""
                    SELECT *
                    FROM vacancy_locations
                    WHERE vacancy_id IN ({placeholders})
                    ORDER BY vacancy_id, is_primary DESC, confidence DESC, id
                    """,
                    tuple(chunk),
                ).fetchall()
                for row in rows:
                    data = dict(row)
                    data["is_primary"] = bool(data["is_primary"])
                    data["is_remote"] = bool(data["is_remote"])
                    data["evidence"] = json.loads(data["evidence"] or "{}")
                    locations.setdefault(data["vacancy_id"], []).append(data)
        return locations

    def classification_overrides(self, vacancy_id: str) -> dict[str, str]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT field_name, override_value
                FROM classification_overrides
                WHERE vacancy_id = ?
                """,
                (vacancy_id,),
            ).fetchall()
        return {row["field_name"]: row["override_value"] for row in rows}

    def upsert_classification_override(
        self,
        *,
        vacancy_id: str,
        field_name: str,
        override_value: str,
        reason: str | None = None,
        created_by: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO classification_overrides (
                    vacancy_id, field_name, override_value, reason, created_at, created_by
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(vacancy_id, field_name) DO UPDATE SET
                    override_value = excluded.override_value,
                    reason = excluded.reason,
                    created_at = excluded.created_at,
                    created_by = excluded.created_by
                """,
                (
                    vacancy_id,
                    field_name,
                    override_value,
                    reason,
                    _dt(datetime.now(tz=UTC)),
                    created_by,
                ),
            )
            # Invalidate the cached classification source_hash for this
            # vacancy so the next ``classify_database`` run re-applies the
            # new override instead of skipping the row as unchanged.
            conn.execute(
                "UPDATE vacancy_classifications SET source_hash = NULL WHERE vacancy_id = ?",
                (vacancy_id,),
            )
