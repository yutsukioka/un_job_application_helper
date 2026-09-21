from datetime import datetime, timezone
from pathlib import Path

import pytest

from jobagg.adapters.base import AdapterContext
from jobagg.adapters.taleo import TaleoAdapter
from jobagg.models import OrganizationSource

FIXTURE = Path(__file__).parent / 'fixtures/taleo/adb_260856_manila_20260913.html'
BASE = 'https://adb.taleo.net/careersection/1/jobdetail.ftl?job=260856'


def adapter():
    return TaleoAdapter(AdapterContext(OrganizationSource('adb_taleo', 'ADB', 'taleo',
        'https://adb.taleo.net/careersection/1/jobsearch.ftl',
        extra={'tz': 'GMT+08:00', 'tzname': 'Asia/Manila'}), None))


def test_current_manila_payload_fixes_five_hour_unqualified_deadline_error():
    a = adapter()
    url = a._with_detail_timezone(BASE)
    job = a.parse_detail_html(FIXTURE.read_text(), url)
    assert job.external_id == '260856'
    assert job.closes_at == datetime(2026, 9, 21, 15, 59, tzinfo=timezone.utc)
    assert job.posted_at == datetime(2026, 9, 7, 3, 10, 28, tzinfo=timezone.utc)
    assert job.closes_at_local == '21-Sep-2026, 11:59:00 PM'
    assert job.closes_tz == 'Asia/Manila'
    assert job.raw['_taleo_deadline_resolution']['kind'] == 'known_instant'
    assert job.raw['_taleo_posting_time_resolution']['kind'] == 'known_instant'


@pytest.mark.parametrize('query', ['', '&tz=GMT%2B08%3A00', '&tzname=Asia%2FManila',
    '&tz=GMT%2B07%3A00&tzname=Asia%2FManila',
    '&tz=GMT%2B08%3A00&tzname=Asia%2FManila&tzname=Etc%2FUTC'])
def test_unqualified_or_ambiguous_source_clock_never_becomes_utc(query):
    job = adapter().parse_detail_html(FIXTURE.read_text(), BASE + query)
    assert job.closes_at is None and job.posted_at is None
    assert job.closes_at_local == '21-Sep-2026, 11:59:00 PM'
    assert job.raw['_taleo_deadline_resolution']['kind'] == 'unknown_timezone'


@pytest.mark.parametrize('source_id,zone,offset', [('who_taleo','Europe/Zurich','GMT+02:00'),
    ('wipo_taleo','Europe/Zurich','GMT+02:00'), ('iaea_taleo','Europe/Vienna','GMT+02:00')])
def test_configured_timezone_is_pinned_without_fao_specific_flags(source_id,zone,offset):
    source = OrganizationSource(source_id, source_id, 'taleo', 'https://example.taleo.net',extra={'tz':offset,'tzname':zone})
    a = TaleoAdapter(AdapterContext(source,None))
    url = a._with_detail_timezone('https://example.taleo.net/jobdetail.ftl?job=123')
    assert 'tz=GMT%2B02%3A00' in url and 'tzname=Europe%2F' in url
    assert a._with_detail_timezone(url) == url
