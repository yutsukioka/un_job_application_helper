"""Tests for the P1 batch of improvements (#5 #8 #17 #18 #2 #24 #12)."""

from __future__ import annotations

import time

from jobagg.db import JobDatabase
from jobagg.hashing import content_hash, posting_fingerprint
from jobagg.http import JobAggHTTPClient
from jobagg.models import JobRecord, OrganizationSource
from jobagg.normalize import build_job, parse_datetime


def _source() -> OrganizationSource:
    return OrganizationSource(
        id="undp_inspira",
        name="UNDP",
        ats_family="inspira",
        base_url="https://example.org/",
    )


# ---------- #5: HTML / whitespace noise excluded from content hash ----------


def test_content_hash_ignores_html_whitespace_noise():
    base = dict(
        source_id="undp_inspira",
        org_id="undp_inspira",
        ats_family="inspira",
        title="Programme Officer",
        apply_url="https://x/1",
        external_id="J1",
        location="Geneva",
    )
    plain = JobRecord(
        **base,
        description="Lead programme delivery and report results.",
    )
    noisy = JobRecord(
        **base,
        description=(
            "<p>Lead   programme\u00a0delivery\n\nand\treport "
            "<!-- internal note --><script>analytics()</script> "
            "results.</p>\u200b"
        ),
    )
    assert content_hash(plain) == content_hash(noisy)


def test_content_hash_changes_on_real_content_change():
    base = dict(
        source_id="undp_inspira",
        org_id="undp_inspira",
        ats_family="inspira",
        title="Programme Officer",
        apply_url="https://x/1",
        external_id="J1",
        location="Geneva",
    )
    a = JobRecord(**base, description="Lead programme delivery.")
    b = JobRecord(**base, description="Lead financial delivery.")
    assert content_hash(a) != content_hash(b)


# ---------- #8: per-host throttle ----------


def test_per_host_throttle_does_not_delay_other_hosts(monkeypatch):
    client = JobAggHTTPClient(min_delay_seconds=10.0)
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    # Pretend host A was just hit.
    client._mark_request("host-a.example")
    client._respect_min_delay("host-b.example")
    # Different host should not be throttled at all.
    assert sleeps == []
    # Same host should be throttled.
    client._respect_min_delay("host-a.example")
    assert sleeps and sleeps[0] > 0


# ---------- #2: closes_at_local + closes_tz round-trip ----------


def test_closes_at_local_and_tz_round_trip(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    src = _source()
    job = build_job(
        src,
        title="Field Coordinator",
        external_id="F1",
        location="Nairobi",
        apply_url="https://x/f1",
        closes_at="2026-01-15T17:00:00+00:00",
    )
    job.closes_at_local = "2026-01-15 20:00"
    job.closes_tz = "Africa/Nairobi"
    db.upsert_job(job)
    stored = db.get_job(job.identity_key())
    assert stored is not None
    assert stored["closes_at_local"] == "2026-01-15 20:00"
    assert stored["closes_tz"] == "Africa/Nairobi"


def test_listing_only_update_preserves_local_tz(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    src = _source()
    detailed = build_job(
        src,
        title="Field Coordinator",
        external_id="F1",
        location="Nairobi",
        apply_url="https://x/f1",
        closes_at="2026-01-15T17:00:00+00:00",
    )
    detailed.closes_at_local = "2026-01-15 20:00"
    detailed.closes_tz = "Africa/Nairobi"
    db.upsert_job(detailed)

    listing_only = build_job(
        src,
        title="Field Coordinator",
        external_id="F1",
        location="Nairobi",
        apply_url="https://x/f1",
    )
    db.upsert_job(listing_only)
    stored = db.get_job(detailed.identity_key())
    assert stored["closes_at_local"] == "2026-01-15 20:00"
    assert stored["closes_tz"] == "Africa/Nairobi"


def test_numeric_date_locale_controls_ambiguous_dates():
    assert parse_datetime("06/05/2026") is None
    assert (
        parse_datetime("06/05/2026", date_locale="US").isoformat()
        == "2026-06-05T00:00:00+00:00"
    )
    assert (
        parse_datetime("06/05/2026", date_locale="EU").isoformat()
        == "2026-05-06T00:00:00+00:00"
    )


# ---------- #24: cross-source posting fingerprint ----------


def test_posting_fingerprint_collides_for_same_role_across_sources(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    src_a = OrganizationSource(
        id="undp_inspira",
        name="UNDP",
        ats_family="inspira",
        base_url="https://a/",
    )
    src_b = OrganizationSource(
        id="undp_workday",
        name="UNDP",
        ats_family="workday",
        base_url="https://b/",
    )
    common_kwargs = dict(
        title="Programme Officer",
        location="Geneva, Switzerland",
        description="Lead programme delivery and report results to senior management on quarterly basis.",
    )
    a = build_job(src_a, external_id="A1", apply_url="https://a/1", **common_kwargs)
    b = build_job(src_b, external_id="B1", apply_url="https://b/1", **common_kwargs)
    db.upsert_job(a)
    db.upsert_job(b)
    groups = db.find_cross_source_duplicates(status=None)
    assert len(groups) == 1
    sources = {row["source_id"] for row in groups[0]}
    assert sources == {"undp_inspira", "undp_workday"}


def test_posting_fingerprint_returns_none_without_title():
    rec = JobRecord(
        source_id="x",
        org_id="x",
        ats_family="generic",
        title="",
        apply_url="https://x/",
    )
    assert posting_fingerprint(rec) is None


# ---------- #12: FTS5 free-text search ----------


def test_fts_table_created_and_synced(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    src = _source()
    job = build_job(
        src,
        title="Humanitarian Affairs Officer",
        external_id="H1",
        location="Geneva",
        apply_url="https://x/h1",
        description="Coordinate humanitarian response and access negotiations.",
    )
    db.upsert_job(job)
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT rowid FROM jobs_fts WHERE jobs_fts MATCH ?",
            ['{title description location} : "humanitarian"'],
        ).fetchall()
    assert len(rows) == 1
