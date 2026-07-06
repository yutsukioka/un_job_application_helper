import json
import sqlite3
from datetime import UTC, datetime, timedelta

from jobagg.adapters.base import JobAdapter, register_adapter
from jobagg.db import JobDatabase
from jobagg.models import OrganizationSource, SourceRunDiagnostics, SyncResult
from jobagg.normalize import build_job
from jobagg.pipelines.bundles import (
    BundleResult,
    _copy_sqlite_seed_database,
    publish_canonical_results,
    source_output_paths,
    source_output_slug,
    validate_bundle_dir,
    write_source_bundle,
)
from jobagg.pipelines.consolidation import consolidate_bundle_databases
from jobagg.robots import RobotsPolicy
from jobagg.scheduler import _bundle_health_sidecar, _refresh_existing_health_report_after_consolidation, main

FRESH_OBSERVED_AT = datetime.now(tz=UTC)
STALE_OBSERVED_AT = FRESH_OBSERVED_AT - timedelta(days=30)
FUTURE_CLOSES_AT = "2099-07-30"


@register_adapter
class StaticBundleTestAdapter(JobAdapter):
    family = "static_bundle_test"

    def fetch_jobs(self):
        return [
            build_job(
                self.source,
                title=self.source.extra.get("title", "Role 1"),
                external_id="A1",
                location="Geneva",
                closes_at="2099-05-30",
                description=(
                    "Detailed static bundle role text with responsibilities, "
                    "qualifications, and enough context for quality checks."
                ),
                apply_url="https://example.org/jobs/A1",
            )
        ]


@register_adapter
class DegradedBundleTestAdapter(JobAdapter):
    family = "degraded_bundle_test"

    def fetch_jobs(self):
        return [
            build_job(
                self.source,
                title="Listing Role",
                external_id="A1",
                apply_url="https://example.org/jobs/A1",
                raw={"id": "A1"},
            )
        ]

    def fetch_detail_for_listing_item(self, item):
        return None


@register_adapter
class BrowserAssistBundleTestAdapter(JobAdapter):
    family = "browser_assist_bundle_test"

    def fetch_jobs(self):
        return [
            build_job(
                self.source,
                title="Listing Role",
                external_id="A1",
                apply_url="https://jobs.example.org/jobs/A1",
                raw={"id": "A1"},
            )
        ]

    def fetch_detail_for_listing_item(self, item):
        if not self.context.http.default_headers.get("Cookie"):
            return None
        return build_job(
            self.source,
            title="Detailed Role",
            external_id="A1",
            apply_url="https://jobs.example.org/jobs/A1",
            description=(
                "Detailed role text after browser cookie assist with responsibilities, "
                "qualifications, and source-provided vacancy context."
            ),
            raw={"id": "A1", "detail": True},
        )


@register_adapter
class DetailLimitBundleTestAdapter(JobAdapter):
    family = "detail_limit_bundle_test"

    def fetch_jobs(self):
        return [
            build_job(
                self.source,
                title=f"Listing Role {index}",
                external_id=f"A{index}",
                apply_url=f"https://example.org/jobs/A{index}",
                raw={"id": f"A{index}"},
            )
            for index in range(1, 4)
        ]

    def fetch_detail_for_listing_item(self, item):
        return build_job(
            self.source,
            title=f"Detailed Role {item['id']}",
            external_id=item["id"],
            description=(
                f"Detailed role text for {item['id']} includes responsibilities, "
                "qualifications, reporting lines, selection criteria, and application context."
            ),
            apply_url=f"https://example.org/jobs/{item['id']}",
            raw={"id": item["id"], "detail": True},
        )


def test_source_output_slug_uses_configured_slug_and_known_suffixes():
    assert (
        source_output_slug(
            OrganizationSource(
                id="cern_smartrecruiters",
                name="CERN",
                ats_family="smartrecruiters",
                base_url="https://example.org",
                extra={"output_slug": "cern"},
            )
        )
        == "cern"
    )
    assert (
        source_output_slug(
            OrganizationSource(
                id="unops_avature",
                name="UNOPS",
                ats_family="avature",
                base_url="https://example.org",
            )
        )
        == "unops"
    )


def test_publish_canonical_results_keeps_best_duplicate_source(tmp_path):
    output = tmp_path / "output"
    staging = tmp_path / "staging"
    archive = tmp_path / "archive"
    staging.mkdir()

    weak = _staged_result(
        staging,
        source_id="cern_custom_html",
        slug="cern",
        fetched=3,
    )
    strong = _staged_result(
        staging,
        source_id="cern_smartrecruiters",
        slug="cern",
        fetched=5,
    )
    (output).mkdir()
    (output / "old_noncanonical.json").write_text("old", encoding="utf-8")

    selected, duplicates = publish_canonical_results(
        [weak, strong],
        output_dir=output,
        archive_dir=archive,
        prune_output_dir=True,
    )

    assert [result.source.id for result in selected] == ["cern_smartrecruiters"]
    assert [result.source.id for result in duplicates] == ["cern_custom_html"]
    assert (output / "cern_jobs.sqlite3").exists()
    assert (output / "cern_jobs_current.json").exists()
    assert not (output / "old_noncanonical.json").exists()
    assert (archive / "previous_output_root" / "old_noncanonical.json").exists()
    assert (archive / "duplicate_sources" / "cern_custom_html" / "cern_custom_html_jobs.sqlite3").exists()


def test_validate_bundle_dir_detects_missing_files_and_duplicates(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    db = JobDatabase(output / "who_jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="who_taleo",
        name="WHO",
        ats_family="taleo",
        base_url="https://example.org",
    )
    db.upsert_job(
        build_job(
            source,
            title="Role 1",
            external_id="A1",
            apply_url="https://example.org/jobs/A1",
        )
    )
    db.upsert_job(
        build_job(
            source,
            title="Role 2",
            external_id="A2",
            apply_url="https://example.org/jobs/A1",
        )
    )

    validation = validate_bundle_dir(output, {"who"})

    assert validation.missing_files["who"] == [
        "_jobs_current.csv",
        "_jobs_current.json",
        "_jobs_history.csv",
        "_jobs_history.json",
    ]

    for suffix in (
        "_jobs_current.csv",
        "_jobs_current.json",
        "_jobs_history.csv",
        "_jobs_history.json",
    ):
        (output / f"who{suffix}").write_text("", encoding="utf-8")

    validation = validate_bundle_dir(output, {"who"})

    assert validation.missing_files == {}
    assert validation.duplicate_apply_urls["who"] == [("https://example.org/jobs/A1", 2)]
    assert validation.duplicate_external_ids == {}


def test_write_source_bundle_seeds_existing_canonical_database(tmp_path):
    seed_dir = tmp_path / "output"
    staging_dir = tmp_path / "staging"
    source = OrganizationSource(
        id="org_static_bundle",
        name="Org",
        ats_family="static_bundle_test",
        base_url="https://example.org",
        extra={"output_slug": "org"},
    )
    seed_paths = source_output_paths(seed_dir, "org")
    seed_db = JobDatabase(seed_paths["db"])
    seed_db.initialize()
    seed_db.upsert_job(
            build_job(
                source,
                title="Role 1",
                external_id="A1",
                location="Geneva",
                closes_at="2099-05-30",
                description=(
                    "Detailed static bundle role text with responsibilities, "
                    "qualifications, and enough context for quality checks."
                ),
                apply_url="https://example.org/jobs/A1",
            )
        )

    result = write_source_bundle(
        source,
        output_dir=staging_dir,
        policy=RobotsPolicy(honor_robots_txt=False),
        file_slug=source.id,
        seed_db_path=seed_paths["db"],
        selective_details=False,
    )

    assert result.sync_result.inserted == 0
    assert result.sync_result.unchanged == 1


def test_sqlite_seed_copy_uses_backup_api_for_wal_state(tmp_path):
    source_path = tmp_path / "seed.sqlite3"
    target_path = tmp_path / "target.sqlite3"
    with sqlite3.connect(source_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO sample (value) VALUES ('visible from wal')")
        conn.commit()
        assert source_path.with_name(f"{source_path.name}-wal").exists()

        _copy_sqlite_seed_database(source_path, target_path)

    with sqlite3.connect(target_path) as copied:
        assert copied.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert copied.execute("SELECT value FROM sample").fetchone()[0] == "visible from wal"
    assert not target_path.with_name(f"{target_path.name}-wal").exists()
    assert not target_path.with_name(f"{target_path.name}-shm").exists()


def test_sync_bundles_returns_error_for_any_missing_requested_source(tmp_path):
    config = tmp_path / "organizations.yaml"
    config.write_text(
        """
sources:
  - id: org_static_bundle
    name: Org
    ats_family: static_bundle_test
    base_url: https://example.org
    enabled: true
""",
        encoding="utf-8",
    )

    exit_code = main(
        [
            "sync-bundles",
            "--config",
            str(config),
            "--source-id",
            "org_static_bundle",
            "--source-id",
            "missing_source",
            "--output-dir",
            str(tmp_path / "output"),
        ]
    )

    assert exit_code == 1


def test_consolidate_bundle_databases_writes_all_jobs_outputs(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    source_a = OrganizationSource(
        id="org_a_workday",
        name="Org A",
        ats_family="workday",
        base_url="https://example.org/a",
        extra={"output_slug": "org_a"},
    )
    source_b = OrganizationSource(
        id="org_b_taleo",
        name="Org B",
        ats_family="taleo",
        base_url="https://example.org/b",
        extra={"output_slug": "org_b"},
    )

    db_a = JobDatabase(output / "org_a_jobs.sqlite3")
    db_a.initialize()
    db_a.upsert_job(
        build_job(
            source_a,
            title="Open Role A",
            external_id="A1",
            apply_url="https://example.org/a/A1",
        )
    )
    db_b = JobDatabase(output / "org_b_jobs.sqlite3")
    db_b.initialize()
    db_b.upsert_job(
        build_job(
            source_b,
            title="Closed Role B",
            external_id="B1",
            status="closed",
            apply_url="https://example.org/b/B1",
        )
    )

    result = consolidate_bundle_databases(output_dir=output)

    assert result.current_count == 1
    assert result.history_count == 1
    assert result.total_count == 2
    assert result.status_counts == {"closed": 1, "open": 1}
    assert result.db_path == output / "all_jobs.sqlite3"
    assert (output / "all_jobs_current.csv").exists()
    assert (output / "all_jobs_history.csv").exists()

    current_rows = json.loads((output / "all_jobs_current.json").read_text(encoding="utf-8"))
    history_rows = json.loads((output / "all_jobs_history.json").read_text(encoding="utf-8"))

    assert [row["title"] for row in current_rows] == ["Open Role A"]
    assert [row["title"] for row in history_rows] == ["Closed Role B"]
    with sqlite3.connect(output / "all_jobs.sqlite3") as conn:
        assert conn.execute("SELECT COUNT(*) FROM grade_mappings").fetchone()[0] >= 700


def test_consolidate_bundle_databases_preserves_detail_and_breaker_metadata(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    source = OrganizationSource(
        id="org_a_workday",
        name="Org A",
        ats_family="workday",
        base_url="https://example.org/a",
        extra={"output_slug": "org_a"},
    )
    db = JobDatabase(output / "org_a_jobs.sqlite3")
    db.initialize()
    job = build_job(
        source,
        title="Open Role A",
        external_id="A1",
        apply_url="https://example.org/a/A1",
        description=(
            "Detailed role text with responsibilities, qualifications, and enough "
            "context to count as a complete detail record."
        ),
    )
    db.upsert_job(job)
    db.record_detail_backlog_attempt(
        job_key=job.identity_key(),
        source_id=source.id,
        status="complete",
        listing_hash=job.normalized_hash or "listing-hash",
    )
    db.set_source_breaker(
        source_id=source.id,
        breaker_type="detail",
        state="open",
        failure_count=3,
        reason="adapter probe failed",
    )
    db.add_source_run(
        SyncResult(
            source_id=source.id,
            fetched=1,
            inserted=1,
            diagnostics=SourceRunDiagnostics(
                source_id=source.id,
                pagination_complete=True,
                health_status="ok",
                run_classification="ok",
                publishability_classification="ok",
                missing_transition_allowed=True,
                observed_at=FRESH_OBSERVED_AT,
            ),
        ),
        observed_at=FRESH_OBSERVED_AT,
    )

    consolidate_bundle_databases(output_dir=output)

    with sqlite3.connect(output / "all_jobs.sqlite3") as conn:
        conn.row_factory = sqlite3.Row
        backlog = conn.execute("SELECT * FROM detail_backlog").fetchone()
        breaker = conn.execute("SELECT * FROM source_circuit_breakers").fetchone()
        source_status = conn.execute("SELECT * FROM consolidated_source_status").fetchone()
        job_row = conn.execute("SELECT * FROM jobs").fetchone()

    assert backlog["job_key"] == job.identity_key()
    assert backlog["detail_status"] == "complete"
    assert breaker["source_id"] == source.id
    assert breaker["breaker_type"] == "detail"
    assert breaker["state"] == "open"
    assert source_status["source_freshness_status"] == "fresh"
    assert job_row["stale_current"] == 0
    assert job_row["consolidation_status"] == "active"


def test_bundle_health_sidecar_counts_only_open_backlog_items(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    source = OrganizationSource(
        id="org_a_workday",
        name="Org A",
        ats_family="workday",
        base_url="https://example.org/a",
        extra={"output_slug": "org_a"},
    )
    db = JobDatabase(output / "org_a_jobs.sqlite3")
    db.initialize()
    open_job = build_job(
        source,
        title="Open Role A",
        external_id="A1",
        apply_url="https://example.org/a/A1",
    )
    closed_job = build_job(
        source,
        title="Closed Placeholder",
        external_id="A2",
        status="closed",
        apply_url="https://example.org/a/A2",
    )
    db.upsert_job(open_job)
    db.upsert_job(closed_job)
    db.record_detail_backlog_attempt(
        job_key=open_job.identity_key(),
        source_id=source.id,
        status="complete",
        listing_hash=open_job.normalized_hash or "open-listing-hash",
    )
    db.queue_detail_backlog_item(
        job_key=closed_job.identity_key(),
        source_id=source.id,
        listing_hash=closed_job.normalized_hash or "closed-listing-hash",
        reason="detail_quality_placeholder_only",
    )
    with db.connect() as conn:
        conn.execute(
            """
            UPDATE detail_backlog
            SET last_error = 'closed row should not affect current health'
            WHERE job_key = ?
            """,
            (closed_job.identity_key(),),
        )

    sidecar = _bundle_health_sidecar(output, source_output_slug(source))

    assert sidecar["detail_backlog_counts"] == {"complete": 1}
    assert sidecar["last_backlog_error"] is None


def test_consolidation_health_refresh_recomputes_open_backlog_counts(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    source = OrganizationSource(
        id="org_a_workday",
        name="Org A",
        ats_family="workday",
        base_url="https://example.org/a",
        extra={"output_slug": "org_a"},
    )
    db = JobDatabase(output / "org_a_jobs.sqlite3")
    db.initialize()
    open_job = build_job(
        source,
        title="Open Role A",
        external_id="A1",
        apply_url="https://example.org/a/A1",
    )
    closed_job = build_job(
        source,
        title="Closed Placeholder",
        external_id="A2",
        status="closed",
        apply_url="https://example.org/a/A2",
    )
    db.upsert_job(open_job)
    db.upsert_job(closed_job)
    db.record_detail_backlog_attempt(
        job_key=open_job.identity_key(),
        source_id=source.id,
        status="complete",
        listing_hash=open_job.normalized_hash or "open-listing-hash",
    )
    db.queue_detail_backlog_item(
        job_key=closed_job.identity_key(),
        source_id=source.id,
        listing_hash=closed_job.normalized_hash or "closed-listing-hash",
        reason="detail_quality_placeholder_only",
    )
    with db.connect() as conn:
        conn.execute(
            """
            UPDATE detail_backlog
            SET last_error = 'closed row should not affect refreshed health'
            WHERE job_key = ?
            """,
            (closed_job.identity_key(),),
        )
    (output / "sync_bundles_health.json").write_text(
        json.dumps(
            {
                "publish_result": "success_with_source_warnings",
                "source_health_exit_code": 2,
                "sources": [
                    {
                        "source_id": source.id,
                        "slug": source_output_slug(source),
                        "errors": [],
                        "warning": False,
                        "detail_pending": 1,
                        "detail_backlog_counts": {"pending": 1},
                        "last_error_summary": "stale closed backlog error",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = consolidate_bundle_databases(output_dir=output)
    _refresh_existing_health_report_after_consolidation(result)

    report = json.loads((output / "sync_bundles_health.json").read_text(encoding="utf-8"))
    source_report = report["sources"][0]
    assert source_report["detail_pending"] == 0
    assert source_report["detail_backlog_counts"] == {"complete": 1}
    assert source_report["last_error_summary"] is None


def test_consolidate_bundle_databases_quarantines_stale_split_inspira_current_rows(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    source = OrganizationSource(
        id="isa_inspira_split",
        name="International Seabed Authority",
        ats_family="inspira",
        base_url="https://careers.un.org",
        extra={"output_slug": "isa"},
    )
    fresh_source = OrganizationSource(
        id="un_inspira",
        name="United Nations",
        ats_family="inspira",
        base_url="https://careers.un.org",
        extra={"output_slug": "un"},
    )
    stale_db = JobDatabase(output / "isa_jobs.sqlite3")
    stale_db.initialize()
    stale_db.upsert_job(
        build_job(
            source,
            title="Stale Split Role",
            external_id="279000",
            apply_url="https://careers.un.org/jobSearchDescription/279000?language=en",
            closes_at=FUTURE_CLOSES_AT,
        )
    )
    stale_db.add_source_run(
        SyncResult(
            source_id=source.id,
            fetched=1,
            inserted=1,
            diagnostics=SourceRunDiagnostics(
                source_id=source.id,
                pagination_complete=True,
                health_status="ok",
                run_classification="ok",
                publishability_classification="ok",
                missing_transition_allowed=True,
                observed_at=STALE_OBSERVED_AT,
            ),
        ),
        observed_at=STALE_OBSERVED_AT,
    )
    fresh_db = JobDatabase(output / "un_jobs.sqlite3")
    fresh_db.initialize()
    fresh_db.upsert_job(
        build_job(
            fresh_source,
            title="Fresh Role",
            external_id="280000",
            apply_url="https://careers.un.org/jobSearchDescription/280000?language=en",
            closes_at=FUTURE_CLOSES_AT,
        )
    )
    fresh_db.add_source_run(
        SyncResult(
            source_id=fresh_source.id,
            fetched=1,
            inserted=1,
            diagnostics=SourceRunDiagnostics(
                source_id=fresh_source.id,
                pagination_complete=True,
                health_status="ok",
                run_classification="ok",
                publishability_classification="ok",
                missing_transition_allowed=True,
                observed_at=FRESH_OBSERVED_AT,
            ),
        ),
        observed_at=FRESH_OBSERVED_AT,
    )

    result = consolidate_bundle_databases(output_dir=output)

    current_rows = json.loads((output / "all_jobs_current.json").read_text(encoding="utf-8"))
    assert result.current_count == 1
    assert [row["source_id"] for row in current_rows] == ["un_inspira"]
    with sqlite3.connect(output / "all_jobs.sqlite3") as conn:
        conn.row_factory = sqlite3.Row
        stale = conn.execute(
            "SELECT status, stale_current, consolidation_status, source_freshness_status "
            "FROM jobs WHERE source_id = 'isa_inspira_split'"
        ).fetchone()
    assert stale["status"] == "stale_current"
    assert stale["stale_current"] == 1
    assert stale["consolidation_status"] == "stale_split_inspira_quarantined"
    assert stale["source_freshness_status"] == "stale"


def test_consolidate_bundle_databases_keeps_inconclusive_source_rows_open_without_stale_flag(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    source = OrganizationSource(
        id="inconclusive_source",
        name="Inconclusive",
        ats_family="workday",
        base_url="https://example.org",
        extra={"output_slug": "inconclusive"},
    )
    db = JobDatabase(output / "inconclusive_jobs.sqlite3")
    db.initialize()
    db.upsert_job(
        build_job(
            source,
            title="Previously Open Role",
            external_id="A1",
            closes_at="2099-07-30",
            description=(
                "Detailed role text with responsibilities, qualifications, and enough "
                "context to remain useful while the latest list run is inconclusive."
            ),
            apply_url="https://example.org/jobs/A1",
        )
    )
    db.add_source_run(
        SyncResult(
            source_id=source.id,
            fetched=0,
            inserted=0,
            errors=["temporary parser mismatch"],
            diagnostics=SourceRunDiagnostics(
                source_id=source.id,
                pagination_complete=False,
                health_status="issue",
                run_classification="inconclusive",
                publishability_classification="source_inconclusive",
                scope_validation_status="passed",
                missing_transition_allowed=False,
                observed_at=FRESH_OBSERVED_AT,
            ),
        ),
        observed_at=FRESH_OBSERVED_AT,
    )

    result = consolidate_bundle_databases(output_dir=output)

    assert result.current_count == 1
    current_rows = json.loads((output / "all_jobs_current.json").read_text(encoding="utf-8"))
    assert [row["source_id"] for row in current_rows] == ["inconclusive_source"]
    with sqlite3.connect(output / "all_jobs.sqlite3") as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT status, stale_current, source_freshness_status
            FROM jobs
            WHERE source_id = 'inconclusive_source'
            """
        ).fetchone()
        open_stale = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE status = 'open' AND stale_current = 1"
        ).fetchone()[0]
    assert row["status"] == "open"
    assert row["stale_current"] == 0
    assert row["source_freshness_status"] == "inconclusive"
    assert open_stale == 0


def test_consolidate_bundle_databases_ages_stale_sources_against_wall_clock(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    source = OrganizationSource(
        id="old_workday",
        name="Old Workday",
        ats_family="workday",
        base_url="https://example.org",
        extra={"output_slug": "old"},
    )
    db = JobDatabase(output / "old_jobs.sqlite3")
    db.initialize()
    db.upsert_job(
        build_job(
            source,
            title="Old Role",
            external_id="OLD1",
            closes_at="2099-07-30",
            description=(
                "Detailed old role text with responsibilities and qualifications "
                "that would otherwise appear complete."
            ),
            apply_url="https://example.org/jobs/OLD1",
        )
    )
    old_observed_at = datetime(2000, 1, 1, tzinfo=UTC)
    db.add_source_run(
        SyncResult(
            source_id=source.id,
            fetched=1,
            inserted=1,
            diagnostics=SourceRunDiagnostics(
                source_id=source.id,
                pagination_complete=True,
                health_status="ok",
                run_classification="ok",
                publishability_classification="ok",
                missing_transition_allowed=True,
                observed_at=old_observed_at,
            ),
        ),
        observed_at=old_observed_at,
    )

    result = consolidate_bundle_databases(output_dir=output)

    assert result.current_count == 0
    with sqlite3.connect(output / "all_jobs.sqlite3") as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT status, stale_current, source_freshness_status, consolidation_status
            FROM jobs
            WHERE source_id = 'old_workday'
            """
        ).fetchone()
    assert row["status"] == "stale_current"
    assert row["stale_current"] == 1
    assert row["source_freshness_status"] == "stale"
    assert row["consolidation_status"] == "stale_source_flagged"


def test_consolidate_bundle_databases_deduplicates_current_rows_and_records_aliases(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    canonical_source = OrganizationSource(
        id="un_inspira",
        name="United Nations",
        ats_family="inspira",
        base_url="https://careers.un.org",
        extra={"output_slug": "un"},
    )
    duplicate_source = OrganizationSource(
        id="mirror_inspira",
        name="Mirror",
        ats_family="inspira",
        base_url="https://mirror.example.org",
        extra={"output_slug": "mirror"},
    )
    for source, path, description in (
        (canonical_source, output / "un_jobs.sqlite3", "Canonical detailed text."),
        (duplicate_source, output / "mirror_jobs.sqlite3", "Short."),
    ):
        db = JobDatabase(path)
        db.initialize()
        job = build_job(
            source,
            title="Duplicated Role",
            external_id="279100",
            apply_url="https://careers.un.org/jobSearchDescription/279100?language=en",
            closes_at=FUTURE_CLOSES_AT,
            description=description,
        )
        db.upsert_job(job)
        db.record_detail_backlog_attempt(
            job_key=job.identity_key(),
            source_id=source.id,
            status="complete" if source.id == "un_inspira" else "pending",
            listing_hash=job.normalized_hash or "listing-hash",
        ) if source.id == "un_inspira" else db.update_detail_backlog_status(
            job_key=job.identity_key(),
            source_id=source.id,
            status="pending",
            listing_hash=job.normalized_hash or "listing-hash",
            reason="test",
        )
        db.add_source_run(
            SyncResult(
                source_id=source.id,
                fetched=1,
                inserted=1,
                diagnostics=SourceRunDiagnostics(
                    source_id=source.id,
                    pagination_complete=True,
                    health_status="ok",
                    run_classification="ok",
                    publishability_classification="ok",
                    missing_transition_allowed=True,
                    observed_at=FRESH_OBSERVED_AT,
                ),
            ),
            observed_at=FRESH_OBSERVED_AT,
        )

    result = consolidate_bundle_databases(output_dir=output)

    assert result.current_count == 1
    current_rows = json.loads((output / "all_jobs_current.json").read_text(encoding="utf-8"))
    assert [row["source_id"] for row in current_rows] == ["un_inspira"]
    with sqlite3.connect(output / "all_jobs.sqlite3") as conn:
        conn.row_factory = sqlite3.Row
        duplicate = conn.execute(
            "SELECT status, duplicate_of_job_key, consolidation_status "
            "FROM jobs WHERE source_id = 'mirror_inspira'"
        ).fetchone()
        alias = conn.execute("SELECT * FROM consolidated_job_aliases").fetchone()

    assert duplicate["status"] == "duplicate"
    assert duplicate["duplicate_of_job_key"] == "un_inspira:279100"
    assert duplicate["consolidation_status"] == "duplicate_quarantined"
    assert alias["duplicate_job_key"] == "mirror_inspira:279100"
    assert alias["canonical_job_key"] == "un_inspira:279100"
    assert alias["reason"] == "same_apply_url"


def test_consolidate_bundle_databases_keeps_cross_source_external_id_collisions(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    sources = [
        OrganizationSource(
            id="org_a_oracle",
            name="Org A",
            ats_family="oracle_hcm",
            base_url="https://oracle.example.org",
            extra={"output_slug": "org_a"},
        ),
        OrganizationSource(
            id="org_b_oracle",
            name="Org B",
            ats_family="oracle_hcm",
            base_url="https://oracle.example.org",
            extra={"output_slug": "org_b"},
        ),
    ]
    for index, source in enumerate(sources, start=1):
        db = JobDatabase(output / f"{source.extra['output_slug']}_jobs.sqlite3")
        db.initialize()
        job = build_job(
            source,
            title=f"Tenant Role {index}",
            external_id="34126",
            apply_url=f"https://oracle.example.org/sites/CX_{index}/job/34126",
            closes_at="2099-07-30",
            description=(
                f"Detailed tenant {index} role text with responsibilities, "
                "qualifications, and enough context to remain complete."
            ),
        )
        db.upsert_job(job)
        db.add_source_run(
            SyncResult(
                source_id=source.id,
                fetched=1,
                inserted=1,
                diagnostics=SourceRunDiagnostics(
                    source_id=source.id,
                    pagination_complete=True,
                    health_status="ok",
                    run_classification="ok",
                    publishability_classification="ok",
                    missing_transition_allowed=True,
                    observed_at=FRESH_OBSERVED_AT,
                ),
            ),
            observed_at=FRESH_OBSERVED_AT,
        )

    result = consolidate_bundle_databases(output_dir=output)

    assert result.current_count == 2
    current_rows = json.loads((output / "all_jobs_current.json").read_text(encoding="utf-8"))
    assert sorted(row["source_id"] for row in current_rows) == ["org_a_oracle", "org_b_oracle"]
    with sqlite3.connect(output / "all_jobs.sqlite3") as conn:
        duplicate_count = conn.execute("SELECT COUNT(*) FROM consolidated_job_aliases").fetchone()[0]
        non_open = conn.execute("SELECT COUNT(*) FROM jobs WHERE status <> 'open'").fetchone()[0]
    assert duplicate_count == 0
    assert non_open == 0


def test_consolidate_bundle_databases_marks_detail_quality_deadlines_and_trusted_exports(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    source = OrganizationSource(
        id="org_oracle_hcm",
        name="Org",
        ats_family="oracle_hcm",
        base_url="https://example.org",
        extra={"output_slug": "org"},
    )
    db = JobDatabase(output / "org_jobs.sqlite3")
    db.initialize()
    jobs = [
        build_job(
            source,
            title="Good Future Role",
            external_id="A1",
            closes_at="2099-07-30",
            description=(
                "Detailed responsibilities and qualifications for a substantive vacancy, "
                "including delivery, coordination, reporting, and stakeholder engagement."
            ),
            apply_url="https://example.org/jobs/A1",
        ),
        build_job(
            source,
            title="Expired Role",
            external_id="A2",
            closes_at="2020-01-01",
            description=(
                "Detailed responsibilities and qualifications for an expired vacancy, "
                "including delivery, coordination, reporting, and stakeholder engagement."
            ),
            apply_url="https://example.org/jobs/A2",
        ),
        build_job(
            source,
            title="Heading Only Role",
            external_id="A3",
            closes_at="2099-07-30",
            description="Duties and Responsibilities",
            apply_url="https://example.org/jobs/A3",
            raw={
                "ShortDescriptionStr": "Duties and Responsibilities",
                "ExternalResponsibilitiesStr": "",
                "ExternalQualificationsStr": "",
            },
        ),
        build_job(
            source,
            title="Pending But Substantive Role",
            external_id="A4",
            closes_at="2099-07-30",
            description=(
                "Substantive listing or cached detail text remains useful even while "
                "the persistent detail backlog item is still pending refresh."
            ),
            apply_url="https://example.org/jobs/A4",
        ),
    ]
    for job in jobs:
        db.upsert_job(job)
        if job.external_id == "A4":
            db.update_detail_backlog_status(
                job_key=job.identity_key(),
                source_id=source.id,
                status="pending",
                listing_hash=job.normalized_hash or "listing-hash",
                reason="test_pending",
            )
        else:
            db.record_detail_backlog_attempt(
                job_key=job.identity_key(),
                source_id=source.id,
                status="complete",
                listing_hash=job.normalized_hash or "listing-hash",
            )
    db.add_source_run(
        SyncResult(
            source_id=source.id,
            fetched=4,
            inserted=4,
            diagnostics=SourceRunDiagnostics(
                source_id=source.id,
                pagination_complete=True,
                health_status="ok",
                run_classification="ok",
                publishability_classification="ok",
                missing_transition_allowed=True,
                observed_at=FRESH_OBSERVED_AT,
            ),
        ),
        observed_at=FRESH_OBSERVED_AT,
    )

    result = consolidate_bundle_databases(output_dir=output)

    assert result.current_count == 3
    assert result.history_count == 1
    assert result.total_count == 4
    assert result.trusted_current_count == 3
    assert result.application_ready_count == 2
    assert result.current_detail_complete_count == 2
    assert result.current_detail_weak_count == 1
    assert result.expired_moved_to_history_count == 1
    assert not (output / "all_jobs_trusted_current.json").exists()
    assert not (output / "all_jobs_application_ready.json").exists()
    current_rows = json.loads((output / "all_jobs_current.json").read_text(encoding="utf-8"))
    history_rows = json.loads((output / "all_jobs_history.json").read_text(encoding="utf-8"))
    assert sorted(row["external_id"] for row in current_rows) == ["A1", "A3", "A4"]
    assert [row["external_id"] for row in history_rows] == ["A2"]
    with sqlite3.connect(output / "all_jobs.sqlite3") as conn:
        conn.row_factory = sqlite3.Row
        rows = {
            row["external_id"]: row
            for row in conn.execute(
                """
                SELECT j.external_id, j.status, j.detail_quality_status, j.deadline_state,
                       j.source_listed_current, j.trusted_current, j.application_ready,
                       b.detail_status, b.queued_reason
                FROM jobs j
                JOIN detail_backlog b ON b.job_key = j.job_key
                """
            )
        }
        source_status = conn.execute(
            """
            SELECT expired_current_jobs
            FROM consolidated_source_status
            WHERE source_id = 'org_oracle_hcm'
            """
        ).fetchone()
    assert rows["A1"]["detail_quality_status"] == "complete"
    assert rows["A1"]["deadline_state"] == "future"
    assert rows["A1"]["source_listed_current"] == 1
    assert rows["A1"]["trusted_current"] == 1
    assert rows["A1"]["application_ready"] == 1
    assert rows["A2"]["deadline_state"] == "expired"
    assert rows["A2"]["status"] == "expired"
    assert rows["A2"]["source_listed_current"] == 0
    assert rows["A2"]["trusted_current"] == 0
    assert rows["A2"]["application_ready"] == 0
    assert rows["A3"]["detail_quality_status"] == "placeholder_only"
    assert rows["A3"]["detail_status"] == "pending"
    assert rows["A3"]["queued_reason"] == "detail_quality_placeholder_only"
    assert rows["A3"]["application_ready"] == 0
    assert rows["A4"]["detail_quality_status"] == "complete"
    assert rows["A4"]["detail_status"] == "pending"
    assert rows["A4"]["application_ready"] == 1
    assert source_status["expired_current_jobs"] == 1


def test_consolidate_bundles_health_report_includes_consolidated_only_stale_sources(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    fresh_source = OrganizationSource(
        id="fresh_source",
        name="Fresh",
        ats_family="workday",
        base_url="https://example.org/fresh",
        extra={"output_slug": "fresh"},
    )
    stale_source = OrganizationSource(
        id="stale_source",
        name="Stale",
        ats_family="workday",
        base_url="https://example.org/stale",
        extra={"output_slug": "stale"},
    )
    for source, observed_at in (
        (fresh_source, FRESH_OBSERVED_AT),
        (stale_source, STALE_OBSERVED_AT),
    ):
        external_id = "A1" if source.id == "fresh_source" else "A2"
        db = JobDatabase(output / f"{source.extra['output_slug']}_jobs.sqlite3")
        db.initialize()
        db.upsert_job(
            build_job(
                source,
                title=f"{source.name} Role",
                external_id=external_id,
                closes_at="2099-07-30",
                description=(
                    "Detailed role text for a consolidated source with responsibilities, "
                    "qualifications, and enough context for quality checks."
                ),
                apply_url=f"{source.base_url}/jobs/{external_id}",
            )
        )
        db.add_source_run(
            SyncResult(
                source_id=source.id,
                fetched=1,
                inserted=1,
                diagnostics=SourceRunDiagnostics(
                    source_id=source.id,
                    pagination_complete=True,
                    health_status="ok",
                    run_classification="ok",
                    publishability_classification="ok",
                    missing_transition_allowed=True,
                    observed_at=observed_at,
                ),
            ),
            observed_at=observed_at,
        )
    (output / "sync_bundles_health.json").write_text(
        json.dumps(
            {
                "publish_result": "success",
                "exit_code": 0,
                "source_health_exit_code": 0,
                "fatal_errors_count": 0,
                "current_jobs_count": 1,
                "total_jobs_count": 1,
                "bundles_published_count": 1,
                "degraded_source_count": 0,
                "inconclusive_source_count": 0,
                "source_adapter_broken_count": 0,
                "sources": [
                    {
                        "source_id": "fresh_source",
                        "slug": "fresh",
                        "publishability_classification": "ok",
                        "run_classification": "ok",
                        "health_status": "ok",
                        "fetched_count": 1,
                        "pagination_complete": True,
                        "verified_empty": False,
                        "missing_transition_allowed": True,
                        "detail_attempted": 0,
                        "detail_succeeded": 0,
                        "detail_failed": 0,
                        "detail_skipped": 0,
                        "detail_pending": 0,
                        "detail_backlog_counts": {},
                        "circuit_breakers": {},
                        "last_error_summary": None,
                        "cooldown_until": None,
                        "warning": False,
                    },
                    {
                        "source_id": "empty_source",
                        "slug": "empty",
                        "publishability_classification": "ok_empty",
                        "run_classification": "ok_empty",
                        "health_status": "ok",
                        "fetched_count": 0,
                        "pagination_complete": True,
                        "verified_empty": True,
                        "missing_transition_allowed": True,
                        "detail_attempted": 0,
                        "detail_succeeded": 0,
                        "detail_failed": 0,
                        "detail_skipped": 0,
                        "detail_pending": 0,
                        "detail_backlog_counts": {},
                        "circuit_breakers": {},
                        "last_error_summary": None,
                        "cooldown_until": None,
                        "warning": False,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert main(["consolidate-bundles", "--output-dir", str(output)]) == 0

    report = json.loads((output / "sync_bundles_health.json").read_text(encoding="utf-8"))
    current_rows = json.loads((output / "all_jobs_current.json").read_text(encoding="utf-8"))
    stale_report = next(row for row in report["sources"] if row["source_id"] == "stale_source")
    assert report["publish_result"] == "success_with_source_warnings"
    assert report["source_health_exit_code"] == 2
    assert [row["source_id"] for row in current_rows] == ["fresh_source"]
    assert stale_report["consolidated_open_jobs"] == 0
    empty_report = next(row for row in report["sources"] if row["source_id"] == "empty_source")
    assert empty_report["consolidated_open_jobs"] == 0
    assert empty_report["consolidated_application_ready_jobs"] == 0
    assert empty_report["consolidated_weak_detail_jobs"] == 0
    assert stale_report["consolidated_only"] is True
    assert stale_report["source_freshness_status"] == "stale"
    assert stale_report["consolidated_stale_current_jobs"] == 1
    assert stale_report["warning"] is True


def test_consolidate_bundle_databases_normalizes_expired_circuit_breakers(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    source = OrganizationSource(
        id="org_breaker",
        name="Org",
        ats_family="workday",
        base_url="https://example.org",
        extra={"output_slug": "org"},
    )
    db = JobDatabase(output / "org_jobs.sqlite3")
    db.initialize()
    db.upsert_job(
        build_job(
            source,
            title="Role",
            external_id="A1",
            apply_url="https://example.org/jobs/A1",
        )
    )
    db.set_source_breaker(
        source_id=source.id,
        breaker_type="transient_detail",
        state="open",
        failure_count=2,
        cooldown_until=datetime(2020, 1, 1, tzinfo=UTC),
        reason="old cooldown",
    )

    consolidate_bundle_databases(output_dir=output)

    with sqlite3.connect(output / "all_jobs.sqlite3") as conn:
        conn.row_factory = sqlite3.Row
        breaker = conn.execute("SELECT * FROM source_circuit_breakers").fetchone()
    assert breaker["state"] == "half_open"
    assert breaker["cooldown_until"] is None
    assert "cooldown expired" in breaker["last_reason"]


def test_sync_bundles_refreshes_consolidated_database_by_default(tmp_path):
    config = tmp_path / "organizations.yaml"
    output = tmp_path / "output"
    config.write_text(
        """
sources:
  - id: org_static_bundle
    name: Org
    ats_family: static_bundle_test
    base_url: https://example.org
    enabled: true
    extra:
      output_slug: org
""",
        encoding="utf-8",
    )

    exit_code = main(
        [
            "sync-bundles",
            "--config",
            str(config),
            "--output-dir",
            str(output),
            "--no-archive",
        ]
    )

    assert exit_code == 0
    assert (output / "all_jobs.sqlite3").exists()
    rows = json.loads((output / "all_jobs_current.json").read_text(encoding="utf-8"))
    assert [row["title"] for row in rows] == ["Role 1"]


def test_sync_bundles_degraded_source_returns_exit_2_after_publish(tmp_path):
    config = tmp_path / "organizations.yaml"
    output = tmp_path / "output"
    config.write_text(
        """
sources:
  - id: org_degraded_bundle
    name: Org
    ats_family: degraded_bundle_test
    base_url: https://example.org
    enabled: true
    extra:
      output_slug: org
""",
        encoding="utf-8",
    )

    exit_code = main(
        [
            "sync-bundles",
            "--config",
            str(config),
            "--output-dir",
            str(output),
            "--no-archive",
            "--skip-classify",
        ]
    )

    assert exit_code == 2
    assert (output / "all_jobs.sqlite3").exists()
    report = json.loads((output / "sync_bundles_health.json").read_text(encoding="utf-8"))
    assert report["publish_result"] == "success_with_source_warnings"
    assert report["fatal_errors_count"] == 0
    assert report["exit_code"] == 2
    assert report["source_health_exit_code"] == 2
    assert report["allow_source_degraded"] is False
    assert report["publishable_degraded_sources"] == ["org_degraded_bundle"]
    assert report["degraded_source_count"] == 1
    assert report["inconclusive_source_count"] == 0
    assert report["source_adapter_broken_count"] == 0
    assert report["current_jobs_count"] == 1
    assert report["total_jobs_count"] == 1
    assert report["detail_backlog_counts"]["permanent_failed"] == 1
    assert report["detail_pending_count"] == 0
    assert report["bundles_published_count"] == 1
    source_report = report["sources"][0]
    assert source_report["source_id"] == "org_degraded_bundle"
    assert source_report["publishability_classification"] == "publishable_detail_degraded"
    assert source_report["fetched_count"] == 1
    assert source_report["pagination_complete"] is True
    assert source_report["verified_empty"] is False
    assert source_report["blocked"] is False
    assert source_report["transient_error"] is False
    assert source_report["scope_validation_status"] == "not_applicable"
    assert source_report["scope_passed"] is True
    assert source_report["missing_transition_allowed"] is True
    assert source_report["missing_closed_safety_gate_passed"] is True
    assert "detail_backlog_counts" in source_report
    assert "circuit_breakers" in source_report
    assert "last_error_summary" in source_report


def test_sync_bundles_allow_source_degraded_converts_exit_2_to_0(tmp_path):
    config = tmp_path / "organizations.yaml"
    output = tmp_path / "output"
    config.write_text(
        """
sources:
  - id: org_degraded_bundle
    name: Org
    ats_family: degraded_bundle_test
    base_url: https://example.org
    enabled: true
    extra:
      output_slug: org
""",
        encoding="utf-8",
    )

    exit_code = main(
        [
            "sync-bundles",
            "--config",
            str(config),
            "--output-dir",
            str(output),
            "--no-archive",
            "--skip-classify",
            "--allow-source-degraded",
        ]
    )

    assert exit_code == 0
    report = json.loads((output / "sync_bundles_health.json").read_text(encoding="utf-8"))
    assert report["publish_result"] == "success_with_source_warnings"
    assert report["exit_code"] == 0
    assert report["source_health_exit_code"] == 2
    assert report["allow_source_degraded"] is True
    assert report["publishable_degraded_sources"] == ["org_degraded_bundle"]


def test_sync_bundles_detail_page_limit_overrides_source_budget(tmp_path):
    config = tmp_path / "organizations.yaml"
    output = tmp_path / "output"
    config.write_text(
        """
sources:
  - id: org_detail_limit_bundle
    name: Org
    ats_family: detail_limit_bundle_test
    base_url: https://example.org
    enabled: true
    extra:
      output_slug: org
      max_detail_pages_per_run: 1
""",
        encoding="utf-8",
    )

    exit_code = main(
        [
            "sync-bundles",
            "--config",
            str(config),
            "--output-dir",
            str(output),
            "--no-archive",
            "--skip-classify",
            "--refresh-all-details",
            "--detail-page-limit",
            "3",
        ]
    )

    assert exit_code == 0
    report = json.loads((output / "sync_bundles_health.json").read_text(encoding="utf-8"))
    source_report = report["sources"][0]
    assert source_report["detail_attempted"] == 3
    assert source_report["detail_succeeded"] == 3
    assert source_report["detail_pending"] == 0
    rows = json.loads((output / "org_jobs_current.json").read_text(encoding="utf-8"))
    assert [row["title"] for row in rows] == ["Detailed Role A1", "Detailed Role A2", "Detailed Role A3"]


def test_sync_bundles_browser_cookie_assist_on_block_retries_only_after_block(tmp_path, monkeypatch):
    config = tmp_path / "organizations.yaml"
    output = tmp_path / "output"
    cookie_file = tmp_path / "cookie.txt"
    cookie_file.write_text("Cookie: aws-waf-token=abc; session=def\n", encoding="utf-8")
    opened = []
    monkeypatch.setattr("jobagg.scheduler.webbrowser.open", opened.append)
    config.write_text(
        """
sources:
  - id: org_browser_assist_bundle
    name: Org
    ats_family: browser_assist_bundle_test
    base_url: https://jobs.example.org
    enabled: true
    extra:
      output_slug: org
      browser_cookie_assist: true
      browser_cookie_url: https://jobs.example.org/listing/
      detail_none_is_transient: true
""",
        encoding="utf-8",
    )

    exit_code = main(
        [
            "sync-bundles",
            "--config",
            str(config),
            "--output-dir",
            str(output),
            "--no-archive",
            "--skip-classify",
            "--browser-cookie-assist-on-block",
            "--browser-cookie-file",
            str(cookie_file),
        ]
    )

    assert exit_code == 0
    assert opened == ["https://jobs.example.org/listing/"]
    rows = json.loads((output / "org_jobs_current.json").read_text(encoding="utf-8"))
    assert rows[0]["title"] == "Detailed Role"
    assert "Detailed role text after browser cookie assist" in rows[0]["description"]
    report = json.loads((output / "sync_bundles_health.json").read_text(encoding="utf-8"))
    assert report["publish_result"] == "success"
    assert report["sources"][0]["detail_failed"] == 0


def test_sync_bundles_browser_cookie_assist_on_block_does_not_prompt_when_healthy(
    tmp_path,
    monkeypatch,
):
    config = tmp_path / "organizations.yaml"
    output = tmp_path / "output"

    def fail_if_opened(url):
        raise AssertionError(f"browser opened unexpectedly: {url}")

    monkeypatch.setattr("jobagg.scheduler.webbrowser.open", fail_if_opened)
    config.write_text(
        """
sources:
  - id: org_static_bundle
    name: Org
    ats_family: static_bundle_test
    base_url: https://example.org
    enabled: true
    extra:
      output_slug: org
      browser_cookie_assist: true
      browser_cookie_url: https://example.org/listing/
""",
        encoding="utf-8",
    )

    exit_code = main(
        [
            "sync-bundles",
            "--config",
            str(config),
            "--output-dir",
            str(output),
            "--no-archive",
            "--skip-classify",
            "--browser-cookie-assist-on-block",
        ]
    )

    assert exit_code == 0
    report = json.loads((output / "sync_bundles_health.json").read_text(encoding="utf-8"))
    assert report["publish_result"] == "success"


def test_sync_bundles_fatal_consolidation_failure_returns_exit_1(tmp_path, monkeypatch):
    config = tmp_path / "organizations.yaml"
    output = tmp_path / "output"
    config.write_text(
        """
sources:
  - id: org_static_bundle
    name: Org
    ats_family: static_bundle_test
    base_url: https://example.org
    enabled: true
    extra:
      output_slug: org
""",
        encoding="utf-8",
    )

    def fail_consolidation(*, output_dir, slug="all"):
        raise RuntimeError("consolidation exploded")

    monkeypatch.setattr("jobagg.scheduler.consolidate_bundle_databases", fail_consolidation)

    exit_code = main(
        [
            "sync-bundles",
            "--config",
            str(config),
            "--output-dir",
            str(output),
            "--no-archive",
            "--skip-classify",
        ]
    )

    assert exit_code == 1
    report = json.loads((output / "sync_bundles_health.json").read_text(encoding="utf-8"))
    assert report["publish_result"] == "failed"
    assert report["fatal_errors_count"] == 1
    assert report["exit_code"] == 1
    assert report["source_health_exit_code"] == 1
    assert report["current_jobs_count"] is None
    assert report["total_jobs_count"] is None


def test_source_health_report_dry_run_summarizes_local_policy_and_schema(tmp_path):
    config = tmp_path / "organizations.yaml"
    output = tmp_path / "output"
    report_path = tmp_path / "source_health.json"
    config.write_text(
        """
sources:
  - id: org_static_bundle
    name: Org
    ats_family: static_bundle_test
    base_url: https://example.org
    enabled: true
    extra:
      output_slug: org
""",
        encoding="utf-8",
    )
    assert main(
        [
            "sync-bundles",
            "--config",
            str(config),
            "--output-dir",
            str(output),
            "--no-archive",
            "--skip-classify",
        ]
    ) == 0

    exit_code = main(
        [
            "source-health-report",
            "--dry-run",
            "--config",
            str(config),
            "--sources",
            "org_static_bundle",
            "--output-dir",
            str(output),
            "--format",
            "json",
            "--output",
            str(report_path),
        ]
    )

    assert exit_code == 0
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["dry_run"] is True
    assert payload["live_fetch_attempted"] is False
    assert payload["health_report_schema"]["ok"] is True
    assert payload["sources"][0]["source_id"] == "org_static_bundle"
    assert payload["sources"][0]["policy"]["list_fetch_interval_minutes"] == 180
    assert payload["sources"][0]["missing_closed_gate_valid"] is True


def _staged_result(tmp_path, *, source_id, slug, fetched):
    source = OrganizationSource(
        id=source_id,
        name=source_id,
        ats_family="custom_html",
        base_url="https://example.org",
        extra={"output_slug": slug},
    )
    paths = source_output_paths(tmp_path, source_id)
    paths["db"].parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(paths["db"]) as conn:
        conn.execute("CREATE TABLE jobs (job_key TEXT)")
    for key, path in paths.items():
        if key != "db":
            path.write_text("[]", encoding="utf-8")
    return BundleResult(
        source=source,
        slug=slug,
        file_slug=source_id,
        paths=paths,
        sync_result=SyncResult(source_id=source_id, fetched=fetched, inserted=fetched),
    )
