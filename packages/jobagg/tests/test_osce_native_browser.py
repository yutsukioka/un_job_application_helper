import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import ssl
import subprocess
import threading

import pytest

from jobagg.http import ResponseTooLargeError
from jobagg.osce_native_browser import OSCENativeBrowser, TRANSPORT
from jobagg.pipelines.host_recovery import authorize_native_browser_probe, host_eligibility
from jobagg.pipelines.sync_source import load_sources


def inputs():
    source = next(s for s in load_sources(Path(__file__).parents[1] / 'config/organizations.yaml') if s.id == 'osce_custom_html')
    state = {'stopped': True, 'failure_category': 'access_denied', 'evidence': 'old.json', 'last_request_at': 100}
    evidence = {'url': source.extra['listing_url'], 'method': 'GET', 'status_code': 403, 'error_type': 'HTTPError',
                'failure_category': 'access_denied', 'phase': {'kind': 'listing'}}
    review = {'url': source.extra['listing_url'], 'observation': 'user_confirmed_browser_access', 'observed_at': 150}
    return source, state, evidence, review


def test_native_recovery_is_single_owner_one_attempt_and_new_denial_invalidates():
    source, state, evidence, review = inputs()
    updated = authorize_native_browser_probe(state, evidence, source, owner='owner', now=200, expires_at=700, access_review=review)
    assert state == {'stopped': True, 'failure_category': 'access_denied', 'evidence': 'old.json', 'last_request_at': 100}
    assert updated['last_request_at'] == 100
    assert host_eligibility(updated, 201, probe_owner='owner')['allowed']
    assert not host_eligibility(updated, 201, probe_owner='worker')['allowed']
    assert not host_eligibility(updated, 701, probe_owner='owner')['allowed']
    assert not host_eligibility({**updated, 'evidence': 'new.json'}, 201, probe_owner='owner')['allowed']
    updated.pop('reviewed_native_browser_probe')
    with pytest.raises(ValueError, match='already attempted'):
        authorize_native_browser_probe(updated, evidence, source, owner='owner', now=210, expires_at=710, access_review=review)


@pytest.mark.parametrize('patch', [{'transport': TRANSPORT}, {'status_code': 429}, {'method': 'POST'},
                                   {'url': 'https://vacancies.osce.org/styles/core.css'}, {'phase': {'kind': 'detail'}}])
def test_native_recovery_rejects_other_denials(patch):
    source, state, evidence, review = inputs()
    with pytest.raises(ValueError):
        authorize_native_browser_probe(state, {**evidence, **patch}, source, owner='owner', now=200, expires_at=700, access_review=review)


def test_native_recovery_requires_recent_review_and_native_config():
    source, state, evidence, review = inputs()
    for change in ({'observed_at': -90000}, {'observed_at': 250}, {'observation': 'guess'}):
        with pytest.raises(ValueError):
            authorize_native_browser_probe(state, evidence, source, owner='owner', now=200, expires_at=700, access_review={**review, **change})
    source.extra['browser_render'].pop('transport')
    with pytest.raises(ValueError):
        authorize_native_browser_probe(state, evidence, source, owner='owner', now=200, expires_at=700, access_review=review)


@pytest.fixture
def native(tmp_path, monkeypatch):
    api = pytest.importorskip('playwright.async_api')
    from test_browser_fetch import make_browser
    from jobagg.http_safe import SafeHTTPPolicy
    cert, key = tmp_path / 'cert.pem', tmp_path / 'key.pem'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(key),
                    '-out', str(cert), '-days', '1', '-subj', '/CN=localhost'], check=True, capture_output=True)
    pub = subprocess.run(['openssl', 'x509', '-in', str(cert), '-pubkey', '-noout'], check=True, capture_output=True).stdout
    der = subprocess.run(['openssl', 'pkey', '-pubin', '-outform', 'DER'], input=pub, check=True, capture_output=True).stdout
    pin = base64.b64encode(hashlib.sha256(der).digest()).decode()
    original_launch = api.BrowserType.launch

    async def launch(self, **kwargs):
        # Trust ONLY this generated local fixture certificate. Production
        # transport has no TLS bypass or certificate override.
        kwargs['args'] = [*kwargs.get('args', []), '--ignore-certificate-errors-spki-list=' + pin]
        return await original_launch(self, **kwargs)

    monkeypatch.setattr(api.BrowserType, 'launch', launch)
    replies, calls = {}, []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append((self.path, self.headers.get('Cookie', '')))
            value = replies.get(self.path, replies.get(__import__('urllib.parse', fromlist=['urlsplit']).urlsplit(self.path).path, (404, {}, 'missing')))
            status, headers, body = value(self) if callable(value) else value
            data = body.encode()
            self.send_response(status)
            for k, v in (headers.items() if isinstance(headers, dict) else headers):
                self.send_header(k, v)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_POST = do_GET

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert, key)
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'https://localhost:{server.server_port}'
    old, client = make_browser(tmp_path, monkeypatch, {})
    client.safe_policy = SafeHTTPPolicy({'localhost'}, resolver=lambda _: ['8.8.8.8'])
    # Deliberate test-only transport seam: production never maps public to local.
    # Keep the policy strict while letting the fixture exercise Chromium TLS.
    from jobagg import browser_proxy
    from jobagg.http_safe import ValidatedEndpoint
    real_dial = browser_proxy._dial
    async def fixture_dial(endpoint, timeout):
        assert endpoint.host == 'localhost' and endpoint.addresses == ('8.8.8.8',)
        return await real_dial(ValidatedEndpoint(endpoint.host, endpoint.port, ('127.0.0.1',)), timeout)
    monkeypatch.setattr(browser_proxy, '_dial', fixture_dial)
    old.capture.default_header_origin = old.capture.origin(url)
    renderer = OSCENativeBrowser(client, old.capture, {
        'url_patterns': ['^' + re.escape(url) + '/.*$'], 'ready_selector': 'h1',
        # Six-hop inventories need pacing plus a useful final request allowance.
        'detail_ready_selector': 'h1', 'timeout_seconds': 60, 'load_stylesheets': False,
        'transport': TRANSPORT,
    })
    yield renderer, url, replies, calls
    server.shutdown()
    server.server_close()
    thread.join()


def records(renderer):
    return [json.loads(p.read_text()) for p in sorted((renderer.capture.target / 'http').glob('*.json'))]


def test_native_redirect_cookie_script_data_and_captured_bodies(native):
    browser, url, replies, calls = native
    replies['/start'] = (302, {'Location': '/job', 'Set-Cookie': 'session=anonymous; Path=/; Secure'}, '')
    replies['/job'] = (200, {'Content-Type': 'text/html'}, '<link rel="stylesheet" href="/style.css"><h1>Role</h1><script>fetch("/data").then(r=>r.text()).then(t=>document.body.append(t))</script>')
    replies['/data'] = (200, {'Content-Type': 'text/plain'}, 'Full duties')
    response = browser.render(url + '/start')
    assert 'Full duties' in response.text
    assert [p for p, _ in calls] == ['/start', '/job', '/data']
    assert all('session=anonymous' in cookie for _, cookie in calls[1:])
    captured = records(browser)
    assert [r['status_code'] for r in captured] == [302, 200, 200]
    assert all(r['transport'] == TRANSPORT and r['body_captured'] for r in captured)
    assert 'anonymous' not in json.dumps(captured)


def test_native_denial_persists_body_and_stops_before_scripts(native):
    browser, url, replies, calls = native
    replies['/deny'] = (403, {'Content-Type': 'text/html'}, '<h1>Access Forbidden</h1><script>fetch("/must-not-run")</script>')
    with pytest.raises(Exception):
        browser.render(url + '/deny')
    assert [p for p, _ in calls] == ['/deny']
    record = records(browser)[0]
    assert record['status_code'] == 403 and record['body_captured']
    assert record['failure_category'] == 'access_denied'
    hold = next(browser.capture.lock_root.glob('host-*.json'))
    assert json.loads(hold.read_text())['stopped']
    with pytest.raises(Exception):
        browser.render(url + '/deny')
    assert len(calls) == 1


def test_native_redirect_to_unreviewed_origin_never_dispatched(native):
    browser, url, replies, calls = native
    replies['/start'] = (302, {'Location': 'https://unreviewed.invalid/private'}, '')
    with pytest.raises(Exception):
        browser.render(url + '/start')
    assert len(calls) == 1
    assert len(records(browser)) == 1


def test_native_byte_limit(native):
    browser, url, replies, calls = native
    browser.client.max_response_bytes = 30
    replies['/large'] = (200, {'Content-Type': 'text/html'}, '<h1>' + 'x' * 100 + '</h1>')
    with pytest.raises(ResponseTooLargeError):
        browser.render(url + '/large')
    assert len(calls) == 1



def test_native_fullboard_pagination_uses_same_session(native, monkeypatch):
    browser, url, replies, calls = native
    from test_osce_fullboard import page as fixture_page
    pattern = re.compile(re.escape(url) + r"/jobs/search/\d+/")
    monkeypatch.setattr("jobagg.osce_native_browser.SESSION", pattern)
    monkeypatch.setattr("jobagg.adapters.osce_inventory.SESSION", pattern)
    first = fixture_page(1, 3, 1, 2)["html"]
    second = fixture_page(2, 3, 3)["html"]
    def decorate(body):
        return body.replace('<strong>3</strong> results', '<div class="number_of_results"><strong>3</strong> results</div>')
    html = decorate(first) + '<div id="jPaginationHldr"><button onclick="fetch(\'/next\').then(r=>r.text()).then(t=>document.body.innerHTML=t)">2</button></div>'
    replies['/jobs/search/'] = (302, {'Location': '/jobs/search/987/'}, '')
    replies['/jobs/search/987/'] = (200, {'Content-Type': 'text/html'}, html)
    replies['/next'] = (200, {'Content-Type': 'text/html'}, decorate(second))
    browser.capture.phase = {'kind': 'listing'}
    browser.contract['ready_selector'] = '.number_of_results'
    response = browser.render(url + '/jobs/search/')
    from jobagg.adapters.osce_inventory import parse_bundle
    source, _, _, _ = inputs()
    jobs, total, count = parse_bundle(source, response.text)
    assert total == 3 and count == 2 and {j.external_id for j in jobs} == {'1', '2', '3'}
    assert len(calls) == 3


def test_native_same_host_redirect_ceiling(native):
    browser, url, replies, calls = native
    browser.client.safe_policy.max_redirects = 1
    replies['/a'] = (302, {'Location': '/b'}, '')
    replies['/b'] = (302, {'Location': '/c'}, '')
    replies['/c'] = (200, {}, '<h1>Never fetched</h1>')
    with pytest.raises(Exception, match='redirect'):
        browser.render(url + '/a')
    assert [p for p, _ in calls] == ['/a', '/b']


def test_native_200_challenge_stops(native):
    browser, url, replies, calls = native
    replies['/challenge'] = (200, {'Content-Type': 'text/html'}, '<title>Access Forbidden</title><h1>No</h1>')
    with pytest.raises(Exception, match='challenge'):
        browser.render(url + '/challenge')
    assert records(browser)[0]['failure_category'] == 'access_denied'
    assert len(calls) == 1


def test_native_csp_blocks_child_network_surfaces(native):
    browser, url, replies, calls = native
    replies['/job'] = (200, {'Content-Type': 'text/html'}, '''<h1>Public role</h1>
        <iframe src="/frame"></iframe><script>
        try { new Worker('/worker'); } catch(e) {}
        window.open('/popup');
        </script>''')
    browser.render(url + '/job')
    assert [p for p, _ in calls] == ['/job']


@pytest.mark.parametrize('complete', [True, False])
@pytest.mark.parametrize('mode', ['native-browser', 'osce-data', 'osce-csrf'])
def test_native_command_releases_only_complete_listing(tmp_path, monkeypatch, complete, mode):
    from contextlib import nullcontext
    from types import SimpleNamespace
    import time
    from jobagg import recover_public_sources as command
    from jobagg.models import SourceRunDiagnostics
    source, state, evidence, review = inputs()
    if mode == 'osce-csrf':
        from test_osce_csrf import inputs as csrf_inputs
        source, state, evidence, review = csrf_inputs()
    if mode == 'osce-data':
        evidence.update(url='https://vacancies.osce.org/js-dict?v=fixture', transport=TRANSPORT)
        review.update(observation='har_verified_full_listing', complete_captured_listing=True, har_sha256='a' * 64)
    evidence_path = tmp_path / 'evidence.json'
    evidence_path.write_text(json.dumps(evidence))
    state['evidence'] = str(evidence_path)
    review['observed_at'] = time.time()
    review_path = tmp_path / 'review.json'
    review_path.write_text(json.dumps(review))
    policy_root = tmp_path / 'policy'
    (policy_root / 'hosts').mkdir(parents=True)
    hold = policy_root / 'hosts' / ('host-' + hashlib.sha256(b'vacancies.osce.org').hexdigest()[:24] + '.json')
    hold.write_text(json.dumps(state))
    finishes, reserves = [], []
    policy = SimpleNamespace(root=policy_root, source_hold=lambda _: False,
                             reserve=lambda *args: reserves.append(args),
                             finish=lambda *args: finishes.append(args))
    adapter = SimpleNamespace(fetch_jobs=lambda: [], run_diagnostics=SourceRunDiagnostics(
        source_id=source.id, pagination_complete=True))
    worker = SimpleNamespace(by_id={source.id: source}, shared_policy=policy,
                             shared_lock=tmp_path / 'owner.lock', workspace=tmp_path / 'workspace',
                             binding={'implementation_sha256': 'fixture'},
                             policy_due=lambda *args: 0,
                             context=lambda *args: (adapter, None, SimpleNamespace(probe_owner='reviewer')))
    monkeypatch.setattr(command, 'Worker', lambda **kwargs: worker)
    monkeypatch.setattr(command, 'shared_owner', lambda _: nullcontext())
    monkeypatch.setattr(command, 'verify_listing', lambda *args: {'complete': complete})
    argv = ['--registry', 'unused', '--robots', 'unused', '--workspace', str(worker.workspace),
            '--shared-lock', str(worker.shared_lock), '--output-dir', str(tmp_path / 'out'),
            '--source', source.id, '--recovery-mode', mode, '--access-evidence', str(review_path)]
    assert command.main(argv) == 0
    assert not reserves and json.loads(hold.read_text()) == state
    if complete:
        assert command.main([*argv, '--execute']) == 0
    else:
        with pytest.raises(ValueError, match='reconciliation'):
            command.main([*argv, '--execute'])
    latest = json.loads(hold.read_text())
    assert latest['stopped'] is (not complete)
    lease, attempt = {'native-browser':('reviewed_native_browser_probe','native_browser_attempt'),
                      'osce-data':('reviewed_osce_data_probe','osce_data_attempt'),
                      'osce-csrf':('reviewed_osce_csrf_probe','osce_csrf_attempt')}[mode]
    assert lease not in latest
    assert latest[attempt]['evidence'] == state['evidence']
    assert latest['last_request_at'] == 100
    assert len(reserves) == len(finishes) == 1
    if not complete:
        with pytest.raises(ValueError, match='already attempted'):
            command.main([*argv, '--execute'])
        assert len(reserves) == 1


def test_native_divergent_dns_cannot_reach_loopback(native, monkeypatch):
    from jobagg import browser_proxy
    browser, url, replies, calls = native
    attempts = []
    async def reject_public(endpoint, timeout):
        attempts.append(endpoint)
        raise OSError("Validated public destination unavailable")
    monkeypatch.setattr(browser_proxy, '_dial', reject_public)
    replies['/job'] = (200, {}, '<h1>Must not reach loopback</h1>')
    with pytest.raises(Exception):
        browser.render(url + '/job')
    assert attempts and all(e.addresses == ('8.8.8.8',) for e in attempts)
    assert calls == []
