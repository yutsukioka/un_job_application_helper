"""SQLite persistence for job records and change events."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from jobagg.detail_quality import DETAIL_QUALITY_COMPLETE, detail_quality_status
from jobagg.hashing import ensure_job_hash, posting_fingerprint
from jobagg.models import ChangeEvent, JobRecord, SourceRunDiagnostics, SyncResult
from jobagg.normalize import clean_text
from jobagg.oracle_public import public_description_parts
from jobagg.vacancy_outcomes import UNAVAILABLE_STATUSES


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
    def __init__(self, path: str | Path = "jobagg.sqlite3", *, read_only: bool = False) -> None:
        self.path = Path(path)
        self.read_only = read_only
        self._persistent_conn: sqlite3.Connection | None = None

    def _open_connection(self) -> sqlite3.Connection:
        if self.read_only:
            # mode=ro preserves committed WAL visibility without requesting a
            # journal-mode change. immutable=1 would incorrectly ignore a WAL.
            conn = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)
            conn.execute("PRAGMA query_only=ON")
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            if not self.read_only:
                self._apply_pragmas(conn)
        except BaseException:
            conn.close()
            raise
        return conn

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
        conn = self._open_connection()
        try:
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
        conn = self._open_connection()
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

                CREATE TABLE IF NOT EXISTS attachment_blobs (
                    content_sha256 TEXT PRIMARY KEY,
                    media_type TEXT,
                    size_bytes INTEGER NOT NULL,
                    content BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS job_attachments (
                    attachment_id TEXT PRIMARY KEY,
                    job_key TEXT NOT NULL REFERENCES jobs(job_key),
                    source_id TEXT NOT NULL,
                    url TEXT NOT NULL,
                    final_url TEXT,
                    label TEXT,
                    category TEXT,
                    required_for_complete_text INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    content_sha256 TEXT REFERENCES attachment_blobs(content_sha256),
                    extracted_text TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_job_attachments_job ON job_attachments(job_key);

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
                    vacancies_unavailable INTEGER NOT NULL DEFAULT 0,
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
                    detail_unavailable INTEGER NOT NULL DEFAULT 0,
                    unavailable_vacancies TEXT NOT NULL DEFAULT '[]',
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
                            'blocked_by_circuit_breaker',
                            'unavailable_pending_inventory',
                            'listing_detail_conflict'
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
            self._ensure_column(conn, "source_runs", "vacancies_unavailable", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "source_run_diagnostics", "detail_unavailable", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "source_run_diagnostics", "unavailable_vacancies", "TEXT NOT NULL DEFAULT '[]'")
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
                            'blocked_by_circuit_breaker',
                            'unavailable_pending_inventory',
                            'listing_detail_conflict'
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
        if row is None or "listing_detail_conflict" in str(row["sql"] or ""):
            return
        script = """
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
                        'blocked_by_circuit_breaker',
                        'unavailable_pending_inventory',
                        'listing_detail_conflict'
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
        # Execute this fixed migration under a savepoint: executescript would
        # commit the rename/create before a later copy failure can roll it back.
        conn.execute("SAVEPOINT migrate_detail_backlog")
        try:
            for statement in script.split(";"):
                if statement.strip():
                    conn.execute(statement)
        except BaseException:
            conn.execute("ROLLBACK TO migrate_detail_backlog")
            conn.execute("RELEASE migrate_detail_backlog")
            raise
        conn.execute("RELEASE migrate_detail_backlog")

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

    @staticmethod
    def _unv_bound_public_detail(raw: dict[str, Any], external_id: str | None, description: str | None) -> bool:
        if (raw.get("_unv_record_kind") != "detail" or not str(external_id or "").isdigit()
                or str(raw.get("id")) != str(external_id)):
            return False
        digest = hashlib.sha256(" ".join(str(description or "").split()).encode()).hexdigest()
        proof = raw.get("_unv_public_text_verification", {})
        if not isinstance(proof, dict) or type(proof.get("complete")) is not bool:
            return False
        if proof["complete"] and not isinstance(raw.get("_unv_public_render_context"), dict):
            return False
        return bool(description) and all(
            isinstance(raw.get(key), dict) and raw[key].get("complete") is proof["complete"]
            and raw[key].get("normalized_description_sha256") == digest
            for key in ("_unv_public_text_verification", "_jobagg_main_text_verification")
        )

    @staticmethod
    def _europol_bound_public_detail(raw: dict[str, Any], current: sqlite3.Row) -> bool:
        node = raw.get("europol_public_vacancy")
        resolution = raw.get("_eu_official_field_resolution")
        if (raw.get("parser") != "eu_official_detail" or not isinstance(node, dict)
                or not isinstance(resolution, dict) or resolution.get("record_kind") != "detail"
                or resolution.get("provider") != "europol_public_vacancy"
                or resolution.get("utc_resolved") is not True or node.get("type") != "vacancy"):
            return False
        external_id = str(current["external_id"] or "")
        def compact(value):
            return "".join(c for c in str(value).casefold() if c.isalnum())
        url = urlsplit(str(raw.get("official_vacancy_url") or ""))
        path = f"/work-with-us/careers/open-vacancies/vacancy/{node.get('id')}"
        if (type(node.get("id")) is not int or raw.get("external_id") != external_id
                or compact(node.get("referenceNumber")) != compact(external_id)
                or clean_text(node.get("title")) != clean_text(current["title"])
                or url.scheme != "https" or url.netloc != "www.europol.europa.eu"
                or url.path != path or node.get("alias") != path or url.query or url.fragment
                or current["apply_url"] != url.geturl()
                or not clean_text(node.get("body")) or not clean_text(raw.get("official_notice_text"))
                or clean_text(node.get("body")) != clean_text(raw.get("official_notice_text"))
                or clean_text(raw.get("official_notice_text")) not in (clean_text(current["description"]) or "")):
            return False
        for node_key, resolution_key, column in (
            ("published", "published_epoch", "posted_at"), ("deadline", "deadline_epoch", "closes_at"),
        ):
            value = node.get(node_key)
            if (type(value) is not int or not 946684800 <= value < 4102444800
                    or resolution.get(resolution_key) != value
                    or _parse_dt(current[column]) != datetime.fromtimestamp(value, UTC)):
                return False
        return bool(raw.get("detail_html")) and resolution.get("public_timezone") == current["closes_tz"]

    @staticmethod
    def _echa_bound_public_detail(raw: dict[str, Any], row: Any) -> bool:
        """Retain the observed public Download/PDF claim without guessing UTC."""
        import html
        import re

        proof = raw.get("_eu_official_field_resolution", {})
        primary = raw.get("_eu_reviewed_primary_extraction", {})
        download = raw.get("_echa_public_download_response", {})
        if not all(isinstance(value, dict) for value in (proof, primary, download)):
            return False
        reference = str(proof.get("public_reference") or "")
        identity = reference.lower().replace("/", "-")
        body = raw.get("official_notice_text")
        pdf = urlsplit(str(raw.get("official_vacancy_url") or ""))
        entry = urlsplit(str(raw.get("official_wrapper_url") or ""))
        action = "https://jobs.echa.europa.eu/psc/pshrrcr/EMPLOYEE/HRMS/c/HRS_HRAM.HRS_APP_SCHJOB.GBL"
        if (not re.fullmatch(r"ECHA/(?:TA|CA)/\d{4}/\d{2}", reference)
                or str(row["external_id"]) != identity or raw.get("external_id") != identity
                or raw.get("parser") != "eu_official_detail"
                or proof.get("record_kind") != "detail" or proof.get("provider") != "echa_public_notice_pdf"
                or not isinstance(body, str) or not body or row["description"] != body
                or primary.get("text_sha256") != hashlib.sha256(body.encode()).hexdigest()
                or type(primary.get("page_count")) is not int or primary["page_count"] < 1
                or not re.fullmatch(r"[0-9a-f]{64}", str(primary.get("content_sha256") or ""))
                or download.get("url") != action
                or not all(re.fullmatch(r"[0-9a-f]{64}", str(download.get(key) or "")) for key in ("sha256", "body_sha256"))
                or pdf.scheme != "https" or pdf.netloc != "jobs.echa.europa.eu"
                or not pdf.path.startswith("/psc/pshrrcr/view/") or not pdf.path.endswith(".pdf")
                or not pdf.path.rsplit("/", 1)[-1].startswith(reference.replace("/", "-") + "_")
                or pdf.query or pdf.fragment or raw.get("detail_fetch_url") != pdf.geturl()
                or pdf.geturl() not in raw.get("required_attachment_urls", [])
                or pdf.geturl() not in html.unescape(str(raw.get("detail_html") or ""))
                or entry.scheme != "https" or entry.netloc != "jobs.echa.europa.eu"
                or entry.path != "/psp/pshrrcr/EMPLOYEE/HRMS/c/HRS_HRAM.HRS_APP_SCHJOB.GBL"
                or entry.query != "FOCUS=Applicant" or entry.fragment or row["apply_url"] != entry.geturl()
                or proof.get("posting_time_resolved") is not False or proof.get("utc_resolved") is not False
                or any(row[key] is not None for key in ("posted_at", "closes_at", "closes_at_local", "closes_tz"))):
            return False
        public = " ".join(body.split())
        labels = (
            ("Job Title", "Function Group/Grade", "public_title", "title"),
            ("Location", "Publication Date", "public_location", "location"),
            ("Publication Date", "Deadline for Applications", "public_publication_date", None),
            ("Deadline for Applications", "Indicative number of candidates on the reserve list", "public_deadline", None),
        )
        for begin, end, key, column in labels:
            match = re.search(re.escape(begin) + r" (.*?) " + re.escape(end), public)
            if not match or match[1] != proof.get(key) or (column and row[column] != match[1]):
                return False
        contract = re.search(r"Function Group/Grade (Temporary Agent|Contract Agent), ((?:AD|AST|FG)\s*\d+)\b", public)
        return ("Reference number " + reference in public and bool(contract)
                and row["employment_type"] == contract[1] == proof.get("public_contract_type")
                and re.sub(r"\s+", "", contract[2]) == proof.get("official_grade"))

    @staticmethod
    def _eurlex_bound_public_detail(raw: dict[str, Any], row: Any) -> bool:
        """Keep the full official notice and only its source-explicit fields."""
        from jobagg.adapters.eurlex_public import render_public_notice
        from jobagg.models import OrganizationSource

        proof = raw.get("_eu_official_field_resolution")
        if (row["source_id"] != "eu_careers_static" or raw.get("parser") != "eu_official_detail"
                or not isinstance(proof, dict) or proof.get("record_kind") != "detail"
                or proof.get("provider") != "eurlex_official_public_notice"
                or str(raw.get("external_id")) != str(row["external_id"])
                or not isinstance(raw.get("detail_html"), str)):
            return False
        try:
            expected = render_public_notice(
                OrganizationSource("eu_careers_static", "EU Careers", "static_html", "https://eu-careers.europa.eu"),
                raw["detail_html"], page_url=raw.get("official_vacancy_url", ""),
                external_id=str(row["external_id"]), summary_url=raw.get("detail_url", ""),
                expected_title=row["title"], summary_metadata=raw.get("summary_metadata"),
            )
        except (ValueError, TypeError, AttributeError, KeyError):
            return False
        if proof != expected.raw["_eu_official_field_resolution"]:
            return False
        if any(row[key] != getattr(expected, key) for key in (
            "title", "description", "department", "location", "employment_type", "apply_url", "source_url",
            "closes_at_local", "closes_tz",
        )):
            return False
        if _parse_dt(row["posted_at"]) != expected.posted_at or _parse_dt(row["closes_at"]) != expected.closes_at:
            return False
        return all(raw.get(key) == expected.raw.get(key) for key in (
            "official_notice_text", "detail_html", "detail_url", "detail_fetch_url", "official_vacancy_url",
            "required_attachment_urls", "grade", "institution", "identity_verification",
        ))

    @staticmethod
    def _eu_reviewed_pdf_bound_public_detail(raw: dict[str, Any], row: Any) -> bool:
        """Reproduce separately reviewed EUIPO/EUDA fields from ordered pages."""
        from jobagg.adapters.eu_primary_public import render_public_notice
        from jobagg.models import OrganizationSource
        if row["source_id"] != "eu_careers_static" or raw.get("parser") != "eu_official_detail":
            return False
        try:
            expected = render_public_notice(
                OrganizationSource("eu_careers_static", "EU Careers", "static_html", "https://eu-careers.europa.eu"),
                external_id=str(row["external_id"]), summary_url=raw.get("detail_url"),
                primary_url=raw.get("official_vacancy_url"), page_units=raw.get("public_primary_page_units"),
                document_proof=raw.get("public_primary_document_proof"),
                required_attachment_urls=raw.get("required_attachment_urls"),
                source_conflicts=raw.get("reviewed_source_content_conflicts"),
            )
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            return False
        if any(row[key] != getattr(expected, key) for key in (
            "external_id", "title", "description", "location", "department", "employment_type",
            "source_url", "apply_url", "closes_at_local", "closes_tz",
        )):
            return False
        if _parse_dt(row["posted_at"]) != expected.posted_at or _parse_dt(row["closes_at"]) != expected.closes_at:
            return False
        return all(raw.get(key) == value for key, value in expected.raw.items())

    @staticmethod
    def _eu_bound_public_detail(raw: dict[str, Any], row: Any) -> bool:
        """Bind recognized official field claims to the retained source/body."""
        resolution = raw.get("_eu_official_field_resolution")
        if not isinstance(resolution, dict) or resolution.get("record_kind") != "detail":
            return False
        provider = resolution.get("provider")
        if provider == "europol_public_vacancy":
            return JobDatabase._europol_bound_public_detail(raw, row)
        if provider == "echa_public_notice_pdf":
            return JobDatabase._echa_bound_public_detail(raw, row)
        if provider == "eurlex_official_public_notice":
            return JobDatabase._eurlex_bound_public_detail(raw, row)
        if provider in {"euipo_reviewed_primary_pdf", "euda_reviewed_primary_pdf"}:
            return JobDatabase._eu_reviewed_pdf_bound_public_detail(raw, row)
        if provider not in {"sesar_official_vacancy_pdf", "enisa_official_wrapper_and_pdf", "eda_public_notice_api"}:
            return False
        from jobagg.adapters.eda_public import render_public_notice
        from jobagg.adapters.static_html import _enisa_public_wrapper, _sesar_notice_section
        from jobagg.models import OrganizationSource
        from zoneinfo import ZoneInfo
        import re

        def compact(value):
            return "".join(c for c in str(value).casefold() if c.isalnum())
        external_id = str(row["external_id"] or "")
        notice = clean_text(raw.get("official_notice_text"))
        url = urlsplit(str(raw.get("official_vacancy_url") or ""))
        pdf_proof = raw.get("_eu_reviewed_primary_extraction", {})
        public_pdf = (url.path.lower().endswith(".pdf")
                      and url.geturl() in raw.get("required_attachment_urls", [])
                      and raw.get("identity_verification") == "official_link_and_title_or_reference")
        if pdf_proof and (not isinstance(pdf_proof, dict)
                          or pdf_proof.get("text_sha256") != hashlib.sha256(str(raw.get("official_notice_text") or "").encode()).hexdigest()):
            return False
        if (raw.get("parser") != "eu_official_detail" or not external_id
                or not (raw.get("detail_html") or public_pdf) or not notice
                or notice not in (clean_text(row["description"]) or "")
                or row["apply_url"] != url.geturl() or url.scheme != "https"):
            return False
        if provider == "eda_public_notice_api":
            try:
                expected = render_public_notice(
                    OrganizationSource("eu_careers_static", "EU", "static_html", "https://eu-careers.europa.eu"),
                    raw.get("eda_public_notice"), page_url=url.geturl(), external_id=external_id,
                    expected_title=row["title"], summary_html=raw.get("summary_html", ""),
                    summary_url=raw.get("detail_url", ""),
                )
            except (ValueError, TypeError, AttributeError, KeyError):
                return False
            return (resolution == expected.raw["_eu_official_field_resolution"]
                    and all(row[key] == getattr(expected, key) for key in (
                        "title", "description", "department", "location", "employment_type", "closes_at_local", "closes_tz"))
                    and raw["detail_html"] == expected.raw["detail_html"]
                    and raw.get("detail_fetch_url") == expected.raw["detail_fetch_url"]
                    and row["posted_at"] is None and row["closes_at"] is None)
        if raw.get("external_id") != external_id or compact(external_id) not in compact(notice):
            return False
        if provider == "sesar_official_vacancy_pdf":
            directory = raw.get("official_directory_notice")
            if not isinstance(directory, dict) or url.hostname != "www.sesarju.eu":
                return False
            try:
                observed = _sesar_notice_section(directory["section_html"], directory["directory_url"], external_id)
            except (ValueError, TypeError, KeyError):
                return False
            contract = re.search(r"\b(?:Administrator|Assistant)\s*[-–—]\s*(TA\s*2\s*\(\s*[a-z]\s*\))\s*[-–—]\s*((?:AD|AST)\s*\d{1,2})\b", notice, re.I)
            expected_contract = re.sub(r"\s+", " ", contract[1]).strip() if contract else None
            return (observed == directory and directory["notice_url"] == url.geturl()
                    and row["employment_type"] == expected_contract
                    and resolution.get("employment_type") == expected_contract
                    and resolution.get("contract_type_resolved") is bool(contract)
                    and resolution.get("public_contract_phrase") == (contract[0] if contract else None)
                    and resolution.get("official_grade") == (re.sub(r"\s+", "", contract[2]).upper() if contract else None))
        wrapper = raw.get("enisa_public_wrapper")
        if (not isinstance(wrapper, dict) or url.hostname != "www.enisa.europa.eu"
                or urlsplit(str(raw.get("official_wrapper_url"))).hostname != "www.enisa.europa.eu"
                or not url.path.lower().endswith(".pdf") or not isinstance(wrapper.get("fields"), dict)
                or wrapper.get("title") != row["title"] or compact(row["title"]) not in compact(notice)
                or not clean_text(wrapper.get("text"))
                or clean_text(wrapper["text"]) not in (clean_text(row["description"]) or "")):
            return False
        fields = wrapper["fields"]
        try:
            sections = wrapper["sections_html"]
            reparsed = _enisa_public_wrapper("<main>" + "".join(sections[key] for key in ("title", "body", "how_to_apply")) + "</main>")
        except (KeyError, TypeError, ValueError):
            return False
        if reparsed != wrapper:
            return False
        if (resolution.get("public_fields") != fields
                or any(row[column] != fields.get(label) for column, label in (
                    ("department", "area"), ("location", "place of employment"), ("employment_type", "type of contract")))):
            return False
        deadline = fields.get("deadline for applications", "")
        match = re.fullmatch(r"(\d{2}/\d{2}/\d{4}) at (\d{2}:\d{2}:\d{2}) Greek time", deadline, re.I)
        if resolution.get("public_deadline") != deadline or resolution.get("utc_resolved") is not bool(match):
            return False
        if not match:
            return all(row[key] is None for key in ("closes_at", "closes_at_local", "closes_tz"))
        local = datetime.strptime(match[1] + " " + match[2], "%d/%m/%Y %H:%M:%S").replace(tzinfo=ZoneInfo("Europe/Athens"))
        return (_parse_dt(row["closes_at"]) == local.astimezone(UTC)
                and row["closes_at_local"] == local.isoformat() and row["closes_tz"] == "Europe/Athens")

    @staticmethod
    def _osce_bound_public_detail(raw: dict[str, Any], row: Any) -> bool:
        from jobagg.adapters.static_html import parse_detail_page
        from jobagg.models import OrganizationSource
        url = urlsplit(str(raw.get("href") or ""))
        if (raw.get("parser") != "static_detail" or not raw.get("detail_html")
                or url.scheme != "https" or url.hostname != "vacancies.osce.org"
                or not url.path.startswith("/jobs/") or url.query or url.fragment
                or row["apply_url"] != url.geturl()):
            return False
        try:
            expected = parse_detail_page(
                OrganizationSource("osce_custom_html", "OSCE", "static_html", "https://vacancies.osce.org"),
                raw["detail_html"], url.geturl(),
            )
        except (ValueError, TypeError, KeyError):
            return False
        return (raw.get("_osce_public_field_resolution") == expected.raw.get("_osce_public_field_resolution")
                and all(row[key] == getattr(expected, key) for key in (
                    "external_id", "title", "description", "department", "location", "employment_type",
                    "closes_at_local", "closes_tz"))
                and all(raw.get(key) == expected.raw.get(key) for key in ("grade", "contract_type"))
                and row["posted_at"] is None and row["closes_at"] is None)

    @staticmethod
    def _wipo_bound_public_detail(raw: dict[str, Any], external_id: str | None,
                                  title: str | None, description: str | None,
                                  employment_type: str | None) -> bool:
        resolution = raw.get("_taleo_public_metadata_resolution", {})
        if not isinstance(resolution, dict) or resolution.get("kind") != "paired_public_dom_bindings":
            return False
        from jobagg.adapters.base import AdapterContext
        from jobagg.adapters.taleo import TaleoAdapter
        from jobagg.models import OrganizationSource
        from urllib.parse import parse_qs
        url = str(raw.get("_taleo_detail_url") or "")
        parsed_url = urlsplit(url)
        if (raw.get("_taleo_record_kind") != "detail" or not raw.get("detail_html")
                or parsed_url.scheme != "https" or parsed_url.netloc != "wipo.taleo.net"
                or parse_qs(parsed_url.query).get("job") != [str(external_id)]):
            raise ValueError("WIPO public detail metadata lacks its matching source URL/identity")
        adapter = TaleoAdapter(AdapterContext(
            OrganizationSource("wipo_taleo", "WIPO", "taleo", "https://wipo.taleo.net"), None))
        expected = adapter.parse_detail_html(raw["detail_html"], url)
        if (expected.external_id != external_id or expected.title != title or expected.description != description
                or expected.employment_type != employment_type
                or expected.raw.get("_taleo_flat") != raw.get("_taleo_flat")
                or expected.raw.get("_taleo_public_metadata_resolution") != resolution):
            raise ValueError("WIPO public metadata differs from its captured HTML fields or body")
        return True

    @staticmethod
    def _iom_contract_detail(raw: dict[str, Any], external_id: str | None) -> bool:
        resolution = raw.get("_oracle_contract_resolution")
        if not isinstance(resolution, dict):
            return False
        from jobagg.adapters.oracle_hcm import _flex_value
        contract = raw.get("ContractType") or _flex_value(raw, "Contract Type")
        field = "ContractType" if raw.get("ContractType") else "requisitionFlexFields.Contract Type" if contract else None
        if (resolution.get("record_kind") != "detail" or str(raw.get("Id")) != str(external_id)
                or resolution.get("public_contract_type") != contract
                or resolution.get("resolved") is not bool(contract) or resolution.get("source_field") != field):
            raise ValueError("IOM contract resolution differs from its public identity or fields")
        return True

    @staticmethod
    def _labelled_notice_bound_public_detail(source_id: str, raw: dict[str, Any], row: Any) -> bool:
        """Validate normalized public fields against their exact source body."""
        import re
        from jobagg.adapters.base import AdapterContext
        from jobagg.models import OrganizationSource
        marker = {"unu_recruitee": ("_unu_public_field_resolution", "unu_rendered_public_fields"),
                  "itu_successfactors": ("_itu_public_field_resolution", "itu_labelled_public_notice"),
                  "paho_workday": ("_paho_public_field_resolution", "paho_labelled_public_notice"),
                  "cern_custom_html": ("_cern_public_field_resolution", "cern_labelled_public_notice"),
                  "idb_successfactors": ("_idb_public_field_resolution", "idb_labelled_public_notice"),
                  "ebrd_successfactors": ("_ebrd_public_field_resolution", "ebrd_labelled_public_notice"),
                  "unops_avature": ("_avature_posting_time_resolution", "unops_labelled_public_notice"),
                  "worldbank_csod": ("_worldbank_public_field_resolution", "worldbank_public_jobposting"),
                  "icc_successfactors_legacy": ("_legacy_public_field_resolution", "successfactors_legacy_public_page"),
                  "afdb_successfactors_legacy": ("_legacy_public_field_resolution", "successfactors_legacy_public_page")}.get(source_id)
        resolution = raw.get(marker[0]) if marker else None
        if (not isinstance(resolution, dict) or resolution.get("record_kind") != "detail"
                or resolution.get("provider") != marker[1]):
            return False
        if source_id == "unu_recruitee":
            from jobagg.adapters.static_html import parse_detail_page
            html_text, detail_url = raw.get("detail_html"), row["source_url"]
            url = urlsplit(str(detail_url or ""))
            if (not isinstance(html_text, str) or raw.get("parser") != "recruitee_public_detail_tab"
                    or url.scheme != "https" or url.netloc != "careers.unu.edu" or url.query or url.fragment
                    or url.path != "/o/" + str(row["external_id"]) or row["apply_url"] != detail_url):
                return False
            expected = parse_detail_page(OrganizationSource(
                source_id, "UNU", "unu_recruitee", "https://careers.unu.edu/"), html_text, detail_url)
            keys = tuple(expected.raw)
        elif source_id == "unops_avature":
            from jobagg.adapters.avature import AvatureAdapter
            html_text, detail_url = raw.get("detail_html"), raw.get("_detail_url")
            url = urlsplit(str(detail_url or ""))
            if (not isinstance(html_text, str) or not isinstance(detail_url, str)
                    or url.scheme != "https" or url.netloc != "careers.unops.org" or url.query or url.fragment
                    or not re.fullmatch(r"/careersmarketplace/JobDetail/[^/]+/" + re.escape(str(row["external_id"])), url.path)
                    or row["apply_url"] != detail_url or row["source_url"] != detail_url):
                return False
            expected = AvatureAdapter(AdapterContext(OrganizationSource(
                source_id, "UNOPS", "avature", "https://careers.unops.org"), None)).parse_detail_html(html_text, detail_url)
            keys = ("_avature_posting_time_resolution", "avature_fields", "_avature_deadline_resolution",
                    "_avature_field_resolution", "_avature_competency_text_resolution")
        elif source_id == "itu_successfactors":
            from jobagg.adapters.itu_public import apply_public_fields
            from jobagg.adapters.successfactors_rmk import _detail_description, _detail_title, _job_id_from_url
            from jobagg.normalize import build_job
            url = urlsplit(str(raw.get("detail_url") or ""))
            html_text = raw.get("detail_html")
            if (raw.get("parser") != "successfactors_detail" or not isinstance(html_text, str)
                    or url.scheme != "https" or url.netloc != "jobs.itu.int"
                    or not url.path.startswith("/job/") or url.query or url.fragment
                    or _job_id_from_url(url.geturl()) != row["external_id"]
                    or row["apply_url"] != url.geturl() or row["source_url"] != url.geturl()):
                return False
            expected = apply_public_fields(build_job(
                OrganizationSource(source_id, "ITU", "successfactors_rmk", "https://jobs.itu.int"),
                title=_detail_title(html_text), external_id=row["external_id"],
                description=_detail_description(html_text), apply_url=url.geturl(), raw={},
            ), html_text)
            keys = ("_itu_public_field_resolution", "itu_public_fields", "grade", "contract_type", "position_number")
        elif source_id in {"idb_successfactors", "ebrd_successfactors"}:
            if source_id == "idb_successfactors":
                from jobagg.adapters.idb_public import apply_public_fields
                name, host = "IDB", "https://jobs.iadb.org"
                keys = ("_idb_public_field_resolution", "company", "contract_type")
            else:
                from jobagg.adapters.ebrd_public import apply_public_fields
                name, host = "EBRD", "https://jobs.ebrd.com"
                keys = ("_ebrd_public_field_resolution", "company", "contract_type", "requisition_id")
            from jobagg.adapters.successfactors_rmk import _detail_description, _detail_title
            from jobagg.normalize import build_job
            html_text, detail_url = raw.get("detail_html"), raw.get("detail_url")
            if (raw.get("parser") != "successfactors_detail" or not isinstance(html_text, str)
                    or not isinstance(detail_url, str) or row["apply_url"] != detail_url or row["source_url"] != detail_url):
                return False
            expected = apply_public_fields(build_job(
                OrganizationSource(source_id, name, "successfactors_rmk", host),
                title=_detail_title(html_text), external_id=row["external_id"],
                description=_detail_description(html_text), apply_url=row["apply_url"],
                source_url=row["source_url"], raw={"detail_url": detail_url},
            ), html_text)
        elif source_id == "worldbank_csod":
            from jobagg.adapters.worldbank_public import render_public_notice
            posting, detail_url = raw.get("worldbank_public_jobposting"), raw.get("detail_url")
            if (raw.get("_worldbank_record_kind") != "detail" or not isinstance(posting, dict)
                    or raw.get("detail_html") != posting.get("Description") or not isinstance(detail_url, str)
                    or row["apply_url"] != detail_url or row["source_url"] != detail_url):
                return False
            expected = render_public_notice(
                OrganizationSource(source_id, "World Bank", "csod", "https://worldbankgroup.csod.com"),
                posting, page_url=detail_url, external_id=str(row["external_id"]), expected_title=row["title"],
                listing_raw=raw.get("worldbank_listing_metadata"), public_page_sha256=raw.get("public_page_sha256"),
            )
            keys = ("_worldbank_record_kind", "_worldbank_public_field_resolution", "worldbank_public_jobposting",
                    "grade", "company", "sector", "recruitment_type", "term_duration", "required_languages", "preferred_languages")
        elif source_id in {"icc_successfactors_legacy", "afdb_successfactors_legacy"}:
            from jobagg.adapters.legacy_public import render_public_notice
            html_text, detail_url = raw.get("detail_html"), raw.get("detail_url")
            if (raw.get("parser") != "successfactors_legacy_public" or not isinstance(html_text, str)
                    or not isinstance(detail_url, str) or row["apply_url"] != detail_url or row["source_url"] != detail_url):
                return False
            expected = render_public_notice(
                OrganizationSource(source_id, "Public legacy source", "successfactors_legacy", detail_url),
                html_text, page_url=detail_url, external_id=str(row["external_id"]), expected_title=row["title"],
            )
            keys = ("_legacy_public_field_resolution", "legacy_public_notice_html", "grade", "contract_type")
        elif source_id == "paho_workday":
            from jobagg.adapters.workday import WorkdayAdapter
            info = raw.get("jobPostingInfo")
            if not isinstance(info, dict):
                return False
            url = urlsplit(str(info.get("externalUrl") or ""))
            identity = info.get("jobReqId") or info.get("jobPostingId") or info.get("id")
            if (url.scheme != "https" or url.netloc != "paho.wd5.myworkdayjobs.com"
                    or not url.path.startswith("/pahocareers/job/") or url.query or url.fragment
                    or str(identity) != str(row["external_id"])
                    or url.path.rsplit("/", 1)[-1] != info.get("jobPostingId")
                    or not re.search("_" + re.escape(str(identity)) + r"(?:-\d+)?$", url.path)
                    or row["apply_url"] != url.geturl() or row["source_url"] != url.geturl()):
                return False
            expected = WorkdayAdapter(AdapterContext(OrganizationSource(
                source_id, "PAHO", "workday", "https://paho.wd5.myworkdayjobs.com/pahocareers"), None)).parse_detail(
                    {"jobPostingInfo": info})
            keys = ("_paho_public_field_resolution",)
        elif source_id == "cern_custom_html":
            from jobagg.adapters.static_html import parse_detail_page
            url = urlsplit(str(raw.get("href") or ""))
            if (raw.get("parser") != "static_detail" or not isinstance(raw.get("detail_html"), str)
                    or url.scheme != "https" or url.netloc != "careers.cern"
                    or not re.fullmatch(r"/jobs/[^/]+/", url.path) or url.query or url.fragment
                    or row["apply_url"] != url.geturl() or row["source_url"] != url.geturl()):
                return False
            expected = parse_detail_page(OrganizationSource(
                source_id, "CERN", "custom_html", "https://careers.cern"), raw["detail_html"], url.geturl())
            keys = ("_cern_public_field_resolution", "grade", "contract_type")
        else:
            return False
        return (all(raw.get(key) == expected.raw.get(key) for key in keys)
                and all(row[key] == getattr(expected, key) for key in (
                    "external_id", "title", "description", "location", "department", "employment_type",
                    "closes_at_local", "closes_tz"))
                and _parse_dt(row["posted_at"]) == expected.posted_at
                and _parse_dt(row["closes_at"]) == expected.closes_at)

    def _merge_labelled_public_observation(self, job: JobRecord, current: sqlite3.Row,
                                         current_raw: dict[str, Any]) -> bool:
        marker = {"unu_recruitee": "_unu_public_field_resolution",
                  "itu_successfactors": "_itu_public_field_resolution",
                  "paho_workday": "_paho_public_field_resolution",
                  "cern_custom_html": "_cern_public_field_resolution",
                  "idb_successfactors": "_idb_public_field_resolution",
                  "ebrd_successfactors": "_ebrd_public_field_resolution",
                  "unops_avature": "_avature_posting_time_resolution",
                  "worldbank_csod": "_worldbank_public_field_resolution",
                  "icc_successfactors_legacy": "_legacy_public_field_resolution",
                  "afdb_successfactors_legacy": "_legacy_public_field_resolution"}.get(job.source_id)
        if marker is None:
            return False
        incoming_detail = marker in job.raw
        prior_detail = marker in current_raw
        if not incoming_detail and not prior_detail:
            return False
        if current["source_id"] != job.source_id or current["external_id"] != job.external_id:
            raise ValueError("Labelled public field retention requires matching source and identity")
        if incoming_detail:
            row = {key: getattr(job, key) for key in (
                "external_id", "title", "description", "location", "department", "employment_type",
                "apply_url", "source_url", "closes_at_local", "closes_tz",
            )}
            row.update(posted_at=_dt(job.posted_at), closes_at=_dt(job.closes_at))
            if not self._labelled_notice_bound_public_detail(job.source_id, job.raw, row):
                raise ValueError("Labelled incoming public fields lack a matching source/body binding")
            if job.source_id == "unops_avature" and "_avature_previous_posting_observation" not in job.raw:
                if isinstance(current_raw.get("_avature_previous_posting_observation"), dict):
                    job.raw["_avature_previous_posting_observation"] = current_raw["_avature_previous_posting_observation"]
                elif current["posted_at"] != _dt(job.posted_at):
                    prior_html = current_raw.get("detail_html")
                    prior_fields = current_raw.get("avature_fields", {})
                    job.raw["_avature_previous_posting_observation"] = {
                        "posted_at": current["posted_at"],
                        "public_fields": {key: prior_fields[key] for key in ("Posted", "Posting Start Date") if key in prior_fields},
                        "detail_html_sha256": hashlib.sha256(prior_html.encode()).hexdigest() if isinstance(prior_html, str) else None,
                        "raw_json_sha256": hashlib.sha256(current["raw_json"].encode()).hexdigest(),
                        "superseded_reason": "Public posting precision is owned by the new source-bound detail observation.",
                    }
            if job.source_id in {"icc_successfactors_legacy", "afdb_successfactors_legacy"}:
                if "_legacy_xml_listing_snapshot" not in job.raw:
                    if isinstance(current_raw.get("_legacy_xml_listing_snapshot"), dict):
                        job.raw["_legacy_xml_listing_snapshot"] = current_raw["_legacy_xml_listing_snapshot"]
                    elif current_raw.get("parser") == "successfactors_xml":
                        job.raw["_legacy_xml_listing_snapshot"] = {
                            key: current_raw.get(key) for key in ("reqid", "jobtitle", "jobdescription", "detail_html")
                        }
            for key in ("attachments", "_jobagg_listing_verification"):
                if key not in job.raw and key in current_raw:
                    job.raw[key] = current_raw[key]
            if "attachment_verification" not in job.raw and isinstance(current_raw.get("attachment_verification"), dict):
                proof = dict(current_raw["attachment_verification"])
                if (job.description != current["description"]
                        or any(job.raw.get(key) != current_raw.get(key) for key in ("detail_html", "jobPostingInfo"))):
                    proof.update(complete=False, discovery_complete=False,
                                 invalidated_reason="job_content_changed_requires_attachment_reverification")
                job.raw["attachment_verification"] = proof
            if job.source_id in {"idb_successfactors", "ebrd_successfactors", "unops_avature", "icc_successfactors_legacy", "afdb_successfactors_legacy", "worldbank_csod"}:
                proof = job.raw.get("attachment_verification")
                if isinstance(proof, dict) and (proof.get("complete") is not False or proof.get("discovery_complete") is not False):
                    job.raw["attachment_verification"] = {
                        **proof, "complete": False, "discovery_complete": False,
                        "invalidated_reason": "public_detail_refresh_requires_attachment_reverification",
                    }
            return True
        if job.source_id == "unu_recruitee":
            url = urlsplit(str(job.raw.get("href") or ""))
            listing = (not job.raw.get("detail_html") and job.raw.get("parser") == "public_links"
                       and str(job.raw.get("external_id")) == job.external_id
                       and url.scheme == "https" and url.netloc == "careers.unu.edu" and not url.query and not url.fragment
                       and url.path == "/o/" + job.external_id and job.apply_url == url.geturl() and job.source_url == url.geturl())
        elif job.source_id == "unops_avature":
            from jobagg.adapters.avature import _job_id_from_url
            url = urlsplit(str(job.raw.get("_detail_url") or ""))
            listing = (not job.raw.get("detail_html") and bool(job.raw.get("listing_html"))
                       and url.scheme == "https" and url.netloc == "careers.unops.org" and not url.query and not url.fragment
                       and url.path.startswith("/careersmarketplace/JobDetail/")
                       and _job_id_from_url(url.geturl()) == job.external_id
                       and job.source_url == url.geturl() and job.apply_url == url.geturl())
        elif job.source_id == "itu_successfactors":
            from jobagg.adapters.successfactors_rmk import _job_id_from_url
            url = urlsplit(str(job.raw.get("detail_url") or ""))
            listing = (not job.raw.get("detail_html") and bool(job.raw.get("listing_html"))
                       and url.scheme == "https" and url.netloc == "jobs.itu.int"
                       and url.path.startswith("/job/") and not url.query and not url.fragment
                       and _job_id_from_url(url.geturl()) == job.external_id and job.apply_url == url.geturl())
        elif job.source_id in {"idb_successfactors", "ebrd_successfactors"}:
            from jobagg.adapters.successfactors_rmk import _job_id_from_url
            url = urlsplit(str(job.raw.get("detail_url") or ""))
            host = "jobs.iadb.org" if job.source_id == "idb_successfactors" else "jobs.ebrd.com"
            listing = (not job.raw.get("detail_html")
                       and (bool(job.raw.get("listing_html"))
                            or (job.source_id == "idb_successfactors" and job.raw.get("parser") == "browser_inventory"))
                       and url.scheme == "https" and url.netloc == host
                       and url.path.startswith("/job/") and not url.query and not url.fragment
                       and _job_id_from_url(url.geturl()) == job.external_id
                       and job.apply_url == url.geturl() and job.source_url == url.geturl())
        elif job.source_id == "worldbank_csod":
            from jobagg.adapters.csod import CSODAdapter
            from jobagg.adapters.base import AdapterContext
            from jobagg.models import OrganizationSource
            adapter = CSODAdapter(AdapterContext(OrganizationSource(
                job.source_id, "World Bank", "csod", "https://worldbankgroup.csod.com"), None))
            exact_url = f"https://worldbankgroup.csod.com/ux/ats/careersite/1/home/requisition/{job.external_id}?c=worldbankgroup"
            listing = (job.raw.get("_worldbank_record_kind") == "listing"
                       and not job.raw.get("worldbank_public_jobposting")
                       and adapter._external_id(job.raw) == job.external_id
                       and job.source_url == exact_url and job.apply_url == exact_url)
        elif job.source_id in {"icc_successfactors_legacy", "afdb_successfactors_legacy"}:
            from urllib.parse import parse_qs
            from jobagg.adapters.legacy_public import _SOURCES
            host, company = _SOURCES[job.source_id]
            url = urlsplit(str(job.source_url or ""))
            query = parse_qs(url.query, keep_blank_values=True)
            listing = (job.raw.get("parser") == "successfactors_xml"
                       and str(job.raw.get("reqid")) == str(job.external_id)
                       and url.scheme == "https" and url.hostname == host and url.path == "/career"
                       and not url.username and not url.password and url.port in (None, 443) and not url.fragment
                       and query.get("company") == [company] and query.get("career_ns") == ["job_listing"]
                       and query.get("career_job_req_id") == [str(job.external_id)]
                       and job.apply_url == url.geturl())
        elif job.source_id == "paho_workday":
            from jobagg.adapters.workday import WorkdayAdapter
            from jobagg.adapters.base import AdapterContext
            from jobagg.models import OrganizationSource
            listing_job = WorkdayAdapter(AdapterContext(OrganizationSource(
                "paho_workday", "PAHO", "workday", "https://paho.wd5.myworkdayjobs.com/pahocareers"), None)).parse_listing_item(job.raw)
            listing = (not job.raw.get("jobPostingInfo") and bool(job.raw.get("externalPath"))
                       and listing_job.external_id == job.external_id)
        else:
            url = urlsplit(str(job.raw.get("href") or ""))
            listing = (not job.raw.get("detail_html") and job.raw.get("parser") in (None, "public_links")
                       and str(job.raw.get("external_id")) == job.external_id
                       and url.scheme == "https" and url.netloc == "careers.cern"
                       and url.path.rstrip("/").rsplit("/", 1)[-1] == job.external_id
                       and job.apply_url == url.geturl())
        if not listing:
            if job.source_id in {"unu_recruitee", "idb_successfactors", "ebrd_successfactors", "unops_avature", "icc_successfactors_legacy", "afdb_successfactors_legacy", "worldbank_csod"}:
                raise ValueError("Public listing refresh lacks a matching source/identity binding")
            return False
        if not self._labelled_notice_bound_public_detail(job.source_id, current_raw, current):
            raise ValueError("Labelled retained public fields lack a matching source/body binding")
        observation_key = {"unu_recruitee": "_unu_listing_observation", "itu_successfactors": "_itu_listing_observation", "paho_workday": "_paho_listing_observation",
                           "cern_custom_html": "_cern_listing_observation", "idb_successfactors": "_idb_listing_observation",
                           "ebrd_successfactors": "_ebrd_listing_observation",
                           "unops_avature": "_avature_listing_observation",
                           "worldbank_csod": "_worldbank_listing_observation",
                           "icc_successfactors_legacy": "_legacy_listing_observation",
                           "afdb_successfactors_legacy": "_legacy_listing_observation"}[job.source_id]
        incoming = {key: value for key, value in job.raw.items() if key != observation_key}
        listing_proof = incoming.get("_jobagg_listing_verification")
        observation = {
            "raw": incoming, "normalized": {key: getattr(job, key) for key in (
                "title", "location", "department", "employment_type", "apply_url", "source_url", "closes_at_local", "closes_tz",
            )}, "posted_at": _dt(job.posted_at), "closes_at": _dt(job.closes_at),
            "observed_at": listing_proof.get("observed_at") if isinstance(listing_proof, dict) else None,
        }
        job.raw = {**current_raw, observation_key: observation}
        if isinstance(listing_proof, dict):
            job.raw["_jobagg_listing_verification"] = listing_proof
        for key in ("title", "location", "department", "employment_type", "description", "apply_url", "source_url", "closes_at_local", "closes_tz"):
            setattr(job, key, current[key])
        job.posted_at, job.closes_at = _parse_dt(current["posted_at"]), _parse_dt(current["closes_at"])
        return True

    @staticmethod
    def _workday_precision_bound_public_detail(raw: dict[str, Any], row: Any) -> bool:
        from jobagg.adapters.workday_precision import MARKER, SOURCES, instant, precision_resolution
        source_id = row["source_id"]
        if source_id not in SOURCES or MARKER not in raw or not isinstance(raw.get("jobPostingInfo"), dict):
            return False
        info = raw["jobPostingInfo"]
        try:
            expected = precision_resolution(source_id, info)
        except (ValueError, TypeError, KeyError):
            return False
        if expected is None or raw[MARKER] != expected:
            return False
        deadline = expected["deadline"]
        return (row["external_id"] == expected["external_id"]
                and row["source_url"] == expected["source_url"] == row["apply_url"]
                and row["title"] == clean_text(info.get("title") or info.get("jobTitle"))
                and row["description"] == clean_text(info.get("jobDescription") or info.get("description"))
                and _parse_dt(row["posted_at"]) == instant(expected["posting"]["posted_at"])
                and _parse_dt(row["closes_at"]) == instant(deadline["closes_at"])
                and row["closes_at_local"] == deadline["closes_at_local"]
                and row["closes_tz"] == deadline["closes_tz"])

    def _merge_workday_precision_observation(self, job: JobRecord, current: sqlite3.Row,
                                           current_raw: dict[str, Any]) -> bool:
        from jobagg.adapters.workday_precision import MARKER, SOURCES
        if job.source_id not in SOURCES or (MARKER not in job.raw and MARKER not in current_raw):
            return False
        if (job.source_id != current["source_id"] or job.external_id != current["external_id"]
                or job.ats_family != "workday"):
            raise ValueError("Workday precision retention requires the same source and identity")
        if MARKER in job.raw:
            row = {key: getattr(job, key) for key in (
                "source_id", "external_id", "title", "description", "source_url", "apply_url",
                "closes_at_local", "closes_tz",
            )}
            row.update(posted_at=_dt(job.posted_at), closes_at=_dt(job.closes_at))
            if not self._workday_precision_bound_public_detail(job.raw, row):
                raise ValueError("Incoming Workday dates lack an exact source/body/precision binding")
            previous_key = "_workday_previous_date_observation"
            if previous_key not in job.raw:
                if previous_key in current_raw:
                    job.raw[previous_key] = current_raw[previous_key]
                elif any(row[key] != current[key] for key in ("posted_at", "closes_at", "closes_at_local", "closes_tz")):
                    job.raw[previous_key] = {
                        "dates": {key: current[key] for key in ("posted_at", "closes_at", "closes_at_local", "closes_tz")},
                        "raw_json_sha256": hashlib.sha256(current["raw_json"].encode()).hexdigest(),
                        "source_claims": {key: current_raw[key] for key in (MARKER, "_workday_deadline_resolution") if key in current_raw},
                        "scope": "previous_stored_observation_not_current_deadline",
                    }
            for key in ("attachments", "_jobagg_listing_verification"):
                if key not in job.raw and key in current_raw:
                    job.raw[key] = current_raw[key]
            proof = job.raw.get("attachment_verification", current_raw.get("attachment_verification"))
            if isinstance(proof, dict):
                if (job.description != current["description"] or job.raw.get("jobPostingInfo") != current_raw.get("jobPostingInfo")):
                    proof = {**proof, "complete": False, "discovery_complete": False,
                             "invalidated_reason": "job_content_changed_requires_attachment_reverification"}
                job.raw["attachment_verification"] = proof
            return True
        if job.raw.get("jobPostingInfo"):
            raise ValueError("New Workday detail must resolve its own date precision")
        from jobagg.adapters.base import AdapterContext
        from jobagg.adapters.workday import WorkdayAdapter
        from jobagg.models import OrganizationSource
        listing = WorkdayAdapter(AdapterContext(OrganizationSource(
            job.source_id, job.source_id, "workday", SOURCES[job.source_id]), None)).parse_listing_item(job.raw)
        if (not job.raw.get("externalPath") or listing.external_id != job.external_id
                or listing.source_url != job.source_url or listing.apply_url != job.apply_url
                or job.source_url != current["source_url"] or job.apply_url != current["apply_url"]
                or not self._workday_precision_bound_public_detail(current_raw, current)):
            raise ValueError("Workday listing retention lacks an exact source/identity/date binding")
        observation_key = "_workday_listing_observation"
        incoming = {key: value for key, value in job.raw.items() if key != observation_key}
        listing_proof = incoming.get("_jobagg_listing_verification")
        job.raw = {**current_raw, observation_key: {
            "raw": incoming,
            "normalized": {key: getattr(job, key) for key in ("title", "location", "department", "employment_type")},
            "posted_at": _dt(job.posted_at),
            "observed_at": listing_proof.get("observed_at") if isinstance(listing_proof, dict) else None,
        }}
        if isinstance(listing_proof, dict):
            job.raw["_jobagg_listing_verification"] = listing_proof
        for key in ("title", "description", "location", "department", "employment_type", "source_url", "apply_url", "closes_at_local", "closes_tz"):
            setattr(job, key, current[key])
        job.posted_at, job.closes_at = _parse_dt(current["posted_at"]), _parse_dt(current["closes_at"])
        return True

    @staticmethod
    def _eu_primary_metadata_bound_public_detail(raw: dict[str, Any], row: Any) -> bool:
        from jobagg.eu_primary_metadata_observation import bound_public_metadata
        return bound_public_metadata(raw, row)

    def _merge_eu_primary_metadata_observation(self, job: JobRecord, current: sqlite3.Row,
                                             current_raw: dict[str, Any]) -> bool:
        from jobagg.adapters.eu_primary_metadata import FIELDS, MARKER
        incoming = MARKER in job.raw
        listing = job.raw.get("parser") == "eu_careers_open_vacancies"
        if not incoming and not (listing and MARKER in current_raw):
            return False
        if (job.source_id != "eu_careers_static" or current["source_id"] != job.source_id
                or str(current["external_id"]) != str(job.external_id)):
            raise ValueError("EU primary metadata requires matching source and identity")
        if incoming:
            row = {key: getattr(job, key) for key in (
                "source_id", "external_id", "source_url", "apply_url", "description", *FIELDS,
            )}
            if not self._eu_primary_metadata_bound_public_detail(job.raw, row):
                raise ValueError("EU primary metadata lacks its exact source/body/provenance binding")
            for key in ("attachments", "_jobagg_listing_verification"):
                if key not in job.raw and key in current_raw:
                    job.raw[key] = current_raw[key]
            proof = job.raw.get("attachment_verification", current_raw.get("attachment_verification"))
            if isinstance(proof, dict):
                proof = dict(proof)
                if proof.get("complete") is not False or proof.get("discovery_complete") is not False:
                    proof.update(complete=False, discovery_complete=False,
                                 invalidated_reason="public_detail_refresh_requires_attachment_reverification")
                job.raw["attachment_verification"] = proof
            return True
        if not self._eu_primary_metadata_bound_public_detail(current_raw, current):
            raise ValueError("Retained EU primary metadata lacks its source/body/provenance binding")
        if (str(job.raw.get("external_id")) != str(job.external_id)
                or job.source_url != current["source_url"] or job.apply_url != current["source_url"]
                or job.raw.get("href") != current["source_url"]
                or job.raw.get("detail_html") or job.raw.get("official_notice_text")):
            raise ValueError("EU primary metadata listing lacks its exact source/identity binding")
        observation_key = "_eu_primary_metadata_listing_observation"
        payload = {key: value for key, value in job.raw.items() if key != observation_key}
        listing_proof = payload.get("_jobagg_listing_verification")
        observation = {
            "raw": payload,
            "normalized": {key: getattr(job, key) for key in FIELDS if key not in {"posted_at", "closes_at"}},
            "posted_at": _dt(job.posted_at), "closes_at": _dt(job.closes_at),
            "observed_at": listing_proof.get("observed_at") if isinstance(listing_proof, dict) else None,
            "wrapper_text_metadata_reconciled": False,
        }
        job.raw = {**current_raw, observation_key: observation}
        if isinstance(listing_proof, dict):
            job.raw["_jobagg_listing_verification"] = listing_proof
        for key in ("title", "location", "department", "employment_type", "description", "apply_url",
                    "source_url", "closes_at_local", "closes_tz"):
            setattr(job, key, current[key])
        job.posted_at, job.closes_at = _parse_dt(current["posted_at"]), _parse_dt(current["closes_at"])
        return True

    @staticmethod
    def _eu_primary_text_bound_public_detail(raw: dict[str, Any], row: Any) -> bool:
        from jobagg.eu_primary_observation import bound_public_text
        return bound_public_text(raw, row)

    def _merge_eu_primary_text_observation(self, job: JobRecord, current: sqlite3.Row,
                                         current_raw: dict[str, Any]) -> bool:
        """Keep reviewed PDF text and its evidence together on an EU list refresh."""
        from jobagg.eu_primary_observation import MARKER
        incoming = MARKER in job.raw
        listing = job.raw.get("parser") == "eu_careers_open_vacancies"
        if not incoming and not (listing and MARKER in current_raw):
            # A fresh independently parsed detail owns its new observation;
            # this spacing-only proof never overrides a new detail producer.
            return False
        if (job.source_id != "eu_careers_static" or current["source_id"] != job.source_id
                or str(current["external_id"]) != str(job.external_id)):
            raise ValueError("EU primary text retention requires matching source and identity")
        if incoming:
            row = {key: getattr(job, key) for key in (
                "source_id", "external_id", "title", "description", "department", "location", "employment_type",
                "apply_url", "source_url", "posted_at", "closes_at", "closes_at_local", "closes_tz",
            )}
            if not self._eu_primary_text_bound_public_detail(job.raw, row):
                raise ValueError("EU primary text observation lacks its source/body/provenance binding")
            for key in ("attachments", "_jobagg_listing_verification"):
                if key not in job.raw and key in current_raw:
                    job.raw[key] = current_raw[key]
            proof = job.raw.get("attachment_verification", current_raw.get("attachment_verification"))
            if isinstance(proof, dict):
                job.raw["attachment_verification"] = dict(proof)
                if proof.get("complete") is not False or proof.get("discovery_complete") is not False:
                    job.raw["attachment_verification"].update(
                        complete=False, discovery_complete=False,
                        invalidated_reason="public_detail_refresh_requires_attachment_reverification",
                    )
            return True
        if not self._eu_primary_text_bound_public_detail(current_raw, current):
            raise ValueError("EU retained primary text observation has an invalid provenance binding")
        if (str(job.raw.get("external_id")) != str(job.external_id)
                or job.source_url != current["source_url"] or job.apply_url != current["source_url"]
                or job.raw.get("href") != current["source_url"]
                or job.raw.get("detail_html") or job.raw.get("official_notice_text")):
            raise ValueError("EU primary text listing refresh lacks its exact source/identity binding")
        observation_key = "_eu_primary_pdf_listing_observation"
        payload = {key: value for key, value in job.raw.items() if key != observation_key}
        listing_proof = payload.get("_jobagg_listing_verification")
        observation = {
            "raw": payload,
            "normalized": {key: getattr(job, key) for key in (
                "title", "location", "department", "employment_type", "apply_url", "source_url",
                "closes_at_local", "closes_tz",
            )},
            "posted_at": _dt(job.posted_at), "closes_at": _dt(job.closes_at),
            "observed_at": listing_proof.get("observed_at") if isinstance(listing_proof, dict) else None,
            "metadata_completeness_certified": False,
        }
        job.raw = {**current_raw, observation_key: observation}
        if isinstance(listing_proof, dict):
            job.raw["_jobagg_listing_verification"] = listing_proof
        for key in ("title", "location", "department", "employment_type", "description", "apply_url",
                    "source_url", "closes_at_local", "closes_tz"):
            setattr(job, key, current[key])
        job.posted_at, job.closes_at = _parse_dt(current["posted_at"]), _parse_dt(current["closes_at"])
        return True

    def _merge_verified_public_observation(self, job: JobRecord, current: sqlite3.Row,
                                          current_raw: dict[str, Any]) -> bool:
        """Keep one coherent public detail observation across explicit summaries."""
        is_unv = job.source_id == "unv_uvp" and job.ats_family == "unv"
        recognized_eu = {"europol_public_vacancy", "sesar_official_vacancy_pdf", "echa_public_notice_pdf",
                         "enisa_official_wrapper_and_pdf", "eda_public_notice_api", "eurlex_official_public_notice",
                         "euipo_reviewed_primary_pdf", "euda_reviewed_primary_pdf"}
        incoming_eu = job.raw.get("_eu_official_field_resolution", {})
        prior_eu = current_raw.get("_eu_official_field_resolution", {})
        is_eu_detail = (job.source_id == "eu_careers_static" and isinstance(incoming_eu, dict)
                        and incoming_eu.get("provider") in recognized_eu)
        is_eu_list = (job.source_id == "eu_careers_static"
                      and job.raw.get("parser") == "eu_careers_open_vacancies"
                      and isinstance(prior_eu, dict) and prior_eu.get("provider") in recognized_eu)
        is_osce_detail = (job.source_id == "osce_custom_html"
                          and isinstance(job.raw.get("_osce_public_field_resolution"), dict))
        is_osce_list = (job.source_id == "osce_custom_html"
                        and job.raw.get("parser") in {"public_links", "browser_inventory"}
                        and not job.raw.get("detail_html")
                        and isinstance(current_raw.get("_osce_public_field_resolution"), dict))
        if not any((is_unv, is_eu_list, is_eu_detail, is_osce_list, is_osce_detail)):
            return False
        if current["source_id"] != job.source_id or str(current["external_id"]) != str(job.external_id):
            raise ValueError("Public detail retention requires the same source and external ID")
        if is_eu_detail or is_osce_detail:
            incoming_row = {key: getattr(job, key) for key in (
                "source_id", "external_id", "title", "description", "department", "location", "employment_type",
                "apply_url", "source_url", "closes_at_local", "closes_tz",
            )}
            incoming_row.update(posted_at=_dt(job.posted_at), closes_at=_dt(job.closes_at))
            binding = self._osce_bound_public_detail if is_osce_detail else self._eu_bound_public_detail
            if not binding(job.raw, incoming_row):
                raise ValueError("EU incoming official detail fields are not bound to its identity and body")
            # A fresh official observation owns even explicitly unknown fields.
            # Attachment bytes remain available; changed discovery inputs cannot
            # inherit their previous completeness certificate.
            for key in ("attachments", "_jobagg_listing_verification"):
                if key not in job.raw and key in current_raw:
                    job.raw[key] = current_raw[key]
            if "attachment_verification" not in job.raw and isinstance(current_raw.get("attachment_verification"), dict):
                verification = dict(current_raw["attachment_verification"])
                if (job.description != current["description"]
                        or any(job.raw.get(key) != current_raw.get(key) for key in (
                            "detail_html", "official_vacancy_url", "required_attachment_urls", "official_directory_notice",
                            "enisa_public_wrapper", "eda_public_notice", "europol_public_vacancy"))):
                    verification.update(complete=False, discovery_complete=False,
                                        invalidated_reason="job_content_changed_requires_attachment_reverification")
                job.raw["attachment_verification"] = verification
            return True
        if is_unv:
            current_bound = self._unv_bound_public_detail(current_raw, job.external_id, current["description"])
            current_proof = current_raw.get("_unv_public_text_verification", {})
            kind = job.raw.get("_unv_record_kind")
            if (kind == "listing" and current_raw.get("_unv_record_kind") == "detail"
                    and isinstance(current_proof, dict) and current_proof and not current_bound):
                raise ValueError("UNV current public detail proof is not bound to its identity and body")
            if str(job.raw.get("id")) != str(job.external_id):
                raise ValueError("UNV observation identity does not match its external ID")
            if kind == "detail":
                incoming_bound = self._unv_bound_public_detail(job.raw, job.external_id, job.description)
                if not incoming_bound:
                    raise ValueError("UNV incoming detail proof is not bound to its identity and body")
                resolution = job.raw.get("_unv_deadline_resolution", {})
                if resolution.get("kind") not in {"known_instant", "unknown"}:
                    raise ValueError("UNV detail deadline resolution is missing")
                if resolution["kind"] == "unknown":
                    job.closes_at = job.closes_at_local = job.closes_tz = None
                elif (job.closes_at != _parse_dt(resolution.get("utc")) or job.closes_at is None
                      or job.closes_tz != "UTC"
                      or job.closes_at_local != job.closes_at.astimezone(UTC).replace(tzinfo=None).isoformat()):
                    raise ValueError("UNV detail deadline differs from its public resolution")
                # A new full observation owns its entire flat public field group.
                # Retain document bytes, but context/body changes invalidate discovery.
                if "attachments" not in job.raw and "attachments" in current_raw:
                    job.raw["attachments"] = current_raw["attachments"]
                if "_jobagg_listing_verification" not in job.raw and "_jobagg_listing_verification" in current_raw:
                    job.raw["_jobagg_listing_verification"] = current_raw["_jobagg_listing_verification"]
                if not isinstance(job.raw.get("attachment_verification"), dict) and isinstance(current_raw.get("attachment_verification"), dict):
                    verification = dict(current_raw["attachment_verification"])
                    def discovery(raw):
                        # Full flat payload includes HTML link targets and the
                        # public render context, not just extracted visible text.
                        return {key: value for key, value in raw.items()
                                if key not in {"attachments", "attachment_verification", "_unv_listing_observation"}
                                and not key.startswith("_jobagg_")
                                and key not in {"_unv_public_text_verification", "_unv_public_field_provenance"}}
                    if clean_text(job.description) != clean_text(current["description"]) or discovery(job.raw) != discovery(current_raw):
                        verification.update(complete=False, discovery_complete=False,
                                            invalidated_reason="job_content_changed_requires_attachment_reverification")
                    job.raw["attachment_verification"] = verification
                job.apply_url = job.source_url = f"https://app.unv.org/opportunities/{job.external_id}"
                return True
            if kind != "listing" or not current_bound:
                return False
            resolution = current_raw.get("_unv_deadline_resolution", {})
            if resolution.get("kind") == "known_instant":
                instant = _parse_dt(current["closes_at"])
                if (instant != _parse_dt(resolution.get("utc")) or instant is None or current["closes_tz"] != "UTC"
                        or current["closes_at_local"] != instant.astimezone(UTC).replace(tzinfo=None).isoformat()):
                    raise ValueError("UNV retained deadline differs from its public resolution")
            elif resolution.get("kind") == "unknown":
                if any(current[key] is not None for key in ("closes_at", "closes_at_local", "closes_tz")):
                    raise ValueError("UNV unknown deadline retained an unsupported normalized date")
            else:
                raise ValueError("UNV retained public deadline resolution is missing")
            observation_key = "_unv_listing_observation"
        else:
            binding = self._osce_bound_public_detail if is_osce_list else self._eu_bound_public_detail
            if not binding(current_raw, current):
                raise ValueError("EU official public detail identity, body or field binding is invalid")
            if str(job.raw.get("external_id")) != str(job.external_id):
                raise ValueError("EU listing identity does not match its external ID")
            if (is_eu_list and prior_eu.get("provider") in {"euipo_reviewed_primary_pdf", "euda_reviewed_primary_pdf"}
                    and (job.source_url != current["source_url"] or job.apply_url != current["source_url"]
                         or job.raw.get("href") != current["source_url"] or job.raw.get("official_notice_text"))):
                raise ValueError("EU reviewed PDF listing lacks its exact source URL binding")
            observation_key = "_osce_listing_observation" if is_osce_list else "_eu_listing_observation"
        # Preserve the old observation's immutable capture times. The latest
        # listing has a separate payload and never becomes detail provenance.
        incoming = {key: value for key, value in job.raw.items()
                    if key not in {observation_key, "_unv_retained_detail_observation"}}
        listing_proof = incoming.get("_jobagg_listing_verification")
        observation = {
            "raw": incoming,
            "normalized": {key: getattr(job, key) for key in (
                "title", "location", "department", "employment_type", "apply_url", "source_url", "closes_at_local", "closes_tz",
            )},
            "posted_at": _dt(job.posted_at), "closes_at": _dt(job.closes_at),
            "observed_at": listing_proof.get("observed_at") if isinstance(listing_proof, dict) else None,
        }
        job.raw = {**current_raw, observation_key: observation}
        if is_unv:
            from jobagg.adapters.unv_public import render_public

            provided = {key: value for key, value in incoming.items()
                        if not key.startswith("_") or key in {
                            "_unv_public_render_context", "_unv_duty_station_response", "_unv_eligibility_criteria",
                        }}
            candidate = {**current_raw, **provided}
            previous_text, previous_scope = render_public(current_raw)
            candidate_text, candidate_scope = render_public(candidate)
            public_changed = (
                " ".join(str(candidate_text or "").split()) != " ".join(str(previous_text or "").split())
                or candidate_scope.get("complete") != previous_scope.get("complete")
            )
            # Lookup expansion and equivalent date serialization are not new
            # public content. Compare the observed UI projection, preserving
            # HTML link targets and conditional fields, under retained context.
            changed_fields = sorted(key for key, value in provided.items()
                                    if value != current_raw.get(key)) if public_changed else []
            observation["changed_detail_inputs"] = changed_fields
            if public_changed:
                reason = "listing_content_changed_requires_detail_reverification"
                history_key = "_unv_retained_detail_observation"
                if history_key not in job.raw:
                    job.raw[history_key] = {
                        "raw": {key: value for key, value in current_raw.items()
                                if key not in {history_key, observation_key}},
                        "description": current["description"],
                        "normalized": {key: current[key] for key in (
                            "title", "location", "department", "employment_type", "apply_url", "source_url",
                            "posted_at", "closes_at", "closes_at_local", "closes_tz",
                        )},
                    }
                for proof_key in ("_unv_public_text_verification", "_jobagg_main_text_verification"):
                    proof = dict(current_raw[proof_key])
                    proof.update(complete=False, invalidated_reason=reason,
                                 missing=sorted(set([*proof.get("missing", []), reason])))
                    job.raw[proof_key] = proof
                observation["current_public_scope"] = "requires_detail_reverification"
                if isinstance(current_raw.get("attachment_verification"), dict):
                    job.raw["attachment_verification"] = {
                        **current_raw["attachment_verification"], "complete": False, "discovery_complete": False,
                        "invalidated_reason": "listing_content_changed_requires_detail_and_attachment_reverification",
                    }
        if isinstance(listing_proof, dict):
            job.raw["_jobagg_listing_verification"] = listing_proof
        for key in ("title", "location", "department", "employment_type", "description", "apply_url", "source_url", "closes_at_local", "closes_tz"):
            setattr(job, key, current[key])
        job.posted_at, job.closes_at = _parse_dt(current["posted_at"]), _parse_dt(current["closes_at"])
        if is_unv:
            job.apply_url = job.source_url = f"https://app.unv.org/opportunities/{job.external_id}"
        return True

    def _merge_existing_detail_fields(self, job: JobRecord, current: sqlite3.Row) -> None:
        """Preserve detail-only fields when a listing-only sync omits them."""

        new_row_is_listing_only = self._new_row_is_listing_only(job.raw)
        current_raw = self._load_raw_json(current["raw_json"])
        from jobagg.adapters.imo_public import MARKER as IMO_CALENDAR_MARKER, bound_public_date_fields, public_claims, restore_raw_public_claims
        imo_calendar_fields = bound_public_date_fields(job.raw, job)
        imo_calendar_resolution = job.raw.get(IMO_CALENDAR_MARKER) if imo_calendar_fields else None
        imo_source_claims = public_claims(job.raw) if imo_calendar_fields else None
        if self._merge_labelled_public_observation(job, current, current_raw):
            return
        if self._merge_workday_precision_observation(job, current, current_raw):
            return
        if self._merge_eu_primary_metadata_observation(job, current, current_raw):
            return
        if self._merge_eu_primary_text_observation(job, current, current_raw):
            return
        if self._merge_verified_public_observation(job, current, current_raw):
            return
        incoming_attachment_verification = job.raw.get("attachment_verification")
        is_iom = job.source_id == "iom_oracle_hcm" and job.ats_family == "oracle_hcm"
        incoming_iom_detail = is_iom and self._iom_contract_detail(job.raw, job.external_id)
        current_iom_detail = is_iom and self._iom_contract_detail(current_raw, current["external_id"])
        if (incoming_iom_detail or current_iom_detail) and (
            current["source_id"] != job.source_id or str(current["external_id"]) != str(job.external_id)
        ):
            raise ValueError("IOM contract retention requires the same source and external ID")
        incoming_iom_contract_fields = {key: job.raw[key] for key in (
            "ContractType", "requisitionFlexFields", "_oracle_contract_resolution",
        ) if key in job.raw}
        oracle_full_fields = (
            "ExternalDescriptionStr", "Description",
            "ExternalResponsibilitiesStr", "ExternalQualificationsStr",
        )
        oracle_listing_only = job.ats_family == "oracle_hcm" and not any(
            self._raw_has_value(job.raw.get(key)) for key in oracle_full_fields
        )
        current_has_detail = self._current_row_has_detail(current["raw_json"])
        current_has_oracle_detail = job.ats_family == "oracle_hcm" and any(
            self._raw_has_value(current_raw.get(key)) for key in oracle_full_fields
        )
        taleo_listing_only = job.ats_family == "taleo" and job.raw.get("_taleo_record_kind") == "listing"
        incoming_taleo_detail = job.ats_family == "taleo" and self._taleo_record_is_detail(
            job.raw, job.external_id
        )
        current_has_taleo_detail = job.ats_family == "taleo" and self._taleo_record_is_detail(
            current_raw, current["external_id"]
        )
        incoming_wipo_detail = (job.source_id == "wipo_taleo" and incoming_taleo_detail
                                and self._wipo_bound_public_detail(job.raw, job.external_id, job.title, job.description, job.employment_type))
        current_wipo_detail = (job.source_id == "wipo_taleo" and current_has_taleo_detail
                               and self._wipo_bound_public_detail(current_raw, current["external_id"], current["title"], current["description"], current["employment_type"]))
        if (incoming_wipo_detail or current_wipo_detail) and (
            current["source_id"] != job.source_id or str(current["external_id"]) != str(job.external_id)
        ):
            raise ValueError("WIPO public field retention requires the same source and external ID")
        incoming_wipo_employment = job.employment_type
        incoming_taleo_flat = job.raw.get("_taleo_flat")
        incoming_taleo_posting_resolution = job.raw.get("_taleo_posting_time_resolution")
        clear_incoming_posted_at = (
            incoming_taleo_detail and isinstance(incoming_taleo_posting_resolution, dict)
            and incoming_taleo_posting_resolution.get("kind") in {"public_calendar_date_only", "unknown_timezone"}
        )
        incoming_resolution = job.raw.get("_taleo_deadline_resolution")
        current_resolution = current_raw.get("_taleo_deadline_resolution")
        clear_resolution_kinds = {"open_ended", "unknown_timezone", "unparsed"}
        incoming_ilo_resolution = job.raw.get("_ilo_deadline_resolution")
        current_ilo_resolution = current_raw.get("_ilo_deadline_resolution")
        incoming_ilo_detail = (
            job.source_id == "ilo_successfactors" and isinstance(incoming_ilo_resolution, dict)
            and incoming_ilo_resolution.get("record_kind") == "detail"
            and self._raw_has_value(job.raw.get("detail_html"))
        )
        current_has_ilo_detail = (
            job.source_id == "ilo_successfactors" and isinstance(current_ilo_resolution, dict)
            and current_ilo_resolution.get("record_kind") == "detail"
            and self._raw_has_value(current_raw.get("detail_html"))
        )
        ilo_listing_only = job.source_id == "ilo_successfactors" and not incoming_ilo_detail
        incoming_avature_resolution = job.raw.get("_avature_deadline_resolution")
        current_avature_resolution = current_raw.get("_avature_deadline_resolution")
        incoming_avature_detail = (
            job.source_id == "unops_avature" and isinstance(incoming_avature_resolution, dict)
            and incoming_avature_resolution.get("record_kind") == "detail"
            and self._raw_has_value(job.raw.get("detail_html"))
        )
        avature_listing_only = job.source_id == "unops_avature" and not incoming_avature_detail
        current_has_avature_detail = (
            job.source_id == "unops_avature" and isinstance(current_avature_resolution, dict)
            and current_avature_resolution.get("record_kind") == "detail"
            and self._raw_has_value(current_raw.get("detail_html"))
        )
        incoming_workday_resolution = job.raw.get("_workday_deadline_resolution")
        current_workday_resolution = current_raw.get("_workday_deadline_resolution")
        recognized_workday = job.source_id in {"wfp_workday", "unhcr_workday", "wto_workday"}
        incoming_workday_detail = (
            recognized_workday and isinstance(incoming_workday_resolution, dict)
            and incoming_workday_resolution.get("record_kind") == "detail"
            and isinstance(job.raw.get("jobPostingInfo"), dict)
            and self._raw_has_value(job.raw["jobPostingInfo"].get("jobDescription"))
        )
        current_has_workday_detail = (
            recognized_workday and isinstance(current_workday_resolution, dict)
            and current_workday_resolution.get("record_kind") == "detail"
            and isinstance(current_raw.get("jobPostingInfo"), dict)
            and self._raw_has_value(current_raw["jobPostingInfo"].get("jobDescription"))
        )
        workday_listing_only = recognized_workday and not incoming_workday_detail
        prior_workday_timezone_evidence = None
        if current_has_workday_detail:
            if current_workday_resolution.get("utc_resolved") is True and current_workday_resolution.get("public_timezone"):
                prior_workday_timezone_evidence = {
                    key: current_workday_resolution.get(key) for key in (
                        "public_calendar_date", "public_timezone", "closes_tz",
                    )
                }
            elif isinstance(current_workday_resolution.get("retained_timezone_evidence"), dict):
                prior_workday_timezone_evidence = current_workday_resolution["retained_timezone_evidence"]
            if prior_workday_timezone_evidence and not all(
                prior_workday_timezone_evidence.get(key)
                for key in ("public_calendar_date", "public_timezone", "closes_tz")
            ):
                prior_workday_timezone_evidence = None
        clear_incoming_deadline = (
            incoming_taleo_detail and isinstance(incoming_resolution, dict)
            and incoming_resolution.get("kind") in clear_resolution_kinds
        ) or (incoming_ilo_detail and incoming_ilo_resolution.get("utc_resolved") is False) or (
            incoming_avature_detail and incoming_avature_resolution.get("utc_resolved") is False
        ) or (incoming_workday_detail and incoming_workday_resolution.get("utc_resolved") is False)
        preserve_cleared_deadline = (
            taleo_listing_only and current_has_taleo_detail and isinstance(current_resolution, dict)
            and current_resolution.get("kind") in clear_resolution_kinds
        ) or (ilo_listing_only and current_has_ilo_detail and current_ilo_resolution.get("utc_resolved") is False) or (
            avature_listing_only and current_has_avature_detail and current_avature_resolution.get("utc_resolved") is False
        ) or (workday_listing_only and current_has_workday_detail and current_workday_resolution.get("utc_resolved") is False)
        preserve_detail_dates = (
            (new_row_is_listing_only or oracle_listing_only)
            and (current_has_detail or current_has_oracle_detail)
        ) or (taleo_listing_only and current_has_taleo_detail) or (ilo_listing_only and current_has_ilo_detail) or (
            avature_listing_only and current_has_avature_detail
        ) or (workday_listing_only and current_has_workday_detail)
        if incoming_taleo_detail or incoming_ilo_detail or incoming_avature_detail or incoming_workday_detail:
            preserve_detail_dates = False
        listing_date_observation = {
            "posted_at": _dt(job.posted_at),
            "closes_at": _dt(job.closes_at),
            "closes_at_local": job.closes_at_local,
            "closes_tz": job.closes_tz,
        }
        oracle_date_keys = {
            "posted_at": ("PostedDate", "ExternalPostedStartDate"),
            "closes_at": ("ExternalPostedEndDate", "PostingEndDate"),
        }
        if preserve_detail_dates and oracle_listing_only:
            listing_date_observation["raw_oracle_dates"] = {
                key: job.raw[key]
                for keys in oracle_date_keys.values() for key in keys
                if key in job.raw
            }
        if taleo_listing_only and current_has_taleo_detail:
            listing_date_observation["raw_taleo_flat"] = incoming_taleo_flat
            listing_date_observation["raw_taleo_detail_url"] = job.raw.get("_taleo_detail_url")
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
        if incoming_iom_detail:
            # The successful detail response owns this complete public field
            # group, including omitted/null Contract Type. Do not revive a
            # stale contract through generic raw-field fallback.
            for key in ("ContractType", "requisitionFlexFields", "_oracle_contract_resolution"):
                if key in incoming_iom_contract_fields:
                    job.raw[key] = incoming_iom_contract_fields[key]
                else:
                    job.raw.pop(key, None)
            job.employment_type = clean_text(job.raw["_oracle_contract_resolution"]["public_contract_type"])
        elif current_iom_detail and oracle_listing_only:
            job.raw["_oracle_listing_contract_observation"] = incoming_iom_contract_fields
            for key in ("ContractType", "requisitionFlexFields", "_oracle_contract_resolution"):
                if key in current_raw:
                    job.raw[key] = current_raw[key]
                else:
                    job.raw.pop(key, None)
            job.employment_type = current["employment_type"]
        if ilo_listing_only and current_has_ilo_detail:
            job.raw["_ilo_deadline_resolution"] = current_ilo_resolution
            for key in ("ilo_public_fields", "_ilo_field_resolution", "grade", "contract_type"):
                if key not in job.raw and key in current_raw:
                    job.raw[key] = current_raw[key]
        if workday_listing_only and current_has_workday_detail:
            job.raw["_workday_deadline_resolution"] = current_workday_resolution
        if incoming_taleo_detail and isinstance(incoming_taleo_flat, dict):
            # A complete new structured detail owns its field set. Do not fill
            # absent/newly open-ended metadata from the previous full payload.
            job.raw["_taleo_flat"] = dict(incoming_taleo_flat)
        if taleo_listing_only and current_has_taleo_detail:
            for key in (
                "_taleo_flat", "_taleo_record_kind", "detail_url", "_taleo_detail_url",
                "_taleo_deadline_resolution", "_taleo_deadline_timezone_evidence",
                "_taleo_deadline_open_ended", "_taleo_posting_time_resolution",
                "_taleo_public_metadata_resolution", "_taleo_public_binding_capture",
                "_taleo_previous_detail_representation",
            ):
                if key in current_raw:
                    job.raw[key] = current_raw[key]
                else:
                    job.raw.pop(key, None)
        if incoming_wipo_detail:
            # An omitted public contract field is unknown. Keep the prior
            # listing classification inspectable without publishing it as a
            # field of this newer complete public detail observation.
            if job.employment_type != current["employment_type"]:
                job.raw["_wipo_previous_employment_observation"] = {
                    "employment_type": current["employment_type"],
                    "raw_json_sha256": hashlib.sha256(str(current["raw_json"] or "").encode()).hexdigest(),
                    "last_seen_at": current["last_seen_at"], "scope": "previous_stored_observation",
                }
            elif "_wipo_previous_employment_observation" in current_raw:
                job.raw["_wipo_previous_employment_observation"] = current_raw["_wipo_previous_employment_observation"]
        elif taleo_listing_only and current_wipo_detail:
            job.raw["_wipo_listing_employment_observation"] = {
                "employment_type": incoming_wipo_employment, "raw_taleo_flat": incoming_taleo_flat,
            }
            job.employment_type = current["employment_type"]
            if "_wipo_previous_employment_observation" in current_raw:
                job.raw["_wipo_previous_employment_observation"] = current_raw["_wipo_previous_employment_observation"]
        if avature_listing_only and current_has_avature_detail:
            for key in ("avature_fields", "_avature_deadline_resolution", "_avature_field_resolution",
                        "_avature_competency_text_resolution"):
                if key in current_raw:
                    job.raw[key] = current_raw[key]
        if preserve_detail_dates:
            # Keep the fresh list observation inspectable without allowing its
            # date-only fields to masquerade as retained full-detail metadata.
            if "_jobagg_listing_date_observation" not in job.raw:
                job.raw["_jobagg_listing_date_observation"] = listing_date_observation
            if oracle_listing_only:
                for field, keys in oracle_date_keys.items():
                    if current[field] is None:
                        continue
                    for key in keys:
                        if key in current_raw:
                            job.raw[key] = current_raw[key]
                        else:
                            job.raw.pop(key, None)
        if current_raw.get("parser") == "successfactors_detail":
            # Older RMK details retained a parser marker but not full HTML.
            # Preserve that historical provenance separately from the incoming
            # listing's own parser; a long generic listing is never sufficient.
            job.raw["_jobagg_retained_detail_parser"] = "successfactors_detail"
        avature_field_resolution = job.raw.get("_avature_field_resolution", {})
        clear_unobserved_avature_department = incoming_avature_detail and avature_field_resolution.get("department_observed") is False
        if clear_unobserved_avature_department:
            job.department = None
        elif job.department is None:
            job.department = current["department"]
        if job.employment_type is None and not (incoming_iom_detail or incoming_wipo_detail):
            job.employment_type = current["employment_type"]
        if clear_incoming_posted_at:
            job.posted_at = None
        elif taleo_listing_only and current_has_taleo_detail:
            # Explicitly unknown public posting time remains unknown after a
            # listing contributes an unsupported date-only midnight.
            job.posted_at = _parse_dt(current["posted_at"])
        elif job.posted_at is None or (preserve_detail_dates and current["posted_at"] is not None):
            job.posted_at = _parse_dt(current["posted_at"])
        if clear_incoming_deadline:
            job.closes_at = None
            if (
                incoming_workday_detail and current_has_workday_detail
                and incoming_workday_resolution.get("kind") == "public_calendar_date_only"
                and not job.closes_tz and prior_workday_timezone_evidence
                and prior_workday_timezone_evidence["public_calendar_date"] == incoming_workday_resolution.get("public_calendar_date")
            ):
                # A proven zone for the same public calendar date may survive
                # an omitted clock. The missing cutoff still has no UTC value.
                job.closes_tz = prior_workday_timezone_evidence["closes_tz"]
                job.raw["_workday_deadline_resolution"] = {
                    **incoming_workday_resolution, "closes_tz": job.closes_tz,
                    "retained_timezone_evidence": dict(prior_workday_timezone_evidence),
                }
        elif preserve_detail_dates and (current["closes_at"] is not None or preserve_cleared_deadline):
            job.closes_at = _parse_dt(current["closes_at"])
            job.closes_at_local = current["closes_at_local"] if "closes_at_local" in current.keys() else None
            job.closes_tz = current["closes_tz"] if "closes_tz" in current.keys() else None
        elif job.closes_at is None:
            job.closes_at = _parse_dt(current["closes_at"])
        if not clear_incoming_deadline and job.closes_at_local is None and "closes_at_local" in current.keys():
            job.closes_at_local = current["closes_at_local"]
        if not clear_incoming_deadline and job.closes_tz is None and "closes_tz" in current.keys():
            job.closes_tz = current["closes_tz"]
        # Date-only public claims explicitly clear unsupported old UTC values.
        for key, value in imo_calendar_fields.items():
            setattr(job, key, value)
        if imo_calendar_resolution:
            restore_raw_public_claims(job.raw, imo_source_claims)
            job.raw[IMO_CALENDAR_MARKER] = imo_calendar_resolution
        if oracle_listing_only and any(
            self._raw_has_value(current_raw.get(key)) for key in oracle_full_fields
        ):
            # Oracle's list summary can pass generic text quality. Rebuild a
            # known summary or expand a contained fragment from retained raw.
            # Older rows can also have richer main text than their retained raw
            # fields: a listing refresh must not shorten that saved detail.
            # A fresh full-detail observation still replaces older content.
            parts = public_description_parts(
                job.raw.get("ShortDescription") or job.raw.get("ShortDescriptionStr"),
                current_raw.get("ExternalDescriptionStr") or current_raw.get("Description"),
                current_raw.get("ExternalResponsibilitiesStr"),
                current_raw.get("ExternalQualificationsStr"),
            )
            rebuilt = clean_text("\n\n".join(str(part) for part in parts if part))
            current_text = clean_text(current["description"])
            retained_summaries = {
                clean_text(current_raw.get(key))
                for key in ("ShortDescription", "ShortDescriptionStr")
                if isinstance(current_raw.get(key), str)
            }
            if not current_text or current_text in retained_summaries or (rebuilt and current_text in rebuilt):
                job.description = rebuilt
            else:
                job.description = current["description"]
        elif taleo_listing_only and current_has_taleo_detail:
            job.description = current["description"]
        elif job.description is None:
            job.description = current["description"]
        elif (
            current["description"]
            and current_quality == DETAIL_QUALITY_COMPLETE
            and new_quality != DETAIL_QUALITY_COMPLETE
        ):
            job.description = current["description"]
        elif new_row_is_listing_only and self._current_row_has_detail(current["raw_json"]):
            job.description = current["description"]

        # Keep captured documents across ordinary listing/detail refreshes.
        # A changed main body requires document discovery to be checked again;
        # retained bytes remain evidence, not a fresh completeness certificate.
        if not isinstance(incoming_attachment_verification, dict) and isinstance(
            current_raw.get("attachment_verification"), dict
        ):
            verification = dict(current_raw["attachment_verification"])
            discovery_inputs_changed = any(
                (self._raw_has_value(job.raw.get(key)) or self._raw_has_value(current_raw.get(key)))
                and job.raw.get(key) != current_raw.get(key)
                for key in (
                    "detail_html", "ExternalDescriptionStr", "Description",
                    "ExternalResponsibilitiesStr", "ExternalQualificationsStr",
                    "jobPostingInfo", "jobAd", "_smartrecruiters_public_variants",
                    "_jobagg_public_language_variants",
                    "_jobagg_listing_date_observation", "_jobagg_retained_detail_parser",
                    "PostedDate", "ExternalPostedStartDate", "ExternalPostedEndDate", "PostingEndDate",
                    "_taleo_flat", "_taleo_record_kind", "_taleo_deadline_resolution",
                    "_taleo_deadline_timezone_evidence", "_taleo_deadline_open_ended",
                )
            )
            if clean_text(job.description) != clean_text(current["description"]) or discovery_inputs_changed:
                verification.update(complete=False, discovery_complete=False,
                                    invalidated_reason="job_content_changed_requires_attachment_reverification")
            job.raw["attachment_verification"] = verification

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
            "attachments",
            "attachment_verification",
            "_jobagg_listing_verification",
            "_jobagg_main_text_verification",
            "_jobagg_retained_detail_parser",
            "_jobagg_public_language_variants",
            "_smartrecruiters_public_variants",
            "_smartrecruiters_variant_manifest_sha256",
            "jobAd",
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
        return (
            JobDatabase._raw_has_value(raw.get("listing_html"))
            and not JobDatabase._raw_has_value(raw.get("detail_html"))
            and raw.get("parser") != "successfactors_detail"
            and raw.get("_taleo_record_kind") != "detail"
        )

    @staticmethod
    def _taleo_record_is_detail(raw: dict[str, Any], external_id: str | None) -> bool:
        if raw.get("_taleo_record_kind") == "detail":
            return True
        if raw.get("_taleo_record_kind") == "listing":
            return False
        flat = raw.get("_taleo_flat")
        return (
            JobDatabase._raw_has_value(raw.get("detail_url"))
            and isinstance(flat, dict)
            and flat.get("_taleo_parser") == "requisitionDescriptionInterface.fillList"
            and bool(str(flat.get("Requisition Title") or "").strip())
            and bool(external_id)
            and str(flat.get("Job Number") or "").strip() == str(external_id).strip()
        )

    @staticmethod
    def _current_row_has_detail(current_raw_json: str | None) -> bool:
        try:
            current_raw = json.loads(current_raw_json or "{}")
        except json.JSONDecodeError:
            return False
        if not isinstance(current_raw, dict):
            return False
        return (
            JobDatabase._raw_has_value(current_raw.get("detail_html"))
            or current_raw.get("parser") == "successfactors_detail"
            or current_raw.get("_jobagg_retained_detail_parser") == "successfactors_detail"
        )

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

    def record_listing_observation(
        self,
        source_id: str,
        observed_job_keys: set[str],
        *,
        observed_at: datetime,
        inventory_complete: bool | None = None,
        evidence_ref: str = "canonical_source_sync",
        enabled: bool = True,
    ) -> int:
        """Bind current-list membership to a captured frame without closing jobs.

        An incomplete frame proves the presence of returned jobs, but it cannot
        prove the absence/closure of other retained jobs. Their history stays
        intact while they stop being labelled as observed in the latest frame.
        """
        if observed_at.tzinfo is None:
            raise ValueError("listing observation timestamp must be timezone-aware")
        count = 0
        with self.connection_scope() as conn:
            rows = conn.execute(
                "SELECT job_key, raw_json FROM jobs WHERE source_id = ?", (source_id,)
            ).fetchall()
            for row in rows:
                raw = self._load_raw_json(row["raw_json"])
                seen = enabled and row["job_key"] in observed_job_keys
                raw["_jobagg_listing_verification"] = {
                    "version": 1,
                    "source_id": source_id,
                    "observed_at": _dt(observed_at),
                    "observed_in_latest_listing": seen,
                    "inventory_complete": inventory_complete,
                    "evidence_ref": evidence_ref,
                    "reason": "observed" if seen else (
                        "not_observed_in_latest_frame" if enabled else "disabled_source"
                    ),
                }
                conn.execute(
                    "UPDATE jobs SET raw_json = ? WHERE job_key = ?",
                    (json.dumps(raw, ensure_ascii=False), row["job_key"]),
                )
                count += 1
        return count

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
                    closed, vacancies_unavailable, errors_json, observed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.source_id,
                    result.fetched,
                    result.inserted,
                    result.updated,
                    result.unchanged,
                    result.missing,
                    result.closed,
                    result.vacancies_unavailable,
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
                    detail_succeeded, detail_failed, detail_skipped,
                    detail_unavailable, unavailable_vacancies, empty_reason,
                    zero_fetched_evidence, observed_agency_counts,
                    observed_organization_counts, count_delta_pct, health_status,
                    run_classification, publishability_classification,
                    blocked, transient_error, list_breaker_state,
                    detail_breaker_state,
                    scope_validation_status,
                    missing_transition_allowed, observed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    detail_unavailable = excluded.detail_unavailable,
                    unavailable_vacancies = excluded.unavailable_vacancies,
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
                    diagnostics.detail_unavailable,
                    json.dumps(diagnostics.unavailable_vacancies, sort_keys=True, ensure_ascii=True),
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
                data["unavailable_vacancies"] = json.loads(data.get("unavailable_vacancies") or "[]")
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

        Complete rows for the same listing hash are left untouched unless
        content quality or an explicit refresh requires another observation.
        Existing cooldown timestamps are preserved when an item is requeued.
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
                and reason != "refresh_requested"
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
        reason: str | None = None,
    ) -> None:
        if status not in {
            "complete",
            "transient_failed",
            "permanent_failed",
            "skipped",
            "adapter_failed",
            "blocked_by_circuit_breaker",
        } | UNAVAILABLE_STATUSES:
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
                    cooldown_until, listing_hash_at_detail_fetch, queued_reason, updated_at
                )
                VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_key) DO UPDATE SET
                    source_id = excluded.source_id,
                    detail_status = excluded.detail_status,
                    attempt_count = detail_backlog.attempt_count + 1,
                    last_attempt_at = excluded.last_attempt_at,
                    last_success_at = COALESCE(excluded.last_success_at, detail_backlog.last_success_at),
                    last_error = excluded.last_error,
                    cooldown_until = excluded.cooldown_until,
                    listing_hash_at_detail_fetch = excluded.listing_hash_at_detail_fetch,
                    queued_reason = COALESCE(excluded.queued_reason, detail_backlog.queued_reason),
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
                    reason,
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
        if status not in {"pending", "adapter_failed", "blocked_by_circuit_breaker"} | UNAVAILABLE_STATUSES:
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
        query += " ORDER BY source_id, title, job_key"
        with self.connect() as conn:
            rows = conn.execute(query, tuple(params))
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
        query += " ORDER BY j.source_id, j.title, j.job_key"
        with self.connect() as conn:
            try:
                rows = conn.execute(query, tuple(params))
            except sqlite3.OperationalError as exc:
                # Legacy databases may lack classification columns. Do not
                # disguise I/O, lock or corruption failures as a schema fallback.
                if not str(exc).startswith(("no such table:", "no such column:")):
                    raise
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
