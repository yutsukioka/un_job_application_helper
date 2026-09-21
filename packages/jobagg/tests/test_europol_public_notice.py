from datetime import datetime, timezone
from pathlib import Path
import json
import re

import pytest

from jobagg.adapters.static_html import _europol_public_vacancy
from test_eu_careers_details import adapter

URL = 'https://www.europol.europa.eu/work-with-us/careers/open-vacancies/vacancy/1080'
ID = 'europol-2026-ta-ad9-769'
TITLE = 'Head of Unit – Operational Centre'
FIXTURE = Path(__file__).parent / 'fixtures/eu_careers/europol_769_server_data_20260913.html'


def test_actual_server_payload_recovers_all_eight_sections_and_utc_deadline():
    summary = f'<main><h1>{TITLE}</h1><div class="field--name-field-epso-link"><a href="{URL}">Link</a></div></main>'
    job = adapter([summary, FIXTURE.read_text()]).fetch_detail_for_listing_item({
        'parser': 'eu_careers_open_vacancies', 'href': 'https://eu-careers.europa.eu/en/jobs/' + ID,
        'external_id': ID, 'title': TITLE, 'closes_at': '2026-09-14T21:14:00Z'})
    node = job.raw['europol_public_vacancy']
    assert node['referenceNumber'] == 'Europol/2026/TA/AD9/769'
    for section in ('Organisational Context', 'Functions and duties', 'Eligibility criteria',
                    'Selection criteria', 'Selection procedure', 'Compensation and benefits',
                    'Terms and conditions', 'Additional information'):
        assert section in job.description
    assert '70 302 5022' in job.description
    assert job.closes_at == datetime(2026, 9, 14, 21, 59, 59, tzinfo=timezone.utc)
    assert job.closes_tz == 'Europe/Amsterdam'
    assert job.closes_at_local == '2026-09-14T23:59:59+02:00'
    assert job.employment_type == 'Restricted Temporary Agent'
    assert job.department == 'O1  Operational and Analysis Centre (OAC)'
    assert job.raw['_eu_official_field_resolution']['utc_resolved'] is True


def changed_node(field, value):
    body = FIXTURE.read_text()
    start = re.search(r'window\.SERVER_DATA\s*=\s*', body).end()
    data, length = json.JSONDecoder().raw_decode(body[start:])
    data['NodeLoader']['node'][field] = value
    return body[:start] + json.dumps(data) + body[start + length:]


@pytest.mark.parametrize(('field', 'value'), [
    ('id', 1081), ('alias', '/work-with-us/careers/open-vacancies/vacancy/1081'),
    ('referenceNumber', 'Europol/2026/TA/AD9/768'), ('title', 'A different post'),
    ('type', 'article'), ('body', 'Apply now'), ('deadline', True), ('published', None),
])
def test_wrong_or_incomplete_public_record_is_rejected(field, value):
    with pytest.raises(ValueError, match='Europol'):
        _europol_public_vacancy(changed_node(field, value), URL, ID, TITLE)


@pytest.mark.parametrize('body', [
    '<div>Loading</div>', '<script>window.SERVER_DATA=fetch("evil")</script>',
    FIXTURE.read_text() + '<script>window.SERVER_DATA={}</script>',
])
def test_missing_malformed_or_duplicate_assignment_is_rejected(body):
    with pytest.raises(ValueError, match='Europol'):
        _europol_public_vacancy(body, URL, ID, TITLE)


def test_board_title_may_omit_official_department_and_grade_suffix():
    body = (FIXTURE.parent / 'europol_174_server_data_20260913.html').read_text()
    url = URL.rsplit('/', 1)[0] + '/1081'
    node = _europol_public_vacancy(body, url, 'europol-2026-ca-fgiv-174', 'Senior Agent – Cybersecurity Awareness')
    assert node['title'].endswith('Security Risk Management & Services Unit')
    with pytest.raises(ValueError, match='identity'):
        _europol_public_vacancy(body, url, 'europol-2026-ca-fgiv-174', 'Senior Agent – Cybersecurity Awarenes')
