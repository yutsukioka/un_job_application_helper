import sqlite3
from datetime import datetime, timezone
import pytest
from jobagg.db import JobDatabase
from jobagg.models import OrganizationSource, SourceRunDiagnostics, SyncResult
from jobagg.normalize import build_job
from jobagg.pipelines.consolidation import consolidate_bundle_databases

BODY = "Responsibilities include programme delivery and reporting. Qualifications include relevant education and extensive professional experience."


def frame(observed=True, **updates):
    return dict(
        source_id="source",
        observed_in_latest_listing=observed,
        observed_at=datetime.now(timezone.utc).isoformat(),
        capture="retained-official-listing.json",
        **updates,
    )


def consolidate_row(tmp_path, raw, *, inconclusive=False, enabled=True):
    source = OrganizationSource("source", "Source", "test", "https://example.org", enabled=enabled)
    db = JobDatabase(tmp_path / "source_jobs.sqlite3")
    db.initialize()
    job = build_job(
        source,
        title="Programme Officer",
        external_id="1",
        apply_url="https://example.org/job/1",
        description=BODY,
        closes_at="2099-12-31",
        raw=raw,
    )
    db.upsert_job(job)
    db.add_source_run(
        SyncResult(
            source_id="source",
            fetched=1,
            diagnostics=SourceRunDiagnostics(
                source_id="source",
                health_status="issue" if inconclusive else "ok",
                run_classification="inconclusive" if inconclusive else "ok",
                publishability_classification="source_inconclusive" if inconclusive else "ok",
                pagination_complete=not inconclusive,
            ),
        )
    )
    result = consolidate_bundle_databases(output_dir=tmp_path)
    with sqlite3.connect(result.db_path) as conn:
        conn.row_factory = sqlite3.Row
        return dict(conn.execute("select * from jobs").fetchone())


def test_retained_unseen_row_stays_visible_without_claiming_source_current(tmp_path):
    row = consolidate_row(
        tmp_path,
        {
            "_jobagg_listing_verification": frame(False),
            "attachment_verification": {"complete": True, "discovery_complete": True},
        },
    )
    assert row["status"] == "open" and row["description"] == BODY
    assert row["source_listed_current"] == row["trusted_current"] == row["application_ready"] == 0


def test_individually_verified_job_can_be_ready_despite_incomplete_source_inventory(tmp_path):
    row = consolidate_row(
        tmp_path,
        {
            "_jobagg_listing_verification": frame(True),
            "attachment_verification": {"complete": True, "discovery_complete": True},
        },
        inconclusive=True,
    )
    assert row["source_freshness_status"] == "inconclusive"
    assert row["source_listed_current"] == row["trusted_current"] == row["application_ready"] == 1


def test_disabled_frame_preserves_record_without_current_claim(tmp_path):
    row = consolidate_row(
        tmp_path,
        {"_jobagg_listing_verification": frame(False, reason="disabled_source")},
        enabled=False,
    )
    assert row["status"] == "open"
    assert row["source_listed_current"] == row["application_ready"] == 0


@pytest.mark.parametrize(
    "verification",
    [
        {"complete": True},
        {"complete": True, "discovery_complete": False},
        None,
        {"complete": False, "discovery_complete": True},
    ],
)
def test_attachment_claim_requires_explicit_complete_discovery_when_metadata_exists(
    tmp_path, verification
):
    row = consolidate_row(
        tmp_path, {"_jobagg_listing_verification": frame(), "attachment_verification": verification}
    )
    assert row["source_listed_current"] == 1
    assert row["application_ready"] == 0


@pytest.mark.parametrize(
    "verification",
    [
        None,
        {},
        {
            "observed_in_latest_listing": True,
            "source_id": "other",
            "observed_at": "2026-09-10T00:00:00Z",
        },
        {"observed_in_latest_listing": True, "source_id": "source", "observed_at": "invalid"},
    ],
)
def test_malformed_or_wrong_source_listing_frame_cannot_certify_current(tmp_path, verification):
    row = consolidate_row(tmp_path, {"_jobagg_listing_verification": verification})
    assert row["status"] == "open" and row["source_listed_current"] == 0


def test_legacy_absent_metadata_preserves_existing_behavior(tmp_path):
    row = consolidate_row(tmp_path, {})
    assert row["source_listed_current"] == row["application_ready"] == 1


def test_fresh_listing_requires_attachment_discovery_before_readiness(tmp_path):
    row = consolidate_row(tmp_path, {"_jobagg_listing_verification": frame()})
    assert row["source_listed_current"] == 1
    assert row["application_ready"] == 0


def test_conflicting_attachment_complete_flag_cannot_hide_unresolved_documents(tmp_path):
    row = consolidate_row(
        tmp_path,
        {
            "_jobagg_listing_verification": frame(),
            "attachment_verification": {
                "complete": True,
                "discovery_complete": True,
                "unresolved_attachment_ids": ["required-tor"],
            },
        },
    )
    assert row["application_ready"] == 0
