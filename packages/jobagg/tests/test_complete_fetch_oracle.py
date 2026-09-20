"""Regressions observed by the full-content fetch audit; no live network."""
import json
from types import SimpleNamespace

import pytest

from jobagg.adapters.base import AdapterContext
from jobagg.adapters.oracle_hcm import OracleHCMAdapter
from jobagg.db import JobDatabase
from jobagg.models import OrganizationSource
from jobagg.normalize import build_job, clean_text
from jobagg.pipelines.sync_source import _listing_payload_satisfies_detail


def source():
    return OrganizationSource(
        id="audit_oracle", name="Audit Oracle", ats_family="oracle_hcm",
        base_url="https://example.org", extra={"site_number": "CX_1", "listing_payload_is_detail_complete": True},
    )


@pytest.mark.parametrize("already_truncated", [False, True])
def test_oracle_two_listing_refreshes_preserve_or_recover_raw_full_body(tmp_path, already_truncated):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    org = source()
    summary = "This is the current public listing summary for this role, which exceeds the previous completeness threshold."
    body = "<p>Responsibilities: " + "Oversee programme delivery, staffing and oversight. " * 30 + "</p>"
    qualifications = "<p>Qualifications: Advanced degree and seven years of relevant experience.</p>"
    full = clean_text("\n\n".join((summary, body, qualifications)))
    initial = build_job(org, title="Officer", external_id="123", apply_url="/job/123",
        description=summary if already_truncated else full,
        raw={"source_priority": "oracle_hcm_ce", "ShortDescription": summary,
             "ExternalDescriptionStr": body, "ExternalQualificationsStr": qualifications})
    db.upsert_job(initial)
    for location in ("Rome", "Nairobi"):
        listing = build_job(org, title="Officer", external_id="123", apply_url="/job/123",
            location=location, description=summary,
            raw={"source_priority": "oracle_hcm_ce", "ShortDescription": summary})
        db.upsert_job(listing)
        stored = db.get_job(initial.identity_key())
        assert stored["description"] == full
        assert stored["location"] == location
        assert stored["raw"]["ExternalQualificationsStr"] == qualifications

    revised = "Responsibilities: A substantively revised and shorter full job description. " * 3
    db.upsert_job(build_job(org, title="Officer", external_id="123", apply_url="/job/123",
        description=revised, raw={"source_priority": "oracle_hcm_ce", "ExternalDescriptionStr": revised}))
    assert db.get_job(initial.identity_key())["description"] == revised.strip()


def test_oracle_listing_teaser_does_not_skip_required_detail_request():
    org = source()
    teaser = "A vacancy summary describes an opportunity without the full terms of reference or complete requirements."
    job = build_job(org, title="Officer", external_id="123", apply_url="/job/123", description=teaser,
        raw={"source_priority": "oracle_hcm_ce", "ShortDescription": teaser})
    assert not _listing_payload_satisfies_detail(org, job)
    job.raw["ExternalDescriptionStr"] = teaser + " Responsibilities and qualifications follow."
    assert _listing_payload_satisfies_detail(org, job)


@pytest.mark.parametrize("returned_ids,expected", [(["999"], None), (["999", "123"], "123"), (["123", "123"], None)])
def test_oracle_detail_requires_one_matching_response_identity(returned_ids, expected):
    payload = {"items": [{"Id": value, "Title": "Officer", "ExternalDescriptionStr": "Duties and qualifications: " + "Programme oversight. " * 20} for value in returned_ids]}
    response = SimpleNamespace(text=json.dumps(payload), status_code=200,
        headers={"Content-Type": "application/json"}, url="https://example.org/api")
    response.json = lambda: payload
    http = SimpleNamespace(get=lambda *args, **kwargs: response)
    adapter = OracleHCMAdapter(AdapterContext(source=source(), http=http))
    result = adapter.fetch_detail_for_listing_item({"Id": "123"})
    assert (result.external_id if result else None) == expected
    if result:
        assert result.raw["oracle_detail_response_verified"] is True
