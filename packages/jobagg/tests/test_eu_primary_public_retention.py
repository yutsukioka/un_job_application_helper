"""Official EUIPO/EUDA PDF fields replace board guesses and survive summaries."""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from jobagg.adapters.eu_primary_public import render_public_notice
from jobagg.db import JobDatabase
from jobagg.models import OrganizationSource
from jobagg.normalize import build_job

FIXTURES = Path(__file__).parent / "fixtures/eu_primary_public"
SOURCE = OrganizationSource("eu_careers_static", "EU Careers", "static_html", "https://eu-careers.europa.eu")


def inputs(identity):
    data = json.loads((FIXTURES / (identity + ".json")).read_text())
    data.pop("provenance")
    return data


def listing(data):
    return build_job(SOURCE, title="Board title", external_id=data["external_id"],
                     apply_url=data["summary_url"], source_url=data["summary_url"],
                     posted_at="2026-08-01", closes_at="2026-09-22", department="Board domain", employment_type="AD9",
                     raw={"parser": "eu_careers_open_vacancies", "external_id": data["external_id"], "href": data["summary_url"]})


def row(job):
    return json.loads(json.dumps(asdict(job), default=lambda value: value.isoformat()))


@pytest.mark.parametrize("identity", ["ext-26-40-ad-9-cpd", "ext-26-41-ad-9-boa", "ca202604"])
def test_actual_pdf_fields_replace_old_guesses_and_survive_two_sparse_lists(tmp_path, identity):
    data = inputs(identity)
    job = render_public_notice(SOURCE, **data)
    assert JobDatabase._eu_bound_public_detail(job.raw, row(job))
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    old = listing(data)
    old.raw["attachments"] = [{"content_sha256": "c" * 64, "extracted_text": "Prior document text"}]
    old.raw["attachment_verification"] = {"complete": True, "discovery_complete": True}
    db.upsert_job(old)
    db.upsert_job(job)
    for _ in range(2):
        db.upsert_job(listing(data))
        stored = db.get_job(job.identity_key())
        assert stored["posted_at"] is None
        assert stored["closes_at"] == (job.closes_at.isoformat() if job.closes_at else None)
        for key in ("title", "department", "employment_type", "location", "source_url", "apply_url", "description", "closes_at_local", "closes_tz"):
            assert stored[key] == getattr(job, key)
        for key in job.raw:
            assert stored["raw"][key] == job.raw[key]
        assert stored["raw"]["attachments"] == old.raw["attachments"]
        assert stored["raw"]["attachment_verification"]["complete"] is False
        assert "_eu_listing_observation" not in stored["raw"]["_eu_listing_observation"]["raw"]


@pytest.mark.parametrize("field", ["title", "source_url", "apply_url", "department", "employment_type", "description", "posted_at", "closes_at"])
def test_canonical_public_field_or_source_tamper_rejected(field):
    job = render_public_notice(SOURCE, **inputs("ext-26-40-ad-9-cpd"))
    value = datetime(2026, 9, 1, tzinfo=timezone.utc) if field.endswith("_at") else "Changed public field"
    setattr(job, field, value)
    assert not JobDatabase._eu_bound_public_detail(job.raw, row(job))


@pytest.mark.parametrize("defect", ["page", "document", "links", "grade", "resolution"])
def test_raw_page_provenance_and_public_field_tamper_rejected(defect):
    job = render_public_notice(SOURCE, **inputs("ca202604"))
    if defect == "page":
        job.raw["public_primary_page_units"][0]["text"] += "Changed public page."
    elif defect == "document":
        job.raw["public_primary_document_proof"]["content_sha256"] = "b" * 64
    elif defect == "links":
        job.raw["required_attachment_urls"] = []
    elif defect == "grade":
        job.raw["grade"] = "AD15"
    else:
        job.raw["_eu_official_field_resolution"]["utc_resolved"] = False
    assert not JobDatabase._eu_bound_public_detail(job.raw, row(job))


def test_wrong_listing_source_cannot_refresh_retained_public_pdf(tmp_path):
    data = inputs("ca202604")
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    db.upsert_job(listing(data))
    db.upsert_job(render_public_notice(SOURCE, **data))
    bad = listing(data)
    bad.raw["href"] = "https://example.org/wrong"
    with pytest.raises(ValueError, match="source URL"):
        db.upsert_job(bad)


def test_new_source_bound_pdf_observation_replaces_prior_conflict_claims(tmp_path):
    data = inputs("ca202604")
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    db.upsert_job(listing(data))
    first = render_public_notice(SOURCE, **data, source_conflicts=[{"field": "footnote", "claim": "Original source ambiguity"}])
    db.upsert_job(first)
    fresh = render_public_notice(SOURCE, **deepcopy(data))
    db.upsert_job(fresh)
    stored = db.get_job(fresh.identity_key())
    assert stored["raw"]["source_content_conflicts"] == []
    assert stored["raw"]["reviewed_source_content_conflicts"] == []
