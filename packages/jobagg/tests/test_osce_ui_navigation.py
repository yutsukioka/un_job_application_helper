"""Exercise publisher-driven pagination through the native capture boundary."""
import gzip
import json
import re
from urllib.parse import parse_qs, urlsplit
from pathlib import Path

import pytest

from jobagg.adapters.osce_inventory import parse_bundle
from jobagg.osce_fragments import DATA_ROUTE, UI_NAVIGATION
from test_osce_native_browser import inputs, native as native, records


def panel(number, ids, *, active=False):
    return (
        '<div class="jResultsContent' + (' jResultsActive' if active else '') + '" '
        'data-keywords="" data-location-ids="" data-keyword-string="All jobs" '
        'data-location-string="All locations">'
        '<input type="checkbox" aria-label="New Jobs">'
        f'<div id="jPaginateCurrPage">{number}</div><div id="jPaginateNumPages">2.0</div>'
        '<div class="number_of_results"><strong>3</strong> results</div>'
        + ''.join(f'<a class="job_link" href="https://vacancies.osce.org/jobs/role-{i}">Role {i}</a>' for i in ids)
        + ('<div class="jPagination"><a aria-label="next" href="#page2">Next</a></div>' if number == 1 else '')
        + '</div>'
    )


def ui_fixture(native, monkeypatch, *, outcome='complete'):
    browser, origin, replies, calls = native
    pattern = re.compile(re.escape(origin) + r'/jobs/search/\d+(?:/page\d+)?/?')
    monkeypatch.setattr('jobagg.osce_native_browser.SESSION', pattern)
    monkeypatch.setattr('jobagg.adapters.osce_inventory.SESSION', pattern)
    browser.capture.phase = {'kind': 'listing'}
    browser.contract.update(data_route=DATA_ROUTE, navigation=UI_NAVIGATION,
                            ready_selector='.number_of_results', load_stylesheets=True)
    replies['/jobs/search/'] = (302, {'Location': '/jobs/search/123',
                                    'Set-Cookie': 'anon=fresh; Path=/; Secure; HttpOnly'}, '')
    replies['/jobs/search/123'] = (200, {'Content-Type': 'text/html'},
        '<html><head><style id="antiClickjacking">body{display:none !important}</style>'
        '<link rel="stylesheet" href="/missing.css">'
        '<script src="https://unreviewed.invalid/analytics.js"></script><script src="/rfl.apply.js"></script></head><body>'
        '<input type="hidden" name="tsstoken" id="tsstoken" value="fresh-token">'
        '<div id="jResultsArea">' + panel(1, [1, 2], active=True) + '</div>'
        '<script>var TVAPP={site:{short_name:"default1"}};</script>'
        '<script src="/ui.js"></script></body></html>')
    # The collector never constructs this request. Its only action is the
    # visible Next click; the publisher script supplies its anonymous state.
    script = '''document.querySelector('#antiClickjacking').remove();
        fetch('/ajax/content/login_content?uid=7', {method:'POST',headers:{'tss-token':'fresh-token'}});
        document.addEventListener('click', async e => {
            if (!e.target.matches('a[aria-label="next"]')) return;
            e.preventDefault();
            const token = document.querySelector('#tsstoken').value;
            const r = await fetch('/ajax/content/job_results?JobSearch.id=123&page_index=2&site-name=default1&include_site=true&uid=8',
                {method:'POST',headers:{'tss-token':TOKEN}});
            if (r.status !== 200) return;
            const payload = await r.json();
            const old = document.querySelector('.jResultsActive');
            old.classList.remove('jResultsActive'); old.style.display='none';
            document.querySelector('#jResultsArea').insertAdjacentHTML('afterbegin', payload.Result);
            document.querySelector('.jResultsContent').classList.add('jResultsActive');
            TAMPER
            history.replaceState(null,'','/jobs/search/123/page2');
        });'''.replace('TOKEN', "'wrong-token'" if outcome == 'wrong_header' else 'token')
    script = script.replace('TAMPER', "document.querySelector('.jResultsActive a.job_link').textContent='Wrong title';"
                            if outcome == 'tamper' else "document.querySelector('.jResultsActive input').checked=true;" if outcome == 'tamper_filter' else '')
    replies['/ui.js'] = (200, {'Content-Type': 'application/javascript'}, script)
    replies['/ajax/content/login_content'] = (200, {'Content-Type': 'application/json'}, '{"Status":"OK","Result":""}')
    def fragment(handler):
        assert handler.command == 'POST'
        assert handler.headers.get('tss-token') == 'fresh-token'
        assert handler.headers.get('Cookie') == 'anon=fresh'
        assert parse_qs(urlsplit(handler.path).query)['page_index'] == ['2']
        return (403, {}, 'Forbidden') if outcome == 'denied' else (200,
            {'Content-Type': 'application/json'}, json.dumps({'Status':'OK', 'Result':panel(2, [3])}))
    replies['/ajax/content/job_results'] = fragment
    return browser, origin, replies, calls


def test_ui_driven_next_uses_scripts_cookies_and_raw_captures_with_cached_panels(native, monkeypatch):
    browser, origin, replies, calls = ui_fixture(native, monkeypatch)
    response = browser.render(origin + '/jobs/search/')
    source, _, _, _ = inputs()
    jobs, total, count = parse_bundle(source, response.text)
    assert (total, count) == (3, 2)
    assert {j.raw['provider_id'] for j in jobs} == {'1', '2', '3'}
    assert any(p == '/ui.js' for p, _ in calls)
    assert len([p for p, _ in calls if p.startswith('/ajax/content/job_results?')]) == 1
    assert not any('unreviewed.invalid' in p or p.startswith('/ajax/content/login_content')
                   or p == '/rfl.apply.js' for p, _ in calls)
    receipt = json.loads(browser.last_receipt.read_text())
    assert any(r['reason'] == 'missing_ui_asset' for r in receipt['omitted_resources'])
    pages = json.loads(re.fullmatch(r'<script[^>]+>(.*)</script>', response.text, re.S)[1])
    captures = records(browser)
    for page in pages:
        meta = json.loads(Path(page['capture_path']).read_text())
        raw = gzip.decompress(Path(meta['artifact']).read_bytes()).decode()
        assert page['html'] == (raw if page['number'] == 1 else json.loads(raw)['Result'])
        assert meta['url'] == page['request_url']
    fragment_meta = next(m for m in captures if '/job_results?' in m['url'])
    assert fragment_meta['transport_diagnostics']['csrf_observation'] == {
        'available': True, 'present': True, 'matches_document': True}


@pytest.mark.parametrize('outcome', ['denied', 'wrong_header', 'tamper', 'tamper_filter'])
def test_ui_stops_on_denial_or_unbound_request_or_altered_display(native, monkeypatch, outcome):
    browser, origin, replies, calls = ui_fixture(native, monkeypatch, outcome=outcome)
    with pytest.raises(Exception):
        browser.render(origin + '/jobs/search/')
    captured = records(browser)
    if outcome == 'denied':
        assert captured[-1]['status_code'] == 403
        assert captured[-1]['failure_category'] == 'access_denied'
        assert captured[-1]['body_captured'] is True
    if outcome == 'wrong_header':
        assert not any('/job_results?' in p for p, _ in calls)



def test_only_reviewed_osce_ui_contract_has_measured_startup_budget():
    from jobagg.browser_fetch import BrowserContractError, GuardedBrowser

    contract = {"url_patterns": [r"^https://vacancies\.osce\.org/jobs/search/$"],
                "ready_selector": ".number_of_results", "timeout_seconds": 360,
                "transport": "chromium_cdp_native_v1", "data_route": DATA_ROUTE,
                "navigation": UI_NAVIGATION, "inventory": "osce_full_search_v1"}
    assert GuardedBrowser(None, None, contract).timeout == 360
    for key in ("transport", "data_route", "navigation", "inventory"):
        with pytest.raises(BrowserContractError, match="300"):
            GuardedBrowser(None, None, {k:v for k,v in contract.items() if k != key})
    with pytest.raises(BrowserContractError, match="360"):
        GuardedBrowser(None, None, {**contract, "timeout_seconds": 361})
