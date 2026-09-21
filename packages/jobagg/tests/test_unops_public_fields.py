from pathlib import Path

import pytest

from jobagg.adapters.avature import AvatureAdapter, _detail_fields
from jobagg.adapters.base import AdapterContext
from jobagg.db import JobDatabase
from jobagg.http import JobAggHTTPClient
from jobagg.models import OrganizationSource
from jobagg.normalize import build_job

URL = "https://careers.unops.org/careersmarketplace/JobDetail/Associate-Civil-Engineer-Quantity-Surveyor-Cost-Estimator/4362"
SOURCE = OrganizationSource(id="unops_avature", name="UNOPS", ats_family="avature", base_url="https://careers.unops.org")
FIXTURE = Path(__file__).parent / "fixtures/unops/4362_20260913.html"


def parsed():
    return AvatureAdapter(AdapterContext(SOURCE, JobAggHTTPClient())).parse_detail_html(FIXTURE.read_text(), URL)


def test_real_unops_metadata_matches_independent_browser_review():
    job = parsed()
    assert job.title == "Associate Civil Engineer (Quantity Surveyor/Cost Estimator)"
    assert job.location == "Tashkent"
    assert job.employment_type == "ICA - LICA - Support - Regular"
    assert job.department is None
    assert job.posted_at is None
    assert job.raw["_avature_posting_time_resolution"]["calendar_date"] == "2026-08-28"
    assert job.raw["_avature_posting_time_resolution"]["kind"] == "public_calendar_date_only"
    assert job.closes_at is None
    assert job.closes_at_local == "14-Sep-2026"
    assert job.closes_tz == "Europe/Copenhagen"
    fields = job.raw["avature_fields"]
    assert len(fields) == 10
    assert fields["Contract Level"] == "LICA 6"
    assert fields["ICS Level"] == "ICS 06"
    assert fields["Seniority Level"] == "Associate"
    assert fields["Duration"].endswith("extension of the SES project.")
    assert "Knowledge of ABC or TNQurilish software" in job.description
    assert "Handles conflict effectively" in job.description


def test_nested_field_value_is_not_truncated():
    text = """<div class='article__content__view__field'><div class='article__content__view__field__label'>Duty <span>Station(s)</span></div><div class='article__content__view__field__value'><div>Tashkent</div><br>Remote</div></div>"""
    assert _detail_fields(text) == {"Duty Station(s)": "Tashkent Remote"}


def test_conflicting_duplicate_field_fails():
    template = "<div class='article__content__view__field'><div class='article__content__view__field__label'>Location</div><div class='article__content__view__field__value'>{}</div></div>"
    with pytest.raises(ValueError, match="Conflicting"):
        _detail_fields(template.format("Paris") + template.format("Rome"))


def test_current_detail_clears_guessed_deadline_and_seniority_department(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    old = build_job(SOURCE, title="Associate Civil Engineer", external_id="4362", apply_url=URL,
                    closes_at="2026-09-14", department="Associate")
    db.upsert_job(old)
    db.upsert_job(parsed())
    row = db.get_job("unops_avature:4362")
    assert row["department"] is None
    assert row["closes_at"] is None
    assert row["closes_at_local"] == "14-Sep-2026"
    assert row["closes_tz"] == "Europe/Copenhagen"
    # A later listing cannot replace explicit uncertainty with date-at-UTC.
    for _ in range(2):
        db.upsert_job(build_job(SOURCE, title=row["title"], external_id="4362", apply_url=URL, source_url=URL,
                                closes_at="2026-09-14", raw={"listing_html": "current listing", "_detail_url": URL}))
        row = db.get_job("unops_avature:4362")
        assert row["closes_at"] is None
        assert row["closes_at_local"] == "14-Sep-2026"
        assert row["closes_tz"] == "Europe/Copenhagen"
        assert row["raw"]["avature_fields"]["Duty Station(s)"] == "Tashkent"
        assert row["raw"]["_avature_deadline_resolution"]["utc_resolved"] is False
        assert row["raw"]["_avature_competency_text_resolution"]["public_labels_in_order"] == [
            "Respect", "Collaboration", "Partnerships", "Excellence", "Adaptability", "Decision-making", "Communication"]
        assert row["raw"]["_avature_competency_text_resolution"]["labels_inserted_at_original_image_positions"] is True
        assert "Respect Treats all individuals with respect" in " ".join(row["description"].split())


def test_missing_notice_does_not_infer_timezone():
    source = FIXTURE.read_text().replace("before midnight Copenhagen time (CET)", "by the deadline")
    job = AvatureAdapter(AdapterContext(SOURCE, JobAggHTTPClient())).parse_detail_html(source, URL)
    assert job.closes_at is None
    assert job.closes_tz is None
    assert job.closes_at_local == "14-Sep-2026"
