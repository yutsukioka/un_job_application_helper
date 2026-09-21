from datetime import datetime
from urllib.parse import parse_qs, urlsplit

import pytest

from jobagg.adapters.base import AdapterContext
from jobagg.adapters.taleo import TaleoAdapter
from jobagg.models import OrganizationSource


def adapter(**extra):
    source = OrganizationSource(id='fao_taleo', name='FAO', ats_family='taleo', base_url='https://jobs.fao.org', extra=extra)
    return TaleoAdapter(AdapterContext(source, object()))


def detail(value='22/Sep/2026, 12:59:00 AM'):
    values = [''] * 33
    values[4] = 'Job Description - Specialist (2502737)'
    values[10:15] = ['2502737', 'Specialist', '07/Sep/2026', '07/Sep/2026', value]
    values[32] = '%3Cp%3EComplete vacancy description.%3C/p%3E'
    return "api.fillList('requisitionDescriptionInterface', 'descRequisition', " + repr(values) + ');'


URL = 'https://jobs.fao.org/jobdetail.ftl?job=2502737'
PAIR = '&tz=GMT%2B03%3A00&tzname=Africa%2FNairobi'


def test_english_captured_clock_is_bound_to_explicit_requested_timezone():
    job = adapter().parse_detail_html(detail(), URL + PAIR)
    assert job.closes_at.isoformat() == '2026-09-21T21:59:00+00:00'
    assert job.closes_at_local == '22/Sep/2026, 12:59:00 AM'
    assert job.closes_tz == 'Africa/Nairobi'
    assert job.raw['_taleo_record_kind'] == 'detail'
    assert job.raw['_taleo_deadline_resolution']['kind'] == 'known_instant'


@pytest.mark.parametrize('value,expected', [('12:00:00 AM', 0), ('12:00:00 PM', 12), ('1:00:00 PM', 13), ('11:00:00 AM', 11)])
def test_twelve_hour_clock_boundaries(value, expected):
    assert TaleoAdapter._localized_taleo_date('22/Sep/2026, ' + value).hour == expected


@pytest.mark.parametrize('value', ['0:00:00 AM', '13:00:00 PM', '25:00:00'])
def test_invalid_clock_is_not_normalized(value):
    assert TaleoAdapter._localized_taleo_date('22/Sep/2026, ' + value) is None


@pytest.mark.parametrize('query', ['', '&tzname=Africa%2FNairobi', '&tz=GMT%2B02%3A00&tzname=Africa%2FNairobi', '&tz=GMT%2B03%3A00&tzname=Invalid%2FZone', PAIR + '&tzname=Africa%2FNairobi', PAIR + '&tz=GMT%2B03%3A00', '&tzname=Europe%2FRome' + PAIR, '&tz=&tzname=Africa%2FNairobi', '&tz=GMT%2B03%3A00&tzname='])
def test_unbound_or_conflicting_timezone_retains_clock_without_false_utc(query):
    job = adapter().parse_detail_html(detail(), URL + query)
    assert job.closes_at is None and job.closes_tz is None
    assert job.closes_at_local == '22/Sep/2026, 12:59:00 AM'
    assert job.raw['_taleo_deadline_resolution']['kind'] == 'unknown_timezone'


@pytest.mark.parametrize('value,kind', [('Continuo', 'open_ended'), ('Ongoing', 'open_ended'), ('Contact HR', 'unparsed')])
def test_no_invented_deadline_from_open_ended_or_unparsed_public_value(value, kind):
    job = adapter().parse_detail_html(detail(value), URL + PAIR)
    assert job.closes_at is None
    assert job.raw['_taleo_deadline_resolution']['kind'] == kind


def test_dst_gap_or_fold_and_configured_seasonal_offset_conflict_fail_closed():
    assert TaleoAdapter._bound_deadline(datetime(2026, 3, 29, 2, 30), {'tz': 'GMT+01:00', 'tzname': 'Europe/Rome'}) is None
    assert TaleoAdapter._bound_deadline(datetime(2026, 10, 25, 2, 30), {'tz': 'GMT+02:00', 'tzname': 'Europe/Rome'}) is None
    assert TaleoAdapter._bound_deadline(datetime(2026, 12, 1, 12), {'tz': 'GMT+02:00', 'tzname': 'Europe/Rome'}) is None


def test_cached_unqualified_detail_url_gets_the_same_pair_as_new_urls():
    obj = adapter(enumerate_job_locales=True, tz='GMT+03:00', tzname='Africa/Nairobi', detail_url_template=URL)
    requests = []
    obj.fetch_text = lambda url: requests.append(url) or detail()
    row = obj.parse_listing_item({'contestNo': '2502737', 'title': 'Specialist'})
    assert row.raw['_taleo_record_kind'] == 'listing'
    assert parse_qs(urlsplit(row.apply_url).query)['tz'] == ['GMT+03:00']
    job = obj.fetch_detail_for_listing_item({'contestNo': '2502737', '_taleo_detail_url': URL})
    assert requests == [URL + PAIR]
    assert job.closes_tz == 'Africa/Nairobi'
    assert obj._with_detail_timezone(URL + PAIR) == URL + PAIR


@pytest.mark.parametrize('query', ['&tzname=Africa%2FNairobi', '&tz=GMT%2B03%3A00', PAIR + '&tzname=UTC'])
def test_partial_or_duplicate_timezone_pair_cannot_be_silently_combined(query):
    with pytest.raises(ValueError, match='timezone pair'):
        adapter(enumerate_job_locales=True, tz='GMT+02:00', tzname='Europe/Rome')._with_detail_timezone(URL + query)


def test_fallback_page_is_not_claimed_as_structured_detail():
    job = adapter().parse_detail_html('<title>Job portal</title><p>Navigation only</p>', URL)
    assert '_taleo_record_kind' not in job.raw
