import copy
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from jobagg.adapters.base import AdapterContext
from jobagg.adapters.taleo import TaleoAdapter
from jobagg.db import JobDatabase
from jobagg.models import OrganizationSource

FIXTURE = Path(__file__).parent / 'fixtures/fao/detail_2601865_20260913.html'
PROVENANCE = json.loads(FIXTURE.with_suffix('.provenance.json').read_text())
URL = PROVENANCE['original_request_url']


def adapter():
    return TaleoAdapter(AdapterContext(OrganizationSource(
        id='fao_taleo', name='FAO', ats_family='taleo', base_url='https://jobs.fao.org',
        extra={'enumerate_job_locales': True, 'tz': 'GMT+02:00', 'tzname': 'Europe/Rome'}), object()))


def listing():
    return {'contestNo': '2601865', '_taleo_detail_url': URL,
            '_taleo_posting_locale': 'es', '_taleo_available_locales': ['es'],
            '_taleo_locale_listings': {'es': {'contestNo': '2601865'}},
            '_taleo_language_inventory': {'locales': {'es': {'observed_ids': ['2601865']}}}}


def test_fresh_spanish_posting_pins_actual_request_and_published_urls_with_same_clock():
    obj = adapter()
    body = FIXTURE.read_text()
    original = obj.parse_detail_html(body, URL)
    requests = []
    obj.fetch_text = lambda url: requests.append(url) or body
    item = listing()
    before = copy.deepcopy(item)
    job = obj.fetch_detail_for_listing_item(item)
    assert requests[1] == URL + '&lang=es'
    assert job.apply_url == job.source_url == job.raw['detail_url'] == requests[1]
    assert job.raw['_taleo_listing']['_taleo_detail_url'] == URL
    assert job.description == original.description
    assert job.raw['detail_html'] == body
    assert job.closes_at == original.closes_at
    assert job.closes_at.isoformat() == '2026-09-16T21:59:00+00:00'
    assert job.closes_tz == original.closes_tz == 'Europe/Rome'
    assert job.closes_at_local == original.closes_at_local
    assert parse_qs(urlsplit(job.apply_url).query)['tz'] == ['GMT+02:00']
    assert item == before


@pytest.mark.parametrize('change', ['missing_locale_row', 'different_public_id', 'unadvertised_locale', 'different_inventory', 'wrong_url_id', 'duplicate_url_id', 'injected_locale'])
def test_locale_and_identity_fail_before_any_request(change):
    obj, item = adapter(), listing()
    if change == 'missing_locale_row':
        item['_taleo_locale_listings'] = {}
    elif change == 'different_public_id':
        item['_taleo_locale_listings']['es']['contestNo'] = '2601999'
    elif change == 'unadvertised_locale':
        item['_taleo_available_locales'] = ['en']
    elif change == 'different_inventory':
        item['_taleo_language_inventory']['locales']['es']['observed_ids'] = ['2601999']
    elif change == 'wrong_url_id':
        item['_taleo_detail_url'] = URL.replace('2601865', '2601999')
    elif change == 'duplicate_url_id':
        item['_taleo_detail_url'] += '&job=2601865'
    else:
        item['_taleo_posting_locale'] = 'es&job=2601999'
    requests = []
    obj.fetch_text = lambda url: requests.append(url) or FIXTURE.read_text()
    with pytest.raises(ValueError):
        obj.fetch_detail_for_listing_item(item)
    assert requests == []


def test_valid_locale_request_cannot_promote_portal_fallback_to_detail():
    obj = adapter()
    obj.fetch_text = lambda url: '<title>Job portal</title><p>This job is unavailable.</p>'
    with pytest.raises(ValueError, match='identity differs'):
        obj.fetch_detail_for_listing_item(listing())


def test_pinning_only_changes_locale_and_keeps_all_other_query_components():
    obj = adapter()
    localized = obj._posting_locale_url(URL + '&lang=en&portal=123', 'es', '2601865')
    original = parse_qs(urlsplit(URL + '&portal=123').query)
    assert parse_qs(urlsplit(localized).query) == {**original, 'lang': ['es']}
    assert obj._posting_locale_url(localized, 'es', '2601865') == localized


def test_listing_locale_refresh_updates_public_link_without_retiming_captured_dates(tmp_path):
    obj = adapter()
    original = obj.parse_detail_html(FIXTURE.read_text(), URL)
    original.raw['detail_html'] = FIXTURE.read_text()
    db = JobDatabase(tmp_path / 'jobs.sqlite3')
    db.initialize()
    db.upsert_job(original)
    for _ in range(2):
        incoming = obj.parse_listing_item({'contestNo': '2601865', 'title': original.title, 'url': URL})
        obj._bind_listing_locale(incoming, 'es')
        db.upsert_job(incoming)
    stored = db.get_job(original.identity_key())
    assert stored['apply_url'] == stored['source_url'] == URL + '&lang=es'
    assert stored['raw']['detail_url'] == stored['raw']['_taleo_detail_url'] == URL
    assert stored['raw']['_taleo_deadline_resolution'] == original.raw['_taleo_deadline_resolution']
    assert stored['description'] == original.description
    assert stored['closes_at'] == original.closes_at.isoformat()


def test_inline_multilingual_details_receive_locale_before_fetch(monkeypatch):
    obj = adapter()
    obj.source.extra.update(search_api_url='https://jobs.fao.org/search?lang=en',
                            warmup_search_page=False, fetch_details=True)
    requests = []

    def post(self, url, payload, headers=None):
        locale = parse_qs(urlsplit(url).query)['lang'][0]
        rows = [{'contestNo': '2601865', 'title': 'Role', 'url': URL}] if locale == 'es' else []
        return {'requisitionList': rows, 'pagingData': {'totalCount': len(rows), 'pageSize': 10},
                'facetResults': [{'id': 'JOB_LOCALE', 'facetValueResults': [{'id': 'en'}, {'id': 'es'}]}]}

    monkeypatch.setattr(TaleoAdapter, 'post_json', post)
    monkeypatch.setattr(TaleoAdapter, 'fetch_text', lambda self, url: requests.append(url) or FIXTURE.read_text())
    jobs = obj.fetch_jobs()
    assert len(jobs) == 1
    assert jobs[0].apply_url == URL + '&lang=es'
    assert jobs[0].raw['_taleo_record_kind'] == 'detail'
    assert jobs[0].raw['_taleo_available_locales'] == ['es']
    assert [u for u in requests if 'jobdetail.ftl' in u] == [URL + '&lang=es']
