from pathlib import Path
import re

import pytest

from jobagg.adapters.static_html import _itcilo_vacancy_title, parse_detail_page
from jobagg.models import OrganizationSource

FIXTURES = Path(__file__).parent / 'fixtures/itcilo'
SOURCE = OrganizationSource('itcilo_custom_html', 'ITCILO', 'static_html', 'https://jobs.itcilo.org/')
TITLES = {
    '207': 'Associate Programme Officer, Rural and Local Economic Transformation and Entrepreneurship',
    '208': 'Associate Programme Officer, Productivity and Innovation in SMEs',
    '209': 'Programme Assistant (Internal Vacancy)',
}


@pytest.mark.parametrize('identity', TITLES)
def test_actual_vacancy_heading_wins_over_site_jobs_header(identity):
    body = (FIXTURES / f'detail_{identity}_20260913.html').read_text()
    assert re.search(r'<h1[^>]*>Jobs</h1>', body)
    job = parse_detail_page(SOURCE, body, SOURCE.base_url + 'view_vacancy/' + identity)
    assert job.external_id == identity
    assert job.title == TITLES[identity]
    assert len(job.description) > 6000
    assert job.raw['_itcilo_title_resolution'] == {
        'selector': 'h1.titlevacancy', 'public_title': TITLES[identity]}


@pytest.mark.parametrize('body', [
    '<h1>Jobs</h1><title>Recruitment</title>',
    '<h1 class="titlevacancy"></h1>',
    '<h1 class="titlevacancy">A</h1><h1 class="titlevacancy">B</h1>',
    '<h1 class="not-titlevacancy">A</h1>',
    '<h1 class="titlevacancy">Unclosed',
])
def test_missing_or_ambiguous_vacancy_heading_fails_closed(body):
    with pytest.raises(ValueError, match='ITCILO'):
        parse_detail_page(SOURCE, body, SOURCE.base_url + 'view_vacancy/207')


def test_role_suffix_and_nested_inline_text_are_preserved():
    body = '<h1>Jobs</h1><h1 class="heading titlevacancy">Officer <span>- Learning</span> &amp; Skills</h1>'
    assert _itcilo_vacancy_title(body) == 'Officer - Learning & Skills'


def test_other_source_keeps_its_existing_generic_title_behavior():
    source = OrganizationSource('other_static', 'Other', 'static_html', 'https://example.org')
    job = parse_detail_page(source, '<h1>Public job</h1><p>Requirements.</p>', source.base_url + '/207')
    assert job.title == 'Public job'
    assert '_itcilo_title_resolution' not in job.raw
