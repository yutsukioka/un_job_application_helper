"""Local-only consolidation of per-organization output bundles."""

from __future__ import annotations

import csv
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from jobagg.db import JobDatabase
from jobagg.detail_quality import (
    DETAIL_QUALITY_COMPLETE,
    detail_quality_requeue_reason,
    detail_quality_status,
)
from jobagg.pipelines.exports import export_jobs


CONSOLIDATED_SLUG = "all"

CONSOLIDATED_TABLES = (
    ("jobs", "job_key"),
    ("vacancy_source_features", "vacancy_id"),
    ("vacancy_classifications", "vacancy_id"),
    ("grade_mappings", "mapping_version, organization, raw_grade_code"),
    ("classification_overrides", "vacancy_id, field_name"),
    ("vacancy_locations", None),
    ("change_events", None),
    ("vacancy_snapshots", None),
    ("source_runs", None),
    ("source_run_diagnostics", "source_run_id"),
    ("detail_backlog", "job_key"),
    ("source_circuit_breakers", "source_id, breaker_type"),
)

AUTOINCREMENT_COLUMNS = {
    "change_events": {"id"},
    "vacancy_snapshots": {"id"},
    "source_runs": {"id"},
    "vacancy_locations": {"id"},
}

DEFAULT_STALE_CURRENT_MAX_AGE_DAYS = 7
DEFAULT_EXPIRED_CURRENT_GRACE_HOURS = 24
STALE_CURRENT_STATUS = "stale_current"
EXPIRED_STATUS = "expired"
DUPLICATE_STATUS = "duplicate"
SPLIT_INSPIRA_VIEW_SOURCE_IDS = {"isa_inspira_split", "itc_inspira_split"}
SOURCE_PREFERENCE = {
    "un_inspira": 100,
    "isa_inspira_split": -100,
    "itc_inspira_split": -100,
}


@dataclass(slots=True)
class ConsolidationResult:
    output_dir: Path
    db_path: Path
    current_json_path: Path
    current_csv_path: Path
    trusted_current_json_path: Path
    trusted_current_csv_path: Path
    application_ready_json_path: Path
    application_ready_csv_path: Path
    history_json_path: Path
    history_csv_path: Path
    source_db_paths: list[Path]
    table_rows: dict[str, int] = field(default_factory=dict)
    status_counts: dict[str, int] = field(default_factory=dict)
    organization_counts: list[dict[str, object]] = field(default_factory=list)
    trusted_current_count_value: int = 0
    application_ready_count_value: int = 0
    current_detail_complete_count_value: int = 0
    current_detail_weak_count_value: int = 0
    expired_moved_to_history_count_value: int = 0
    total_count_value: int = 0

    @property
    def history_count(self) -> int:
        return sum(
            count for status, count in self.status_counts.items() if status != "open"
        )

    @property
    def current_count(self) -> int:
        return self.status_counts.get("open", 0)

    @property
    def total_count(self) -> int:
        return self.total_count_value

    @property
    def trusted_current_count(self) -> int:
        return self.trusted_current_count_value

    @property
    def application_ready_count(self) -> int:
        return self.application_ready_count_value

    @property
    def current_detail_complete_count(self) -> int:
        return self.current_detail_complete_count_value

    @property
    def current_detail_weak_count(self) -> int:
        return self.current_detail_weak_count_value

    @property
    def expired_moved_to_history_count(self) -> int:
        return self.expired_moved_to_history_count_value


def consolidated_output_paths(output_dir: str | Path, slug: str = CONSOLIDATED_SLUG) -> dict[str, Path]:
    output = Path(output_dir)
    return {
        "db": output / f"{slug}_jobs.sqlite3",
        "current_json": output / f"{slug}_jobs_current.json",
        "current_csv": output / f"{slug}_jobs_current.csv",
        "trusted_current_json": output / f"{slug}_jobs_trusted_current.json",
        "trusted_current_csv": output / f"{slug}_jobs_trusted_current.csv",
        "application_ready_json": output / f"{slug}_jobs_application_ready.json",
        "application_ready_csv": output / f"{slug}_jobs_application_ready.csv",
        "history_json": output / f"{slug}_jobs_history.json",
        "history_csv": output / f"{slug}_jobs_history.csv",
    }


def _remove_legacy_quality_exports(paths: dict[str, Path]) -> None:
    """Remove pre-existing side exports that are no longer canonical outputs."""

    for key in (
        "trusted_current_json",
        "trusted_current_csv",
        "application_ready_json",
        "application_ready_csv",
    ):
        path = paths[key]
        if path.exists():
            path.unlink()


def consolidate_bundle_databases(
    *,
    output_dir: str | Path,
    slug: str = CONSOLIDATED_SLUG,
    stale_current_max_age_days: int = DEFAULT_STALE_CURRENT_MAX_AGE_DAYS,
    expired_current_grace_hours: int = DEFAULT_EXPIRED_CURRENT_GRACE_HOURS,
) -> ConsolidationResult:
    """Merge existing per-organization SQLite bundles into one all-jobs bundle.

    This function performs no network fetch. It reads only existing
    ``*_jobs.sqlite3`` files under ``output_dir`` and skips the destination
    ``{slug}_jobs.sqlite3`` file when re-run.
    """

    output = Path(output_dir)
    paths = consolidated_output_paths(output, slug)
    source_db_paths = _source_databases(output, destination=paths["db"])
    if not source_db_paths:
        raise ValueError(f"No source *_jobs.sqlite3 files found in {output}")

    temp_db = paths["db"].with_name(f".{paths['db'].name}.tmp")
    _remove_sqlite_artifacts(temp_db)

    db = JobDatabase(temp_db)
    db.initialize()

    table_rows: dict[str, int] = {table: 0 for table, _ in CONSOLIDATED_TABLES}
    with closing(sqlite3.connect(temp_db)) as dest:
        dest.row_factory = sqlite3.Row
        dest.execute("PRAGMA foreign_keys=ON")
        dest.execute("BEGIN")
        try:
            for source_path in source_db_paths:
                with closing(_connect_source(source_path)) as source:
                    source.row_factory = sqlite3.Row
                    source_run_id_map: dict[int, int] = {}
                    for table, conflict_target in CONSOLIDATED_TABLES:
                        if table == "source_runs":
                            copied, source_run_id_map = _copy_source_runs(source, dest)
                        elif table == "source_run_diagnostics":
                            copied = _copy_source_run_diagnostics(
                                source,
                                dest,
                                source_run_id_map,
                            )
                        else:
                            copied = _copy_table(
                                source,
                                dest,
                                table=table,
                                conflict_target=conflict_target,
                            )
                        table_rows[table] += copied
            _apply_consolidation_quality_rules(
                dest,
                stale_current_max_age_days=stale_current_max_age_days,
                expired_current_grace_hours=expired_current_grace_hours,
            )
            dest.commit()
        except Exception:
            dest.rollback()
            raise

    _checkpoint_and_compact(temp_db)
    _remove_sqlite_artifacts(paths["db"])
    temp_db.replace(paths["db"])
    _remove_sqlite_sidecars(temp_db)

    all_db = JobDatabase(paths["db"])
    export_jobs(
        all_db,
        output_path=paths["current_json"],
        output_format="json",
        status="open",
    )
    export_jobs(
        all_db,
        output_path=paths["current_csv"],
        output_format="csv",
        status="open",
    )
    _remove_legacy_quality_exports(paths)
    export_jobs(
        all_db,
        output_path=paths["history_json"],
        output_format="json",
        history_only=True,
    )
    export_jobs(
        all_db,
        output_path=paths["history_csv"],
        output_format="csv",
        history_only=True,
    )

    status_counts = _status_counts(paths["db"])
    trusted_current_count = _trusted_current_count(paths["db"])
    application_ready_count = _application_ready_count(paths["db"])
    current_detail_complete_count = _current_detail_count(
        paths["db"],
        complete=True,
    )
    current_detail_weak_count = _current_detail_count(paths["db"], complete=False)
    expired_moved_to_history_count = _expired_moved_to_history_count(paths["db"])
    total_count = _total_count(paths["db"])
    organization_counts = _organization_counts(paths["db"])
    _checkpoint_and_compact(paths["db"])
    _remove_sqlite_sidecars(paths["db"])

    return ConsolidationResult(
        output_dir=output,
        db_path=paths["db"],
        current_json_path=paths["current_json"],
        current_csv_path=paths["current_csv"],
        trusted_current_json_path=paths["trusted_current_json"],
        trusted_current_csv_path=paths["trusted_current_csv"],
        application_ready_json_path=paths["application_ready_json"],
        application_ready_csv_path=paths["application_ready_csv"],
        history_json_path=paths["history_json"],
        history_csv_path=paths["history_csv"],
        source_db_paths=source_db_paths,
        table_rows=table_rows,
        status_counts=status_counts,
        organization_counts=organization_counts,
        trusted_current_count_value=trusted_current_count,
        application_ready_count_value=application_ready_count,
        current_detail_complete_count_value=current_detail_complete_count,
        current_detail_weak_count_value=current_detail_weak_count,
        expired_moved_to_history_count_value=expired_moved_to_history_count,
        total_count_value=total_count,
    )


def write_organization_summary(result: ConsolidationResult, output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["org_id", "source_id", "ats_family", "current", "history", "missing", "closed"],
        )
        writer.writeheader()
        writer.writerows(result.organization_counts)


def _apply_consolidation_quality_rules(
    conn: sqlite3.Connection,
    *,
    stale_current_max_age_days: int,
    expired_current_grace_hours: int,
) -> None:
    """Annotate consolidated rows and quarantine unsafe current duplicates.

    Source bundles are intentionally preserved as fetched. The consolidated
    all-jobs database is the canonical user-facing view, so this layer can be
    stricter: it marks stale rows, removes stale split-Inspira view rows from
    current output, and keeps one canonical row per source-native vacancy.
    """

    _create_consolidation_tables(conn)
    now = datetime.now(tz=UTC).isoformat()
    _normalize_expired_circuit_breakers(conn, now=now)
    source_status = _latest_source_status(
        conn,
        stale_current_max_age_days=stale_current_max_age_days,
        observed_at=now,
    )
    _annotate_jobs_with_source_status(conn, source_status)
    _quarantine_stale_split_inspira_views(conn)
    _quarantine_stale_source_current_rows(conn)
    duplicate_count = _deduplicate_open_jobs(conn, created_at=now)
    _annotate_detail_quality_and_deadlines(
        conn,
        now=now,
        expired_current_grace_hours=expired_current_grace_hours,
    )
    conn.execute(
        """
        UPDATE jobs
        SET canonical_job_key = COALESCE(canonical_job_key, job_key),
            consolidation_status = COALESCE(
                consolidation_status,
                CASE
                    WHEN stale_current = 1 THEN 'stale_source_flagged'
                    ELSE 'active'
                END
            )
        WHERE status = 'open'
        """
    )
    _refresh_consolidated_source_status(
        conn,
        source_status,
        updated_at=now,
        duplicate_count=duplicate_count,
    )


def _create_consolidation_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
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


def _latest_source_status(
    conn: sqlite3.Connection,
    *,
    stale_current_max_age_days: int,
    observed_at: str | datetime | None = None,
) -> dict[str, dict[str, object]]:
    rows = conn.execute(
        """
        SELECT DISTINCT source_id
        FROM jobs
        ORDER BY source_id
        """
    ).fetchall()
    source_ids = [str(row["source_id"]) for row in rows]
    latest_rows: dict[str, dict[str, object]] = {}
    for source_id in source_ids:
        row = conn.execute(
            """
            SELECT
                sr.source_id,
                sr.fetched,
                sr.observed_at AS run_observed_at,
                d.observed_at AS diagnostics_observed_at,
                d.health_status,
                d.run_classification,
                d.publishability_classification,
                d.pagination_complete,
                d.missing_transition_allowed
            FROM source_runs sr
            LEFT JOIN source_run_diagnostics d ON d.source_run_id = sr.id
            WHERE sr.source_id = ?
            ORDER BY COALESCE(d.observed_at, sr.observed_at) DESC, sr.id DESC
            LIMIT 1
            """,
            (source_id,),
        ).fetchone()
        if row is None:
            latest_rows[source_id] = {
                "source_id": source_id,
                "latest_observed_at": None,
                "health_status": None,
                "run_classification": None,
                "publishability_classification": None,
                "fetched": None,
                "pagination_complete": None,
                "missing_transition_allowed": None,
                "source_freshness_status": "unknown",
            }
            continue
        latest_observed_at = row["diagnostics_observed_at"] or row["run_observed_at"]
        latest_rows[source_id] = {
            "source_id": source_id,
            "latest_observed_at": latest_observed_at,
            "health_status": row["health_status"],
            "run_classification": row["run_classification"],
            "publishability_classification": row["publishability_classification"],
            "fetched": row["fetched"],
            "pagination_complete": row["pagination_complete"],
            "missing_transition_allowed": row["missing_transition_allowed"],
            "source_freshness_status": "unknown",
        }

    comparison_time = _parse_dt(observed_at) if isinstance(observed_at, str) else observed_at
    if comparison_time is None:
        comparison_time = datetime.now(tz=UTC)
    cutoff = (
        comparison_time - timedelta(days=max(stale_current_max_age_days, 0))
        if stale_current_max_age_days >= 0
        else None
    )
    for source_id, status in latest_rows.items():
        observed_at = _parse_dt(status.get("latest_observed_at"))
        status["source_freshness_status"] = _source_freshness_status(
            observed_at=observed_at,
            cutoff=cutoff,
            health_status=status.get("health_status"),
            run_classification=status.get("run_classification"),
            publishability_classification=status.get("publishability_classification"),
        )
    return latest_rows


def _source_freshness_status(
    *,
    observed_at: datetime | None,
    cutoff: datetime | None,
    health_status: object,
    run_classification: object,
    publishability_classification: object,
) -> str:
    if observed_at is None:
        return "unknown"
    if cutoff is not None and observed_at < cutoff:
        return "stale"
    run_text = str(run_classification or "").casefold()
    publish_text = str(publishability_classification or "").casefold()
    health_text = str(health_status or "").casefold()
    if publish_text in {
        "source_inconclusive",
        "source_blocked",
        "source_parser_error",
        "source_adapter_broken",
    }:
        return "inconclusive"
    if run_text in {"inconclusive", "blocked", "transient_error", "parser_error"}:
        return "inconclusive"
    if publish_text in {"publishable_detail_degraded", "publishable_list_only"}:
        return "detail_degraded"
    if run_text == "detail_degraded":
        return "detail_degraded"
    if health_text == "issue":
        return "issue"
    return "fresh"


def _annotate_jobs_with_source_status(
    conn: sqlite3.Connection,
    source_status: dict[str, dict[str, object]],
) -> None:
    stale_flag_statuses = {"stale"}
    for source_id, status in source_status.items():
        freshness = str(status["source_freshness_status"])
        conn.execute(
            """
            UPDATE jobs
            SET source_latest_observed_at = ?,
                source_freshness_status = ?,
                source_health_status = ?,
                source_run_classification = ?,
                source_publishability_classification = ?,
                stale_current = CASE
                    WHEN status = 'open' AND ? = 1 THEN 1
                    ELSE stale_current
                END,
                consolidation_status = CASE
                    WHEN status = 'open' AND ? = 1 AND consolidation_status IS NULL
                        THEN 'stale_source_flagged'
                    ELSE consolidation_status
                END
            WHERE source_id = ?
            """,
            (
                status.get("latest_observed_at"),
                freshness,
                status.get("health_status"),
                status.get("run_classification"),
                status.get("publishability_classification"),
                int(freshness in stale_flag_statuses),
                int(freshness in stale_flag_statuses),
                source_id,
            ),
        )


def _quarantine_stale_split_inspira_views(conn: sqlite3.Connection) -> int:
    placeholders = ", ".join("?" for _ in SPLIT_INSPIRA_VIEW_SOURCE_IDS)
    cursor = conn.execute(
        f"""
        UPDATE jobs
        SET status = ?,
            stale_current = 1,
            consolidation_status = 'stale_split_inspira_quarantined',
            canonical_job_key = COALESCE(canonical_job_key, job_key)
        WHERE status = 'open'
            AND source_id IN ({placeholders})
            AND COALESCE(source_freshness_status, 'unknown') = 'stale'
        """,
        (STALE_CURRENT_STATUS, *sorted(SPLIT_INSPIRA_VIEW_SOURCE_IDS)),
    )
    return int(cursor.rowcount or 0)


def _quarantine_stale_source_current_rows(conn: sqlite3.Connection) -> int:
    cursor = conn.execute(
        """
        UPDATE jobs
        SET status = ?,
            stale_current = 1,
            consolidation_status = COALESCE(consolidation_status, 'stale_source_quarantined'),
            canonical_job_key = COALESCE(canonical_job_key, job_key)
        WHERE status = 'open'
            AND COALESCE(source_freshness_status, 'unknown') = 'stale'
        """,
        (STALE_CURRENT_STATUS,),
    )
    return int(cursor.rowcount or 0)


def _normalize_expired_circuit_breakers(conn: sqlite3.Connection, *, now: str) -> int:
    now_dt = _parse_dt(now)
    if now_dt is None or not _table_exists(conn, "source_circuit_breakers"):
        return 0
    rows = conn.execute(
        """
        SELECT source_id, breaker_type, cooldown_until, last_reason
        FROM source_circuit_breakers
        WHERE state = 'open'
            AND cooldown_until IS NOT NULL
            AND TRIM(cooldown_until) <> ''
        """
    ).fetchall()
    normalized = 0
    for row in rows:
        cooldown = _parse_dt(row["cooldown_until"])
        if cooldown is None or cooldown > now_dt:
            continue
        reason = str(row["last_reason"] or "cooldown expired")
        if "cooldown expired" not in reason.casefold():
            reason = f"{reason}; cooldown expired"
        conn.execute(
            """
            UPDATE source_circuit_breakers
            SET state = 'half_open',
                cooldown_until = NULL,
                last_reason = ?,
                updated_at = ?
            WHERE source_id = ? AND breaker_type = ?
            """,
            (reason, now, row["source_id"], row["breaker_type"]),
        )
        normalized += 1
    return normalized


def _annotate_detail_quality_and_deadlines(
    conn: sqlite3.Connection,
    *,
    now: str,
    expired_current_grace_hours: int,
) -> None:
    now_dt = _parse_dt(now) or datetime.now(tz=UTC)
    rows = conn.execute(
        """
        SELECT j.job_key, j.source_id, j.title, j.description, j.raw_json,
               j.status, j.stale_current, j.closes_at, b.detail_status,
               b.listing_hash_at_detail_fetch, b.queued_reason
        FROM jobs j
        LEFT JOIN detail_backlog b ON b.job_key = j.job_key
        """
    ).fetchall()
    for row in rows:
        raw = _load_raw_json(row["raw_json"])
        quality = detail_quality_status(
            title=row["title"],
            description=row["description"],
            raw=raw,
            detail_status=row["detail_status"],
        )
        row_status = str(row["status"])
        expired_after_grace = (
            row_status == "open"
            and _expired_grace_elapsed(
                row["closes_at"],
                now=now_dt,
                grace_hours=expired_current_grace_hours,
            )
        )
        deadline = _deadline_state(row["closes_at"], now=now_dt)
        if row_status == "open" and deadline == "expired" and not expired_after_grace:
            deadline = "today"
        new_status = EXPIRED_STATUS if expired_after_grace else row_status
        source_listed_current = new_status == "open"
        trusted_current = (
            source_listed_current
            and not bool(row["stale_current"])
            and deadline != "expired"
        )
        application_ready = trusted_current and quality == DETAIL_QUALITY_COMPLETE
        conn.execute(
            """
            UPDATE jobs
            SET status = ?,
                detail_quality_status = ?,
                deadline_state = ?,
                source_listed_current = ?,
                trusted_current = ?,
                application_ready = ?,
                consolidation_status = CASE
                    WHEN ? = 1 THEN 'expired_deadline_grace_elapsed'
                    ELSE consolidation_status
                END,
                canonical_job_key = CASE
                    WHEN ? = 1 THEN COALESCE(canonical_job_key, job_key)
                    ELSE canonical_job_key
                END
            WHERE job_key = ?
            """,
            (
                new_status,
                quality,
                deadline,
                int(source_listed_current),
                int(trusted_current),
                int(application_ready),
                int(expired_after_grace),
                int(expired_after_grace),
                row["job_key"],
            ),
        )
        reason = detail_quality_requeue_reason(quality)
        if (
            source_listed_current
            and reason is not None
            and row["detail_status"] == "complete"
        ):
            conn.execute(
                """
                UPDATE detail_backlog
                SET detail_status = 'pending',
                    last_error = ?,
                    queued_reason = ?,
                    updated_at = ?
                WHERE job_key = ?
                """,
                (
                    f"detail content quality is {quality}; requeued for detail refresh",
                    reason,
                    now,
                    row["job_key"],
                ),
            )
        if (
            source_listed_current
            and quality == DETAIL_QUALITY_COMPLETE
            and row["detail_status"] == "pending"
            and str(row["queued_reason"] or "").startswith("detail_quality_")
        ):
            conn.execute(
                """
                UPDATE detail_backlog
                SET detail_status = 'complete',
                    last_error = NULL,
                    last_success_at = COALESCE(last_success_at, ?),
                    queued_reason = 'detail_quality_complete_after_recheck',
                    updated_at = ?
                WHERE job_key = ?
                """,
                (now, now, row["job_key"]),
            )


def _deduplicate_open_jobs(conn: sqlite3.Connection, *, created_at: str) -> int:
    conn.create_function("normalized_apply_url", 1, _normalized_apply_url)
    duplicate_count = 0
    duplicate_count += _deduplicate_open_groups(
        conn,
        group_sql="""
            SELECT source_id || ':' || ats_family || ':' || external_id AS group_key
            FROM jobs
            WHERE status = 'open'
                AND external_id IS NOT NULL
                AND TRIM(external_id) <> ''
            GROUP BY source_id, ats_family, external_id
            HAVING COUNT(*) > 1
        """,
        rows_sql="""
            SELECT j.*, b.detail_status
            FROM jobs j
            LEFT JOIN detail_backlog b ON b.job_key = j.job_key
            WHERE j.status = 'open'
                AND j.source_id || ':' || j.ats_family || ':' || j.external_id = ?
            ORDER BY j.source_id, j.job_key
        """,
        reason="same_ats_external_id",
        created_at=created_at,
    )
    duplicate_count += _deduplicate_open_groups(
        conn,
        group_sql="""
            SELECT normalized_apply_url AS group_key
            FROM (
                SELECT job_key, normalized_apply_url(apply_url) AS normalized_apply_url
                FROM jobs
                WHERE status = 'open'
                    AND apply_url IS NOT NULL
                    AND TRIM(apply_url) <> ''
            )
            WHERE normalized_apply_url IS NOT NULL
                AND normalized_apply_url <> ''
            GROUP BY normalized_apply_url
            HAVING COUNT(*) > 1
        """,
        rows_sql="""
            SELECT j.*, b.detail_status
            FROM jobs j
            LEFT JOIN detail_backlog b ON b.job_key = j.job_key
            WHERE j.status = 'open'
                AND normalized_apply_url(j.apply_url) = ?
            ORDER BY j.source_id, j.job_key
        """,
        reason="same_apply_url",
        created_at=created_at,
    )
    return duplicate_count


def _deduplicate_open_groups(
    conn: sqlite3.Connection,
    *,
    group_sql: str,
    rows_sql: str,
    reason: str,
    created_at: str,
) -> int:
    duplicate_count = 0
    groups = [row["group_key"] for row in conn.execute(group_sql).fetchall()]
    for group_key in groups:
        rows = [dict(row) for row in conn.execute(rows_sql, (group_key,)).fetchall()]
        if len(rows) < 2:
            continue
        canonical = max(rows, key=_canonical_job_score)
        for row in rows:
            if row["job_key"] == canonical["job_key"]:
                continue
            _mark_duplicate_job(
                conn,
                duplicate=row,
                canonical=canonical,
                reason=reason,
                created_at=created_at,
            )
            duplicate_count += 1
    return duplicate_count


def _canonical_job_score(row: dict[str, object]) -> tuple[object, ...]:
    freshness_rank = {
        "fresh": 5,
        "detail_degraded": 4,
        "issue": 2,
        "inconclusive": 1,
        "stale": 0,
        "unknown": 0,
    }.get(str(row.get("source_freshness_status") or "unknown"), 0)
    detail_rank = {
        "complete": 5,
        "skipped": 4,
        "pending": 3,
        None: 2,
        "": 2,
        "transient_failed": 1,
        "blocked_by_circuit_breaker": 1,
        "adapter_failed": 0,
        "permanent_failed": 0,
    }.get(row.get("detail_status"), 0)
    description_len = len(str(row.get("description") or ""))
    return (
        SOURCE_PREFERENCE.get(str(row.get("source_id") or ""), 0),
        freshness_rank,
        detail_rank,
        1 if description_len else 0,
        min(description_len, 5000),
        str(row.get("source_latest_observed_at") or ""),
        str(row.get("last_seen_at") or ""),
        str(row.get("job_key") or ""),
    )


def _mark_duplicate_job(
    conn: sqlite3.Connection,
    *,
    duplicate: dict[str, object],
    canonical: dict[str, object],
    reason: str,
    created_at: str,
) -> None:
    conn.execute(
        """
        UPDATE jobs
        SET status = ?,
            duplicate_of_job_key = ?,
            canonical_job_key = ?,
            consolidation_status = 'duplicate_quarantined',
            stale_current = 0
        WHERE job_key = ?
        """,
        (
            DUPLICATE_STATUS,
            canonical["job_key"],
            canonical["job_key"],
            duplicate["job_key"],
        ),
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO consolidated_job_aliases (
            duplicate_job_key,
            canonical_job_key,
            duplicate_source_id,
            canonical_source_id,
            duplicate_external_id,
            duplicate_apply_url,
            reason,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            duplicate["job_key"],
            canonical["job_key"],
            duplicate["source_id"],
            canonical["source_id"],
            duplicate.get("external_id"),
            duplicate.get("apply_url"),
            reason,
            created_at,
        ),
    )


def _refresh_consolidated_source_status(
    conn: sqlite3.Connection,
    source_status: dict[str, dict[str, object]],
    *,
    updated_at: str,
    duplicate_count: int,
) -> None:
    del duplicate_count  # count is useful for callers; per-source counts are computed below.
    for source_id, status in source_status.items():
        metrics = conn.execute(
            """
            SELECT
                SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) AS open_jobs,
                SUM(CASE WHEN stale_current = 1 THEN 1 ELSE 0 END) AS stale_current_jobs,
                SUM(CASE WHEN status = ? THEN 1 ELSE 0 END) AS duplicate_jobs,
                SUM(CASE WHEN trusted_current = 1 THEN 1 ELSE 0 END) AS trusted_current_jobs,
                SUM(CASE WHEN application_ready = 1 THEN 1 ELSE 0 END)
                    AS application_ready_jobs,
                SUM(CASE WHEN status = 'open' AND deadline_state = 'expired' THEN 1 ELSE 0 END)
                    AS expired_current_jobs,
                SUM(CASE
                    WHEN status = 'open'
                        AND detail_quality_status IS NOT NULL
                        AND detail_quality_status <> ?
                    THEN 1 ELSE 0 END
                ) AS weak_detail_jobs
            FROM jobs
            WHERE source_id = ?
            """,
            (DUPLICATE_STATUS, DETAIL_QUALITY_COMPLETE, source_id),
        ).fetchone()
        conn.execute(
            """
            INSERT OR REPLACE INTO consolidated_source_status (
                source_id,
                latest_observed_at,
                health_status,
                run_classification,
                publishability_classification,
                fetched,
                pagination_complete,
                missing_transition_allowed,
                source_freshness_status,
                open_jobs,
                stale_current_jobs,
                duplicate_jobs,
                trusted_current_jobs,
                application_ready_jobs,
                expired_current_jobs,
                weak_detail_jobs,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_id,
                status.get("latest_observed_at"),
                status.get("health_status"),
                status.get("run_classification"),
                status.get("publishability_classification"),
                status.get("fetched"),
                status.get("pagination_complete"),
                status.get("missing_transition_allowed"),
                status.get("source_freshness_status"),
                int(metrics["open_jobs"] or 0),
                int(metrics["stale_current_jobs"] or 0),
                int(metrics["duplicate_jobs"] or 0),
                int(metrics["trusted_current_jobs"] or 0),
                int(metrics["application_ready_jobs"] or 0),
                int(metrics["expired_current_jobs"] or 0),
                int(metrics["weak_detail_jobs"] or 0),
                updated_at,
            ),
        )


def _parse_dt(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _deadline_state(value: object, *, now: datetime) -> str:
    parsed = _parse_dt(value)
    if parsed is None:
        return "unknown"
    if parsed < now:
        return "expired"
    if parsed.date() == now.date():
        return "today"
    return "future"


def _expired_grace_elapsed(value: object, *, now: datetime, grace_hours: int) -> bool:
    parsed = _parse_dt(value)
    if parsed is None:
        return False
    return parsed + timedelta(hours=max(grace_hours, 0)) <= now


def _load_raw_json(value: object) -> dict[str, object]:
    try:
        parsed = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _normalized_apply_url(value: object) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = urlsplit(text)
    except ValueError:
        return text.casefold().rstrip("/")
    query_pairs = [
        (key, val)
        for key, val in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_")
    ]
    query = urlencode(sorted(query_pairs), doseq=True)
    path = parsed.path.rstrip("/") or parsed.path
    return urlunsplit(
        (
            parsed.scheme.casefold(),
            parsed.netloc.casefold(),
            path,
            query,
            "",
        )
    )


def _source_databases(output_dir: Path, *, destination: Path) -> list[Path]:
    return sorted(
        path
        for path in output_dir.glob("*_jobs.sqlite3")
        if path.is_file() and path.resolve() != destination.resolve()
    )


def _connect_source(path: Path) -> sqlite3.Connection:
    uri = f"{path.resolve().as_uri()}?mode=ro"
    try:
        return sqlite3.connect(uri, uri=True)
    except sqlite3.OperationalError:
        # Some fetched bundle databases are WAL-mode files without their sidecar
        # WAL/SHM files. Immutable mode opens those files read-only without
        # attempting to create sidecars, preserving the fetched artifacts.
        immutable_uri = f"{path.resolve().as_uri()}?immutable=1"
        return sqlite3.connect(immutable_uri, uri=True)


def _copy_table(
    source: sqlite3.Connection,
    dest: sqlite3.Connection,
    *,
    table: str,
    conflict_target: str | None,
) -> int:
    if not _table_exists(source, table):
        return 0
    dest_columns = _copyable_columns(dest, table)
    source_columns = set(_copyable_columns(source, table))
    columns = [column for column in dest_columns if column in source_columns]
    if not columns:
        return 0

    select_sql = f"SELECT {', '.join(columns)} FROM {table}"
    rows = source.execute(select_sql).fetchall()
    if not rows:
        return 0

    placeholders = ", ".join("?" for _ in columns)
    conflict_sql = ""
    if conflict_target is not None:
        conflict_sql = f" ON CONFLICT({conflict_target}) DO NOTHING"
    insert_sql = (
        f"INSERT INTO {table} ({', '.join(columns)}) "
        f"VALUES ({placeholders}){conflict_sql}"
    )
    dest.executemany(insert_sql, [tuple(row[column] for column in columns) for row in rows])
    return len(rows)


def _copy_source_runs(
    source: sqlite3.Connection,
    dest: sqlite3.Connection,
) -> tuple[int, dict[int, int]]:
    if not _table_exists(source, "source_runs"):
        return 0, {}
    columns = [column for column in _copyable_columns(dest, "source_runs") if column != "id"]
    source_columns = set(_copyable_columns(source, "source_runs"))
    columns = [column for column in columns if column in source_columns]
    if not columns:
        return 0, {}
    rows = source.execute(f"SELECT id, {', '.join(columns)} FROM source_runs").fetchall()
    if not rows:
        return 0, {}
    placeholders = ", ".join("?" for _ in columns)
    insert_sql = f"INSERT INTO source_runs ({', '.join(columns)}) VALUES ({placeholders})"
    id_map: dict[int, int] = {}
    for row in rows:
        cursor = dest.execute(insert_sql, tuple(row[column] for column in columns))
        id_map[int(row["id"])] = int(cursor.lastrowid)
    return len(rows), id_map


def _copy_source_run_diagnostics(
    source: sqlite3.Connection,
    dest: sqlite3.Connection,
    source_run_id_map: dict[int, int],
) -> int:
    if not source_run_id_map or not _table_exists(source, "source_run_diagnostics"):
        return 0
    columns = [column for column in _copyable_columns(dest, "source_run_diagnostics")]
    source_columns = set(_copyable_columns(source, "source_run_diagnostics"))
    columns = [column for column in columns if column in source_columns]
    if not columns:
        return 0
    rows = source.execute(f"SELECT {', '.join(columns)} FROM source_run_diagnostics").fetchall()
    if not rows:
        return 0
    placeholders = ", ".join("?" for _ in columns)
    insert_sql = (
        f"INSERT OR REPLACE INTO source_run_diagnostics ({', '.join(columns)}) "
        f"VALUES ({placeholders})"
    )
    copied = 0
    for row in rows:
        old_run_id = int(row["source_run_id"])
        new_run_id = source_run_id_map.get(old_run_id)
        if new_run_id is None:
            continue
        values = []
        for column in columns:
            values.append(new_run_id if column == "source_run_id" else row[column])
        dest.execute(insert_sql, tuple(values))
        copied += 1
    return copied


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _copyable_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    excluded = AUTOINCREMENT_COLUMNS.get(table, set())
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    columns = []
    for row in rows:
        name = row["name"] if isinstance(row, sqlite3.Row) else row[1]
        if name not in excluded:
            columns.append(name)
    return columns


def _status_counts(path: Path) -> dict[str, int]:
    with closing(sqlite3.connect(path)) as conn:
        return {
            str(status): int(count)
            for status, count in conn.execute(
                "SELECT status, COUNT(*) FROM jobs GROUP BY status ORDER BY status"
            )
        }


def _trusted_current_count(path: Path) -> int:
    with closing(sqlite3.connect(path)) as conn:
        row = conn.execute("SELECT COUNT(*) FROM jobs WHERE trusted_current = 1").fetchone()
        return int(row[0] or 0)


def _application_ready_count(path: Path) -> int:
    with closing(sqlite3.connect(path)) as conn:
        row = conn.execute("SELECT COUNT(*) FROM jobs WHERE application_ready = 1").fetchone()
        return int(row[0] or 0)


def _current_detail_count(path: Path, *, complete: bool) -> int:
    comparator = "=" if complete else "<>"
    with closing(sqlite3.connect(path)) as conn:
        row = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM jobs
            WHERE status = 'open'
                AND COALESCE(detail_quality_status, '') {comparator} ?
            """,
            (DETAIL_QUALITY_COMPLETE,),
        ).fetchone()
        return int(row[0] or 0)


def _expired_moved_to_history_count(path: Path) -> int:
    with closing(sqlite3.connect(path)) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM jobs
            WHERE status = ?
                AND consolidation_status = 'expired_deadline_grace_elapsed'
            """,
            (EXPIRED_STATUS,),
        ).fetchone()
        return int(row[0] or 0)


def _total_count(path: Path) -> int:
    with closing(sqlite3.connect(path)) as conn:
        row = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()
        return int(row[0] or 0)


def _organization_counts(path: Path) -> list[dict[str, object]]:
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT
                org_id,
                source_id,
                ats_family,
                SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) AS current,
                SUM(CASE WHEN status <> 'open' THEN 1 ELSE 0 END) AS history,
                SUM(CASE WHEN status = 'missing' THEN 1 ELSE 0 END) AS missing,
                SUM(CASE WHEN status = 'closed' THEN 1 ELSE 0 END) AS closed
            FROM jobs
            GROUP BY org_id, source_id, ats_family
            ORDER BY org_id, source_id
            """
        ).fetchall()
        return [dict(row) for row in rows]


def _checkpoint_and_compact(path: Path) -> None:
    with closing(sqlite3.connect(path, isolation_level=None)) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        conn.execute("PRAGMA journal_mode=DELETE").fetchall()


def _remove_sqlite_artifacts(path: Path) -> None:
    for artifact in (path, *_sqlite_sidecars(path)):
        if artifact.exists():
            artifact.unlink()


def _remove_sqlite_sidecars(path: Path) -> None:
    for sidecar in _sqlite_sidecars(path):
        if sidecar.exists():
            sidecar.unlink()


def _sqlite_sidecars(path: Path) -> tuple[Path, Path]:
    return (
        path.with_name(f"{path.name}-wal"),
        path.with_name(f"{path.name}-shm"),
    )
