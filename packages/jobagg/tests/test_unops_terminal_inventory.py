import gzip
import hashlib
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from jobagg.adapters.avature import AvatureAdapter
from jobagg.adapters.base import AdapterContext
from jobagg.models import OrganizationSource
from jobagg.pipelines.inventory_recovery_contracts import verify_recovery_listing


ENDPOINT = 'https://careers.unops.org/careersmarketplace/SearchJobs'


def html_page(ids, total):
    return ('<div class="list-controls__text__legend" aria-label="' + str(total) + ' results"></div>'
            + ''.join(f'<article class="article--result"><a href="/careersmarketplace/JobDetail/Role/{key}">Role</a></article>' for key in ids))


def fixture(tmp_path, total, *, old_terminator=False, max_pages=20):
    source = OrganizationSource('unops_avature', 'UNOPS', 'avature', ENDPOINT, extra={
        'listing_url': ENDPOINT, 'page_size': 6, 'max_pages': max_pages, 'fetch_details': False})
    adapter = AvatureAdapter(AdapterContext(source, None))
    captures = []

    def fetch(url):
        page = int(parse_qs(urlsplit(url).query)['jobOffset'][0])
        body = html_page(range(page + 1, min(page + 6, total) + 1), total).encode()
        path = tmp_path / f'{page:04}.json'
        blob = path.with_suffix('.gz'); blob.write_bytes(gzip.compress(body))
        path.write_text(json.dumps({'phase': {'kind': 'listing'}, 'state': 'response_captured',
            'method': 'GET', 'status_code': 200, 'url': url, 'response_url': url,
            'body_captured': True, 'artifact': str(blob), 'body_sha256': hashlib.sha256(body).hexdigest(),
            'started_at': '2026-09-30T00:00:00+00:00', 'finished_at': '2026-09-30T00:00:01+00:00'}))
        captures.append(path)
        return body.decode()

    adapter.fetch_text = fetch
    jobs = adapter.fetch_jobs()
    if old_terminator:
        fetch(ENDPOINT + f'?jobRecordsPerPage=6&jobOffset={total}')
    return source, jobs, captures


@pytest.mark.parametrize('total', [0, 6, 12, 89, 90, 91])
def test_advertised_total_terminates_without_extra_request(tmp_path, total):
    source, jobs, captures = fixture(tmp_path, total)
    assert len(jobs) == total
    assert len(captures) == max(1, (total + 5) // 6)
    result = verify_recovery_listing(source, jobs, captures)
    assert result['complete'], result
    assert result['reported_total'] == total


def test_verified_empty_terminal_page_from_old_collector_is_accepted(tmp_path):
    source, jobs, captures = fixture(tmp_path, 90, old_terminator=True)
    result = verify_recovery_listing(source, jobs, captures)
    assert result['complete'] and result['pages'] == 16
    assert len(result['capture_paths']) == 16


@pytest.mark.parametrize('damage', ['nonempty', 'changed_total', 'wrong_offset', 'second_empty', 'truncated', 'cap'])
def test_terminal_fix_keeps_strict_inventory_contract(tmp_path, damage):
    source, jobs, captures = fixture(tmp_path, 12, old_terminator=True)
    meta = json.loads(captures[-1].read_text())
    if damage in {'nonempty', 'changed_total'}:
        body = html_page([12] if damage == 'nonempty' else [], 13 if damage == 'changed_total' else 12).encode()
        from pathlib import Path
        Path(meta['artifact']).write_bytes(gzip.compress(body))
        meta['body_sha256'] = hashlib.sha256(body).hexdigest()
    elif damage == 'wrong_offset':
        meta['url'] = meta['url'].replace('jobOffset=12', 'jobOffset=18')
    elif damage == 'second_empty':
        captures.append(captures[-1])
    elif damage == 'truncated':
        captures = captures[:1]
    elif damage == 'cap':
        source.extra['max_pages'] = 2
    if damage not in {'truncated', 'second_empty'}:
        captures[-1].write_text(json.dumps(meta))
    assert not verify_recovery_listing(source, jobs, captures)['complete']
