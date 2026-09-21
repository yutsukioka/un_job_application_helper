"""Regression fixture for EDA's browser-rendered public vacancy API."""
import json
from pathlib import Path

import pytest

from jobagg.adapters.eda_public import public_notice_endpoint, render_public_notice
from jobagg.models import OrganizationSource


def fixture():
    return json.loads((Path(__file__).parent / "fixtures/eu_careers/eda_1112_public_20260913.json").read_text())


def render(payload):
    return render_public_notice(
        OrganizationSource(id="eu_careers_static", name="EU Careers", ats_family="static_html",
                           base_url="https://eu-careers.europa.eu"),
        payload, page_url="https://vacancies.eda.europa.eu/vacanciesnotice/1112",
        external_id="eda-2026-189a", expected_title="OSRA and RTI Work Strands Officer",
        summary_html="<p>Public EU vacancy summary</p>", summary_url="https://eu-careers.europa.eu/job/189a")


def test_public_notice_has_all_eleven_sections_and_metadata_without_invented_utc():
    payload = fixture()
    job = render(payload)
    assert job.title == "OSRA and RTI Work Strands Officer"
    assert job.department == "Research, Technology and Innovation Directorate (RTI)"
    assert job.employment_type == "Temporary agent"
    assert job.location == "Brussels"
    assert job.closes_at is None and job.posted_at is None
    assert "06/10/2026" in job.description
    assert "GMT+1" in job.description
    assert "SECRET UE/EU SECRET" in job.description
    assert len(payload["Notices"]) == 11
    for notice in payload["Notices"]:
        assert f'{notice["Order"]}. {notice["Label"]}' in job.description
    assert job.raw["eda_public_notice"] == payload
    assert job.raw["required_attachment_urls"] == [
        "https://vacancies.eda.europa.eu/api/public/Vacancies/ExportPdf/1112/false"]


@pytest.mark.parametrize("defect", ["reference", "version", "title", "order", "missing_section", "foreign_section", "missing_identity"])
def test_public_notice_rejects_identity_or_section_defects(defect):
    payload = fixture()
    if defect == "reference":
        payload["Reference"] = "EDA/2026/999a"
    elif defect == "version":
        payload["IDVacanciesVersion"] = 123
    elif defect == "title":
        payload["FrendlyTitleFrontend"] = "Different public job"
    elif defect == "order":
        payload["Notices"][1]["Order"] = payload["Notices"][0]["Order"]
    elif defect == "missing_section":
        payload["Notices"] = [n for n in payload["Notices"] if n["Label"] != "DUTIES"]
    elif defect == "foreign_section":
        payload["Notices"][0]["IdVacancy"] = 999
    else:
        payload.pop("IdVacancy")
        for notice in payload["Notices"]:
            notice.pop("IdVacancy")
    with pytest.raises(ValueError):
        render(payload)


def test_endpoint_requires_exact_official_notice_identity():
    for url in ["https://example.org/vacanciesnotice/1112", "https://vacancies.eda.europa.eu/vacancies",
                "https://vacancies.eda.europa.eu/vacanciesnotice/1112?job=999",
                "https://vacancies.eda.europa.eu/vacanciesnotice/1112#different",
                "https://user:secret@vacancies.eda.europa.eu/vacanciesnotice/1112",
                "https://vacancies.eda.europa.eu:8443/vacanciesnotice/1112"]:
        with pytest.raises(ValueError):
            public_notice_endpoint(url)


def short_fixtures():
    path = Path(__file__).parent / "fixtures/eu_careers/eda_short_board_refs_20260913.json"
    return json.loads(path.read_text())


def render_short(row):
    return render_public_notice(
        OrganizationSource("eu_careers_static", "EU Careers", "static_html", "https://eu-careers.europa.eu"),
        row["payload"], external_id=row["external_id"], page_url=row["page_url"],
        expected_title=row["payload"]["FrendlyTitleFrontend"] or row["payload"]["Post"],
        summary_url=row["summary_url"], summary_html=row["summary_html"])


@pytest.mark.parametrize("index", range(4))
def test_short_board_reference_requires_own_official_link_and_keeps_full_reference(index):
    row = short_fixtures()[index]
    job = render_short(row)
    assert job.external_id == row["external_id"]
    resolution = job.raw["_eu_official_field_resolution"]
    assert resolution["reference"] == "EDA/2026/" + row["external_id"]
    assert resolution["board_reference_binding"]["observed_notice_url"] == row["page_url"]
    assert job.description.count("10. APPLICATION PROCEDURE") == 1
    assert job.posted_at is None and job.closes_at is None
    assert "13/10/2026" in job.description


@pytest.mark.parametrize("defect", ["wrong_reference", "wrong_version", "wrong_budget", "wrong_alpha",
                                   "wrong_board_url", "foreign_board", "missing_link", "wrong_title",
                                   "two_titles", "reference_suffix_only"])
def test_short_board_reference_rejects_unbound_or_ambiguous_identity(defect):
    row = short_fixtures()[0]
    if defect == "wrong_reference":
        row["payload"]["Reference"] = "EDA/2026/187a"
    elif defect == "wrong_version":
        row["payload"]["IDVacanciesVersion"] = 1124
    elif defect == "wrong_budget":
        row["payload"]["BudgetCode"] = "187"
    elif defect == "wrong_alpha":
        row["payload"]["AlphaPart"] = "b"
    elif defect == "wrong_board_url":
        row["summary_url"] = row["summary_url"].replace("/171a", "/187a")
    elif defect == "foreign_board":
        row["summary_url"] = row["summary_url"].replace("eu-careers.europa.eu", "example.org")
    elif defect == "missing_link":
        row["summary_html"] = row["summary_html"].replace(row["page_url"], "https://example.org")
    elif defect == "wrong_title":
        row["summary_html"] = row["summary_html"].replace("BraveTech EU | Project Officer Electronic Warfare", "Another role")
    elif defect == "two_titles":
        row["summary_html"] += '<h2 class="job-title">BraveTech EU | Project Officer Electronic Warfare</h2>'
    else:
        row["payload"]["Reference"] = "Unrelated/171a"
    with pytest.raises(ValueError):
        render_short(row)
