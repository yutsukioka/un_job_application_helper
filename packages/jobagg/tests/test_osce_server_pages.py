"""Actual native browser document walks; no fixture replaces HTTP admission."""
import gzip
import hashlib
import json
from pathlib import Path
import re
import time

import pytest

from jobagg.adapters.osce_inventory import parse_bundle
from jobagg.osce_fragments import DATA_ROUTE, SERVER_NAVIGATION
from jobagg.pipelines.http_checkpoint import HostIneligible
from test_osce_fullboard import page
from test_osce_native_browser import inputs, native as native, records


def server_fixture(native, monkeypatch, *, counts=(2, 2, 1)):
    browser, origin, replies, calls = native
    monkeypatch.setattr('jobagg.adapters.osce_inventory.SESSION',
                        re.compile(re.escape(origin) + r'/jobs/search/\d+(?:/page\d+)?/?'))
    browser.capture.phase = {'kind': 'listing'}
    browser.contract.update(data_route=DATA_ROUTE, navigation=SERVER_NAVIGATION,
                            ready_selector='#jPaginateCurrPage')
    replies['/jobs/search/'] = (307, {'Location': '/jobs/search/987654',
                                    'Set-Cookie': 'anon=fresh; Path=/; Secure; HttpOnly'}, '')
    index, total = 1, sum(counts)
    for number, count in enumerate(counts, 1):
        html = page(number, total, *range(index, index+count), total_pages=len(counts))['html']
        html = ('<html><head><style>body{display:none}</style>'
                '<script src="/js-dict"></script><link rel="stylesheet" href="/style.css">'
                '</head><body><script>fetch("/never");document.body.textContent="Wrong";</script>'
                '<img src="/image.jpg"><iframe src="/child"></iframe>' + html + '</body></html>')
        path = '/jobs/search/987654' + (f'/page{number}' if number > 1 else '')
        replies[path] = (200, {'Content-Type': 'text/html'}, html)
        index += count
    return browser, origin, replies, calls


def bundle(response):
    return json.loads(re.fullmatch(r'<script[^>]+>(.*)</script>', response.text, re.S)[1])


@pytest.mark.parametrize('counts', [(2, 2, 1), (2, 2, 2, 2, 1), (1,), (0,)])
def test_full_server_document_walk_has_exact_raw_page_proof_and_no_ui_requests(native, monkeypatch, counts):
    browser, origin, replies, calls = server_fixture(native, monkeypatch, counts=counts)
    response = browser.render(origin + '/jobs/search/')
    source, _, _, _ = inputs()
    jobs, total, count = parse_bundle(source, response.text)
    assert (total, count) == (sum(counts), len(counts))
    assert {j.raw['provider_id'] for j in jobs} == {str(i) for i in range(1, total+1)}
    assert [p for p, _ in calls] == ['/jobs/search/', '/jobs/search/987654'] + [
        f'/jobs/search/987654/page{i}' for i in range(2, len(counts)+1)]
    assert all(cookie == 'anon=fresh' for _, cookie in calls[1:])
    assert all(m['method'] == 'GET' and m['transport_diagnostics']['resource_type'] == 'Document'
               for m in records(browser))
    assert records(browser)[0]['transport_diagnostics']['redirect_url'] == origin + '/jobs/search/987654'
    for proof in bundle(response):
        path = Path(proof['capture_path'])
        meta = json.loads(path.read_text())
        raw = gzip.decompress(Path(meta['artifact']).read_bytes())
        assert proof['capture_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert proof['response_sha256'] == hashlib.sha256(raw).hexdigest()
        assert proof['html'] == raw.decode()
        assert proof['request_url'] == meta['url']
        assert proof['navigation'] == SERVER_NAVIGATION
    receipt = json.loads(browser.last_receipt.read_text())
    assert receipt['complete'] is False  # Independent verifier certifies census.
    assert receipt['contract']['navigation'] == SERVER_NAVIGATION


@pytest.mark.parametrize('outcome', ['repeated_page', 'overlap', 'wrong_session', 'filtered',
                                   'missing_title', 'missing_ids', 'denied', 'query', 'next_page_redirect'])
def test_invalid_document_cannot_certify_or_dispatch_unreviewed_page(native, monkeypatch, outcome):
    browser, origin, replies, calls = server_fixture(native, monkeypatch)
    status, headers, html = replies['/jobs/search/987654/page2']
    if outcome == 'repeated_page':
        html = replies['/jobs/search/987654'][2]
    elif outcome == 'overlap':
        html = page(2, 5, 2, 3, total_pages=3)['html']
    elif outcome == 'filtered':
        html = html.replace('data-keywords=""', 'data-keywords="manager"')
    elif outcome == 'missing_title':
        html = html.replace('Role 3', '')
    elif outcome == 'missing_ids':
        html = page(2, 5, total_pages=3)['html']
    elif outcome == 'denied':
        status, html = 403, '<title>Access Forbidden</title>'
    else:
        status, html = 302, ''
        path = {'wrong_session': '/jobs/search/123/page2', 'query': '/jobs/search/987654/page2?x=1',
                'next_page_redirect': '/jobs/search/987654/page3'}[outcome]
        headers = {'Location': path}
    replies['/jobs/search/987654/page2'] = (status, headers, html)
    with pytest.raises(Exception) as error:
        browser.render(origin + '/jobs/search/')
    assert not isinstance(error.value, HostIneligible), 'Invalid page must not become routine churn'
    assert [p for p, _ in calls] == ['/jobs/search/', '/jobs/search/987654', '/jobs/search/987654/page2']
    assert browser.last_receipt is None
    if outcome == 'denied':
        assert records(browser)[-1]['failure_category'] == 'access_denied'
        hold = next(browser.capture.lock_root.glob('host-*.json'))
        assert json.loads(hold.read_text())['stopped'] is True


@pytest.mark.parametrize('change', ['total', 'pages'])
def test_valid_midwalk_inventory_change_is_bounded_cooldown(native, monkeypatch, change):
    browser, origin, replies, calls = server_fixture(native, monkeypatch)
    replies['/jobs/search/987654/page2'] = (200, {'Content-Type': 'text/html'},
        page(2, 6 if change == 'total' else 5, 3, 4,
             total_pages=4 if change == 'pages' else 3)['html'])
    started = time.time()
    with pytest.raises(HostIneligible) as error:
        browser.render(origin + '/jobs/search/')
    assert error.value.category == 'cooldown'
    assert started + 900 <= error.value.eligible_at <= time.time() + 900
    assert len(records(browser)) == 3
    assert records(browser)[-1]['status_code'] == 200
    assert browser.last_receipt is None
    assert not any(json.loads(p.read_text()).get('stopped') for p in browser.capture.lock_root.glob('host-*.json'))


def test_detail_still_uses_prior_raw_data_behavior(native):
    browser, origin, replies, calls = native
    browser.capture.phase = {'kind': 'detail'}
    browser.contract.update(data_route=DATA_ROUTE, navigation=SERVER_NAVIGATION)
    replies['/jobs/role-123'] = (200, {'Content-Type': 'text/html'},
        '<h1>Role detail</h1><p>Full duties</p><script src="/js-dict"></script>')
    response = browser.render(origin + '/jobs/role-123')
    assert response.text == replies['/jobs/role-123'][2]
    assert [p for p, _ in calls] == ['/jobs/role-123']
    assert browser.server_pages is False and browser.data_only is True
