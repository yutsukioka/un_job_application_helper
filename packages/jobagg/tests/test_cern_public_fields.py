"""Exact CERN public metadata, clock precision and listing persistence."""

from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest

from jobagg.adapters.base import AdapterContext
from jobagg.adapters.cern_public import public_fields
from jobagg.adapters.static_html import StaticHTMLAdapter, parse_detail_page
from jobagg.db import JobDatabase
from jobagg.http import HttpResponse
from jobagg.models import OrganizationSource
from jobagg.normalize import build_job

FIX = Path(__file__).parent / "fixtures/cern_public"
ID = "te-crg-ic-2026-94-grap"
SOURCE = OrganizationSource("cern_custom_html", "CERN", "custom_html", "https://careers.cern")


def html(identity=ID):
    return (FIX / (identity + "_20260913.html")).read_text()


def job(identity=ID, body=None):
    return parse_detail_page(SOURCE, body if body is not None else html(identity), "https://careers.cern/jobs/" + identity + "/")


def listing(first):
    return build_job(SOURCE, title=first.title + " 24 month contract Hybrid", external_id=first.external_id,
                     apply_url=first.apply_url, department="Old guessed department", employment_type="Old grade",
                     posted_at="2026-12-01T00:00:00Z", closes_at="2026-09-13T00:00:00Z",
                     raw={"href": first.apply_url, "external_id": first.external_id, "title": first.title,
                          "_jobagg_listing_verification": {"observed_at": "2026-09-13T16:00:00Z"}})


@pytest.fixture
def database(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    return db


def test_actual_summer_clock_public_fields_and_separate_duration_start_date():
    parsed = job()
    assert parsed.title == "Instrumentation Engineer"
    assert parsed.location == "Geneva, Switzerland" and parsed.department == "TE"
    assert parsed.closes_at == datetime(2026, 9, 13, 21, 59, tzinfo=UTC)
    assert parsed.closes_at_local == "2026-09-13T23:59" and parsed.closes_tz == "Europe/Zurich"
    assert parsed.posted_at is None and parsed.employment_type is None
    proof = parsed.raw["_cern_public_field_resolution"]
    assert proof["header_fields"]["reference"] == "TE-CRG-IC-2026-94-GRAP"
    assert proof["condition_fields"]["Contract duration (in months)"] == "24"
    assert proof["condition_fields"]["Ideal start date"] == "01/12/2026"
    assert proof["public_department_text"].startswith("The TE department")


def test_actual_winter_clock_uses_geneva_winter_offset_and_grade_is_not_contract():
    parsed = job("th-sp-2026-68-ld")
    assert parsed.closes_at == datetime(2026, 12, 31, 22, 59, tzinfo=UTC)
    assert parsed.raw["grade"] == "6" and parsed.employment_type is None
    assert parsed.raw["_cern_public_field_resolution"]["condition_fields"]["Ideal start date"] == "TBD"
    assert parsed.posted_at is None


def test_no_department_section_remains_unknown_despite_public_reference():
    parsed = job("tsc-cv")
    assert parsed.department is None
    assert parsed.raw["_cern_public_field_resolution"]["header_fields"]["reference"] == "TSC-2027-1/CV"


@pytest.mark.parametrize("replacement,kind", [
    ("Before 25/10/2026 at 02:30 (Geneva Time)", "ambiguous_public_local_time"),
    ("Before 29/03/2026 at 02:30 (Geneva Time)", "nonexistent_public_local_time"),
    ("Before 13/09/2026", "public_calendar_date_only"),
    ("Before 13/09/2026 at midnight (unknown)", "missing_or_unparsed_public_deadline"),
])
def test_ambiguous_missing_and_date_only_deadlines_never_invent_utc(replacement, kind):
    parsed = job(body=html().replace("Before 13/09/2026 at 23:59 (Geneva Time)", replacement))
    assert parsed.closes_at is None
    assert parsed.raw["_cern_public_field_resolution"]["deadline_kind"] == kind


def test_fetch_wrapper_never_revives_listing_dates_type_or_location():
    body = html().replace("Before 13/09/2026 at 23:59 (Geneva Time)", "Deadline not yet specified")
    class HTTP:
        def get(self, url):
            return HttpResponse(url, 200, {}, body)
    adapter = StaticHTMLAdapter(AdapterContext(SOURCE, HTTP()))
    parsed = adapter.fetch_detail_for_listing_item({"href": "https://careers.cern/jobs/" + ID + "/", "external_id": ID,
        "posted_at": "2026-12-01T00:00:00Z", "closes_at": "2026-09-13T00:00:00Z", "employment_type": "24 months"})
    assert parsed.closes_at is None and parsed.posted_at is None and parsed.employment_type is None


def test_fresh_fields_clear_old_guesses_and_two_actual_shape_lists_preserve_observation(database):
    public = job()
    database.upsert_job(listing(public))
    database.upsert_job(public)
    for _ in range(2):
        database.upsert_job(listing(public))
        row = database.get_job(public.identity_key())
        for key in ("title", "description", "location", "department", "employment_type", "closes_at_local", "closes_tz"):
            assert row[key] == getattr(public, key)
        assert row["posted_at"] is None and row["closes_at"] == public.closes_at.isoformat()
        assert row["raw"]["_cern_public_field_resolution"] == public.raw["_cern_public_field_resolution"]
        assert row["raw"]["_cern_listing_observation"]["normalized"]["employment_type"] == "Old grade"


def test_new_public_uncertainty_clears_old_dates_but_retains_document_bytes(database):
    old = job()
    old.raw["attachments"] = [{"sha256": "stored", "text": "Terms of reference"}]
    old.raw["attachment_verification"] = {"complete": True, "discovery_complete": True}
    database.upsert_job(old)
    fresh = job(body=html().replace("Before 13/09/2026 at 23:59 (Geneva Time)", "Before 13/09/2026"))
    database.upsert_job(fresh)
    row = database.get_job(old.identity_key())
    assert row["closes_at"] is None and row["closes_at_local"] == "2026-09-13"
    assert row["raw"]["attachments"] == old.raw["attachments"]
    assert row["raw"]["attachment_verification"]["complete"] is False


@pytest.mark.parametrize("defect", ["source_url", "contract", "body", "reference", "canonical", "proof"])
def test_wrong_identity_body_or_metadata_cannot_keep_verified_public_marker(database, defect):
    parsed = job()
    database.upsert_job(listing(parsed))
    if defect == "source_url":
        parsed.source_url = "https://example.org/wrong"
    elif defect == "contract":
        parsed.employment_type = "24 months"
    elif defect == "body":
        parsed.description = "Unrelated description"
    elif defect == "reference":
        parsed.raw["_cern_public_field_resolution"] = deepcopy(parsed.raw["_cern_public_field_resolution"])
        parsed.raw["_cern_public_field_resolution"]["header_fields"]["reference"] = "UNRELATED"
    elif defect == "canonical":
        parsed.raw["detail_html"] = parsed.raw["detail_html"].replace('rel="canonical" href="https://careers.cern/', 'rel="canonical" href="https://example.org/')
    else:
        parsed.raw["_cern_public_field_resolution"] = None
    with pytest.raises(ValueError):
        database.upsert_job(parsed)


def test_duplicate_header_is_rejected():
    with pytest.raises(ValueError, match="header"):
        public_fields(html() + '<div class="job-offer__header-infos">Another header</div>', "https://careers.cern/jobs/" + ID + "/")
