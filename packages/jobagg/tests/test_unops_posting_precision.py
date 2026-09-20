"""Actual UNOPS public calendar dates are not UTC instants."""
from datetime import UTC, datetime
from pathlib import Path

import pytest

from jobagg.adapters.avature import AvatureAdapter, _unops_posting_time
from jobagg.adapters.base import AdapterContext
from jobagg.db import JobDatabase
from jobagg.models import OrganizationSource
from jobagg.normalize import build_job

SOURCE = OrganizationSource("unops_avature", "UNOPS", "avature", "https://careers.unops.org")
URL = "https://careers.unops.org/careersmarketplace/JobDetail/Associate-Civil-Engineer-Quantity-Surveyor-Cost-Estimator/4362"
FIXTURE = Path(__file__).parent / "fixtures/unops/4362_20260913.html"


def detail():
    return AvatureAdapter(AdapterContext(SOURCE, None)).parse_detail_html(FIXTURE.read_text(), URL)


def listing(*, bound=True):
    return build_job(SOURCE, external_id="4362", title=detail().title, apply_url=URL, source_url=URL,
                     posted_at="2026-09-14", description=None,
                     raw={"listing_html": "Public listing row", "_detail_url": URL if bound else "https://example.org/4362"})


@pytest.mark.parametrize("public,kind,calendar,expected", [
    ("28-Aug-2026", "public_calendar_date_only", "2026-08-28", None),
    ("2026-08-28", "public_calendar_date_only", "2026-08-28", None),
    ("2026-08-28T12:10:00", "public_clock_timezone_unknown", None, None),
    ("2026-08-28T00:15:00+03:00", "explicit_public_offset", None, datetime(2026, 8, 27, 21, 15, tzinfo=UTC)),
    ("2026-08-28T23:45:00-03:00", "explicit_public_offset", None, datetime(2026, 8, 29, 2, 45, tzinfo=UTC)),
    ("not a public date", "unparsed_public_posting_date", None, None),
])
def test_calendar_and_clock_precision(public, kind, calendar, expected):
    value, proof = _unops_posting_time({"Posting Start Date": public})
    assert value == expected and proof["kind"] == kind and proof["calendar_date"] == calendar
    assert proof["public_fields"] == {"Posting Start Date": public}
    assert proof["utc_resolved"] is (expected is not None)


def test_absent_and_conflicting_labels_remain_unknown():
    assert _unops_posting_time({})[1]["kind"] == "public_posting_date_absent"
    value, proof = _unops_posting_time({"Posted": "2026-08-28", "Posting Start Date": "2026-09-01"})
    assert value is None and proof["kind"] == "conflicting_public_posting_labels"
    assert len(proof["public_fields"]) == 2


def test_actual_sample4383_preserves_full_body_and_eight_competency_labels():
    fixture = FIXTURE.with_name("4383_20260913.html")
    url = "https://careers.unops.org/careersmarketplace/JobDetail/Support-Services-Manager/4383"
    job = AvatureAdapter(AdapterContext(SOURCE, None)).parse_detail_html(fixture.read_text(), url)
    assert job.title == "Support Services Manager" and job.location == "Cap Haitien"
    assert job.posted_at is None
    assert job.raw["_avature_posting_time_resolution"]["calendar_date"] == "2026-08-28"
    assert job.raw["_avature_posting_time_resolution"]["public_fields"] == {"Posting Start Date": "28-Aug-2026"}
    assert job.closes_at is None and job.closes_at_local == "15-Sep-2026"
    assert job.closes_tz == "Europe/Copenhagen"
    labels = job.raw["_avature_competency_text_resolution"]["public_labels_in_order"]
    assert len(labels) == 8 and all(label in job.description for label in labels)
    assert "before midnight Copenhagen time (CET)" in job.description
    assert job.raw["detail_html"] == fixture.read_text()


def test_unlabelled_listing_subtitle_never_becomes_posted_time():
    html = ('<article class="article--result"><a href="' + URL + '">Job title</a>'
            '<div class="article__header__text__subtitle">Tashkent • Associate • 14-Sep-2026</div>'
            '<div class="article__content">Summary</div></article>')
    job = AvatureAdapter(AdapterContext(SOURCE, None)).parse_listing_html(html)[0]
    assert job.posted_at is None
    assert job.raw["listing_date_text"] == "14-Sep-2026"


def test_calendar_only_detail_clears_old_utc_and_two_sparse_lists_preserve_claim_history(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    db.upsert_job(listing())
    job = detail()
    body = job.description
    db.upsert_job(job)
    for _ in range(2):
        db.upsert_job(listing())
        stored = db.get_job(job.identity_key())
        assert stored["posted_at"] is None and stored["description"] == body
        assert stored["raw"]["_avature_posting_time_resolution"]["calendar_date"] == "2026-08-28"
        assert stored["raw"]["_avature_previous_posting_observation"]["posted_at"] == "2026-09-14T00:00:00+00:00"
        assert "_avature_listing_observation" not in stored["raw"]["_avature_listing_observation"]["raw"]
    with pytest.raises(ValueError, match="source/identity"):
        db.upsert_job(listing(bound=False))


@pytest.mark.parametrize("defect", ["posted_at", "source_url", "apply_url", "external_id", "body", "field", "hash", "calendar"])
def test_tampered_public_detail_binding_fails(tmp_path, defect):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    db.upsert_job(listing())
    job = detail()
    if defect == "posted_at":
        job.posted_at = datetime(2026, 8, 28, tzinfo=UTC)
    elif defect in {"source_url", "apply_url"}:
        setattr(job, defect, "https://example.org/elsewhere")
    elif defect == "external_id":
        job.raw["_detail_url"] = URL.replace("/4362", "/4383")
    elif defect == "body":
        job.description += " Added condition."
    elif defect == "field":
        job.raw["avature_fields"]["Posting Start Date"] = "01-Sep-2026"
    else:
        job.raw["_avature_posting_time_resolution"]["detail_html_sha256" if defect == "hash" else "calendar_date"] = "tampered"
    with pytest.raises(ValueError, match="source/body"):
        db.upsert_job(job)
