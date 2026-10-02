import gzip
import hashlib
import io
import json
import urllib.error
import urllib.request

import pytest

from jobagg.http import HTTPError, HttpResponse, JobAggHTTPClient
from jobagg.models import OrganizationSource
from jobagg.pipelines.http_checkpoint import DurableCapture, RedirectHop
from jobagg.robots import load_policy
from jobagg.vacancy_outcomes import VacancyUnavailable, captured_unavailable, classify_unavailable


BASE = 'https://unhcr.wd3.myworkdayjobs.com/wday/cxs/unhcr/External'
URL = BASE + '/job/City/Role_JR123'
S22 = {'errorCode': 'S22', 'errorCaseId': '123456', 'httpStatus': 403,
       'message': 'permission denied', 'messageParams': {}}
SOURCE = OrganizationSource('unhcr_workday', 'UNHCR', 'workday',
                            'https://unhcr.wd3.myworkdayjobs.com/External', extra={'cxs_base_url': BASE})
UNOPS_URL = 'https://careers.unops.org/careersmarketplace/JobDetail/Role/4461'
UNOPS_ERROR = 'https://careers.unops.org/careersmarketplace/Error'


def make_capture(tmp_path, monkeypatch, transport, source=SOURCE, identity='JR123', kind='detail'):
    monkeypatch.setattr('jobagg.pipelines.http_checkpoint.time.sleep', lambda _: None)
    policy = tmp_path / 'policy.yaml'
    policy.write_text('default:\n  honor_robots_txt: false\n  min_delay_seconds: 0\n')
    client = JobAggHTTPClient(max_retries=0)
    client._request = transport
    guard = DurableCapture(client, load_policy(policy), tmp_path / 'capture', {},
        lock_root=tmp_path / 'hosts', source=source, phase={'kind': kind, 'job_id': identity})
    guard.current_id = identity
    return guard


def error_response(url, body=S22, status=403):
    is_json = isinstance(body, dict)
    content = json.dumps(body).encode() if is_json else body
    return HttpResponse(url, status, {'Content-Type': 'application/json' if is_json else 'text/html',
                                     'Set-Cookie': 'private-secret'}, content.decode(), content)


def test_full_bounded_error_body_survives_http_wrapper():
    client = JobAggHTTPClient(max_retries=0)
    class Opener:
        def open(self, request, timeout):
            raise urllib.error.HTTPError(URL, 403, 'Forbidden', {'Content-Type': 'application/json'},
                                         io.BytesIO(json.dumps(S22).encode()))
    client._opener = Opener()
    with pytest.raises(HTTPError) as caught:
        client.get(URL)
    assert caught.value.response.json() == S22


def test_s22_capture_is_bound_job_outcome_without_host_stop(tmp_path, monkeypatch):
    def transport(url, **kwargs):
        raise HTTPError('Only a truncated message', response=error_response(url))
    guard = make_capture(tmp_path, monkeypatch, transport)
    with pytest.raises(VacancyUnavailable):
        guard.request(URL, method='GET')
    paths = list((tmp_path / 'capture/http').glob('*.json'))
    meta = json.loads(paths[0].read_text())
    assert meta['state'] == 'response_captured' and meta['failure_category'] == 'vacancy_detail_denied'
    assert 'private-secret' not in paths[0].read_text()
    assert json.loads(gzip.decompress(open(meta['artifact'], 'rb').read())) == S22
    evidence = captured_unavailable(SOURCE.id, 'JR123', paths)
    assert evidence['category'] == 'vacancy_detail_denied'
    assert evidence['closure_inferred'] is evidence['detail_complete'] is False
    state = json.loads(next((tmp_path / 'hosts').glob('*.json')).read_text())
    assert not state.get('stopped')


@pytest.mark.parametrize('damage', ['listing', 'wrong_job', 'wrong_tenant', 'wrong_host', 'wrong_source',
                                  'generic403', 'challenge', 'auth401', 'non_s22', 'post'])
def test_unrecognized_denials_still_hold_host(tmp_path, monkeypatch, damage):
    body, url, status = S22.copy(), URL, 403
    kind, identity, method = 'detail', 'JR123', 'GET'
    source = SOURCE
    if damage == 'listing': kind, url = 'listing', BASE + '/jobs'
    if damage == 'wrong_job': identity = 'JR999'
    if damage == 'wrong_tenant': url = URL.replace('/unhcr/', '/other/')
    if damage == 'wrong_host': url = URL.replace('unhcr.wd3', 'other.wd3')
    if damage == 'wrong_source': source = None
    if damage == 'generic403': body = b'<h1>Permission denied</h1>'
    if damage == 'challenge': body = b'<title>Access denied</title><h1>Page not found</h1>'
    if damage == 'auth401': status = 401
    if damage == 'non_s22': body['errorCode'] = 'S23'
    if damage == 'post': method = 'POST'
    guard = make_capture(tmp_path, monkeypatch, lambda url, **kw: error_response(url, body, status),
                         source, identity, kind)
    with pytest.raises(urllib.error.HTTPError):
        guard.request(url, method=method)
    state = json.loads(next((tmp_path / 'hosts').glob('*.json')).read_text())
    assert state['stopped'] and state['failure_category'] == 'access_denied'
    assert captured_unavailable(SOURCE.id, identity, (tmp_path / 'capture/http').glob('*.json')) is None


@pytest.mark.parametrize('damage', [None, 'direct_error', 'wrong_job', 'wrong_host', 'challenge', 'script_only', 'live_job'])
def test_unops_requires_exact_detail_redirect_and_visible_unavailable_heading(tmp_path, monkeypatch, damage):
    source = OrganizationSource('unops_avature', 'UNOPS', 'avature', 'https://careers.unops.org/careersmarketplace')
    body = b'<h1>Page not found</h1>'
    if damage == 'challenge': body += b'<title>Access denied</title>'
    if damage == 'script_only': body = b'<script>"<h1>Page not found</h1>"</script>'
    if damage == 'live_job': body += b'<div>Position Title</div>'
    destination = UNOPS_ERROR.replace('careers.unops.org', 'other.example') if damage == 'wrong_host' else UNOPS_ERROR
    def transport(url, **kwargs):
        if url == UNOPS_URL:
            raise RedirectHop(urllib.request.Request(destination), 302)
        return error_response(url, body)
    guard = make_capture(tmp_path, monkeypatch, transport, source,
                         '9999' if damage == 'wrong_job' else '4461')
    with pytest.raises(VacancyUnavailable if damage is None else urllib.error.HTTPError):
        guard.request(UNOPS_ERROR if damage == 'direct_error' else UNOPS_URL, method='GET')
    evidence = captured_unavailable(source.id, '4461', (tmp_path / 'capture/http').glob('*.json'))
    if damage is None:
        assert evidence['detector'] == 'unops_exact_detail_error_not_found_v1'
        assert evidence['request_url'] == UNOPS_URL and len(evidence['captures']) == 2
    else:
        assert evidence is None


@pytest.mark.parametrize('damage', ['body_hash', 'source_binding', 'phase', 'changed_body', 'fake_category'])
def test_replay_requires_original_exact_captured_denial(tmp_path, monkeypatch, damage):
    guard = make_capture(tmp_path, monkeypatch, lambda url, **kw: error_response(url))
    with pytest.raises(VacancyUnavailable):
        guard.request(URL, method='GET')
    path = next((tmp_path / 'capture/http').glob('*.json'))
    meta = json.loads(path.read_text())
    body = gzip.decompress(open(meta['artifact'], 'rb').read())
    if damage == 'body_hash': meta['body_sha256'] = 'bad'
    if damage == 'source_binding': meta['source_binding']['source_id'] = 'other_workday'
    if damage == 'phase': meta['phase']['job_id'] = 'JR999'
    if damage == 'changed_body': body += b'changed'
    if damage == 'fake_category':
        body = b'<h1>Access denied</h1>'
        meta['body_sha256'] = hashlib.sha256(body).hexdigest()
        meta['failure_category'] = 'vacancy_detail_denied'
    assert classify_unavailable(SOURCE.id, 'JR123', meta, body) is None
