"""A source-bound public detail must survive two later sparse/template lists."""
from copy import deepcopy
from datetime import UTC, datetime
import gzip
import json
from pathlib import Path

import pytest

from jobagg.adapters.base import AdapterContext
from jobagg.adapters.legacy_public import render_public_notice
from jobagg.adapters.ebrd_public import apply_public_fields as apply_ebrd_public_fields
from jobagg.adapters.successfactors_rmk import SuccessFactorsRMKAdapter
from jobagg.db import JobDatabase
from jobagg.http import HttpResponse
from jobagg.models import OrganizationSource
from jobagg.normalize import build_job

FIXTURES = Path(__file__).parent / "fixtures/successfactors"


def detail(kind, *, text=None):
    if kind == "ebrd":
        from test_ebrd_public_fields import html, original
        body = text if text is not None else html()
        job = apply_ebrd_public_fields(original(body=body), body)
        job.raw.pop("attachments", None)
        return job
    if kind == "idb":
        proof = json.loads((FIXTURES / "idb_public_20260913/manifest.json").read_text())["records"][0]
        text = text if text is not None else gzip.decompress((FIXTURES / "idb_public_20260913" / proof["fixture"]).read_bytes()).decode()
        source = OrganizationSource("idb_successfactors", "IDB", "successfactors_rmk", "https://jobs.iadb.org")

        class HTTP:
            def get(self, url):
                assert url == proof["source_url"]
                return HttpResponse(url, 200, {}, text)

        return SuccessFactorsRMKAdapter(AdapterContext(source, HTTP())).fetch_detail_for_listing_item(
            {"detail_url": proof["source_url"], "title": proof["title"]})
    proof = json.loads((FIXTURES / "legacy_public_first3_20260913.json").read_text())[0 if kind == "icc" else 2]
    source = OrganizationSource(proof["source_id"], kind, "successfactors_legacy", proof["page_url"])
    return render_public_notice(source, text if text is not None else proof["html"], page_url=proof["page_url"],
                                external_id=proof["external_id"], expected_title=proof["title"])


def listing(job):
    if job.source_id == "idb_successfactors":
        raw = {"parser": "browser_inventory", "external_id": job.external_id,
               "detail_url": job.source_url, "title": job.title, "_jobagg_browser_listing_only": True}
    elif job.source_id == "ebrd_successfactors":
        raw = {"detail_url": job.source_url, "listing_html": "<a>Observed EBRD listing</a>", "external_id": job.external_id}
    else:
        raw = {"parser": "successfactors_xml", "reqid": job.external_id, "jobtitle": job.title,
               "jobdescription": "XML template [[division]]", "detail_html": "<p>XML template [[division]]</p>"}
    raw["_jobagg_listing_verification"] = {"observed_at": "2026-09-13T17:00:00Z"}
    return build_job(OrganizationSource(job.source_id, "Public source", job.ats_family, job.source_url),
                     title=job.title, external_id=job.external_id, apply_url=job.apply_url, source_url=job.source_url,
                     location="Stale list location", department="Stale listing category", employment_type="Old grade",
                     posted_at="2026-09-01T00:00:00Z", closes_at="2026-12-31T00:00:00Z",
                     description=raw.get("jobdescription"), raw=raw)


@pytest.fixture
def database(tmp_path):
    result = JobDatabase(tmp_path / "test.sqlite3")
    result.initialize()
    return result


@pytest.mark.parametrize("kind", ["idb", "icc", "afdb", "ebrd"])
def test_fresh_detail_clears_old_fields_and_two_lists_retain_the_full_observation(database, kind):
    job = detail(kind)
    original_list = listing(job)
    database.upsert_job(original_list)
    database.upsert_job(job)
    for _ in range(2):
        database.upsert_job(listing(job))
        stored = database.get_job(job.identity_key())
        for field in ("title", "description", "source_url", "apply_url", "location", "department", "employment_type", "closes_at_local", "closes_tz"):
            assert stored[field] == getattr(job, field)
        assert stored["posted_at"] is None and stored["closes_at"] is None
        assert stored["raw"]["detail_html"] == job.raw["detail_html"]
        key = {"idb": "_idb_listing_observation", "ebrd": "_ebrd_listing_observation"}.get(kind, "_legacy_listing_observation")
        assert key not in stored["raw"][key]["raw"]
        if kind in {"icc", "afdb"}:
            snapshot = stored["raw"]["_legacy_xml_listing_snapshot"]
            assert snapshot["jobdescription"] == "XML template [[division]]"
            assert snapshot["detail_html"] == "<p>XML template [[division]]</p>"


@pytest.mark.parametrize("kind", ["idb", "icc", "afdb", "ebrd"])
@pytest.mark.parametrize("defect", ["source_url", "apply_url", "external_id", "title", "body", "public_fields", "utc", "timezone", "company"])
def test_tampered_detail_cannot_pass_the_public_capture_binding(database, kind, defect):
    job = detail(kind)
    database.upsert_job(listing(job))
    if defect in {"source_url", "apply_url"}:
        setattr(job, defect, "https://example.org/elsewhere")
    elif defect == "external_id":
        job.raw["detail_url"] = job.raw["detail_url"].replace(job.external_id, "999999")
    elif defect == "title":
        job.title = "Different public title"
    elif defect == "body":
        job.description += " Added unobserved condition."
    elif defect == "public_fields":
        job.department = "Wrong public department"
    elif defect == "utc":
        job.closes_at = datetime(2026, 12, 31, tzinfo=UTC)
    elif defect == "timezone":
        job.closes_tz = "America/New_York"
    else:
        key = {"idb": "_idb_public_field_resolution", "ebrd": "_ebrd_public_field_resolution"}.get(kind, "_legacy_public_field_resolution")
        job.raw[key]["company"] = "Unrelated public employer"
    with pytest.raises(ValueError):
        database.upsert_job(job)


@pytest.mark.parametrize("kind", ["idb", "icc", "afdb", "ebrd"])
def test_new_detail_retains_documents_and_xml_snapshot_but_invalidates_incoming_old_certificate(database, kind):
    first = detail(kind)
    first.raw["attachments"] = [{"url": "https://example.org/terms.pdf", "text": "Previously captured terms", "sha256": "old"}]
    first.raw["attachment_verification"] = {"complete": True, "discovery_complete": True}
    if kind in {"icc", "afdb"}:
        first.raw["_legacy_xml_listing_snapshot"] = {"reqid": first.external_id, "jobdescription": "Original XML", "listing_capture": {"sha256": "original"}}
    database.upsert_job(first)
    replacement = detail(kind)
    replacement.raw["attachment_verification"] = deepcopy(first.raw["attachment_verification"])
    database.upsert_job(replacement)
    stored = database.get_job(first.identity_key())
    assert stored["raw"]["attachments"] == first.raw["attachments"]
    assert stored["raw"]["attachment_verification"]["complete"] is False
    assert stored["raw"]["attachment_verification"]["discovery_complete"] is False
    if kind in {"icc", "afdb"}:
        assert stored["raw"]["_legacy_xml_listing_snapshot"] == first.raw["_legacy_xml_listing_snapshot"]


@pytest.mark.parametrize("kind", ["idb", "icc", "afdb", "ebrd"])
def test_importer_invalidated_certificate_is_preserved_exactly(database, kind):
    job = detail(kind)
    database.upsert_job(listing(job))
    proof = {"complete": False, "discovery_complete": False, "invalidated_reason": "reviewed_capture_import_requires_current_attachment_reverification"}
    job.raw["attachment_verification"] = deepcopy(proof)
    database.upsert_job(job)
    assert database.get_job(job.identity_key())["raw"]["attachment_verification"] == proof


@pytest.mark.parametrize("kind", ["idb", "icc", "afdb", "ebrd"])
@pytest.mark.parametrize("field", ["source_url", "apply_url"])
def test_later_listing_cannot_replace_the_bound_public_notice_with_wrong_url(database, kind, field):
    job = detail(kind)
    database.upsert_job(job)
    incoming = listing(job)
    setattr(incoming, field, "https://example.org/wrong-notice")
    with pytest.raises(ValueError, match="listing refresh lacks"):
        database.upsert_job(incoming)
    assert database.get_job(job.identity_key())["source_url"] == job.source_url
