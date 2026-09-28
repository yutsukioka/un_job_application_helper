"""Actual EBRD public metadata with source-bound calendar precision."""
from dataclasses import asdict
from datetime import UTC, datetime
import json
from pathlib import Path

import pytest

from jobagg.adapters.base import AdapterContext
from jobagg.adapters.ebrd_public import apply_public_fields, public_fields
from jobagg.adapters.successfactors_rmk import SuccessFactorsRMKAdapter, _detail_description, _detail_title
from jobagg.models import OrganizationSource
from jobagg.normalize import build_job

FIXTURES = Path(__file__).parent / "fixtures/ebrd_public"
PROVENANCE = {row["external_id"]: row for row in json.loads((FIXTURES / "provenance.json").read_text())}
SOURCE = OrganizationSource("ebrd_successfactors", "EBRD", "successfactors_rmk", "https://jobs.ebrd.com")


def html(identity="1436245533"):
    return (FIXTURES / (identity + "_20260913.html")).read_text()


def original(identity="1436245533", body=None):
    body = body or html(identity)
    url = PROVENANCE[identity]["url"]
    return build_job(SOURCE, external_id=identity, title=_detail_title(body), apply_url=url, source_url=url,
                     description=_detail_description(body), posted_at=datetime(2026, 9, 11, tzinfo=UTC),
                     closes_at=datetime(2026, 9, 18, 23, tzinfo=UTC), employment_type="Old inferred grade",
                     raw={"detail_url": url, "detail_html": body, "parser": "successfactors_detail", "attachments": [{"text": "Keep original bytes"}]})


@pytest.mark.parametrize("identity,department,contract,deadline", [
    ("1436245533", "Banking Countries of Operations", "Intern", "2026-09-18"),
    ("1436231133", "Environment & Sustainability", "Regular", "2026-09-27"),
    ("1434506233", "Operations & Service Management", "Regular and Short-Term contract of 12 months", "2026-09-14"),
])
def test_actual_headers_supply_fields_without_manufacturing_public_clock(identity, department, contract, deadline):
    job = original(identity)
    before = asdict(job)
    apply_public_fields(job, html(identity))
    resolution = job.raw["_ebrd_public_field_resolution"]
    assert job.department == department and job.employment_type == contract
    assert job.posted_at is None and job.closes_at is None and job.closes_tz is None
    assert job.closes_at_local == deadline
    assert "UTC" in resolution["publisher_date_claims"]["datePosted"]
    assert "UTC" in resolution["publisher_date_claims"]["validThrough"]
    assert resolution["posting_time_resolved"] is False and resolution["utc_resolved"] is False
    assert job.description == before["description"] and job.raw["attachments"] == before["raw"]["attachments"]
    assert resolution["public_fields"]["Requisition ID"] != job.external_id
    assert resolution["external_id"] == job.external_id and resolution["canonical_url"] == job.apply_url


@pytest.mark.parametrize("field", ["source_url", "apply_url", "external_id", "title"])
def test_source_url_identity_title_and_canonical_notice_must_all_match(field):
    job = original()
    setattr(job, field, "https://example.org/wrong" if field.endswith("url") else "999999")
    with pytest.raises(ValueError, match="canonical URL"):
        apply_public_fields(job, html())


def test_canonical_url_cannot_bind_a_different_public_notice():
    body = html().replace('rel="canonical" href="' + PROVENANCE["1436245533"]["url"] + '"', 'rel="canonical" href="https://jobs.ebrd.com/job/Other/999999/"')
    # The fixture's attribute order must be observed, rather than assumed.
    if body == html():
        body = html().replace(PROVENANCE["1436245533"]["url"], "https://jobs.ebrd.com/job/Other/999999/")
    with pytest.raises(ValueError, match="canonical URL"):
        apply_public_fields(original(), body)


def test_invalid_or_more_precise_unrecognized_public_date_does_not_use_publisher_timestamp():
    for value in ("31/02/2026", "18/09/2026 at 17:00", "To be confirmed"):
        body = html().replace("18/09/2026", value)
        job = apply_public_fields(original(), body)
        assert job.closes_at is None and job.closes_at_local is None and job.closes_tz is None
        assert job.raw["_ebrd_public_field_resolution"]["publisher_date_claims"]["validThrough"] == "Fri Sep 18 23:00:00 UTC 2026"
        assert job.raw["_ebrd_public_field_resolution"]["deadline_unknown_reason"] == "unrecognized_public_deadline_label"


def test_required_public_label_missing_is_an_explicit_parser_failure():
    with pytest.raises(ValueError, match="metadata table"):
        public_fields(html().replace("Posting End Date", "Different label"))


def test_full_adapter_path_applies_resolution_after_generic_date_fallback(monkeypatch):
    adapter = SuccessFactorsRMKAdapter(AdapterContext(SOURCE, None))
    body = html()
    monkeypatch.setattr(adapter, "fetch_text", lambda url: body)
    job = adapter.fetch_detail_for_listing_item({"external_id": "1436245533", "title": "Intern", "detail_url": PROVENANCE["1436245533"]["url"],
                                                 "location": "Old place", "posted_at": "2026-09-11T00:00:00Z", "closes_at": "2026-09-18T23:00:00Z"})
    assert job.department == "Banking Countries of Operations" and job.employment_type == "Intern"
    assert job.location == "Bucharest, RO"
    assert job.posted_at is None and job.closes_at is None and job.closes_at_local == "2026-09-18"
    assert job.description == original().description
