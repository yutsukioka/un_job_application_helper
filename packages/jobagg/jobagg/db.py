"""SQLite persistence for job records and change events."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jobagg.detail_quality import DETAIL_QUALITY_COMPLETE, detail_quality_status
from jobagg.hashing import ensure_job_hash, posting_fingerprint
from jobagg.models import ChangeEvent, JobRecord, SourceRunDiagnostics, SyncResult


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _bool_to_int(value: bool | None) -> int | None:
    if value is None:
        return None
    return int(bool(value))


def _int_to_optional_bool(value: int | None) -> bool | None:
    if value is None:
        return None
    return bool(value)


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
                    missing_run_count INTEGER NOT NULL DEFAULT 0,
                    stale_current INTEGER NOT NULL DEFAULT 0,
                    canonical_job_key TEXT,
                    duplicate_of_job_key TEXT,
                    consolidation_status TEXT,
                    source_latest_observed_at TEXT,
                    source_freshness_status TEXT,
                    source_health_status TEXT,
                    source_run_classification TEXT,
                    source_publishability_classification TEXT,
                    detail_quality_status TEXT,
                    deadline_state TEXT,
                    source_listed_current INTEGER NOT NULL DEFAULT 0,
                    trusted_current INTEGER NOT NULL DEFAULT 0,
                    application_ready INTEGER NOT NULL DEFAULT 0
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

                CREATE TABLE IF NOT EXISTS source_run_diagnostics (
                    source_run_id INTEGER PRIMARY KEY REFERENCES source_runs(id) ON DELETE CASCADE,
                    source_id TEXT NOT NULL,
                    adapter_version TEXT,
                    fetch_method TEXT,
                    platform_host TEXT,
                    site_number TEXT,
                    expected_site_name TEXT,
                    observed_site_name TEXT,
                    endpoint_family TEXT,
                    http_status INTEGER,
                    total_reported_by_source INTEGER,
                    pages_fetched INTEGER,
                    pagination_complete INTEGER,
                    list_error_count INTEGER NOT NULL DEFAULT 0,
                    detail_attempted INTEGER NOT NULL DEFAULT 0,
                    detail_succeeded INTEGER NOT NULL DEFAULT 0,
                    detail_failed INTEGER NOT NULL DEFAULT 0,
                    detail_skipped INTEGER NOT NULL DEFAULT 0,
                    empty_reason TEXT,
                    zero_fetched_evidence TEXT NOT NULL DEFAULT '{}',
                    observed_agency_counts TEXT NOT NULL DEFAULT '{}',
                    observed_organization_counts TEXT NOT NULL DEFAULT '{}',
                    count_delta_pct REAL,
                    health_status TEXT,
                    run_classification TEXT,
                    publishability_classification TEXT,
                    blocked INTEGER NOT NULL DEFAULT 0,
                    transient_error INTEGER NOT NULL DEFAULT 0,
                    list_breaker_state TEXT,
                    detail_breaker_state TEXT,
                    scope_validation_status TEXT,
                    missing_transition_allowed INTEGER NOT NULL DEFAULT 0,
                    observed_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_source_run_diag_source_observed
                    ON source_run_diagnostics (source_id, observed_at);

                CREATE TABLE IF NOT EXISTS detail_backlog (
                    job_key TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    detail_status TEXT NOT NULL
                        CHECK (detail_status IN (
                            'pending',
                            'complete',
                            'transient_failed',
                            'permanent_failed',
                            'skipped',
                            'adapter_failed',
                            'blocked_by_circuit_breaker'
                        )),
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    last_attempt_at TEXT,
                    last_success_at TEXT,
                    last_error TEXT,
                    cooldown_until TEXT,
                    listing_hash_at_detail_fetch TEXT,
                    queued_reason TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_detail_backlog_source_status
                    ON detail_backlog (source_id, detail_status, cooldown_until);

                CREATE TABLE IF NOT EXISTS source_circuit_breakers (
                    source_id TEXT NOT NULL,
                    breaker_type TEXT NOT NULL
                        CHECK (breaker_type IN ('list', 'detail', 'transient_detail')),
                    state TEXT NOT NULL
                        CHECK (state IN ('closed', 'open', 'half_open')),
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    success_count INTEGER NOT NULL DEFAULT 0,
                    cooldown_until TEXT,
                    last_reason TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (source_id, breaker_type)
                );

                CREATE INDEX IF NOT EXISTS idx_source_breakers_state
                    ON source_circuit_breakers (breaker_type, state, cooldown_until);

                CREATE TABLE IF NOT EXISTS consolidated_source_status (
                    source_id TEXT PRIMARY KEY,
                    latest_observed_at TEXT,
                    health_status TEXT,
                    run_classification TEXT,
                    publishability_classification TEXT,
                    fetched INTEGER,
                    pagination_complete INTEGER,
                    missing_transition_allowed INTEGER,
                    source_freshness_status TEXT NOT NULL,
                    open_jobs INTEGER NOT NULL DEFAULT 0,
                    stale_current_jobs INTEGER NOT NULL DEFAULT 0,
                    duplicate_jobs INTEGER NOT NULL DEFAULT 0,
                    trusted_current_jobs INTEGER NOT NULL DEFAULT 0,
                    application_ready_jobs INTEGER NOT NULL DEFAULT 0,
                    expired_current_jobs INTEGER NOT NULL DEFAULT 0,
                    weak_detail_jobs INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS consolidated_job_aliases (
                    duplicate_job_key TEXT PRIMARY KEY,
                    canonical_job_key TEXT NOT NULL,
                    duplicate_source_id TEXT NOT NULL,
                    canonical_source_id TEXT NOT NULL,
                    duplicate_external_id TEXT,
                    duplicate_apply_url TEXT,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

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

                CREATE TABLE IF NOT EXISTS grade_mappings (
                    mapping_version TEXT NOT NULL,
                    organization TEXT NOT NULL,
                    raw_grade_code TEXT NOT NULL,
                    normalized_raw_grade_code TEXT NOT NULL,
                    normalized_grade_family TEXT,
                    normalized_seniority_tier TEXT,
                    international_national_local TEXT,
                    staff_consultant_contractor_other TEXT,
                    approximate_un_equivalent TEXT,
                    approximate_experience_range TEXT,
                    typical_role_scope TEXT,
                    supervisory_expectations TEXT,
                    notes_caveats TEXT,
                    confidence_level TEXT,
                    evidence_type TEXT,
                    PRIMARY KEY (mapping_version, organization, raw_grade_code)
                );

                CREATE INDEX IF NOT EXISTS idx_grade_mappings_org_code
                    ON grade_mappings (organization, normalized_raw_grade_code);
                CREATE INDEX IF NOT EXISTS idx_grade_mappings_seniority
                    ON grade_mappings (normalized_seniority_tier);
                CREATE INDEX IF NOT EXISTS idx_grade_mappings_scope
                    ON grade_mappings (international_national_local);
                CREATE INDEX IF NOT EXISTS idx_grade_mappings_un_equiv
                    ON grade_mappings (approximate_un_equivalent);

                CREATE TABLE IF NOT EXISTS vacancy_classifications (
                    vacancy_id TEXT PRIMARY KEY REFERENCES jobs(job_key),
                    ccog_primary_code TEXT,
                    ccog_primary_label TEXT,
                    ccog_family_code TEXT,
                    ccog_family_label TEXT,
                    ccog_part TEXT,
                    ccog_confidence REAL,
                    ccog_method TEXT,
                    occupational_family_code TEXT,
                    occupational_family_label TEXT,
                    occupational_medium_code TEXT,
                    occupational_medium_label TEXT,
                    occupational_small_code TEXT,
                    occupational_small_label TEXT,
                    occupational_confidence REAL,
                    occupational_classifier_version TEXT,
                    occupational_evidence TEXT,
                    mandate_network_code TEXT,
                    mandate_network_label TEXT,
                    mandate_family_code TEXT,
                    mandate_family_label TEXT,
                    primary_mandate_network TEXT,
                    primary_mandate_family TEXT,
                    secondary_mandate_families TEXT,
                    mandate_source TEXT,
                    mandate_confidence REAL,
                    mandate_evidence TEXT,
                    source_native_category TEXT,
                    source_native_job_family TEXT,
                    source_native_job_network TEXT,
                    capability_tags TEXT,
                    capability_tag_scores TEXT,
                    capability_tag_evidence TEXT,
                    capability_classifier_version TEXT,
                    contract_category TEXT,
                    contract_subtype TEXT,
                    contract_confidence REAL,
                    contract_group TEXT,
                    contract_group_confidence REAL,
                    contract_group_evidence TEXT,
                    seniority_group TEXT,
                    seniority_confidence REAL,
                    seniority_evidence TEXT,
                    national_international TEXT,
                    national_international_confidence REAL,
                    grade_system TEXT,
                    grade_family TEXT,
                    grade_code TEXT,
                    grade_level TEXT,
                    staff_category TEXT,
                    min_years_experience INTEGER,
                    grade_confidence REAL,
                    grade_mapping_organization TEXT,
                    grade_mapping_raw_grade_code TEXT,
                    standard_grade_family TEXT,
                    standard_seniority_tier TEXT,
                    standard_scope TEXT,
                    standard_employment_category TEXT,
                    standard_un_equivalent TEXT,
                    standard_experience_range TEXT,
                    standard_role_scope TEXT,
                    standard_supervisory_expectations TEXT,
                    grade_mapping_confidence TEXT,
                    grade_mapping_evidence_type TEXT,
                    grade_mapping_notes TEXT,
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
                    quality_flags TEXT,
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
            for column in (
                "grade_mapping_organization",
                "grade_mapping_raw_grade_code",
                "standard_grade_family",
                "standard_seniority_tier",
                "standard_scope",
                "standard_employment_category",
                "standard_un_equivalent",
                "standard_experience_range",
                "standard_role_scope",
                "standard_supervisory_expectations",
                "grade_mapping_confidence",
                "grade_mapping_evidence_type",
                "grade_mapping_notes",
            ):
                self._ensure_column(conn, "vacancy_classifications", column, "TEXT")
            for column, column_type in (
                ("occupational_family_code", "TEXT"),
                ("occupational_family_label", "TEXT"),
                ("occupational_medium_code", "TEXT"),
                ("occupational_medium_label", "TEXT"),
                ("occupational_small_code", "TEXT"),
                ("occupational_small_label", "TEXT"),
                ("occupational_confidence", "REAL NOT NULL DEFAULT 0.0"),
                ("occupational_classifier_version", "TEXT"),
                ("occupational_evidence", "TEXT NOT NULL DEFAULT '{}'"),
                ("mandate_network_code", "TEXT"),
                ("mandate_network_label", "TEXT"),
                ("mandate_family_code", "TEXT"),
                ("mandate_family_label", "TEXT"),
                ("primary_mandate_network", "TEXT"),
                ("primary_mandate_family", "TEXT"),
                ("secondary_mandate_families", "TEXT NOT NULL DEFAULT '[]'"),
                ("mandate_source", "TEXT"),
                ("mandate_confidence", "REAL NOT NULL DEFAULT 0.0"),
                ("mandate_evidence", "TEXT NOT NULL DEFAULT '{}'"),
                ("source_native_category", "TEXT"),
                ("source_native_job_family", "TEXT"),
                ("source_native_job_network", "TEXT"),
                ("capability_tags", "TEXT NOT NULL DEFAULT '[]'"),
                ("capability_tag_scores", "TEXT NOT NULL DEFAULT '{}'"),
                ("capability_tag_evidence", "TEXT NOT NULL DEFAULT '{}'"),
                ("capability_classifier_version", "TEXT"),
                ("contract_group", "TEXT"),
                ("contract_group_confidence", "REAL NOT NULL DEFAULT 0.0"),
                ("contract_group_evidence", "TEXT NOT NULL DEFAULT '{}'"),
                ("seniority_group", "TEXT"),
                ("seniority_confidence", "REAL NOT NULL DEFAULT 0.0"),
                ("seniority_evidence", "TEXT NOT NULL DEFAULT '{}'"),
                ("quality_flags", "TEXT NOT NULL DEFAULT '[]'"),
            ):
                self._ensure_column(conn, "vacancy_classifications", column, column_type)
            # Vendor-supplied wall-clock closing time (kept verbatim) and
            # the IANA timezone identifier used to interpret it. ``closes_at``
            # remains the normalized UTC value used for sorting/indexing.
            self._ensure_column(
                conn,
                "jobs",
                "closes_at_local",
                "TEXT",
            )
            self._ensure_column(
                conn,
                "jobs",
                "closes_tz",
                "TEXT",
            )
            # Cross-source posting fingerprint: a stable hash over
            # canonicalized (title, org, location, description-prefix) used
            # to identify the same vacancy republished on multiple boards.
            self._ensure_column(
                conn,
                "jobs",
                "posting_fingerprint",
                "TEXT",
            )
            for column, column_type in (
                ("stale_current", "INTEGER NOT NULL DEFAULT 0"),
                ("canonical_job_key", "TEXT"),
                ("duplicate_of_job_key", "TEXT"),
                ("consolidation_status", "TEXT"),
                ("source_latest_observed_at", "TEXT"),
                ("source_freshness_status", "TEXT"),
                ("source_health_status", "TEXT"),
                ("source_run_classification", "TEXT"),
                ("source_publishability_classification", "TEXT"),
                ("detail_quality_status", "TEXT"),
                ("deadline_state", "TEXT"),
                ("source_listed_current", "INTEGER NOT NULL DEFAULT 0"),
                ("trusted_current", "INTEGER NOT NULL DEFAULT 0"),
                ("application_ready", "INTEGER NOT NULL DEFAULT 0"),
            ):
                self._ensure_column(conn, "jobs", column, column_type)
            for column, column_type in (
                ("trusted_current_jobs", "INTEGER NOT NULL DEFAULT 0"),
                ("application_ready_jobs", "INTEGER NOT NULL DEFAULT 0"),
                ("expired_current_jobs", "INTEGER NOT NULL DEFAULT 0"),
                ("weak_detail_jobs", "INTEGER NOT NULL DEFAULT 0"),
            ):
                self._ensure_column(conn, "consolidated_source_status", column, column_type)
            self._ensure_column(
                conn,
                "source_run_diagnostics",
                "detail_skipped",
                "INTEGER NOT NULL DEFAULT 0",
            )
            self._ensure_column(
                conn,
                "source_run_diagnostics",
                "observed_agency_counts",
                "TEXT NOT NULL DEFAULT '{}'",
            )
            self._ensure_column(
                conn,
                "source_run_diagnostics",
                "observed_organization_counts",
                "TEXT NOT NULL DEFAULT '{}'",
            )
            for column, column_type in (
                ("run_classification", "TEXT"),
                ("publishability_classification", "TEXT"),
                ("blocked", "INTEGER NOT NULL DEFAULT 0"),
                ("transient_error", "INTEGER NOT NULL DEFAULT 0"),
                ("list_breaker_state", "TEXT"),
                ("detail_breaker_state", "TEXT"),
            ):
                self._ensure_column(conn, "source_run_diagnostics", column, column_type)
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS detail_backlog (
                    job_key TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    detail_status TEXT NOT NULL
                        CHECK (detail_status IN (
                            'pending',
                            'complete',
                            'transient_failed',
                            'permanent_failed',
                            'skipped',
                            'adapter_failed',
                            'blocked_by_circuit_breaker'
                        )),
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    last_attempt_at TEXT,
                    last_success_at TEXT,
                    last_error TEXT,
                    cooldown_until TEXT,
                    listing_hash_at_detail_fetch TEXT,
                    queued_reason TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_detail_backlog_source_status
                    ON detail_backlog (source_id, detail_status, cooldown_until);

                CREATE TABLE IF NOT EXISTS source_circuit_breakers (
                    source_id TEXT NOT NULL,
                    breaker_type TEXT NOT NULL
                        CHECK (breaker_type IN ('list', 'detail', 'transient_detail')),
                    state TEXT NOT NULL
                        CHECK (state IN ('closed', 'open', 'half_open')),
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    success_count INTEGER NOT NULL DEFAULT 0,
                    cooldown_until TEXT,
                    last_reason TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (source_id, breaker_type)
                );

                CREATE INDEX IF NOT EXISTS idx_source_breakers_state
                    ON source_circuit_breakers (breaker_type, state, cooldown_until);

                CREATE TABLE IF NOT EXISTS consolidated_source_status (
                    source_id TEXT PRIMARY KEY,
                    latest_observed_at TEXT,
                    health_status TEXT,
                    run_classification TEXT,
                    publishability_classification TEXT,
                    fetched INTEGER,
                    pagination_complete INTEGER,
                    missing_transition_allowed INTEGER,
                    source_freshness_status TEXT NOT NULL,
                    open_jobs INTEGER NOT NULL DEFAULT 0,
                    stale_current_jobs INTEGER NOT NULL DEFAULT 0,
                    duplicate_jobs INTEGER NOT NULL DEFAULT 0,
                    trusted_current_jobs INTEGER NOT NULL DEFAULT 0,
                    application_ready_jobs INTEGER NOT NULL DEFAULT 0,
                    expired_current_jobs INTEGER NOT NULL DEFAULT 0,
                    weak_detail_jobs INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS consolidated_job_aliases (
                    duplicate_job_key TEXT PRIMARY KEY,
                    canonical_job_key TEXT NOT NULL,
                    duplicate_source_id TEXT NOT NULL,
                    canonical_source_id TEXT NOT NULL,
                    duplicate_external_id TEXT,
                    duplicate_apply_url TEXT,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            self._ensure_detail_backlog_status_schema(conn)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_posting_fingerprint "
                "ON jobs (posting_fingerprint)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_class_standard_seniority "
                "ON vacancy_classifications (standard_seniority_tier)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_class_standard_scope "
                "ON vacancy_classifications (standard_scope)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_class_standard_un_equiv "
                "ON vacancy_classifications (standard_un_equivalent)"
            )
            for index_name, column in (
                ("idx_class_occupational_family", "occupational_family_code"),
                ("idx_class_occupational_medium", "occupational_medium_code"),
                ("idx_class_mandate_network", "mandate_network_code"),
                ("idx_class_mandate_family", "mandate_family_code"),
                ("idx_class_contract_group", "contract_group"),
                ("idx_class_seniority_group", "seniority_group"),
            ):
                conn.execute(
                    f"CREATE INDEX IF NOT EXISTS {index_name} "
                    f"ON vacancy_classifications ({column})"
                )
            self._seed_grade_mappings(conn)
            self._ensure_fts(conn)

    def _ensure_fts(self, conn: sqlite3.Connection) -> None:
        """Create the FTS5 mirror of ``jobs`` and keep it in sync via triggers.

        The mirror is intentionally a *contentless* FTS5 table backed by
        ``jobs`` (``content='jobs'``, ``content_rowid='rowid'``). This avoids
        duplicating the description payload and lets us populate it from
        the existing rows on the first migration. Any later inserts /
        updates / deletes propagate via triggers.
        """

        try:
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS jobs_fts USING fts5(
                    title,
                    description,
                    department,
                    location,
                    content='jobs',
                    content_rowid='rowid',
                    tokenize='unicode61'
                )
                """
            )
        except sqlite3.OperationalError:
            # FTS5 not compiled into this sqlite build; free-text queries
            # will fall back to LIKE in the query layer.
            return
        conn.executescript(
            """
            CREATE TRIGGER IF NOT EXISTS jobs_ai_fts AFTER INSERT ON jobs BEGIN
                INSERT INTO jobs_fts(rowid, title, description, department, location)
                VALUES (new.rowid, new.title, new.description, new.department, new.location);
            END;
            CREATE TRIGGER IF NOT EXISTS jobs_ad_fts AFTER DELETE ON jobs BEGIN
                INSERT INTO jobs_fts(jobs_fts, rowid, title, description, department, location)
                VALUES('delete', old.rowid, old.title, old.description, old.department, old.location);
            END;
            CREATE TRIGGER IF NOT EXISTS jobs_au_fts AFTER UPDATE ON jobs BEGIN
                INSERT INTO jobs_fts(jobs_fts, rowid, title, description, department, location)
                VALUES('delete', old.rowid, old.title, old.description, old.department, old.location);
                INSERT INTO jobs_fts(rowid, title, description, department, location)
                VALUES (new.rowid, new.title, new.description, new.department, new.location);
            END;
            """
        )
        # Backfill on first migration (FTS table just created and still empty).
        existing = conn.execute("SELECT COUNT(*) AS c FROM jobs_fts").fetchone()
        if existing is not None and existing["c"] == 0:
            jobs_count = conn.execute("SELECT COUNT(*) AS c FROM jobs").fetchone()
            if jobs_count is not None and jobs_count["c"] > 0:
                conn.execute("INSERT INTO jobs_fts(jobs_fts) VALUES('rebuild')")

    def fts_available(self) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'jobs_fts'"
            ).fetchone()
            return row is not None

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
            if job.posting_fingerprint is None:
                job.posting_fingerprint = posting_fingerprint(job)
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
                    closes_at_local, closes_tz,
                    apply_url, source_url, description, status, normalized_hash,
                    posting_fingerprint,
                    raw_json, first_seen_at, last_seen_at, missing_run_count
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_key) DO UPDATE SET
                    org_id = excluded.org_id,
                    title = excluded.title,
                    location = excluded.location,
                    department = excluded.department,
                    employment_type = excluded.employment_type,
                    posted_at = excluded.posted_at,
                    closes_at = excluded.closes_at,
                    closes_at_local = excluded.closes_at_local,
                    closes_tz = excluded.closes_tz,
                    apply_url = excluded.apply_url,
                    source_url = excluded.source_url,
                    description = excluded.description,
                    status = excluded.status,
                    normalized_hash = excluded.normalized_hash,
                    posting_fingerprint = excluded.posting_fingerprint,
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
                    job.closes_at_local,
                    job.closes_tz,
                    job.apply_url,
                    job.source_url,
                    job.description,
                    job.status,
                    job.normalized_hash,
                    job.posting_fingerprint,
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

    def _ensure_detail_backlog_status_schema(self, conn: sqlite3.Connection) -> None:
        row = conn.execute(
            """
            SELECT sql
            FROM sqlite_master
            WHERE type = 'table' AND name = 'detail_backlog'
            """
        ).fetchone()
        if row is None or "blocked_by_circuit_breaker" in str(row["sql"] or ""):
            return
        conn.executescript(
            """
            ALTER TABLE detail_backlog RENAME TO detail_backlog_old;

            CREATE TABLE detail_backlog (
                job_key TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                detail_status TEXT NOT NULL
                    CHECK (detail_status IN (
                        'pending',
                        'complete',
                        'transient_failed',
                        'permanent_failed',
                        'skipped',
                        'adapter_failed',
                        'blocked_by_circuit_breaker'
                    )),
                attempt_count INTEGER NOT NULL DEFAULT 0,
                last_attempt_at TEXT,
                last_success_at TEXT,
                last_error TEXT,
                cooldown_until TEXT,
                listing_hash_at_detail_fetch TEXT,
                queued_reason TEXT,
                updated_at TEXT NOT NULL
            );

            INSERT INTO detail_backlog (
                job_key, source_id, detail_status, attempt_count,
                last_attempt_at, last_success_at, last_error, cooldown_until,
                listing_hash_at_detail_fetch, queued_reason, updated_at
            )
            SELECT
                job_key, source_id, detail_status, attempt_count,
                last_attempt_at, last_success_at, last_error, cooldown_until,
                listing_hash_at_detail_fetch, queued_reason, updated_at
            FROM detail_backlog_old;

            DROP TABLE detail_backlog_old;

            CREATE INDEX IF NOT EXISTS idx_detail_backlog_source_status
                ON detail_backlog (source_id, detail_status, cooldown_until);
            """
        )

    def _seed_grade_mappings(self, conn: sqlite3.Connection) -> None:
        from jobagg.classification.grade_mapping import (
            GRADE_MAPPING_VERSION,
            grade_mapping_rows,
        )

        rows = grade_mapping_rows()
        if not rows:
            return
        columns = list(rows[0])
        placeholders = ", ".join("?" for _ in columns)
        updates = ", ".join(
            f"{column} = excluded.{column}"
            for column in columns
            if column not in {"mapping_version", "organization", "raw_grade_code"}
        )
        conn.execute(
            "DELETE FROM grade_mappings WHERE mapping_version = ?",
            (GRADE_MAPPING_VERSION,),
        )
        conn.executemany(
            f"""
            INSERT INTO grade_mappings ({", ".join(columns)})
            VALUES ({placeholders})
            ON CONFLICT(mapping_version, organization, raw_grade_code)
            DO UPDATE SET {updates}
            """,
            [tuple(row[column] for column in columns) for row in rows],
        )

    def _merge_existing_detail_fields(self, job: JobRecord, current: sqlite3.Row) -> None:
        """Preserve detail-only fields when a listing-only sync omits them."""

        new_row_is_listing_only = self._new_row_is_listing_only(job.raw)
        current_raw = self._load_raw_json(current["raw_json"])
        new_quality = detail_quality_status(
            title=job.title,
            description=job.description,
            raw=job.raw,
        )
        current_quality = detail_quality_status(
            title=current["title"],
            description=current["description"],
            raw=current_raw,
        )
        job.raw = self._merge_existing_raw_detail_fields(job.raw, current["raw_json"])
        if job.department is None:
            job.department = current["department"]
        if job.employment_type is None:
            job.employment_type = current["employment_type"]
        if job.posted_at is None:
            job.posted_at = _parse_dt(current["posted_at"])
        if job.closes_at is None:
            job.closes_at = _parse_dt(current["closes_at"])
        if job.closes_at_local is None and "closes_at_local" in current.keys():
            job.closes_at_local = current["closes_at_local"]
        if job.closes_tz is None and "closes_tz" in current.keys():
            job.closes_tz = current["closes_tz"]
        if job.description is None:
            job.description = current["description"]
        elif (
            current["description"]
            and current_quality == DETAIL_QUALITY_COMPLETE
            and new_quality != DETAIL_QUALITY_COMPLETE
        ):
            job.description = current["description"]
        elif new_row_is_listing_only and self._current_row_has_detail(current["raw_json"]):
            job.description = current["description"]

    @staticmethod
    def _load_raw_json(current_raw_json: str | None) -> dict[str, Any]:
        try:
            current_raw = json.loads(current_raw_json or "{}")
        except json.JSONDecodeError:
            current_raw = {}
        return current_raw if isinstance(current_raw, dict) else {}

    @staticmethod
    def _merge_existing_raw_detail_fields(
        new_raw: dict[str, Any],
        current_raw_json: str | None,
    ) -> dict[str, Any]:
        current_raw = JobDatabase._load_raw_json(current_raw_json)

        merged = dict(new_raw or {})

        current_flat = current_raw.get("_taleo_flat")
        new_flat = merged.get("_taleo_flat")
        if isinstance(current_flat, dict) and isinstance(new_flat, dict):
            merged["_taleo_flat"] = {**current_flat, **new_flat}
        elif isinstance(current_flat, dict) and not isinstance(new_flat, dict):
            merged["_taleo_flat"] = current_flat

        for key in (
            "requisitionFlexFields",
            "workLocation",
            "otherWorkLocations",
            "secondaryLocations",
            "jobPostingInfo",
            "detail_html",
            "ExternalDescriptionStr",
            "ExternalResponsibilitiesStr",
            "ExternalQualificationsStr",
            "Description",
        ):
            if not JobDatabase._raw_has_value(merged.get(key)) and JobDatabase._raw_has_value(
                current_raw.get(key)
            ):
                merged[key] = current_raw[key]

        for key in ("ShortDescription", "ShortDescriptionStr"):
            current_value = current_raw.get(key)
            new_value = merged.get(key)
            if JobDatabase._raw_text_value_is_better(current_value, new_value):
                merged[key] = current_value

        return merged

    @staticmethod
    def _raw_text_value_is_better(current_value: Any, new_value: Any) -> bool:
        if not JobDatabase._raw_has_value(current_value):
            return False
        if not JobDatabase._raw_has_value(new_value):
            return True
        if not isinstance(current_value, str) or not isinstance(new_value, str):
            return False
        return len(current_value.strip()) >= 500 and len(current_value.strip()) > len(new_value.strip())

    @staticmethod
    def _new_row_is_listing_only(raw: dict[str, Any]) -> bool:
        return JobDatabase._raw_has_value(raw.get("listing_html")) and not JobDatabase._raw_has_value(
            raw.get("detail_html")
        )

    @staticmethod
    def _current_row_has_detail(current_raw_json: str | None) -> bool:
        try:
            current_raw = json.loads(current_raw_json or "{}")
        except json.JSONDecodeError:
            return False
        if not isinstance(current_raw, dict):
            return False
        return JobDatabase._raw_has_value(current_raw.get("detail_html"))

    @staticmethod
    def _raw_has_value(value: Any) -> bool:
        if value is None or value == "":
            return False
        if isinstance(value, (list, dict)):
            return bool(value)
        return True

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

    def find_cross_source_duplicates(
        self,
        *,
        status: str | None = "open",
    ) -> list[list[dict[str, Any]]]:
        """Group jobs sharing a posting fingerprint across >=2 sources.

        Each returned group is a list of job dicts (one per source) that
        appear to describe the same vacancy. Groups with only a single
        source are excluded.
        """

        clauses = ["posting_fingerprint IS NOT NULL", "posting_fingerprint <> ''"]
        params: list[Any] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        where = " AND ".join(clauses)
        with self.connect() as conn:
            fingerprint_rows = conn.execute(
                f"""
                SELECT posting_fingerprint
                FROM jobs
                WHERE {where}
                GROUP BY posting_fingerprint
                HAVING COUNT(DISTINCT source_id) > 1
                """,
                params,
            ).fetchall()
            groups: list[list[dict[str, Any]]] = []
            for fp_row in fingerprint_rows:
                fp = fp_row["posting_fingerprint"]
                rows = conn.execute(
                    f"SELECT * FROM jobs WHERE posting_fingerprint = ? AND {where}",
                    [fp, *params],
                ).fetchall()
                groups.append([dict(r) for r in rows])
            return groups

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
    ) -> int:
        observed_at = observed_at or datetime.now(tz=UTC)

        def write(connection: sqlite3.Connection) -> int:
            cursor = connection.execute(
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
            run_id = int(cursor.lastrowid)
            if result.diagnostics is not None:
                result.diagnostics.observed_at = observed_at
                self.add_source_run_diagnostics(run_id, result.diagnostics, conn=connection)
            return run_id

        if conn is not None:
            return write(conn)
        with self.connect() as owned_conn:
            return write(owned_conn)

    def add_source_run_diagnostics(
        self,
        source_run_id: int,
        diagnostics: SourceRunDiagnostics,
        *,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        def write(connection: sqlite3.Connection) -> None:
            connection.execute(
                """
                INSERT INTO source_run_diagnostics (
                    source_run_id, source_id, adapter_version, fetch_method, platform_host,
                    site_number, expected_site_name, observed_site_name, endpoint_family,
                    http_status, total_reported_by_source, pages_fetched,
                    pagination_complete, list_error_count, detail_attempted,
                    detail_succeeded, detail_failed, detail_skipped, empty_reason,
                    zero_fetched_evidence, observed_agency_counts,
                    observed_organization_counts, count_delta_pct, health_status,
                    run_classification, publishability_classification,
                    blocked, transient_error, list_breaker_state,
                    detail_breaker_state,
                    scope_validation_status,
                    missing_transition_allowed, observed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_run_id) DO UPDATE SET
                    source_id = excluded.source_id,
                    adapter_version = excluded.adapter_version,
                    fetch_method = excluded.fetch_method,
                    platform_host = excluded.platform_host,
                    site_number = excluded.site_number,
                    expected_site_name = excluded.expected_site_name,
                    observed_site_name = excluded.observed_site_name,
                    endpoint_family = excluded.endpoint_family,
                    http_status = excluded.http_status,
                    total_reported_by_source = excluded.total_reported_by_source,
                    pages_fetched = excluded.pages_fetched,
                    pagination_complete = excluded.pagination_complete,
                    list_error_count = excluded.list_error_count,
                    detail_attempted = excluded.detail_attempted,
                    detail_succeeded = excluded.detail_succeeded,
                    detail_failed = excluded.detail_failed,
                    detail_skipped = excluded.detail_skipped,
                    empty_reason = excluded.empty_reason,
                    zero_fetched_evidence = excluded.zero_fetched_evidence,
                    observed_agency_counts = excluded.observed_agency_counts,
                    observed_organization_counts = excluded.observed_organization_counts,
                    count_delta_pct = excluded.count_delta_pct,
                    health_status = excluded.health_status,
                    run_classification = excluded.run_classification,
                    publishability_classification = excluded.publishability_classification,
                    blocked = excluded.blocked,
                    transient_error = excluded.transient_error,
                    list_breaker_state = excluded.list_breaker_state,
                    detail_breaker_state = excluded.detail_breaker_state,
                    scope_validation_status = excluded.scope_validation_status,
                    missing_transition_allowed = excluded.missing_transition_allowed,
                    observed_at = excluded.observed_at
                """,
                (
                    source_run_id,
                    diagnostics.source_id,
                    diagnostics.adapter_version,
                    diagnostics.fetch_method,
                    diagnostics.platform_host,
                    diagnostics.site_number,
                    diagnostics.expected_site_name,
                    diagnostics.observed_site_name,
                    diagnostics.endpoint_family,
                    diagnostics.http_status,
                    diagnostics.total_reported_by_source,
                    diagnostics.pages_fetched,
                    _bool_to_int(diagnostics.pagination_complete),
                    diagnostics.list_error_count,
                    diagnostics.detail_attempted,
                    diagnostics.detail_succeeded,
                    diagnostics.detail_failed,
                    diagnostics.detail_skipped,
                    diagnostics.empty_reason,
                    json.dumps(diagnostics.zero_fetched_evidence, sort_keys=True, ensure_ascii=True),
                    json.dumps(diagnostics.observed_agency_counts, sort_keys=True, ensure_ascii=True),
                    json.dumps(
                        diagnostics.observed_organization_counts,
                        sort_keys=True,
                        ensure_ascii=True,
                    ),
                    diagnostics.count_delta_pct,
                    diagnostics.health_status,
                    diagnostics.run_classification,
                    diagnostics.publishability_classification,
                    int(bool(diagnostics.blocked)),
                    int(bool(diagnostics.transient_error)),
                    diagnostics.list_breaker_state,
                    diagnostics.detail_breaker_state,
                    diagnostics.scope_validation_status,
                    int(bool(diagnostics.missing_transition_allowed)),
                    _dt(diagnostics.observed_at),
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

    def iter_source_run_diagnostics(self, source_id: str | None = None) -> Iterable[dict[str, Any]]:
        query = "SELECT * FROM source_run_diagnostics"
        params = []
        if source_id:
            query += " WHERE source_id = ?"
            params.append(source_id)
        query += " ORDER BY observed_at, source_run_id"
        with self.connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
            for row in rows:
                data = dict(row)
                data["pagination_complete"] = _int_to_optional_bool(data["pagination_complete"])
                data["blocked"] = bool(data.get("blocked", 0))
                data["transient_error"] = bool(data.get("transient_error", 0))
                data["missing_transition_allowed"] = bool(data["missing_transition_allowed"])
                data["zero_fetched_evidence"] = json.loads(data["zero_fetched_evidence"] or "{}")
                data["observed_agency_counts"] = json.loads(data["observed_agency_counts"] or "{}")
                data["observed_organization_counts"] = json.loads(
                    data["observed_organization_counts"] or "{}"
                )
                yield data

    def get_detail_backlog(self, job_key: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM detail_backlog WHERE job_key = ?",
                (job_key,),
            ).fetchone()
            return dict(row) if row is not None else None

    def queue_detail_backlog_item(
        self,
        *,
        job_key: str,
        source_id: str,
        listing_hash: str,
        reason: str,
    ) -> dict[str, Any]:
        """Create or refresh a queued detail item unless it is already complete.

        Complete rows for the same listing hash are intentionally left untouched:
        they represent an already-enriched unchanged posting and should not be
        refetched by later selective-detail runs.
        """

        now = _dt(datetime.now(tz=UTC))
        with self.connect() as conn:
            current = conn.execute(
                "SELECT * FROM detail_backlog WHERE job_key = ?",
                (job_key,),
            ).fetchone()
            if (
                current is not None
                and current["detail_status"] == "complete"
                and current["listing_hash_at_detail_fetch"] == listing_hash
                and not reason.startswith("detail_quality_")
            ):
                return dict(current)
            if current is None:
                conn.execute(
                    """
                    INSERT INTO detail_backlog (
                        job_key, source_id, detail_status, attempt_count,
                        listing_hash_at_detail_fetch, queued_reason, updated_at
                    )
                    VALUES (?, ?, 'pending', 0, ?, ?, ?)
                    """,
                    (job_key, source_id, listing_hash, reason, now),
                )
            else:
                conn.execute(
                    """
                    UPDATE detail_backlog
                    SET source_id = ?,
                        detail_status = 'pending',
                        listing_hash_at_detail_fetch = ?,
                        queued_reason = ?,
                        updated_at = ?
                    WHERE job_key = ?
                    """,
                    (source_id, listing_hash, reason, now, job_key),
                )
            row = conn.execute(
                "SELECT * FROM detail_backlog WHERE job_key = ?",
                (job_key,),
            ).fetchone()
            return dict(row)

    def record_detail_backlog_attempt(
        self,
        *,
        job_key: str,
        source_id: str,
        status: str,
        listing_hash: str,
        error: str | None = None,
        cooldown_until: datetime | None = None,
    ) -> None:
        if status not in {
            "complete",
            "transient_failed",
            "permanent_failed",
            "skipped",
            "adapter_failed",
            "blocked_by_circuit_breaker",
        }:
            raise ValueError(f"Unsupported detail backlog status: {status}")
        now_dt = datetime.now(tz=UTC)
        now = _dt(now_dt)
        success_at = now if status == "complete" else None
        last_error = None if status == "complete" else error
        cooldown_text = None if status == "complete" else _dt(cooldown_until)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO detail_backlog (
                    job_key, source_id, detail_status, attempt_count,
                    last_attempt_at, last_success_at, last_error,
                    cooldown_until, listing_hash_at_detail_fetch, updated_at
                )
                VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_key) DO UPDATE SET
                    source_id = excluded.source_id,
                    detail_status = excluded.detail_status,
                    attempt_count = detail_backlog.attempt_count + 1,
                    last_attempt_at = excluded.last_attempt_at,
                    last_success_at = COALESCE(excluded.last_success_at, detail_backlog.last_success_at),
                    last_error = excluded.last_error,
                    cooldown_until = excluded.cooldown_until,
                    listing_hash_at_detail_fetch = excluded.listing_hash_at_detail_fetch,
                    updated_at = excluded.updated_at
                """,
                (
                    job_key,
                    source_id,
                    status,
                    now,
                    success_at,
                    last_error,
                    cooldown_text,
                    listing_hash,
                    now,
                ),
            )

    def defer_detail_backlog_item(
        self,
        *,
        job_key: str,
        source_id: str,
        listing_hash: str,
        reason: str,
        cooldown_until: datetime | None = None,
    ) -> None:
        now = _dt(datetime.now(tz=UTC))
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO detail_backlog (
                    job_key, source_id, detail_status, attempt_count,
                    cooldown_until, listing_hash_at_detail_fetch,
                    queued_reason, updated_at
                )
                VALUES (?, ?, 'pending', 0, ?, ?, ?, ?)
                ON CONFLICT(job_key) DO UPDATE SET
                    source_id = excluded.source_id,
                    detail_status = 'pending',
                    cooldown_until = excluded.cooldown_until,
                    listing_hash_at_detail_fetch = excluded.listing_hash_at_detail_fetch,
                    queued_reason = excluded.queued_reason,
                    updated_at = excluded.updated_at
                """,
                (
                    job_key,
                    source_id,
                    _dt(cooldown_until),
                    listing_hash,
                    reason,
                    now,
                ),
            )

    def update_detail_backlog_status(
        self,
        *,
        job_key: str,
        source_id: str,
        status: str,
        listing_hash: str,
        reason: str,
        error: str | None = None,
        cooldown_until: datetime | None = None,
    ) -> None:
        if status not in {"pending", "adapter_failed", "blocked_by_circuit_breaker"}:
            raise ValueError(f"Unsupported detail backlog status update: {status}")
        now = _dt(datetime.now(tz=UTC))
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO detail_backlog (
                    job_key, source_id, detail_status, attempt_count,
                    last_error, cooldown_until, listing_hash_at_detail_fetch,
                    queued_reason, updated_at
                )
                VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?)
                ON CONFLICT(job_key) DO UPDATE SET
                    source_id = excluded.source_id,
                    detail_status = excluded.detail_status,
                    last_error = excluded.last_error,
                    cooldown_until = excluded.cooldown_until,
                    listing_hash_at_detail_fetch = excluded.listing_hash_at_detail_fetch,
                    queued_reason = excluded.queued_reason,
                    updated_at = excluded.updated_at
                """,
                (
                    job_key,
                    source_id,
                    status,
                    error,
                    _dt(cooldown_until),
                    listing_hash,
                    reason,
                    now,
                ),
            )

    def get_source_breaker(self, source_id: str, breaker_type: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM source_circuit_breakers
                WHERE source_id = ? AND breaker_type = ?
                """,
                (source_id, breaker_type),
            ).fetchone()
            return dict(row) if row is not None else None

    def set_source_breaker(
        self,
        *,
        source_id: str,
        breaker_type: str,
        state: str,
        failure_count: int = 0,
        success_count: int = 0,
        cooldown_until: datetime | None = None,
        reason: str | None = None,
    ) -> None:
        if breaker_type not in {"list", "detail", "transient_detail"}:
            raise ValueError(f"Unsupported breaker type: {breaker_type}")
        if state not in {"closed", "open", "half_open"}:
            raise ValueError(f"Unsupported breaker state: {state}")
        now = _dt(datetime.now(tz=UTC))
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO source_circuit_breakers (
                    source_id, breaker_type, state, failure_count, success_count,
                    cooldown_until, last_reason, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id, breaker_type) DO UPDATE SET
                    state = excluded.state,
                    failure_count = excluded.failure_count,
                    success_count = excluded.success_count,
                    cooldown_until = excluded.cooldown_until,
                    last_reason = excluded.last_reason,
                    updated_at = excluded.updated_at
                """,
                (
                    source_id,
                    breaker_type,
                    state,
                    int(failure_count),
                    int(success_count),
                    _dt(cooldown_until),
                    reason,
                    now,
                ),
            )

    def iter_detail_backlog(
        self,
        source_id: str | None = None,
        *,
        status: str | None = None,
    ) -> Iterable[dict[str, Any]]:
        query = "SELECT * FROM detail_backlog"
        clauses = []
        params = []
        if source_id:
            clauses.append("source_id = ?")
            params.append(source_id)
        if status:
            clauses.append("detail_status = ?")
            params.append(status)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY source_id, job_key"
        with self.connect() as conn:
            for row in conn.execute(query, tuple(params)).fetchall():
                yield dict(row)

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
        trusted_current_only: bool = False,
        application_ready_only: bool = False,
        history_only: bool = False,
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
        if history_only:
            clauses.append("status <> 'open'")
        if trusted_current_only:
            clauses.append("trusted_current = 1")
        if application_ready_only:
            clauses.append("application_ready = 1")
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
        trusted_current_only: bool = False,
        application_ready_only: bool = False,
        history_only: bool = False,
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
                c.occupational_family_code,
                c.occupational_family_label,
                c.occupational_medium_code,
                c.occupational_medium_label,
                c.occupational_small_code,
                c.occupational_small_label,
                c.occupational_confidence,
                c.occupational_classifier_version,
                c.occupational_evidence,
                c.mandate_network_code,
                c.mandate_network_label,
                c.mandate_family_code,
                c.mandate_family_label,
                c.primary_mandate_network,
                c.primary_mandate_family,
                c.secondary_mandate_families,
                c.mandate_source,
                c.mandate_confidence,
                c.mandate_evidence,
                c.source_native_category,
                c.source_native_job_family,
                c.source_native_job_network,
                c.capability_tags,
                c.capability_tag_scores,
                c.capability_tag_evidence,
                c.capability_classifier_version,
                c.contract_category,
                c.contract_subtype,
                c.contract_confidence,
                c.contract_group,
                c.contract_group_confidence,
                c.contract_group_evidence,
                c.seniority_group,
                c.seniority_confidence,
                c.seniority_evidence,
                c.national_international,
                c.national_international_confidence,
                c.grade_system,
                c.grade_family,
                c.grade_code,
                c.grade_level,
                c.staff_category,
                c.min_years_experience,
                c.grade_confidence,
                c.grade_mapping_organization,
                c.grade_mapping_raw_grade_code,
                c.standard_grade_family,
                c.standard_seniority_tier,
                c.standard_scope,
                c.standard_employment_category,
                c.standard_un_equivalent,
                c.standard_experience_range,
                c.standard_role_scope,
                c.standard_supervisory_expectations,
                c.grade_mapping_confidence,
                c.grade_mapping_evidence_type,
                c.grade_mapping_notes,
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
                c.quality_flags,
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
        if history_only:
            clauses.append("j.status <> 'open'")
        if trusted_current_only:
            clauses.append("j.trusted_current = 1")
        if application_ready_only:
            clauses.append("j.application_ready = 1")
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY j.source_id, j.title"
        try:
            with self.connect() as conn:
                rows = conn.execute(query, tuple(params)).fetchall()
        except sqlite3.OperationalError:
            yield from self.iter_jobs(
                source_id=source_id,
                status=status,
                trusted_current_only=trusted_current_only,
                application_ready_only=application_ready_only,
                history_only=history_only,
            )
            return
        for row in rows:
            data = dict(row)
            data["raw"] = json.loads(data.pop("raw_json") or "{}")
            for field_name in (
                "unv_expertise_areas",
                "secondary_mandate_families",
                "capability_tags",
                "quality_flags",
            ):
                if data.get(field_name):
                    data[field_name] = json.loads(data[field_name])
            for field_name in (
                "occupational_evidence",
                "mandate_evidence",
                "capability_tag_scores",
                "capability_tag_evidence",
                "contract_group_evidence",
                "seniority_evidence",
            ):
                if data.get(field_name):
                    data[field_name] = json.loads(data[field_name])
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
