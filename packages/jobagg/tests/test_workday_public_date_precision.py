from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime
import json
from pathlib import Path

import pytest

from jobagg.adapters.base import AdapterContext
from jobagg.adapters.workday import WorkdayAdapter
from jobagg.adapters.workday_precision import MARKER, precision_resolution
from jobagg.db import JobDatabase
from jobagg.models import OrganizationSource

SAMPLES = json.loads((Path(__file__).parent / "fixtures/workday/public_date_precision_20260913.json").read_text())


def adapter(sample):
    return WorkdayAdapter(AdapterContext(OrganizationSource(
        sample["source_id"], sample["source_id"], "workday", sample["base_url"]), None))


def row(job):
    result = asdict(job)
    result["raw_json"] = json.dumps(result.pop("raw"), ensure_ascii=False)
    for key in ("posted_at", "closes_at", "first_seen_at", "last_seen_at"):
        if result[key] is not None:
            result[key] = result[key].isoformat()
    return result


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda s: s["source_id"] + ":" + s["payload"]["jobPostingInfo"]["jobReqId"])
def test_exact_current_source_dates_and_repeated_listing_retention(sample):
    a = adapter(sample)
    job = a.parse_detail(deepcopy(sample["payload"]))
    assert job.raw[MARKER] == sample["resolution"]
    assert job.posted_at is None
    assert JobDatabase._workday_precision_bound_public_detail(job.raw, row(job))
    previous = deepcopy(job)
    previous.posted_at = datetime(2026, 9, 2, tzinfo=UTC)
    previous.closes_at = datetime(2026, 9, 17, tzinfo=UTC)
    previous.raw.pop(MARKER)
    previous.raw["attachments"] = [{"url": "https://example.org/incorporated-policy.pdf", "text": "retained"}]
    previous.raw["attachment_verification"] = {"complete": True, "discovery_complete": True}
    db = object.__new__(JobDatabase)
    db._merge_existing_detail_fields(job, row(previous))
    assert job.posted_at is None
    assert job.raw["jobPostingInfo"] == sample["payload"]["jobPostingInfo"]
    assert job.raw["attachments"] == previous.raw["attachments"]
    assert job.raw["attachment_verification"]["complete"]
    assert job.raw["_workday_previous_date_observation"]["dates"]["posted_at"] == previous.posted_at.isoformat()
    for _ in range(2):
        listing = a.parse_listing_item(deepcopy(sample["listing"]))
        db._merge_existing_detail_fields(listing, row(job))
        assert listing.description == job.description
        assert listing.raw[MARKER] == job.raw[MARKER]
        assert listing.raw["attachments"] == job.raw["attachments"]
        assert listing.posted_at is None
        assert listing.closes_at == job.closes_at
        assert listing.closes_at_local == job.closes_at_local
        job = listing


def test_globalfund_displayed_day_does_not_use_following_api_midnight():
    s = next(s for s in SAMPLES if s["source_id"] == "globalfund_workday")
    j = adapter(s).parse_detail(s["payload"])
    assert j.closes_at is None and j.closes_at_local == "2026-09-16" and j.closes_tz is None
    assert j.raw[MARKER]["deadline"]["api_end_date"] == "2026-09-17"


def test_tbi_material_conflict_preserves_both_claims_and_empty_is_unknown():
    samples = [s for s in SAMPLES if s["source_id"] == "tbi_workday"]
    conflict = next(s for s in samples if s["payload"]["jobPostingInfo"]["jobReqId"] == "JR002273")
    j = adapter(conflict).parse_detail(conflict["payload"])
    assert j.closes_at is None and j.closes_at_local == "2026-09-21"
    assert j.raw[MARKER]["deadline"]["kind"] == "conflicting_public_deadline_claims"
    assert j.raw[MARKER]["deadline"]["api_end_date"] == "2026-09-26"
    empty = next(s for s in samples if s is not conflict)
    j = adapter(empty).parse_detail(empty["payload"])
    assert j.closes_at is None and j.closes_at_local is None


@pytest.mark.parametrize("field,value", [("source_url", "https://example.org/wrong"), ("apply_url", "https://example.org/wrong"),
    ("external_id", "JR4666"), ("description", "Other body"), ("posted_at", "2026-09-02T00:00:00+00:00"),
    ("closes_at", "2026-09-17T00:00:00+00:00"), ("closes_at_local", "2026-09-17"), ("title", "Other title")])
def test_source_body_and_date_tampering_rejected(field, value):
    s = SAMPLES[0]
    j = adapter(s).parse_detail(deepcopy(s["payload"]))
    r = row(j)
    r[field] = value
    assert not JobDatabase._workday_precision_bound_public_detail(j.raw, r)


@pytest.mark.parametrize("mutation", ["payload", "marker", "credentials", "port", "query", "fragment"])
def test_portable_proof_cannot_be_reused_with_another_capture(mutation):
    s = SAMPLES[0]
    j = adapter(s).parse_detail(deepcopy(s["payload"]))
    if mutation == "payload":
        j.raw["jobPostingInfo"]["endDate"] = "2030-01-01"
    elif mutation == "marker":
        j.raw[MARKER]["posting"]["kind"] = "explicit_provider_instant"
    else:
        u = j.raw["jobPostingInfo"]["externalUrl"]
        u = {"credentials": u.replace("https://", "https://person@"), "port": u.replace(".com/", ".com:8443/"),
             "query": u + "?other=job", "fragment": u + "#other"}[mutation]
        j.raw["jobPostingInfo"]["externalUrl"] = u
    assert not JobDatabase._workday_precision_bound_public_detail(j.raw, row(j))


def test_explicit_provider_timestamp_is_retained_separately_from_relative_display():
    s = SAMPLES[0]
    info = deepcopy(s["payload"]["jobPostingInfo"])
    info["startDate"] = "2026-09-02T12:30:00+03:00"
    r = precision_resolution(s["source_id"], info)
    assert r["posting"]["posted_at"] == "2026-09-02T09:30:00+00:00"
    assert r["posting"]["api_value"] == info["startDate"]
    info["startDate"] = "2026-09-02T12:30:00"
    assert precision_resolution(s["source_id"], info)["posting"]["posted_at"] is None


def test_new_detail_clears_old_calendar_and_replaces_entire_observation():
    s = next(s for s in SAMPLES if s["source_id"] == "globalfund_workday")
    old = adapter(s).parse_detail(deepcopy(s["payload"]))
    new_payload = deepcopy(s["payload"])
    new_payload["jobPostingInfo"]["jobDescription"] = "<p>Complete revised description without a published deadline.</p>"
    new_payload["jobPostingInfo"].pop("endDate")
    old.raw["attachment_verification"] = {"complete": True, "discovery_complete": True}
    new = adapter(s).parse_detail(new_payload)
    object.__new__(JobDatabase)._merge_existing_detail_fields(new, row(old))
    assert new.closes_at is None and new.closes_at_local is None
    assert new.description != old.description
    assert not new.raw["attachment_verification"]["complete"]


def test_free_text_mention_is_not_a_terminal_label():
    s = next(s for s in SAMPLES if s["source_id"] == "globalfund_workday")
    info = deepcopy(s["payload"]["jobPostingInfo"])
    info["jobDescription"] = "<p>Policy mentions Job Posting End Date 16 September 2026</p>"
    assert precision_resolution(s["source_id"], info)["deadline"]["closes_at_local"] is None
