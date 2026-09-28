from pathlib import Path
import pytest
from jobagg.adapters.base import AdapterContext
from jobagg.adapters.static_html import StaticHTMLAdapter
from jobagg.http import HttpResponse
from jobagg.models import OrganizationSource

URL='https://www.enisa.europa.eu/recruitment/vacancies/senior-cybersecurity-expert'
PDF='https://www.enisa.europa.eu/sites/default/files/2026-09/VN_ENISA-TA-AD8-2026-14.pdf'
ID='enisa-ta-ad8-2026-14'
FIX=Path(__file__).parent/'fixtures/eu_careers/enisa_14_wrapper_20260913.html'


def adapter(wrapper):
    calls=[]
    class HTTP:
        def get(self,url,**kwargs):
            calls.append(url)
            if url==PDF:
                return HttpResponse(url,200,{'Content-Type':'application/pdf'},'',b'%PDF-1.7')
            text=wrapper if url==URL else f'<h1>Cybersecurity Expert</h1><div class="field--name-field-epso-link"><a href="{URL}">Link</a></div>'
            return HttpResponse(url,200,{'Content-Type':'text/html'},text,text.encode())
    source=OrganizationSource('eu_careers_static','EU','static_html','https://eu-careers.europa.eu/en/jobs')
    return StaticHTMLAdapter(AdapterContext(source,HTTP())),calls


def test_exact_reference_pdf_is_required_for_full_enisa_notice(monkeypatch):
    full='ENISA-TA-AD8-2026-14 Senior Cybersecurity Expert\nDuties\n'+('Policy monitoring and analysis responsibilities. '*45)+'\nEligibility requirements:degreeandexperience.'
    monkeypatch.setattr('jobagg.adapters.static_html._extract_pdf_text',lambda content:full)
    obj,calls=adapter(FIX.read_text())
    job=obj._fetch_eu_detail({'external_id':ID,'title':'Cybersecurity Expert'},'https://eu-careers.europa.eu/en/jobs/'+ID)
    assert calls[-1]==PDF and job.raw['required_attachment_urls']==[PDF]
    assert job.raw['official_notice_text']==full


def test_other_download_is_not_accepted_as_the_vacancy_notice():
    obj,calls=adapter(FIX.read_text().replace('VN_ENISA-TA-AD8-2026-14.pdf','VN_ENISA-TA-AD8-2026-13.pdf'))
    with pytest.raises(ValueError,match='identifiable vacancy'):
        obj._fetch_eu_detail({'external_id':ID,'title':'Cybersecurity Expert'},'https://eu-careers.europa.eu/en/jobs/'+ID)
    assert calls==['https://eu-careers.europa.eu/en/jobs/'+ID,URL]


def test_actual_twelve_page_notice_and_wrapper_public_metadata(monkeypatch):
    from datetime import datetime, timezone
    from jobagg.adapters.static_html import _extract_pdf_text
    full = _extract_pdf_text(FIX.with_name('enisa_14_public_notice_20260913.pdf').read_bytes())
    assert 'Ref. ENISA-TA-AD8-2026-14' in full
    monkeypatch.setattr('jobagg.adapters.static_html._extract_pdf_text',lambda content:full)
    obj,_=adapter(FIX.read_text())
    job=obj._fetch_eu_detail({'external_id':ID,'title':'Cybersecurity Expert','employment_type':'AD8'},'https://eu-careers.europa.eu/en/jobs/'+ID)
    assert job.title == 'Senior Cybersecurity Expert'
    assert job.employment_type == 'Temporary Agent'
    assert job.location == 'Athens, Greece'
    assert job.department == 'Policy Monitoring and Analyses Unit (PMA)'
    assert job.closes_at == datetime(2026,10,12,20,59,59,tzinfo=timezone.utc)
    assert job.closes_at_local == '2026-10-12T23:59:59+03:00'
    assert job.closes_tz == 'Europe/Athens'
    assert job.raw['_eu_official_field_resolution']['official_grade'] == 'AD8'
    assert full in job.description
    assert 'You are strongly advised to submit your application well in advance' in job.description
    assert '4 years, renewable' in job.description


@pytest.mark.parametrize('deadline, expected', [
    ('12/01/2027 at 23:59:59 Greek time', '2027-01-12T21:59:59+00:00'),
    ('12/10/2026 at 23:59:59', None),
])
def test_official_deadline_uses_seasonal_greek_zone_or_remains_unknown(monkeypatch,deadline,expected):
    monkeypatch.setattr('jobagg.adapters.static_html._extract_pdf_text',lambda content:'ENISA-TA-AD8-2026-14 Senior Cybersecurity Expert\nDuties\n'+('Experience requirements and responsibilities. '*60))
    obj,_=adapter(FIX.read_text().replace('12/10/2026 at 23:59:59 Greek time',deadline))
    job=obj._fetch_eu_detail({'external_id':ID,'title':'Cybersecurity Expert','closes_at':'2026-10-12T12:00:00+00:00'},'https://eu-careers.europa.eu/en/jobs/'+ID)
    assert (job.closes_at.isoformat() if job.closes_at else None) == expected
    assert job.raw['_eu_official_field_resolution']['utc_resolved'] == bool(expected)


def test_pdf_full_reference_and_official_title_are_both_required(monkeypatch):
    monkeypatch.setattr('jobagg.adapters.static_html._extract_pdf_text',lambda content:'ENISA-TA-AD8-2026-13 Senior Cybersecurity Expert\nDuties\n'+('Experience requirements and responsibilities. '*60))
    obj,_=adapter(FIX.read_text())
    with pytest.raises(ValueError,match='title/reference differs'):
        obj._fetch_eu_detail({'external_id':ID,'title':'Cybersecurity Expert'},'https://eu-careers.europa.eu/en/jobs/'+ID)


def test_nested_wrapper_and_self_closing_breaks_preserve_fields():
    from jobagg.adapters.static_html import _enisa_public_wrapper
    original=_enisa_public_wrapper(FIX.read_text())
    nested=_enisa_public_wrapper(FIX.read_text().replace('<br>','<br/>').replace('Temporary Agent','<span>Temporary Agent</span>'))
    assert nested['fields'] == original['fields']
