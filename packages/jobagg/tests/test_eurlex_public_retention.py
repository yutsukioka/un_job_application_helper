"""The complete public EUR-Lex notice survives sparse EU summary refreshes."""
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import pytest

from jobagg.adapters.eurlex_public import render_public_notice
from jobagg.db import JobDatabase
from jobagg.models import OrganizationSource
from jobagg.normalize import build_job

SOURCE = OrganizationSource("eu_careers_static", "EU Careers", "static_html", "https://eu-careers.europa.eu")
IDENTITY = "com-2026-20124"
SUMMARY = "https://eu-careers.europa.eu/en/job-opportunities/executive-director/com-2026-20124"
OFFICIAL = "https://eur-lex.europa.eu/eli/C/2026/4370/oj"


def html():
    return (Path(__file__).parent / "fixtures/eurlex_public/com_2026_20124_20260913.html").read_text()


def detail(body=None):
    return render_public_notice(SOURCE, body if body is not None else html(), page_url=OFFICIAL,
                                external_id=IDENTITY, summary_url=SUMMARY, expected_title="Executive Director")


def listing():
    return build_job(SOURCE, title="Executive Director", external_id=IDENTITY, apply_url=SUMMARY, source_url=SUMMARY,
                     location="Lisbon", department="Transport", employment_type="AD14",
                     posted_at="2026-09-10T00:00:00Z", closes_at="2026-09-29T00:00:00Z",
                     description="Public board summary", raw={"parser": "eu_careers_open_vacancies", "external_id": IDENTITY,
                                                              "_jobagg_listing_verification": {"observed_at": "2026-09-13T17:00:00Z"}})


@pytest.fixture
def database(tmp_path):
    value = JobDatabase(tmp_path / "jobs.sqlite3")
    value.initialize()
    return value


def test_public_notice_replaces_grade_as_contract_and_two_summaries_preserve_all_fields(database):
    database.upsert_job(listing())
    job = detail()
    expected = asdict(job)
    database.upsert_job(job)
    for _ in range(2):
        database.upsert_job(listing())
        stored = database.get_job(job.identity_key())
        for key in ("title", "description", "location", "department", "employment_type", "source_url", "apply_url", "closes_at_local", "closes_tz"):
            assert stored[key] == expected[key]
        assert stored["employment_type"] == "Temporary Agent"
        assert stored["raw"]["grade"] == "AD 14"
        assert stored["posted_at"] is None
        assert stored["closes_at"] == "2026-09-29T10:00:00+00:00"
        for key, value in expected["raw"].items():
            assert stored["raw"][key] == value
        assert "_eu_listing_observation" not in stored["raw"]["_eu_listing_observation"]["raw"]


@pytest.mark.parametrize("defect", ["description", "title", "location", "department", "employment_type", "source_url", "apply_url", "posted_at", "closes_at", "closes_at_local", "closes_tz", "grade", "institution", "required_urls", "body_html", "raw_external_id", "resolution_reference", "resolution_body_hash"])
def test_modified_public_claim_or_identity_is_rejected(database, defect):
    database.upsert_job(listing())
    job = detail()
    if defect in {"description", "title", "location", "department", "employment_type"}:
        setattr(job, defect, "Unobserved public field")
    elif defect in {"source_url", "apply_url"}:
        setattr(job, defect, "https://example.org/unrelated")
    elif defect in {"posted_at", "closes_at"}:
        setattr(job, defect, datetime(2026, 9, 1, tzinfo=UTC))
    elif defect == "closes_at_local":
        job.closes_at_local = "2026-09-29T00:00:00"
    elif defect == "closes_tz":
        job.closes_tz = "UTC"
    elif defect in {"grade", "institution"}:
        job.raw[defect] = "Unobserved public label"
    elif defect == "required_urls":
        job.raw["required_attachment_urls"] = []
    elif defect == "body_html":
        job.raw["detail_html"] = job.raw["detail_html"].replace("We are", "Changed section")
    elif defect == "raw_external_id":
        job.raw["external_id"] = "com-2026-99999"
    elif defect == "resolution_reference":
        job.raw["_eu_official_field_resolution"]["public_reference"] = "COM/2026/99999"
    else:
        job.raw["_eu_official_field_resolution"]["description_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="not bound"):
        database.upsert_job(job)


def test_future_unqualified_public_deadline_clears_prior_instant_instead_of_reviving_it(database):
    database.upsert_job(detail())
    body = html().replace("12.00 noon Brussels time", "12.00 noon local time")
    assert body != html()
    job = detail(body)
    assert job.closes_at is None and job.closes_at_local is None and job.closes_tz is None
    database.upsert_job(job)
    for _ in range(2):
        database.upsert_job(listing())
        stored = database.get_job(job.identity_key())
        assert all(stored[k] is None for k in ("posted_at", "closes_at", "closes_at_local", "closes_tz"))
        assert "12.00 noon local time" in stored["description"]


def test_changed_public_body_keeps_document_bytes_but_invalidates_discovery(database):
    old = detail()
    old.raw["attachments"] = [{"content_sha256": "a" * 64, "retained_bytes": "historical evidence"}]
    old.raw["attachment_verification"] = {"complete": True, "discovery_complete": True}
    database.upsert_job(old)
    body = html().replace("We are", "We are", 1).replace("maritime", "MARITIME", 1)
    assert body != html()
    updated = detail(body)
    database.upsert_job(updated)
    for _ in range(2):
        database.upsert_job(listing())
        stored = database.get_job(old.identity_key())
        assert stored["raw"]["attachments"] == old.raw["attachments"]
        assert stored["raw"]["attachment_verification"]["complete"] is False
        assert stored["raw"]["attachment_verification"]["discovery_complete"] is False
        assert stored["description"] == updated.description


def test_binding_rejects_foreign_source_even_when_body_and_urls_match():
    job = detail()
    row = asdict(job)
    row["source_id"] = "foreign_source"
    assert JobDatabase._eurlex_bound_public_detail(deepcopy(job.raw), row) is False
