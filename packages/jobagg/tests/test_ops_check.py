from jobagg.db import JobDatabase
from jobagg.models import OrganizationSource, SourceRunDiagnostics, SyncResult
from jobagg.normalize import build_job
from jobagg.ops_check import collect_ops_check, ops_check_to_markdown
from jobagg.scheduler import main


def test_ops_check_collects_bundle_health_and_writes_markdown(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    _write_bundle(
        output / "healthy_jobs.sqlite3",
        source_id="healthy_source",
        fetched=1,
        health_status="ok",
        pagination_complete=True,
        errors=[],
    )
    _write_bundle(
        output / "broken_jobs.sqlite3",
        source_id="broken_source",
        fetched=0,
        health_status="issue",
        pagination_complete=False,
        errors=["parser_no_match"],
    )
    _write_bundle(
        output / "all_jobs.sqlite3",
        source_id="consolidated_latest_source",
        fetched=99,
        health_status="ok",
        pagination_complete=True,
        errors=[],
    )

    report = collect_ops_check(
        db_path=tmp_path / "unused.sqlite3",
        output_dir=output,
        all_bundles=True,
    )
    markdown = ops_check_to_markdown(report)

    assert report.pass_count == 1
    assert report.fail_count == 1
    assert len(report.db_paths) == 2
    assert "healthy_source" in markdown
    assert "`broken_source`" in markdown
    assert "consolidated_latest_source" not in markdown
    assert "latest run recorded 1 error(s)" in markdown
    assert "pagination is incomplete" in markdown


def test_ops_check_command_infers_output_dir_from_report_path(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    _write_bundle(
        output / "healthy_jobs.sqlite3",
        source_id="healthy_source",
        fetched=1,
        health_status="ok",
        pagination_complete=True,
        errors=[],
    )
    report_path = output / "ops_check.md"

    exit_code = main(["ops-check", "--all", "--output", str(report_path)])

    assert exit_code == 0
    assert report_path.exists()
    assert "healthy_source" in report_path.read_text(encoding="utf-8")


def test_ops_check_accepts_verified_text_empty(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    _write_bundle(
        output / "empty_jobs.sqlite3",
        source_id="empty_source",
        fetched=0,
        health_status="ok_empty",
        pagination_complete=True,
        errors=[],
        empty_reason="verified_text_empty",
    )

    report = collect_ops_check(
        db_path=tmp_path / "unused.sqlite3",
        output_dir=output,
        all_bundles=True,
    )

    assert report.pass_count == 1
    assert report.warn_count == 0


def _write_bundle(
    path,
    *,
    source_id: str,
    fetched: int,
    health_status: str,
    pagination_complete: bool,
    errors: list[str],
    empty_reason: str | None = None,
) -> None:
    source = OrganizationSource(
        id=source_id,
        name=source_id,
        ats_family="custom_html",
        base_url="https://example.org",
    )
    db = JobDatabase(path)
    db.initialize()
    if fetched:
        db.upsert_job(
            build_job(
                source,
                title="Role",
                external_id="role-1",
                apply_url="https://example.org/role-1",
            )
        )
    db.add_source_run(
        SyncResult(
            source_id=source_id,
            fetched=fetched,
            inserted=fetched,
            errors=errors,
            diagnostics=SourceRunDiagnostics(
                source_id=source_id,
                health_status=health_status,
                scope_validation_status="passed",
                pagination_complete=pagination_complete,
                total_reported_by_source=fetched,
                empty_reason=empty_reason,
                missing_transition_allowed=health_status in {"ok", "ok_empty"},
            ),
        )
    )
