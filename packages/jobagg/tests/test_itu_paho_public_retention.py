"""Actual public headers must survive a subsequent sparse listing refresh."""

from datetime import UTC, datetime
import json
from pathlib import Path

import pytest

from jobagg.adapters.base import AdapterContext
from jobagg.adapters.itu_public import public_fields
from jobagg.adapters.successfactors_rmk import SuccessFactorsRMKAdapter
from jobagg.adapters.workday import WorkdayAdapter
from jobagg.db import JobDatabase
from jobagg.http import HttpResponse
from jobagg.models import OrganizationSource
from jobagg.normalize import build_job

FIXTURE = Path(__file__).parent / "fixtures"


def itu(identity="1364924755", html=None):
    provenance = json.loads((FIXTURE / "successfactors/itu_public_20260913.provenance.json").read_text())
    proof = next(row for row in provenance["fixtures"] if row["external_id"] == identity)
    html = html if html is not None else (FIXTURE / "successfactors" / proof["fixture"]).read_text()
    class HTTP:
        def get(self, url):
            assert url == proof["source_url"]
            return HttpResponse(url, 200, {}, html)
    source = OrganizationSource("itu_successfactors", "ITU", "successfactors_rmk", "https://jobs.itu.int")
    adapter = SuccessFactorsRMKAdapter(AdapterContext(source, HTTP()))
    return adapter.fetch_detail_for_listing_item({"detail_url": proof["source_url"], "listing_html": "Observed listing tile"})


def paho(index=0, change=None):
    payload = json.loads((FIXTURE / "workday/paho_public_20260913.json").read_text())[index]
    if change:
        change(payload["jobPostingInfo"])
    return WorkdayAdapter(AdapterContext(OrganizationSource(
        "paho_workday", "PAHO", "workday", "https://paho.wd5.myworkdayjobs.com/pahocareers"), None)).parse_detail(payload)


def listing(first):
    source = OrganizationSource(first.source_id, "Public source", first.ats_family, first.source_url)
    raw = ({"listing_html": "Observed listing tile", "title": first.title, "detail_url": first.apply_url}
           if first.source_id == "itu_successfactors" else
           {"title": first.title, "externalPath": first.apply_url.split("/pahocareers", 1)[1], "bulletFields": [first.external_id]})
    raw["_jobagg_listing_verification"] = {"observed_at": "2026-09-13T16:00:00Z"}
    return build_job(source, title=first.title, external_id=first.external_id, apply_url=first.apply_url,
                     department="Old listing category", employment_type="Old grade", posted_at="2026-09-01T00:00:00Z",
                     closes_at="2026-12-31T00:00:00Z", raw=raw)


@pytest.fixture
def database(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    return db


def test_itu_english_exact_fields_and_midnight_claims():
    job = itu()
    proof = job.raw["_itu_public_field_resolution"]
    assert job.department == "Regional Offices"
    assert job.employment_type == "Consultant"
    assert job.location == "Home Based, Remote"
    assert job.raw["grade"] is None and job.raw["position_number"] is None
    assert job.raw["itu_public_fields"]["Grade"] == "[[PositionGrade]]"
    assert job.raw["itu_public_fields"]["Position number"] == "[[positionNumber]]"
    assert job.posted_at == datetime(2026, 8, 18, tzinfo=UTC)
    assert proof["publisher_closes_at_utc"] == "2026-12-31T23:00:00+00:00"
    assert job.closes_at is None and job.closes_at_local == "2026-12-31"
    assert job.closes_tz == "Europe/Zurich" and proof["utc_resolved"] is False


def test_itu_french_fields_preserve_exact_language_and_grade():
    job = itu("1362530455")
    assert job.department == "Bureaux régionaux"
    assert job.employment_type == "Durée determinée"
    assert job.location == "Panama City, Panama"
    assert job.raw["grade"] == "P3"
    assert job.raw["position_number"] == "TD26R/P3/663"
    assert job.closes_at_local == "2026-09-30"
    assert job.closes_at is None


def test_unrelated_prose_or_missing_header_is_not_metadata():
    assert public_fields("<p>The department's next application deadline is 31 December 2026.</p>") is None


@pytest.mark.parametrize("provider", ["itu", "paho"])
def test_fresh_detail_replaces_listing_and_two_lists_preserve_full_observation(database, provider):
    original = itu("1361435855") if provider == "itu" else paho()
    # ITU's blank public Department must clear the older classification.
    if provider == "itu":
        assert original.department is None
    old = listing(original)
    database.upsert_job(old)
    database.upsert_job(original)
    for _ in range(2):
        database.upsert_job(listing(original))
        stored = database.get_job(original.identity_key())
        for key in ("title", "description", "location", "department", "employment_type", "closes_at_local", "closes_tz"):
            assert stored[key] == getattr(original, key)
        for key in ("posted_at", "closes_at"):
            instant = getattr(original, key)
            assert stored[key] == (instant.isoformat() if instant else None)
        for key, value in original.raw.items():
            if key != "_jobagg_listing_verification":
                assert stored["raw"][key] == value
        observation = stored["raw"][f"_{provider}_listing_observation"]
        assert observation["normalized"]["employment_type"] == "Old grade"
        assert observation["observed_at"] == "2026-09-13T16:00:00Z"


@pytest.mark.parametrize("provider", ["itu", "paho"])
@pytest.mark.parametrize("defect", ["identity", "body", "contract", "proof", "host", "source_url"])
def test_invalid_incoming_fields_fail_closed(database, provider, defect):
    job = itu() if provider == "itu" else paho()
    database.upsert_job(listing(job))
    if defect == "identity":
        job.external_id = "wrong"
        # Match the stored job key for the merge under test.
        current = database.get_job(listing(itu() if provider == "itu" else paho()).identity_key())
        with pytest.raises(ValueError, match="matching source and identity"):
            JobDatabase._merge_labelled_public_observation(database, job, current, current["raw"])
        return
    if defect == "body":
        job.description = "Unrelated body"
    elif defect == "contract":
        job.employment_type = "Invented contract"
    elif defect == "proof":
        job.raw[f"_{provider}_public_field_resolution"] = None
    elif defect == "source_url":
        job.source_url = "https://example.org/wrong-job"
    else:
        if provider == "itu":
            job.raw["detail_url"] = job.apply_url.replace("jobs.itu.int", "example.org")
        else:
            job.raw["jobPostingInfo"]["externalUrl"] = job.apply_url.replace("paho.wd5.myworkdayjobs.com", "example.org")
    with pytest.raises(ValueError, match="source/body binding"):
        database.upsert_job(job)


def test_new_paho_unknown_header_clears_previous_normalized_values(database):
    original = paho()
    database.upsert_job(original)
    changed = paho(change=lambda info: info.update(jobDescription="<p>Current notice omitted its metadata header.</p>"))
    database.upsert_job(changed)
    stored = database.get_job(original.identity_key())
    assert stored["department"] is None and stored["employment_type"] is None
    assert stored["closes_at"] is None and stored["posted_at"] is None
    assert stored["raw"]["jobPostingInfo"]["timeType"]


@pytest.mark.parametrize("index", range(20))
def test_all_twenty_public_paho_payloads_bind_their_observed_posting_slug(database, index):
    job = paho(index)
    database.upsert_job(listing(job))
    database.upsert_job(job)
    database.upsert_job(listing(job))
    stored = database.get_job(job.identity_key())
    assert stored["apply_url"] == job.raw["jobPostingInfo"]["externalUrl"]
    assert stored["employment_type"] == job.employment_type


@pytest.mark.parametrize("provider", ["itu", "paho"])
def test_changed_detail_preserves_documents_and_invalidates_old_certificate(database, provider):
    first = itu() if provider == "itu" else paho()
    first.raw["attachments"] = [{"url": "https://example.org/tor.pdf", "text": "Stored terms", "sha256": "stored"}]
    first.raw["attachment_verification"] = {"complete": True, "discovery_complete": True}
    database.upsert_job(first)
    if provider == "itu":
        changed = itu(html=first.raw["detail_html"].replace("Regional Offices", "New public department"))
    else:
        changed = paho(change=lambda info: info.update(jobDescription=info["jobDescription"] + "<p>A new public condition.</p>"))
    database.upsert_job(changed)
    stored = database.get_job(first.identity_key())
    assert stored["raw"]["attachments"] == first.raw["attachments"]
    assert stored["raw"]["attachment_verification"]["complete"] is False
    assert stored["description"] == changed.description
