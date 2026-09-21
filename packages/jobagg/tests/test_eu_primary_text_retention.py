"""Reviewed primary PDF text/provenance survive sparse EU listings together."""
from copy import deepcopy
from dataclasses import fields
from datetime import datetime
import json
from pathlib import Path

import pytest

from jobagg.db import JobDatabase
from jobagg.eu_primary_observation import MARKER, bound_public_text
from jobagg.models import JobRecord, OrganizationSource
from jobagg.normalize import build_job

FIXTURE = Path(__file__).parent / "fixtures/eu_primary_text/retained_pdf_samples_20260913.json"
SOURCE = OrganizationSource("eu_careers_static", "EU Careers", "eu_careers_static", "https://eu-careers.europa.eu")


def sample(index=0):
    return deepcopy(json.loads(FIXTURE.read_text())["jobs"][index])


def record(data):
    values = {field.name: deepcopy(data[field.name]) for field in fields(JobRecord) if field.name in data}
    for key in ("posted_at", "closes_at", "first_seen_at", "last_seen_at"):
        if isinstance(values.get(key), str):
            values[key] = datetime.fromisoformat(values[key])
    return JobRecord(**values)


def listing(data):
    return build_job(SOURCE, external_id=data["external_id"], title=data["title"],
                     source_url=data["source_url"], apply_url=data["source_url"], raw={
                         "parser": "eu_careers_open_vacancies", "external_id": data["external_id"],
                         "href": data["source_url"], "title": data["title"],
                     })


@pytest.mark.parametrize("index", [0, 1])
def test_actual_get_and_post_public_snapshot_binding_without_evidence_files(index):
    data = sample(index)
    assert bound_public_text(data["raw"], data)
    # Validated provenance is portable; DB retention never reopens local files.
    ref = data["raw"][MARKER]["current_reviewed_document"]
    ref["path"] = "/archived/public-document.json"
    assert bound_public_text(data["raw"], data)


@pytest.mark.parametrize("index", [0, 1])
def test_two_listing_refreshes_retain_text_metadata_scope_documents_and_full_proof(tmp_path, index):
    data = sample(index)
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    db.upsert_job(listing(data))
    job = record(data)
    job.raw["attachments"] = [{"content_sha256": "a" * 64, "extracted_text": "Preserved document words"}]
    job.raw["attachment_verification"] = {"complete": True, "discovery_complete": True}
    db.upsert_job(job)
    for _ in range(2):
        incoming = listing(data)
        incoming.raw["current_listing_value"] = "Fresh listing observation"
        db.upsert_job(incoming)
        stored = db.get_job(job.identity_key())
        assert stored["description"] == job.description
        assert stored["raw"]["official_notice_text"] == job.raw["official_notice_text"]
        assert stored["raw"][MARKER] == job.raw[MARKER]
        assert stored["raw"][MARKER]["metadata_completeness_certified"] is False
        assert stored["raw"]["attachments"] == job.raw["attachments"]
        assert stored["raw"]["attachment_verification"]["complete"] is False
        observation = stored["raw"]["_eu_primary_pdf_listing_observation"]
        assert observation["raw"]["current_listing_value"] == "Fresh listing observation"
        assert "_eu_primary_pdf_listing_observation" not in observation["raw"]
        for key in ("title", "location", "department", "employment_type", "apply_url", "source_url"):
            assert stored[key] == data[key]


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(source_id="other_source"),
    lambda d: d.update(source_url=d["source_url"] + "-another"),
    lambda d: d.update(apply_url="https://example.org/notice.pdf"),
    lambda d: d.update(title="Different public vacancy"),
    lambda d: d.update(description=d["description"] + "Changed body"),
    lambda d: d["raw"].update(official_notice_text="Short listing teaser"),
    lambda d: d["raw"][MARKER].update(metadata_completeness_certified=True),
    lambda d: d["raw"][MARKER].update(whole_job_complete=True),
    lambda d: d["raw"][MARKER].update(job_key="eu_careers_static:another"),
    lambda d: d["raw"][MARKER]["retained_normalized_fields"].update(department="Other department"),
    lambda d: d["raw"][MARKER]["primary_document_snapshot"].update(content_sha256="b" * 64),
    lambda d: d["raw"][MARKER]["primary_document_snapshot"].update(text_sha256="b" * 64),
    lambda d: d["raw"][MARKER]["primary_document_snapshot"]["units"][0].update(text="Other page"),
    lambda d: d["raw"][MARKER]["primary_document_snapshot"]["units"].reverse(),
    lambda d: d["raw"][MARKER]["primary_document_snapshot"]["retrieval"].update(method="POST"),
    lambda d: d["raw"][MARKER]["primary_document_snapshot"]["retrieval"]["phase"].update(job_id="another"),
    lambda d: d["raw"][MARKER]["primary_document_snapshot"]["retrieval"].update(finished_at="2000-01-01T00:00:00+00:00"),
    lambda d: d["raw"][MARKER]["current_reviewed_document"].update(sha256="invalid"),
    lambda d: d["raw"][MARKER]["visual_scope"].update(job_key="eu_careers_static:another"),
    lambda d: d["raw"][MARKER]["visual_scope"].update(pages_actually_viewed=[999]),
    lambda d: d["raw"][MARKER]["visual_scope"].update(content_sha256="b" * 64),
])
def test_mutated_identity_body_metadata_or_portable_provenance_rejected(mutation):
    data = sample()
    mutation(data)
    assert not bound_public_text(data["raw"], data)


def test_invalid_incoming_or_listing_url_is_rejected_by_real_merge(tmp_path):
    data = sample()
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    db.upsert_job(listing(data))
    broken = record(data)
    broken.raw[MARKER]["primary_document_snapshot"]["units"][0]["text"] = "Missing source page"
    with pytest.raises(ValueError, match="source/body/provenance"):
        db.upsert_job(broken)
    db.upsert_job(record(data))
    sparse = listing(data)
    sparse.raw["href"] = "https://example.org/unrelated"
    with pytest.raises(ValueError, match="source/identity"):
        db.upsert_job(sparse)


def test_fresh_full_detail_replaces_spacing_observation_without_inheriting_old_proof(tmp_path):
    data = sample()
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    db.upsert_job(listing(data))
    db.upsert_job(record(data))
    newer = record(data)
    newer.raw.pop(MARKER)
    newer.description += " A newly published substantive requirement."
    newer.raw["official_notice_text"] += " A newly published substantive requirement."
    newer.raw["detail_html"] = "<article>Fresh independent full detail representation.</article>"
    db.upsert_job(newer)
    stored = db.get_job(newer.identity_key())
    assert stored["description"] == newer.description
    assert MARKER not in stored["raw"]
