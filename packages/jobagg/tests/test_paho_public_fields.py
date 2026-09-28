import copy
import json
from pathlib import Path

import pytest

from jobagg.adapters.base import AdapterContext
from jobagg.adapters.workday import WorkdayAdapter
from jobagg.models import OrganizationSource
from jobagg.normalize import clean_text

PAYLOADS = json.loads((Path(__file__).parent / "fixtures/workday/paho_public_20260913.json").read_text())
BY_ID = {p["jobPostingInfo"]["jobReqId"]: p for p in PAYLOADS}


def parse(data, source_id="paho_workday"):
    source = OrganizationSource(source_id, "PAHO", "workday", "https://paho.wd5.myworkdayjobs.com/pahocareers")
    return WorkdayAdapter(AdapterContext(source, None)).parse_detail(data)


@pytest.mark.parametrize("data", PAYLOADS, ids=list(BY_ID))
def test_all_twenty_current_public_notices_keep_complete_body_and_coherent_header(data):
    original = copy.deepcopy(data)
    job = parse(data)
    proof = job.raw["_paho_public_field_resolution"]
    fields = proof["public_fields"]
    assert job.description == clean_text(data["jobPostingInfo"]["jobDescription"])
    assert job.employment_type == fields["Contractual Agreement"]
    assert job.employment_type != fields["Schedule"]
    assert job.department == fields["Organization"]
    assert job.location == fields["Primary Location"]
    assert job.posted_at is None
    assert proof["public_posting_calendar_date"]
    assert job.closes_at is not None and proof["utc_resolved"] is True
    assert job.closes_at_local.endswith("T23:59")
    assert data == original
    assert job.raw["jobPostingInfo"] == original["jobPostingInfo"]


@pytest.mark.parametrize(("identity", "utc", "local", "zone"), [
    ("Req-06012", "2026-09-21T03:59:00+00:00", "2026-09-20T23:59", "America/New_York"),
    ("Req-05839", "2027-01-01T04:59:00+00:00", "2026-12-31T23:59", "America/New_York"),
    ("Req-06045", "2026-09-18T05:59:00+00:00", "2026-09-17T23:59", "America/Tegucigalpa"),
    ("Req-06030", "2026-09-17T03:59:00+00:00", "2026-09-16T23:59", "America/Caracas"),
    ("Req-06031", "2026-09-16T02:59:00+00:00", "2026-09-15T23:59", "America/Montevideo"),
    ("Req-05946", "2026-09-15T02:59:00+00:00", "2026-09-14T23:59", "America/Paramaribo"),
])
def test_public_named_timezone_with_summer_winter_and_spanish_calendar(identity, utc, local, zone):
    job = parse(BY_ID[identity])
    assert job.closes_at.isoformat() == utc
    assert job.closes_at_local == local
    assert job.closes_tz == zone


def test_visible_notice_date_and_reposting_claim_remain_distinct():
    job = parse(BY_ID["Req-06012"])
    proof = job.raw["_paho_public_field_resolution"]
    assert proof["public_posting_calendar_date"] == "2026-08-21"
    assert proof["api_start_date"] == "2026-08-31"
    assert job.posted_at is None
    assert job.department == "ITS Information Technology Services"
    assert job.employment_type == "Non-Staff - International PAHO Consultant"


@pytest.mark.parametrize(("old", "new"), [
    ("Eastern Time", "Mystery Time"),
    ("11:59 PM", "13:99 PM"),
    ("September 20, 2026", "February 31, 2026"),
    ("Closing Date:", "Applications mentioned in body:"),
])
def test_unproven_public_deadline_never_revives_date_only_api_midnight(old, new):
    data = copy.deepcopy(BY_ID["Req-06012"])
    data["jobPostingInfo"]["jobDescription"] = data["jobPostingInfo"]["jobDescription"].replace(old, new)
    job = parse(data)
    assert job.closes_at is None
    assert job.raw["jobPostingInfo"]["endDate"] == "2026-09-21"
    assert job.raw["_paho_public_field_resolution"]["utc_resolved"] is False


def test_materially_different_api_date_remains_a_conflict():
    data = copy.deepcopy(BY_ID["Req-06012"])
    data["jobPostingInfo"]["endDate"] = "2027-09-21"
    job = parse(data)
    assert job.closes_at is None
    assert job.closes_at_local == "2026-09-20T23:59"
    assert job.raw["_paho_public_field_resolution"]["kind"] == "conflicting_public_deadline_claims"


def test_paho_policy_does_not_change_other_sources_or_label_listing_as_detail():
    job = parse(BY_ID["Req-06012"], "other_workday")
    assert "_paho_public_field_resolution" not in job.raw
    assert job.employment_type == "Full time"
    assert job.closes_at.isoformat() == "2026-09-21T00:00:00+00:00"
    source = OrganizationSource("paho_workday", "PAHO", "workday", "https://paho.wd5.myworkdayjobs.com/pahocareers")
    listing = WorkdayAdapter(AdapterContext(source, None)).parse_listing_item({"title": "Job", "jobReqId": "Req-06012"})
    assert "_paho_public_field_resolution" not in listing.raw
