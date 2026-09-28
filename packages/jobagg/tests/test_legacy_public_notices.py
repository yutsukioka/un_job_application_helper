"""ICC/AfDB XML bodies are templates; rendered public notices bind the fields."""
import json
from pathlib import Path

import pytest

from jobagg.adapters.base import AdapterContext
from jobagg.adapters.legacy_public import _icc_fields, public_body, render_public_notice
from jobagg.adapters.successfactors_rmk import SuccessFactorsLegacyAdapter
from jobagg.detail_quality import detail_quality_status
from jobagg.models import OrganizationSource
from jobagg.pipelines.sync_source import load_sources, _listing_payload_satisfies_detail


def fixtures():
    return json.loads((Path(__file__).parent / "fixtures/successfactors/legacy_public_first3_20260913.json").read_text())


def source(row):
    icc = row["source_id"] == "icc_successfactors_legacy"
    return OrganizationSource(row["source_id"], "ICC" if icc else "AfDB", "successfactors_legacy",
                              "https://career5.successfactors.eu/career" if icc else "https://career2.successfactors.eu/career",
                              extra={"company_id": "1657261P" if icc else "africandev", "locale": "en_GB"})


def render(row):
    return render_public_notice(source(row), row["html"], page_url=row["page_url"],
                                external_id=row["external_id"], expected_title=row["title"])


def test_actual_icc_public_values_replace_template_fields_without_invented_contract_or_time():
    contractor, visiting = [render(row) for row in fixtures()[:2]]
    assert contractor.location == "The Hague"
    assert contractor.department == "Victims and Witnesses Section, Division of External Operations, Registry"
    assert contractor.employment_type == "Individual contractor on an ad hoc and short-term basis"
    assert "24303 | Registry" in contractor.description
    assert "[[division]]" not in contractor.description
    assert visiting.location == "The Hague - NL"
    assert visiting.department == "Victims Participation and Reparation , Registry"
    assert visiting.employment_type == "Visiting Professional"
    assert visiting.closes_at is None and visiting.closes_at_local == "2026-12-31"
    assert "Two reference letters" in visiting.description


def test_actual_afdb_public_dates_are_calendar_only_and_empty_grade_is_unknown():
    job = render(fixtures()[2])
    assert job.posted_at is None and job.closes_at is None
    assert job.closes_at_local == "2026-09-24" and job.closes_tz is None
    assert job.raw["_legacy_public_field_resolution"]["public_posting_date"] == "08/26/2026"
    assert job.raw["grade"] is None and job.employment_type is None
    assert "[[Custom_endDate]]" not in job.description
    assert "09/24/2026" in job.description
    assert "Position No." in job.raw["_legacy_public_field_resolution"]["public_fields"]
    assert "Position No ." not in job.raw["_legacy_public_field_resolution"]["public_fields"]


def test_public_inline_text_and_escaped_literal_prose_are_preserved():
    row = fixtures()[0]
    row["html"] = row["html"].replace("</div>", "<p>Edu<strong>cation</strong>: use &lt;strong&gt; as a literal example.</p></div>", 1)
    job = render(row)
    assert "Education: use <strong> as a literal example." in job.description
    assert "Edu cation" not in job.description


@pytest.mark.parametrize("index", range(3))
def test_adapter_fetches_public_page_for_original_xml_raw_shape(monkeypatch, index):
    row = fixtures()[index]
    adapter = SuccessFactorsLegacyAdapter(AdapterContext(source(row), None))
    monkeypatch.setattr(adapter, "ensure_allowed", lambda url: None)
    seen = []
    def fetch(url):
        seen.append(url)
        return row["html"]
    monkeypatch.setattr(adapter, "fetch_text", fetch)
    job = adapter.fetch_detail_for_listing_item({"reqid": row["external_id"], "jobtitle": row["title"],
                                               "parser": "successfactors_xml", "jobdescription": "Unresolved template [[division]]"})
    assert seen == [row["page_url"]]
    assert job.description == render(row).description


@pytest.mark.parametrize("defect", ["host", "company", "requisition", "duplicate_requisition", "heading", "second_body", "unclosed_body"])
def test_public_notice_rejects_unbound_identity_or_body(defect):
    row = fixtures()[0]
    if defect == "host":
        row["page_url"] = row["page_url"].replace("career5.successfactors.eu", "example.org")
    elif defect == "company":
        row["page_url"] = row["page_url"].replace("1657261P", "someone_else")
    elif defect == "requisition":
        row["page_url"] = row["page_url"].replace("career_job_req_id=24303", "career_job_req_id=24315")
    elif defect == "duplicate_requisition":
        row["page_url"] += "&career_job_req_id=24315"
    elif defect == "heading":
        row["html"] = row["html"].replace("(24303)</h1>", "(24315)</h1>")
    elif defect == "second_body":
        row["html"] += '<div class="externalPosting">A different body</div>'
    else:
        body = public_body(row["html"])
        end = row["html"].index(body) + len(body)
        assert row["html"][end:end + 6] == '</div>'
        row["html"] = row["html"][:end] + row["html"][end + 6:]
    with pytest.raises(ValueError):
        render(row)


def test_xml_placeholder_body_does_not_pass_detail_quality_by_length():
    row = fixtures()[0]
    body = render(row).description.replace("24303 | Registry", "24303 | [[division]]")
    assert detail_quality_status(title=row["title"], description=body, raw={"parser": "successfactors_xml"}) == "detail_missing"
    assert detail_quality_status(title=row["title"], description=body, raw={"parser": "unrelated_provider"}) == "complete"


def test_registry_requires_actual_icc_and_afdb_details():
    sources = {s.id: s for s in load_sources(Path(__file__).parents[1] / "config/organizations.yaml")}
    for row in fixtures():
        configured = sources[row["source_id"]]
        assert configured.extra["fetch_details"] is True
        assert not _listing_payload_satisfies_detail(configured, render(row))


def test_actual_icc_24231_single_cell_presentation_wrapper_retains_metadata_and_full_body():
    row = json.loads((Path(__file__).parent / 'fixtures/successfactors/legacy_public_24231_20260913.json').read_text())
    job = render(row)
    values = job.raw['_legacy_public_field_resolution']['public_fields']
    assert values == {'Organisational Unit': 'Public Information and Outreach Section, Registry',
                      'Duty Station': 'The Hague - NL', 'Contract Duration': '6 months',
                      'Deadline for Applications': '31 December 2026'}
    assert job.raw['_legacy_public_field_resolution']['public_body_sha256'] == row['public_container_sha256']
    assert job.department == values['Organisational Unit'] and job.location == values['Duty Station']
    assert job.employment_type == 'Internship'  # Explicit public program category, not the six-month duration.
    header = job.raw['_legacy_public_header']
    assert header['category'] == 'Internship'
    assert header['job_field'] == 'Public Information / Journalism / Social Media'
    assert header['posting_date']['rendered_calendar_date'] is None
    assert header['posting_date']['source_epoch_utc_claim'] == '2025-12-31T23:00:00+00:00'
    assert header['posting_date']['pattern'] == 'dd/MM/yyyy'
    assert header['posting_date']['locale'] == 'en_GB'
    assert job.posted_at is None
    assert job.closes_at is None and job.closes_at_local == '2026-12-31'
    assert 'ICC Daily Press Review' in job.description
    assert 'One short essay' in job.description
    assert 'Scanned copies of official academic transcripts' in job.description


@pytest.mark.parametrize('wrapper', [
    '<table><tr><td>Conflicting unit {leaf}</td></tr></table>',
    '<table><tr><td>{leaf} Conflicting unit</td></tr></table>',
    '<table><tr><td>{leaf}</td><td>Another field</td></tr></table>',
    '<table><tr><td>{leaf}</td></tr><tr><td>Another row</td></tr></table>',
    '<table><tr><td>{leaf}{leaf}</td></tr></table>',
    '<table><tr><td><table><tr><td>{leaf}</td></tr></table></td></tr></table>',
])
def test_nested_metadata_with_competing_content_or_multiple_tables_is_rejected(wrapper):
    leaf = ('<table><tr><td>Organisational Unit:</td><td>Unit</td></tr>'
            '<tr><td>Duty Station:</td><td>City</td></tr>'
            '<tr><td>Contract Duration:</td><td>6 months</td></tr></table>')
    with pytest.raises(ValueError):
        _icc_fields(wrapper.format(leaf=leaf))


def test_public_header_fields_keep_categories_separate_from_contracts_and_departments():
    contractor = render(fixtures()[0])
    assert contractor.raw['_legacy_public_header']['category'] == 'Consultant'
    assert contractor.employment_type == 'Individual contractor on an ad hoc and short-term basis'
    row = fixtures()[1]
    header = render(row).raw['_legacy_public_header']['header_html']
    row['html'] = row['html'].replace(header, header.replace('Visiting Professional', 'General Service'))
    general_service = render(row)
    assert general_service.employment_type is None
    assert general_service.raw['public_job_category'] == 'General Service'
    afdb = render(fixtures()[2])
    assert afdb.raw['public_job_field'] == 'Administrative Specialists'
    assert afdb.employment_type is None and afdb.department is None


@pytest.mark.parametrize('defect', ['missing_header', 'duplicate_header', 'header_id', 'date_binding', 'date_pattern'])
def test_public_header_identity_and_date_renderer_changes_require_review(defect):
    row = fixtures()[0]
    header = render(row).raw['_legacy_public_header']['header_html']
    if defect == 'missing_header':
        row['html'] = row['html'].replace(header, '')
    elif defect == 'duplicate_header':
        row['html'] += header
    elif defect == 'header_id':
        row['html'] = row['html'].replace(header, header.replace('<b>24303</b>', '<b>99999</b>'))
    elif defect == 'date_binding':
        row['html'] = row['html'].replace('dateFormatter.format(postedOnDate)', 'anotherFormatter.format(postedOnDate)')
    else:
        row['html'] = row['html'].replace('value="dd/MM/yyyy"', 'value="yyyy/MM/dd"')
    with pytest.raises(ValueError):
        render(row)
