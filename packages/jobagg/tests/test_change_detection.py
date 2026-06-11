from jobagg.db import JobDatabase
from jobagg.models import OrganizationSource
from jobagg.normalize import build_job
from jobagg.pipelines.change_detection import changed_jobs, open_jobs


def test_open_jobs_and_changed_jobs_have_distinct_semantics(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="org_api",
        name="Example Org",
        ats_family="oracle_hcm",
        base_url="https://example.org",
    )
    job = build_job(
        source,
        title="Role",
        external_id="123",
        description="Version 1",
        apply_url="https://example.org/jobs/123",
    )
    db.upsert_job(job)
    db.upsert_job(
        build_job(
            source,
            title="Role",
            external_id="123",
            description="Version 2",
            apply_url="https://example.org/jobs/123",
        )
    )

    open_rows = open_jobs(db, source_id="org_api")
    changed_rows = changed_jobs(db, source_id="org_api")

    assert [row["external_id"] for row in open_rows] == ["123"]
    assert [row["change_type"] for row in changed_rows] == ["created", "updated"]
    assert {row["change_event_id"] for row in changed_rows}
    assert all(row["job_key"] == "org_api:123" for row in changed_rows)
