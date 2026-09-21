"""A cached detailed vacancy must survive a later incomplete listing refresh."""
from datetime import datetime, timezone

import pytest

from jobagg.adapters.base import AdapterContext, JobAdapter, register_adapter
from jobagg.adapters.oracle_hcm import OracleHCMAdapter
from jobagg.adapters.successfactors_rmk import SuccessFactorsRMKAdapter
from jobagg.db import JobDatabase
from jobagg.http import JobAggHTTPClient
from jobagg.models import OrganizationSource
from jobagg.normalize import build_job
from jobagg.pipelines.sync_source import sync_source_with_selective_details
from jobagg.robots import RobotsPolicy


FULL_TEXT = "Responsibilities and qualifications. " * 30


def job(source, raw, *, closes="2026-12-31T23:00:00Z", posted="2026-08-27T02:00:00Z",
        description=FULL_TEXT):
    return build_job(source, title="Internship: Internal Oversight", external_id="1347780957",
                     apply_url="https://example.org/job/1347780957", description=description,
                     posted_at=posted, closes_at=closes, raw=raw)


@pytest.mark.parametrize("family,detail_raw,listing_raw", [
    ("successfactors", {"detail_html": "<article>Full detailed vacancy</article>"},
     {"listing_html": "<tr>Old public listing metadata</tr>"}),
    ("successfactors", {"listing_html": "<tr>Original listing</tr>", "parser": "successfactors_detail"},
     {"listing_html": "<tr>Old public listing metadata</tr>", "parser": "table_listing"}),
    ("oracle_hcm", {"Id": "1347780957", "ExternalDescriptionStr": FULL_TEXT},
     {"Id": "1347780957", "ShortDescriptionStr": "Listing summary"}),
])
def test_repeated_listing_upserts_preserve_authoritative_detail_dates(
    tmp_path, family, detail_raw, listing_raw
):
    source = OrganizationSource("example", "Example", family, "https://example.org")
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    original = job(source, detail_raw)
    original.first_seen_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    original.closes_at_local = "2027-01-01 00:00:00"
    original.closes_tz = "Europe/Paris"
    db.upsert_job(original)
    for _ in range(2):
        listing = job(source, listing_raw, closes="2026-06-30", posted="2026-08-27", description=None)
        listing.closes_at_local = "2026-06-30 00:00:00"
        listing.closes_tz = "UTC"
        db.upsert_job(listing)
    stored = db.get_job(original.identity_key())
    assert stored["posted_at"] == "2026-08-27T02:00:00+00:00"
    assert stored["closes_at"] == "2026-12-31T23:00:00+00:00"
    assert stored["closes_at_local"] == "2027-01-01 00:00:00"
    assert stored["closes_tz"] == "Europe/Paris"
    assert stored["first_seen_at"] == "2026-01-01T00:00:00+00:00"
    assert FULL_TEXT.strip() in stored["description"]
    if detail_raw.get("parser"):
        assert stored["raw"]["parser"] == "table_listing"
        assert stored["raw"]["_jobagg_retained_detail_parser"] == "successfactors_detail"


def test_fresh_detail_changes_dates_and_null_fields_keep_existing_fallback(tmp_path):
    source = OrganizationSource("example", "Example", "successfactors", "https://example.org")
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    original = job(source, {"listing_html": "original", "parser": "successfactors_detail"})
    db.upsert_job(original)
    refreshed = job(source, {"listing_html": "new", "parser": "successfactors_detail"},
                    closes="2027-01-15T12:00:00Z", posted="2026-09-10T09:00:00Z")
    db.upsert_job(refreshed)
    stored = db.get_job(original.identity_key())
    assert stored["closes_at"] == "2027-01-15T12:00:00+00:00"
    assert stored["posted_at"] == "2026-09-10T09:00:00+00:00"
    db.upsert_job(job(source, {"detail_html": "Fresh detail with dates absent"}, closes=None, posted=None))
    stored = db.get_job(original.identity_key())
    assert stored["closes_at"] == "2027-01-15T12:00:00+00:00"
    assert stored["posted_at"] == "2026-09-10T09:00:00+00:00"


def test_long_listing_does_not_gain_detail_precedence(tmp_path):
    source = OrganizationSource("example", "Example", "successfactors", "https://example.org")
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    first = job(source, {"listing_html": FULL_TEXT})
    db.upsert_job(first)
    db.upsert_job(job(source, {"listing_html": FULL_TEXT}, closes="2027-02-01", posted="2026-09-01"))
    stored = db.get_job(first.identity_key())
    assert stored["closes_at"] == "2027-02-01T00:00:00+00:00"
    assert stored["posted_at"] == "2026-09-01T00:00:00+00:00"
    assert "_jobagg_retained_detail_parser" not in stored["raw"]


def test_listing_can_fill_a_date_absent_from_stored_detail(tmp_path):
    source = OrganizationSource("example", "Example", "custom_html", "https://example.org")
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    first = job(source, {"detail_html": "Full description"}, closes=None, posted=None)
    db.upsert_job(first)
    db.upsert_job(job(source, {"listing_html": "Explicit listing dates"}))
    stored = db.get_job(first.identity_key())
    assert stored["closes_at"] == "2026-12-31T23:00:00+00:00"
    assert stored["posted_at"] == "2026-08-27T02:00:00+00:00"


def test_oracle_raw_dates_stay_with_full_detail_and_list_observation_is_separate(tmp_path):
    source = OrganizationSource("example", "Example", "oracle_hcm", "https://example.org")
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    original = job(source, {
        "Id": "1347780957", "ExternalDescriptionStr": FULL_TEXT,
        "ExternalPostedStartDate": "2026-08-27T02:00:00Z",
        "ExternalPostedEndDate": "2026-12-31T23:00:00Z",
    })
    db.upsert_job(original)
    db.upsert_job(job(source, {
        "Id": "1347780957", "ShortDescriptionStr": "Listing summary",
        "PostedDate": "2026-08-27", "ExternalPostedStartDate": "2026-08-27",
        "ExternalPostedEndDate": "2026-06-30", "PostingEndDate": "2026-06-30",
    }, closes="2026-06-30", posted="2026-08-27"))
    stored = db.get_job(original.identity_key())
    raw = stored["raw"]
    assert raw["ExternalPostedStartDate"] == "2026-08-27T02:00:00Z"
    assert raw["ExternalPostedEndDate"] == "2026-12-31T23:00:00Z"
    assert "PostedDate" not in raw
    assert "PostingEndDate" not in raw
    assert raw["_jobagg_listing_date_observation"]["raw_oracle_dates"]["PostedDate"] == "2026-08-27"
    assert raw["_jobagg_listing_date_observation"]["closes_at"] == "2026-06-30T00:00:00+00:00"
    refreshed = job(source, {
        "Id": "1347780957", "ExternalDescriptionStr": "Updated full public detail. " * 20,
        "PostedDate": "2026-09-11T10:00:00Z", "ExternalPostedEndDate": "2027-01-01T23:00:00Z",
    }, posted="2026-09-11T10:00:00Z", closes="2027-01-01T23:00:00Z")
    db.upsert_job(refreshed)
    stored = db.get_job(original.identity_key())
    assert stored["posted_at"] == "2026-09-11T10:00:00+00:00"
    assert stored["closes_at"] == "2027-01-01T23:00:00+00:00"
    assert stored["raw"]["PostedDate"] == "2026-09-11T10:00:00Z"
    assert stored["raw"]["ExternalPostedEndDate"] == "2027-01-01T23:00:00Z"


@pytest.mark.parametrize("metadata,expected_posted,expected_closes", [
    ('<meta itemprop="datePosted" content="2026-08-27T02:00:00Z">'
     '<meta itemprop="validThrough" content="2026-12-31T23:00:00Z">',
     "2026-08-27T02:00:00+00:00", "2026-12-31T23:00:00+00:00"),
    ('<p>Application deadline: 31 December 2026</p>',
     "2026-08-27T00:00:00+00:00", "2026-12-31T00:00:00+00:00"),
    ('', "2026-08-27T00:00:00+00:00", "2026-06-30T00:00:00+00:00"),
])
def test_fresh_rmk_detail_prefers_public_dates_before_listing_fallback(
    monkeypatch, metadata, expected_posted, expected_closes
):
    source = OrganizationSource("example", "Example", "successfactors_rmk", "https://example.org")
    adapter = SuccessFactorsRMKAdapter(AdapterContext(source=source, http=JobAggHTTPClient()))
    monkeypatch.setattr(adapter, "ensure_allowed", lambda url: None)
    monkeypatch.setattr(adapter, "fetch_text", lambda url: metadata +
                        f'<h1>Public vacancy</h1><div class="jobdescription">{FULL_TEXT}</div>')
    detailed = adapter.fetch_detail_for_listing_item({
        "detail_url": "https://example.org/job/1347780957/",
        "unifiedStandardStart": "2026-08-27", "unifiedStandardEnd": "2026-06-30",
    })
    assert detailed.posted_at.isoformat() == expected_posted
    assert detailed.closes_at.isoformat() == expected_closes


@register_adapter
class DatePrecedenceFixtureAdapter(JobAdapter):
    family = "date_precedence_fixture"

    def fetch_jobs(self):
        return [job(self.source, {"listing_html": "Old listing dates"},
                    closes="2099-06-30", posted="2026-08-27", description=None)]

    def fetch_detail_for_listing_item(self, item):
        return job(self.source, {"detail_html": "Complete detailed vacancy"},
                   closes=self.source.extra.get("detail_closes", "2099-12-31T23:00:00Z"))


def test_selective_sync_keeps_detail_dates_when_next_cycle_uses_cached_body(tmp_path):
    source = OrganizationSource("example", "Example", DatePrecedenceFixtureAdapter.family,
                                "https://example.org")
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    policy = RobotsPolicy(honor_robots_txt=False, min_delay_seconds=0)
    first = sync_source_with_selective_details(source, db=db, policy=policy,
                                               refresh_all_details=True, close_missing=False)
    second = sync_source_with_selective_details(source, db=db, policy=policy, close_missing=False)
    assert first.diagnostics.detail_succeeded == 1
    assert second.diagnostics.detail_attempted == 0
    stored = db.get_job("example:1347780957")
    assert stored["closes_at"] == "2099-12-31T23:00:00+00:00"
    assert stored["posted_at"] == "2026-08-27T02:00:00+00:00"
    assert db.get_detail_backlog("example:1347780957")["detail_status"] == "complete"
    source.extra["detail_closes"] = "2100-01-15T23:00:00Z"
    refreshed = sync_source_with_selective_details(source, db=db, policy=policy,
                                                   refresh_all_details=True, close_missing=False)
    assert refreshed.diagnostics.detail_succeeded == 1
    assert db.get_job("example:1347780957")["closes_at"] == "2100-01-15T23:00:00+00:00"


def test_date_provenance_change_invalidates_attachment_certificate_but_same_metadata_is_stable(tmp_path):
    source = OrganizationSource("example", "Example", "successfactors", "https://example.org")
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    verified = {"complete": True, "discovery_complete": True}
    first = job(source, {"detail_html": "Full public body", "attachment_verification": verified})
    db.upsert_job(first)
    list_raw = {"listing_html": "Public listing with old dates"}
    db.upsert_job(job(source, list_raw, closes="2026-06-30", posted="2026-08-27", description=None))
    changed = db.get_job(first.identity_key())
    assert changed["raw"]["attachment_verification"]["complete"] is False
    assert changed["raw"]["attachment_verification"]["discovery_complete"] is False
    observation = changed["raw"]["_jobagg_listing_date_observation"]
    # Simulate the document worker rebinding the unchanged current raw inputs.
    rebound = job(source, {**changed["raw"], "attachment_verification": verified})
    db.upsert_job(rebound)
    db.upsert_job(job(source, list_raw, closes="2026-06-30", posted="2026-08-27", description=None))
    unchanged = db.get_job(first.identity_key())
    assert unchanged["raw"]["_jobagg_listing_date_observation"] == observation
    assert unchanged["raw"]["attachment_verification"] == verified
    # A subsequently changed listing date is a new discovery input even though
    # the authoritative detailed date is still preserved.
    db.upsert_job(job(source, list_raw, closes="2026-07-01", posted="2026-08-27", description=None))
    changed_again = db.get_job(first.identity_key())
    assert changed_again["closes_at"] == "2026-12-31T23:00:00+00:00"
    assert changed_again["raw"]["attachment_verification"]["complete"] is False


def test_new_legacy_detail_provenance_invalidates_old_certificate(tmp_path):
    source = OrganizationSource("example", "Example", "successfactors", "https://example.org")
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    first = job(source, {"listing_html": "original", "parser": "successfactors_detail",
                         "attachment_verification": {"complete": True, "discovery_complete": True}})
    db.upsert_job(first)
    db.upsert_job(job(source, {"listing_html": "new", "parser": "listing"}, description=None))
    stored = db.get_job(first.identity_key())
    assert stored["raw"]["_jobagg_retained_detail_parser"] == "successfactors_detail"
    assert stored["raw"]["attachment_verification"]["complete"] is False
    assert stored["raw"]["attachment_verification"]["discovery_complete"] is False


@pytest.mark.parametrize("full_start,expected", [
    ("2026-08-27T02:00:00Z", "2026-08-27T02:00:00+00:00"),
    (None, "2026-08-27T00:00:00+00:00"),
])
def test_oracle_full_posted_timestamp_precedes_date_only_listing_fallback(full_start, expected):
    source = OrganizationSource("example", "Example", "oracle_hcm", "https://example.org")
    adapter = OracleHCMAdapter(AdapterContext(source=source, http=JobAggHTTPClient()))
    item = {"Id": "1347780957", "Title": "Public vacancy", "PostedDate": "2026-08-27",
            "ExternalPostedStartDate": full_start, "ExternalPostedEndDate": "2026-12-31T23:00:00Z",
            "ExternalDescriptionStr": FULL_TEXT}
    parsed = adapter.parse_jobs({"items": [item]})[0]
    assert parsed.posted_at.isoformat() == expected
    assert parsed.closes_at.isoformat() == "2026-12-31T23:00:00+00:00"
