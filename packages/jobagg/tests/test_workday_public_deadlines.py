import copy
import json
from pathlib import Path

import pytest

from jobagg.adapters.base import AdapterContext
from jobagg.adapters.workday import WorkdayAdapter
from jobagg.models import OrganizationSource
from jobagg.normalize import clean_text

FIXTURES = Path(__file__).parent / "fixtures/workday"


def payload(source_id, identity):
    return json.loads((FIXTURES / f"{source_id}_{identity}_20260913.json").read_text())


def parse(source_id, data):
    source = OrganizationSource(source_id, source_id, "workday", "https://example.invalid")
    return WorkdayAdapter(AdapterContext(source, None)).parse_detail(data)


@pytest.mark.parametrize(("identity", "utc", "local", "zone"), [
    ("JR121484", "2026-10-31T03:59:00+00:00", "2026-10-30T23:59", "UTC-04:00"),
    ("JR126619", "2026-09-21T18:14:00+00:00", "2026-09-21T23:59", "UTC+05:45"),
    ("JR126646", "2026-09-23T23:59:00+00:00", "2026-09-23T23:59", "UTC"),
])
def test_exact_fresh_public_deadlines_use_published_offset(identity, utc, local, zone):
    data = payload("wfp_workday", identity)
    original = copy.deepcopy(data)
    job = parse("wfp_workday", data)
    assert job.closes_at.isoformat() == utc
    assert job.closes_at_local == local
    assert job.closes_tz == zone
    assert job.description == clean_text(data["jobPostingInfo"]["jobDescription"])
    assert {k: v for k, v in job.raw.items() if k not in {"_workday_deadline_resolution", "_workday_public_date_precision"}} == original
    assert data == original  # Parsing may not mutate the captured provider object.


@pytest.mark.parametrize(("source_id", "identity", "local", "api_date"), [
    ("unhcr_workday", "JR2668710", "2026-09-20", "2026-09-21"),
    ("wto_workday", "JR103985", "2026-12-31", "2027-01-01"),
])
def test_date_only_public_notice_does_not_publish_next_day_api_midnight(source_id, identity, local, api_date):
    job = parse(source_id, payload(source_id, identity))
    assert job.closes_at is None
    assert job.closes_at_local == local
    assert job.closes_tz is None
    assert job.raw["jobPostingInfo"]["endDate"] == api_date
    assert job.raw["_workday_deadline_resolution"]["utc_resolved"] is False


@pytest.mark.parametrize(("identity", "claimed_utc", "api_date"), [
    ("JR109486", "2026-03-14T22:59:00+00:00", "2026-12-15"),
    ("JR123148", "2026-05-29T22:59:00+00:00", "2926-05-30"),
])
def test_materially_conflicting_provider_claims_are_retained_as_unresolved(identity, claimed_utc, api_date):
    job = parse("wfp_workday", payload("wfp_workday", identity))
    resolution = job.raw["_workday_deadline_resolution"]
    assert job.closes_at is None
    assert resolution["kind"] == "conflicting_public_deadline_claims"
    assert resolution["public_claimed_utc"] == claimed_utc
    assert resolution["api_end_date"] == api_date


def test_ambiguous_london_gmt_label_does_not_guess_seasonal_offset():
    job = parse("wfp_workday", payload("wfp_workday", "JR126262"))
    assert job.closes_at is None
    assert job.closes_at_local == "2026-09-20T23:59"
    assert job.closes_tz is None
    assert "GMT United Kingdom Time (London)" in job.raw["_workday_deadline_resolution"]["public_timezone_unresolved"]


@pytest.mark.parametrize("body", [
    "This role reviews reports submitted by 14 March 2026. DEADLINE FOR APPLICATIONS 20 September 2026-23:59-GMT+01:00",
    "DEADLINE FOR APPLICATIONS 31 February 2026-23:59-GMT+01:00",
    "DEADLINE FOR APPLICATIONS 20 September 2026-25:59-GMT+01:00",
    "DEADLINE FOR APPLICATIONS 20 September 2026-23:59-GMT+01:99",
])
def test_missing_malformed_and_prose_dates_never_fall_back_to_api_midnight(body):
    data = payload("wfp_workday", "JR121484")
    data["jobPostingInfo"]["jobDescription"] = body
    job = parse("wfp_workday", data)
    assert job.closes_at is None
    assert job.raw["jobPostingInfo"]["endDate"] == "2026-10-31"


def test_other_workday_sources_and_listing_payloads_keep_existing_behavior():
    data = payload("wfp_workday", "JR121484")
    job = parse("other_workday", data)
    assert job.closes_at.isoformat() == "2026-10-31T00:00:00+00:00"
    assert "_workday_deadline_resolution" not in job.raw
    source = OrganizationSource("wfp_workday", "WFP", "workday", "https://example.invalid")
    listing = WorkdayAdapter(AdapterContext(source, None)).parse_listing_item({"title": "Role", "jobReqId": "test"})
    assert "_workday_deadline_resolution" not in listing.raw
