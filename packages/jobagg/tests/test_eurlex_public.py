from datetime import datetime, timezone
import hashlib
from pathlib import Path
import unicodedata

import pytest

from jobagg.adapters.base import AdapterContext
from jobagg.adapters.eurlex_public import render_public_notice
from jobagg.adapters.static_html import StaticHTMLAdapter
from jobagg.http import HttpResponse
from jobagg.models import OrganizationSource
from jobagg.pipelines.sync_source import load_sources

FIXTURE = Path(__file__).parent / 'fixtures/eurlex_public/com_2026_20124_20260913.html'
URL = 'https://eur-lex.europa.eu/eli/C/2026/4370/oj'
SUMMARY = 'https://eu-careers.europa.eu/en/job-opportunities/executive-director/com-2026-20124'
SOURCE = OrganizationSource('eu_careers_static', 'EU', 'eu_careers_static', 'https://eu-careers.europa.eu')


def render(value=None, **changes):
    args = dict(page_url=URL, external_id='com-2026-20124', summary_url=SUMMARY, expected_title='Executive Director')
    args.update(changes)
    return render_public_notice(SOURCE, value or FIXTURE.read_text(), **args)


def test_actual_notice_exact_browser_body_full_footnotes_and_public_fields():
    job = render()
    norm = ''.join(unicodedata.normalize('NFKC', job.description).split())
    assert len(norm) == 19551
    assert hashlib.sha256(norm.encode()).hexdigest() == 'f0021c4d3ef1e7167d3239cb513f75e5b79e15cf8f2e16452c8702896b868f16'
    assert job.title == 'Executive Director'
    assert job.location == 'Lisbon, Portugal'
    assert job.department is None
    assert job.employment_type == 'Temporary Agent'
    assert job.raw['grade'] == 'AD 14'
    assert job.posted_at is None
    assert job.raw['_eu_official_field_resolution']['publication_calendar_date'] == '2026-08-19'
    assert job.closes_at == datetime(2026, 9, 29, 10, tzinfo=timezone.utc)
    assert job.closes_at_local == '2026-09-29T12:00:00+02:00'
    assert job.closes_tz == 'Europe/Brussels'
    assert job.raw['_eu_official_field_resolution']['footnote_numbers'] == list(range(1, 15))
    assert 'https://eur-lex.europa.eu/legal-content/EN/TXT/PDF/?uri=OJ:C_202604370' in job.raw['required_attachment_urls']
    assert len(job.raw['required_attachment_urls']) == 25
    assert sum('/TXT/PDF/' in link for link in job.raw['required_attachment_urls']) == 24


def test_retained_exact_document_and_primary_anchor_reparse_identically():
    first = render()
    second = render(first.raw['detail_html'])
    assert second.raw == first.raw
    assert second.description == first.description


@pytest.mark.parametrize('changes', [
    {'external_id': 'com-2026-20125'},
    {'expected_title': 'Another Director'},
    {'page_url': URL.replace('4370', '4371')},
    {'page_url': URL.replace('eur-lex.europa.eu', 'example.org')},
    {'page_url': URL.replace('eur-lex.europa.eu', 'user@eur-lex.europa.eu')},
    {'page_url': URL.replace('eur-lex.europa.eu', 'eur-lex.europa.eu:8443')},
    {'summary_url': SUMMARY.replace('eu-careers.europa.eu', 'example.org')},
    {'summary_url': SUMMARY.replace('eu-careers.europa.eu', 'user@eu-careers.europa.eu')},
    {'summary_url': SUMMARY + '?another=job'},
    {'summary_url': SUMMARY + '#another-job'},
])
def test_cross_job_or_cross_source_bindings_fail(changes):
    with pytest.raises(ValueError):
        render(**changes)


@pytest.mark.parametrize('old,new', [
    ('We propose', 'Missing section'),
    ('ntr14-C_202604370EN.000101-E0014', 'unverified-footnote'),
    ('id="document1"', 'id="another-document"'),
    ('(Temporary Agent – Grade AD 14)', '(Grade AD 14)'),
])
def test_incomplete_or_unrecognized_public_structure_fails(old, new):
    value = FIXTURE.read_text()
    assert old in value
    with pytest.raises(ValueError):
        render(value.replace(old, new))


def test_unqualified_public_clock_remains_unknown():
    job = render(FIXTURE.read_text().replace('12.00 noon Brussels time', '12.00 local time'))
    assert job.closes_at is None and job.closes_at_local is None and job.closes_tz is None
    assert job.raw['_eu_official_field_resolution']['utc_resolved'] is False
    assert '12.00 local time' in job.raw['_eu_official_field_resolution']['public_deadline']


def test_explicit_brussels_noon_uses_civil_winter_offset():
    value = FIXTURE.read_text().replace('29\u00a0September 2026, 12.00 noon Brussels time', '29 January 2027, 12.00 noon Brussels time')
    assert value != FIXTURE.read_text()
    job = render(value)
    assert job.closes_at == datetime(2027, 1, 29, 11, tzinfo=timezone.utc)
    assert job.closes_at_local == '2027-01-29T12:00:00+01:00'


def test_maintained_adapter_uses_successful_observed_eli_route_and_metadata():
    source = next(s for s in load_sources(Path(__file__).parents[1] / 'config/organizations.yaml') if s.id == 'eu_careers_static')
    original = 'http://data.europa.eu/eli/C/2026/4370/oj'
    assert source.extra['official_notice_url_overrides'][original] == URL
    summary = f'<main><h1>Executive Director</h1><div class="field--name-field-epso-link"><a href="{original}">Link</a></div></main>'

    class HTTP:
        def __init__(self):
            self.urls = []

        def get(self, url):
            self.urls.append(url)
            assert url in {SUMMARY, URL}
            return HttpResponse(url, 200, {}, summary if url == SUMMARY else FIXTURE.read_text())

    http = HTTP()
    adapter = StaticHTMLAdapter(AdapterContext(source, http))
    job = adapter.fetch_detail_for_listing_item({'parser': 'eu_careers_open_vacancies', 'href': SUMMARY,
                                               'external_id': 'com-2026-20124', 'title': 'Executive Director',
                                               'employment_type': 'AD 14', 'posted_at': '2026-08-19T08:07:13Z'})
    assert http.urls == [SUMMARY, URL]
    assert job.description == render().description
    assert job.employment_type == 'Temporary Agent' and job.posted_at is None
    assert job.raw['summary_official_vacancy_url'] == original
