import json
import sqlite3

from jobagg.adapters.base import JobAdapter, register_adapter
from jobagg.db import JobDatabase
from jobagg.models import OrganizationSource, SyncResult
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
from jobagg.scheduler import main


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
                closes_at="2026-05-30",
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
            closes_at="2026-05-30",
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
    assert result.history_count == 2
    assert result.status_counts == {"closed": 1, "open": 1}
    assert result.db_path == output / "all_jobs.sqlite3"
    assert (output / "all_jobs_current.csv").exists()
    assert (output / "all_jobs_history.csv").exists()

    current_rows = json.loads((output / "all_jobs_current.json").read_text(encoding="utf-8"))
    history_rows = json.loads((output / "all_jobs_history.json").read_text(encoding="utf-8"))

    assert [row["title"] for row in current_rows] == ["Open Role A"]
    assert sorted(row["title"] for row in history_rows) == ["Closed Role B", "Open Role A"]
    with sqlite3.connect(output / "all_jobs.sqlite3") as conn:
        assert conn.execute("SELECT COUNT(*) FROM grade_mappings").fetchone()[0] >= 700


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
    assert report["publishable_degraded_sources"] == ["org_degraded_bundle"]
    assert report["current_jobs_count"] == 1
    assert report["total_jobs_count"] == 1
    assert report["bundles_published_count"] == 1


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
