"""OSCE-only browser selection preserves the ordinary detail capture guards."""
import gzip
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from jobagg.browser_fetch import BrowserContractError, GuardedBrowser, install_browser_transport
from jobagg.http import HttpResponse
from jobagg.http_safe import SafeHTTPPolicy
from jobagg.osce_native_browser import OSCENativeBrowser
from jobagg.pipelines.http_checkpoint import HostIneligible
from test_browser_fetch import make_browser
from test_osce_native_browser import inputs

URL = 'https://vacancies.osce.org/jobs/public-role-4998'


def source():
    value, _, _, _ = inputs()
    assert value.extra['browser_render']['listing_only'] is True
    return value


def test_listing_still_installs_native_server_document_renderer():
    client = SimpleNamespace(_request=lambda *args, **kwargs: None)
    capture = SimpleNamespace(phase={'kind': 'listing'})
    renderer = install_browser_transport(client, capture, source())
    assert isinstance(renderer, OSCENativeBrowser)
    assert renderer.contract['navigation'] == 'osce_server_pages_v1'
    assert capture.browser_renderer is renderer
    assert client._request.__self__ is renderer


def guarded_detail(tmp_path, monkeypatch):
    raw = '<h1>Public role</h1><div class="job_description">Full duties and qualifications.</div>'
    browser, client = make_browser(tmp_path, monkeypatch, {URL: raw})
    capture = browser.capture
    capture.phase = {'kind': 'detail', 'job_id': 'public-role-4998'}
    capture.current_id = 'public-role-4998'
    capture.default_header_origin = capture.origin(URL)
    client.safe_policy = SafeHTTPPolicy({'vacancies.osce.org'}, resolver=lambda _: ['8.8.8.8'])
    client._request = capture.request
    assert install_browser_transport(client, capture, source()) is None
    assert client._request.__self__ is capture
    assert getattr(capture, 'browser_renderer', None) is None
    return client, capture, raw


def test_detail_dispatches_ordinary_http_and_keeps_durable_raw_evidence(tmp_path, monkeypatch):
    client, capture, raw = guarded_detail(tmp_path, monkeypatch)
    response = client.get(URL)
    assert response.text == raw and client.calls == [URL] and capture.dispatched == 1
    assert client.tls_verify is True and client.max_retries == 0
    meta_path = capture.target / 'http/00001.json'
    meta = json.loads(meta_path.read_text())
    assert meta['url'] == meta['response_url'] == URL and meta['method'] == 'GET'
    assert meta['body_captured'] is True and meta['phase']['kind'] == 'detail'
    assert meta.get('transport') != 'chromium_cdp_native_v1'
    assert gzip.decompress(Path(meta['artifact']).read_bytes()) == raw.encode()


def test_detail403_still_captures_denial_and_stops_further_dispatch(tmp_path, monkeypatch):
    client, capture, _ = guarded_detail(tmp_path, monkeypatch)
    calls = []
    def denied(url, **kwargs):
        calls.append(url)
        return HttpResponse(url, 403, {'Content-Type': 'text/html'},
                            '<title>Access Forbidden</title>', b'<title>Access Forbidden</title>')
    capture.original = denied
    with pytest.raises(Exception):
        client.get(URL)
    meta = json.loads((capture.target / 'http/00001.json').read_text())
    assert meta['status_code'] == 403 and meta['failure_category'] == 'access_denied'
    assert meta['body_captured'] is True
    hold = next(capture.lock_root.glob('host-*.json'))
    assert json.loads(hold.read_text())['stopped'] is True
    with pytest.raises(HostIneligible):
        client.get(URL)
    assert calls == [URL]


@pytest.mark.parametrize('phase', [None, {}, {'kind': 'document'}, {'kind': 'unknown'}, 'listing'])
def test_listing_only_never_guesses_an_unknown_phase(phase):
    with pytest.raises(BrowserContractError, match='explicit task phase'):
        install_browser_transport(SimpleNamespace(), SimpleNamespace(phase=phase), source())


@pytest.mark.parametrize('flag', ['true', 'false', 1, 0, None])
def test_listing_only_requires_a_real_boolean(flag):
    value = source()
    value.extra['browser_render']['listing_only'] = flag
    with pytest.raises(BrowserContractError, match='boolean'):
        install_browser_transport(SimpleNamespace(), SimpleNamespace(phase={'kind': 'detail'}), value)


@pytest.mark.parametrize('field', ['source_id', 'transport', 'navigation', 'inventory'])
def test_listing_only_cannot_silently_bypass_another_browser_contract(field):
    value = source()
    if field == 'source_id':
        value.id = 'another_source'
    else:
        value.extra['browser_render'][field] = 'another_contract'
    with pytest.raises(BrowserContractError, match='reviewed for OSCE'):
        install_browser_transport(SimpleNamespace(), SimpleNamespace(phase={'kind': 'detail'}), value)


def test_old_osce_configuration_keeps_native_detail_selection():
    value = source()
    value.extra['browser_render'].pop('listing_only')
    client, capture = SimpleNamespace(), SimpleNamespace(phase={'kind': 'detail'})
    assert isinstance(install_browser_transport(client, capture, value), OSCENativeBrowser)


def test_other_browser_source_keeps_existing_selection():
    value = SimpleNamespace(id='another_source', extra={'browser_render':{
        'url_patterns':[r'^https://jobs\.example\.test/jobs/\d+$'],
        'ready_selector':'h1', 'timeout_seconds':120}})
    client, capture = SimpleNamespace(), SimpleNamespace(phase={'kind': 'detail'})
    assert type(install_browser_transport(client, capture, value)) is GuardedBrowser
