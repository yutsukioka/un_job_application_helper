"""Actual public CSOD notices include criteria omitted by the listing API."""
from copy import deepcopy
from datetime import UTC, datetime
import json
from pathlib import Path

import pytest

from jobagg.adapters.base import AdapterContext
from jobagg.adapters.csod import CSODAdapter
from jobagg.adapters.worldbank_public import render_public_notice, render_public_page
from jobagg.models import OrganizationSource
from jobagg.detail_quality import DETAIL_QUALITY_COMPLETE, DETAIL_QUALITY_LIST_ONLY, detail_quality_status
from jobagg.pipelines.sync_source import _listing_payload_satisfies_detail, fetch_schedule_policy, load_sources


FIXTURES = Path(__file__).parent / "fixtures/worldbank_public"
SOURCE = OrganizationSource("worldbank_csod", "World Bank", "csod", "https://worldbankgroup.csod.com")


def posting(identity="38308"):
    return json.loads((FIXTURES / f"{identity}_jobposting_20260913.json").read_text())


def notice(payload=None, identity="38308"):
    payload = payload or posting(identity)
    return render_public_notice(SOURCE, payload, page_url=url(identity), external_id=identity, expected_title=payload["Title"])


def url(identity):
    return f"https://worldbankgroup.csod.com/ux/ats/careersite/1/home/requisition/{identity}?c=worldbankgroup"


def test_actual_notice_retains_all_public_criteria_and_exact_metadata():
    job = notice()
    assert "Selection Criteria" in job.description
    assert "Business Competencies" in job.description and "Recommended Certifications" in job.description
    assert job.location == "Washington, DC,United States"
    assert job.department is None and job.employment_type is None
    assert job.raw["sector"] == "Information Technology"
    assert job.raw["term_duration"] == "1 year 0 months"
    assert job.raw["recruitment_type"] == "Local Recruitment"
    assert job.posted_at is None  # Publisher midnight has no stated timezone.
    assert job.raw["_worldbank_public_field_resolution"]["publisher_date_posted"] == "2026-09-10T00:00:00"
    assert job.closes_at == datetime(2026, 9, 24, 23, 59, tzinfo=UTC)
    assert job.closes_at_local == "2026-09-24T23:59" and job.closes_tz == "UTC"


def test_actual_young_professionals_layout_preserves_duties_and_requirements():
    job = notice(identity="38257")
    assert "Selection Criteria" not in job.description
    assert "What You'll Deliver" in job.description and "What You’ll Bring" in job.description
    assert "master" in job.description.lower() and "WBG Culture Attributes:" in job.description
    assert job.raw["_worldbank_public_field_resolution"]["public_section_layout"] == "young_professionals_deliver_and_bring"
    assert job.raw["sector"] == "Energy" and job.raw["grade"] == "GF"
    assert job.closes_at == datetime(2026, 9, 30, 23, 59, tzinfo=UTC)


def test_actual_digital_access_ypp_uses_expanded_will_heading():
    job = notice(identity="38196")
    assert "What You Will Deliver" in job.description and "What You’ll Bring" in job.description
    assert "Digital Access" in job.description and "master’s degree" in job.description
    assert job.raw["_worldbank_public_field_resolution"]["public_section_layout"] == "young_professionals_deliver_and_bring"


def test_actual_curly_apostrophe_heading_keeps_original_public_typography():
    job = notice(identity="38251")
    assert "What You’ll Deliver" in job.description and "What You’ll Bring" in job.description
    assert job.raw["_worldbank_public_field_resolution"]["public_section_layout"] == "young_professionals_deliver_and_bring"


@pytest.mark.parametrize("identity", ["38189", "38187"])
def test_actual_publisher_requirements_only_layout_never_invents_duties(identity):
    job = notice(identity=identity)
    assert job.raw["_worldbank_public_field_resolution"]["public_section_layout"] == "young_professionals_requirements_only"
    assert job.raw["_worldbank_public_field_resolution"]["duties_section_present"] is False
    assert "What You'll Deliver" not in job.description and "What You Will Deliver" not in job.description
    assert "World Bank Group Core Competencies" in job.description and "three eight-month rotations" in job.description


@pytest.mark.parametrize("missing", ["three eight-month rotations", "World Bank Group Core Competencies", "comprehensive benefits"])
def test_requirements_only_layout_needs_its_observed_intro_and_terminal_public_text(missing):
    data = posting("38189")
    data["Description"] = data["Description"].replace(missing, "Unresolved text")
    with pytest.raises(ValueError, match="recognized duties/requirements"):
        notice(data, "38189")


def test_actual_treasury_internship_retains_absent_fields_and_source_disclaimer():
    job = notice(identity="38076")
    assert job.raw["sector"] is None and job.raw["recruitment_type"] is None and job.raw["term_duration"] is None
    assert job.department is None and job.employment_type is None and job.raw["grade"] == "T3"
    assert job.raw["_worldbank_public_field_resolution"]["public_metadata_labels_absent"] == ["Sector", "Recruitment Type", "Term Duration"]
    assert job.closes_at == datetime(2026, 9, 30, 23, 59, tzinfo=UTC)
    assert "PLEASE DISREGARD THE FOLLOWING INFORMATION" in job.description and "February 17, 2026" in job.description
    assert "Duties and Responsibilities" in job.description and "Selection Criteria" in job.description


def test_unmasked_ambiguous_calendar_clock_is_unknown_even_with_utc_label():
    data = posting("38076")
    data["Description"] = data["Description"].replace("9/30/2026 (11:59pm UTC)", "9/10/2026 (11:59pm UTC)")
    job = notice(data, "38076")
    assert job.closes_at is None and job.closes_at_local is None and job.closes_tz is None


def test_second_observed_pioneer_template_preserves_all_korean_internship_fields():
    job = notice(identity="37705")
    assert job.raw["grade"] == "T4" and job.raw["required_languages"] == "English, Korean"
    assert job.location == "Seoul,South Korea"
    assert job.raw["sector"] is None and job.raw["recruitment_type"] is None
    assert "KGGTF" in job.description and "Currently enrolled in Master’s degree program or a PhD program." in job.description
    assert job.closes_at == datetime(2026, 9, 30, 23, 59, tzinfo=UTC)


def test_pioneer_title_alone_cannot_relax_an_unrelated_header():
    data = posting("37705")
    data["Description"] = data["Description"].replace("WBG Pioneers, the World Bank Group’s Internship Program", "Unrelated programme")
    with pytest.raises(ValueError, match="metadata labels"):
        notice(data, "37705")


@pytest.mark.parametrize("missing", ["What You'll Deliver", "What You’ll Bring"])
def test_young_professionals_requires_both_observed_section_headings(missing):
    data = posting("38257")
    data["Description"] = data["Description"].replace(missing, "Other content")
    with pytest.raises(ValueError, match="recognized duties/requirements"):
        notice(data, "38257")


def test_plain_prose_mentions_do_not_certify_a_young_professionals_notice():
    data = posting("38257")
    data["Description"] = data["Description"].replace("What You'll Deliver", "Other duties").replace("What You’ll Bring", "Other requirements")
    data["Description"] += "<p>What You'll Deliver. What You’ll Bring.</p>"
    with pytest.raises(ValueError, match="recognized duties/requirements"):
        notice(data, "38257")


def test_inline_joins_and_escaped_literal_prose_are_not_reinterpreted():
    data = posting()
    data["Description"] += "<p><strong>Education</strong>: A<span>nalysis</span> &lt;strong&gt;literal&lt;/strong&gt;.</p><script>ignore me</script>"
    body = notice(data).description
    assert "Education: Analysis <strong>literal</strong>." in body
    assert "Education :" not in body and "A nalysis" not in body and "ignore me" not in body


@pytest.mark.parametrize("value", ["9/24/2026", "9/24/2026 (MM/DD/YYYY) at 11:59pm", "9/24/2026 (MM/DD/YYYY) at 13:59pm UTC"])
def test_unresolved_public_clock_never_falls_back_to_unqualified_publisher_date(value):
    data = posting()
    data["Description"] = data["Description"].replace("9/24/2026 (MM/DD/YYYY) at 11:59pm UTC", value)
    job = notice(data)
    assert job.closes_at is None and job.closes_at_local is None and job.closes_tz is None
    assert job.raw["_worldbank_public_field_resolution"]["publisher_valid_through"] == data["ValidThrough"]


def test_public_metadata_identity_must_match_the_requested_requisition():
    data = posting()
    data["Description"] = data["Description"].replace("req38308", "req99999")
    with pytest.raises(ValueError, match="Job #"):
        notice(data)


def test_only_public_jobposting_is_retained_not_page_bootstrap_context():
    data = posting()
    html = '<script>anonymousContext="not-job-content"</script><script type="application/ld+json">' + json.dumps(data) + '</script>'
    job = render_public_page(SOURCE, html, page_url=url("38308"), external_id="38308", expected_title=data["Title"])
    assert "anonymousContext" not in json.dumps(job.raw)
    assert job.raw["worldbank_public_jobposting"] == data
    with pytest.raises(ValueError, match="exactly one"):
        render_public_page(SOURCE, html + html, page_url=url("38308"), external_id="38308", expected_title=data["Title"])


def test_adapter_fetches_exact_public_notice_and_marks_listings(monkeypatch):
    source = deepcopy(SOURCE)
    source.extra["apply_url_template"] = url("{job_id}")
    adapter = CSODAdapter(AdapterContext(source, None))
    data = posting()
    item = {"requisitionId": 38308, "displayJobTitle": data["Title"], "externalDescription": "Description only, without requirements."}
    calls = []
    def public_get(target):
        calls.append(target)
        return '<script type="application/ld+json">' + json.dumps(data) + '</script>'
    monkeypatch.setattr(adapter, "fetch_text", public_get)
    assert adapter.parse_jobs([item])[0].raw["_worldbank_record_kind"] == "listing"
    job = adapter.fetch_detail_for_listing_item(item)
    assert calls == [url("38308")]
    assert job.raw["worldbank_listing_metadata"] == item
    assert "Selection Criteria" in job.description


def test_worldbank_registry_requires_public_details_even_for_long_listing_text():
    root = Path(__file__).resolve().parents[1]
    source = next(s for s in load_sources(root / "config/organizations.yaml") if s.id == "worldbank_csod")
    assert source.extra["fetch_details"] is True
    assert source.extra["listing_payload_is_detail_complete"] is False
    policy = fetch_schedule_policy(source)
    assert policy["detail_min_delay_seconds"] >= 8
    assert policy["detail_jitter_seconds"] >= 3
    assert policy["detail_batch_size"] == 10 and policy["detail_batch_pause_seconds"] >= 120
    payload = posting()
    summary = payload["Description"].split("Selection Criteria")[0]
    job = CSODAdapter(AdapterContext(source, None)).parse_jobs([{
        "requisitionId": 38308, "displayJobTitle": payload["Title"], "externalDescription": summary,
    }])[0]
    assert len(job.description) > 1000
    assert _listing_payload_satisfies_detail(source, job) is False
    assert detail_quality_status(title=job.title, description=job.description, raw=job.raw) == DETAIL_QUALITY_LIST_ONLY
    full = notice()
    assert detail_quality_status(title=full.title, description=full.description, raw=full.raw) == DETAIL_QUALITY_COMPLETE


def test_worldbank_marker_rule_does_not_guess_source_from_generic_provider_keys():
    body = "Substantive public responsibilities and qualifications. " * 40
    other_provider = {"requisitionId": 38308, "externalDescription": body, "displayJobTitle": "Analyst"}
    assert detail_quality_status(title="Analyst", description=body, raw=other_provider) == DETAIL_QUALITY_COMPLETE
    assert detail_quality_status(title="Analyst", description=body, raw={**other_provider, "_worldbank_record_kind": "listing"}) == DETAIL_QUALITY_LIST_ONLY
