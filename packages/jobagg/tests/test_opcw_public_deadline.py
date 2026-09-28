from datetime import datetime,timezone
from pathlib import Path

from jobagg.models import OrganizationSource
from jobagg.adapters.static_html import parse_detail_page

SOURCE=OrganizationSource('opcw_talentsoft_candidatespace','OPCW','static_html','https://jobs.opcw.org',extra={'date_locale':'EU'})
URL='https://jobs.opcw.org/job/job-science-policy-adviser-p-5-_582.aspx'
HTML=(Path(__file__).parent/'fixtures/opcw/current_science_adviser_20260913.html').read_text()


def test_current_public_deadline_uses_netherlands_evening():
    job=parse_detail_page(SOURCE,HTML,URL)
    assert job.external_id=='582'
    assert job.title=='Science Policy Adviser (P-5)'
    assert job.closes_at==datetime(2026,9,28,20,0,tzinfo=timezone.utc)
    assert job.closes_at_local=='2026-09-28T22:00:00'
    assert job.closes_tz=='Europe/Amsterdam'
    assert job.raw['detail_html']==HTML
    assert 'minimum of 10 years' in job.description


def test_winter_deadline_observes_standard_time():
    job=parse_detail_page(SOURCE,HTML.replace('28/09/2026','15/01/2027'),URL)
    assert job.closes_at==datetime(2027,1,15,21,0,tzinfo=timezone.utc)


def test_date_without_public_time_notice_stays_unknown_in_utc():
    job=parse_detail_page(SOURCE,HTML.replace('22:00 The Netherlands local time','the time in your confirmation'),URL)
    assert job.closes_at is None
    assert job.closes_at_local=='28/09/2026'
    assert job.closes_tz is None
