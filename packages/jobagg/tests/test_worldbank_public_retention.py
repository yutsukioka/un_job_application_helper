"""World Bank list refreshes must retain full public selection criteria."""
from copy import deepcopy
from datetime import UTC, datetime
import json
from pathlib import Path

import pytest

from jobagg.adapters.base import AdapterContext
from jobagg.adapters.csod import CSODAdapter
from jobagg.adapters.worldbank_public import render_public_notice
from jobagg.db import JobDatabase
from jobagg.models import OrganizationSource

FIXTURE = Path(__file__).parent / "fixtures/worldbank_public/38308_jobposting_20260913.json"
SOURCE = OrganizationSource("worldbank_csod", "World Bank", "csod", "https://worldbankgroup.csod.com")
URL = "https://worldbankgroup.csod.com/ux/ats/careersite/1/home/requisition/38308?c=worldbankgroup"


def detail(change=None):
    payload = json.loads(FIXTURE.read_text())
    if change:
        change(payload)
    return render_public_notice(SOURCE, payload, page_url=URL, external_id="38308", expected_title=payload["Title"])


def listing():
    return CSODAdapter(AdapterContext(SOURCE, None)).parse_jobs([{
        "requisitionId": 38308, "title": "E T Consultant (Data Analyst)", "companyApplyUrl": URL,
        "description": "Short description before Selection Criteria.", "department": "Stale sector classification",
        "employmentType": "Old inferred type", "postedDate": "2026-09-01T00:00:00Z", "closeDate": "2026-12-31T00:00:00Z",
    }])[0]


@pytest.fixture
def database(tmp_path):
    db = JobDatabase(tmp_path / "test.sqlite3")
    db.initialize()
    return db


def test_actual_public_notice_clears_list_metadata_and_survives_two_list_updates(database):
    database.upsert_job(listing())
    job = detail()
    database.upsert_job(job)
    for _ in range(2):
        database.upsert_job(listing())
        stored = database.get_job(job.identity_key())
        assert stored["description"] == job.description
        assert "Business Competencies" in stored["description"] and "Recommended Certifications" in stored["description"]
        assert stored["location"] == "Washington, DC,United States"
        assert stored["department"] is None and stored["employment_type"] is None and stored["posted_at"] is None
        assert stored["closes_at"] == "2026-09-24T23:59:00+00:00"
        assert stored["closes_at_local"] == "2026-09-24T23:59" and stored["closes_tz"] == "UTC"
        assert stored["raw"]["sector"] == "Information Technology"
        assert stored["raw"]["grade"] == "EC1"
        assert stored["raw"]["term_duration"] == "1 year 0 months"
        assert stored["raw"]["recruitment_type"] == "Local Recruitment"
        assert stored["raw"]["_worldbank_record_kind"] == "detail"
        assert "_worldbank_listing_observation" not in stored["raw"]["_worldbank_listing_observation"]["raw"]


def test_new_public_date_unknown_clears_previous_utc_instead_of_reviving_it(database):
    database.upsert_job(detail())
    job = detail(lambda p: p.update(Description=p["Description"].replace("9/24/2026 (MM/DD/YYYY) at 11:59pm UTC", "Closing date to be announced")))
    database.upsert_job(job)
    stored = database.get_job(job.identity_key())
    assert stored["closes_at"] is None and stored["closes_at_local"] is None and stored["closes_tz"] is None
    assert "Closing date to be announced" in stored["description"]


@pytest.mark.parametrize("defect", ["source_url", "apply_url", "job_number", "title", "body", "raw_html", "sector", "type", "date", "record_kind"])
def test_public_identity_body_and_normalized_fields_are_all_bound(database, defect):
    database.upsert_job(listing())
    job = detail()
    if defect in {"source_url", "apply_url"}:
        setattr(job, defect, "https://example.org/wrong")
    elif defect == "job_number":
        job.raw["worldbank_public_jobposting"]["Description"] = job.raw["detail_html"].replace("req38308", "req99999")
    elif defect == "title":
        job.title = "Different job"
    elif defect == "body":
        job.description = "Incomplete description without the full selection criteria"
    elif defect == "raw_html":
        job.raw["detail_html"] += "Unobserved condition"
    elif defect == "sector":
        job.raw["sector"] = "Incorrect sector"
    elif defect == "type":
        job.employment_type = "Local Recruitment"
    elif defect == "date":
        job.posted_at = datetime(2026, 9, 10, tzinfo=UTC)
    else:
        job.raw["_worldbank_record_kind"] = "listing"
    with pytest.raises(ValueError):
        database.upsert_job(job)


def test_existing_documents_are_preserved_but_old_complete_claim_is_invalidated(database):
    old = detail()
    old.raw["attachments"] = [{"url": "https://example.org/competencies.pdf", "text": "Original extracted text"}]
    old.raw["attachment_verification"] = {"complete": True, "discovery_complete": True}
    database.upsert_job(old)
    new = detail()
    new.raw["attachment_verification"] = deepcopy(old.raw["attachment_verification"])
    database.upsert_job(new)
    stored = database.get_job(new.identity_key())
    assert stored["raw"]["attachments"] == old.raw["attachments"]
    assert stored["raw"]["attachment_verification"]["complete"] is False


def test_explicit_listing_with_wrong_url_cannot_replace_the_reviewed_notice(database):
    old = detail()
    database.upsert_job(old)
    new = listing()
    new.source_url = "https://example.org/elsewhere"
    with pytest.raises(ValueError, match="listing refresh lacks"):
        database.upsert_job(new)
