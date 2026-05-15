from jobagg.db import JobDatabase
from jobagg.models import OrganizationSource
from jobagg.normalize import build_job


def test_listing_only_update_preserves_existing_detail_fields(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="wfp_workday",
        name="World Food Programme",
        ats_family="workday",
        base_url="https://wd3.myworkdaysite.com/recruiting/wfp/job_openings",
    )
    detailed = build_job(
        source,
        title="Programme Officer",
        external_id="JR100",
        location="Nairobi, Kenya",
        employment_type="Full time",
        closes_at="2026-05-30",
        description="Detailed responsibilities and requirements.",
        apply_url="/job/Nairobi-Kenya/Programme-Officer_JR100",
    )
    listing_only = build_job(
        source,
        title="Programme Officer",
        external_id="JR100",
        location="Nairobi, Kenya",
        apply_url="/job/Nairobi-Kenya/Programme-Officer_JR100",
        raw={"externalPath": "/job/Nairobi-Kenya/Programme-Officer_JR100"},
    )

    assert db.upsert_job(detailed) == "inserted"
    assert db.upsert_job(listing_only) == "unchanged"

    stored = db.get_job("wfp_workday:JR100")
    assert stored is not None
    assert stored["employment_type"] == "Full time"
    assert stored["closes_at"] == "2026-05-30T00:00:00+00:00"
    assert stored["description"] == "Detailed responsibilities and requirements."


def test_identity_falls_back_to_title_location_and_closing_date():
    source = OrganizationSource(
        id="org",
        name="Example Org",
        ats_family="custom_html",
        base_url="https://example.org",
    )
    first = build_job(
        source,
        title=" Programme Officer ",
        location="Nairobi, Kenya",
        closes_at="2026-05-30",
        apply_url="https://example.org/a",
    )
    second = build_job(
        source,
        title="programme   officer",
        location="nairobi,   kenya",
        closes_at="2026-05-30T12:00:00+00:00",
        apply_url="https://example.org/b",
    )

    assert first.identity_key() == second.identity_key()
    assert first.identity_key().startswith("org:fallback:")


def test_content_hash_tracks_spec_fields_only():
    source = OrganizationSource(
        id="org",
        name="Example Org",
        ats_family="oracle_hcm",
        base_url="https://example.org",
    )
    first = build_job(
        source,
        title="Role",
        external_id="123",
        location="Geneva",
        employment_type="Fixed term",
        closes_at="2026-05-30",
        description="Same description",
        apply_url="https://example.org/a",
    )
    second = build_job(
        source,
        title="Role",
        external_id="123",
        location="Geneva",
        employment_type="Fixed term",
        closes_at="2026-05-30",
        description="Same description",
        apply_url="https://example.org/b",
    )
    third = build_job(
        source,
        title="Role",
        external_id="123",
        location="Geneva",
        employment_type="Consultancy",
        closes_at="2026-05-30",
        description="Same description",
        apply_url="https://example.org/b",
    )

    assert first.hash_payload() == second.hash_payload()
    assert first.hash_payload() != third.hash_payload()


def test_change_events_snapshots_missing_closed_and_reopened(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = OrganizationSource(
        id="org_api",
        name="Example Org",
        ats_family="oracle_hcm",
        base_url="https://example.org",
        extra={"output_slug": "org"},
    )
    job = build_job(
        source,
        title="Role",
        external_id="123",
        location="Geneva",
        closes_at="2026-05-30",
        description="Version 1",
        apply_url="https://example.org/job/123",
    )

    assert db.upsert_job(job) == "inserted"
    updated = build_job(
        source,
        title="Role",
        external_id="123",
        location="Geneva",
        closes_at="2026-05-30",
        description="Version 2",
        apply_url="https://example.org/job/123",
    )
    assert db.upsert_job(updated) == "updated"
    counts = db.mark_missing("org_api", set(), missing_run_threshold=2)
    assert counts == {"missing": 0, "closed": 0}
    counts = db.mark_missing("org_api", set(), missing_run_threshold=2)
    assert counts == {"missing": 1, "closed": 0}
    assert db.upsert_job(updated) == "updated"

    expired = build_job(
        source,
        title="Expired Role",
        external_id="456",
        location="Geneva",
        closes_at="2020-01-01",
        description="Expired",
        apply_url="https://example.org/job/456",
    )
    assert db.upsert_job(expired) == "inserted"
    counts = db.mark_missing("org_api", {updated.identity_key()}, missing_run_threshold=3)
    assert counts == {"missing": 0, "closed": 1}

    with db.connect() as conn:
        events = [
            row["change_type"]
            for row in conn.execute("SELECT change_type FROM change_events ORDER BY id")
        ]
        snapshots = conn.execute("SELECT COUNT(*) AS count FROM vacancy_snapshots").fetchone()

    assert events == ["created", "updated", "missing", "reopened", "created", "closed"]
    assert snapshots["count"] == 4
