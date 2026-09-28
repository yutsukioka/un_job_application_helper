from pathlib import Path

import pytest

from jobagg.adapters.static_html import _sesar_notice_section

URL = 'https://www.sesarju.eu/careers'
FIXTURE = Path(__file__).parent / 'fixtures/eu_careers/sesar_vn226_directory_20260913.html'


def test_current_external_notice_is_distinct_from_same_title_interagency_notice():
    result = _sesar_notice_section(FIXTURE.read_text(), URL, 'vn226')
    assert result['heading'] == 'EXTERNAL Vacancy Notice - Budget Officer (ref. VN226)'
    assert 'VN226%20-%20Budget%20Officer%20(AD6)' in result['notice_url']
    assert 'Declaration%20of%20honor.doc' in result['section_html']
    assert 'MoU%20for%20EUAN' in result['section_html']
    assert 'Interagency%20publication' not in result['section_html']
    assert 'SN023_Declaration' not in result['section_html']


def test_same_title_or_reference_substring_is_not_sufficient():
    with pytest.raises(ValueError, match='exact vacancy-reference'):
        _sesar_notice_section(FIXTURE.read_text(), URL, 'vn22')
    result = _sesar_notice_section(FIXTURE.read_text(), URL, 'S3JU/IAJM/VN226')
    assert 'Interagency%20publication.pdf' in result['notice_url']


@pytest.mark.parametrize('mutate', [
    lambda body: body.replace('(ref. VN226)', '(ref. VN227)'),
    lambda body: body + '<h3>Duplicate (REF. VN226)</h3><a href="duplicate.pdf">Vacancy notice</a>',
    lambda body: body.replace('VN226%20-%20Budget%20Officer%20(AD6)%20-%20external%20-%20final%20publication%20-%20ares.pdf', 'different.doc'),
])
def test_missing_or_ambiguous_notice_fails_closed(mutate):
    original = FIXTURE.read_text()
    changed = mutate(original)
    assert changed != original
    with pytest.raises(ValueError, match='SESAR'):
        _sesar_notice_section(changed, URL, 'vn226')


def test_cross_host_notice_rejected():
    body = '<h3>Budget Officer (REF. VN226)</h3><a href="https://other.example/vn226.pdf">Vacancy notice</a>'
    with pytest.raises(ValueError, match='official directory host'):
        _sesar_notice_section(body, URL, 'vn226')


def test_actual_primary_pdf_contract_is_separate_from_grade(monkeypatch):
    from jobagg.adapters.base import AdapterContext
    from jobagg.adapters.static_html import StaticHTMLAdapter
    from jobagg.http import HttpResponse
    from jobagg.models import OrganizationSource
    import jobagg.adapters.static_html as module

    original = module._extract_pdf_text
    pdf = FIXTURE.with_name('sesar_vn226_public_notice_20260913.pdf').read_bytes()
    directory = FIXTURE.read_text()
    notice = _sesar_notice_section(directory, URL, 'vn226')['notice_url']
    summary = '<main><h1>Budget Officer</h1><div class="field--name-field-epso-link"><a href="https://www.sesarju.eu/careers">Link</a></div></main>'
    class HTTP:
        def get(self,url):
            if url == URL:
                return HttpResponse(url,200,{},directory)
            if url == notice:
                return HttpResponse(url,200,{'Content-Type':'application/pdf'},'',pdf)
            return HttpResponse(url,200,{},summary)
    source=OrganizationSource('eu_careers_static','EU','static_html','https://eu-careers.europa.eu/en/jobs',extra={'parser':'eu_careers_open_vacancies'})
    item={'parser':'eu_careers_open_vacancies','href':'https://eu-careers.europa.eu/en/jobs/vn226','external_id':'vn226','title':'Budget Officer','employment_type':'AD 6'}
    job=StaticHTMLAdapter(AdapterContext(source,HTTP())).fetch_detail_for_listing_item(item)
    assert job.employment_type == 'TA 2(f)'
    assert job.raw['_eu_official_field_resolution']['official_grade'] == 'AD6'
    assert job.raw['_eu_official_field_resolution']['contract_type_resolved'] is True
    assert 'Administrator - TA 2(f)' in job.raw['_eu_official_field_resolution']['public_contract_phrase']
    # Unsupported contract headings must not revive the summary grade as type.
    monkeypatch.setattr(module,'_extract_pdf_text',lambda value:original(value).replace('Administrator - TA 2(f) – AD6','Administrator - Unknown type – AD6'))
    unknown=StaticHTMLAdapter(AdapterContext(source,HTTP())).fetch_detail_for_listing_item(item)
    assert unknown.employment_type is None
    assert unknown.raw['_eu_official_field_resolution']['contract_type_resolved'] is False
