import pytest
from jobagg.pipelines.host_recovery import release_reviewed_allowlist_hold
from jobagg.http_safe import SafeHTTPPolicy, SSRFProtectionError, allowed_hosts_for_source
from types import SimpleNamespace


def inputs():
    return ({'stopped': True, 'reason': 'HTTP transport failure: SSRFProtectionError',
             'eligible_at': 123, 'last_request_at': 100, 'consecutive_transport_failures': 2},
            {'url': 'https://docs.example/robots.txt', 'error_type': 'SSRFProtectionError',
             'error': 'SSRFProtectionError: URL host is not in the organization allowlist'})


def release(s, e):
    return release_reviewed_allowlist_hold(s, e, host='docs.example', reviewed_hosts={'docs.example'}, reviewed_at='now')


def test_explicit_migration_preserves_pacing_and_input():
    s,e=inputs(); result=release(s,e)
    assert s['stopped'] and not result['stopped']
    for k in ('eligible_at','last_request_at','consecutive_transport_failures'): assert result[k]==s[k]


@pytest.mark.parametrize('patch', [{'reason':'HTTP 403'}, {'inherited_stop':{'status':403}},
    {'failure_category':'access_denied'}, {'recovery':{'phase':'half_open'}}, {'stopped':False}])
def test_other_holds_cannot_be_released(patch):
    s,e=inputs();s.update(patch)
    with pytest.raises(ValueError): release(s,e)


@pytest.mark.parametrize('patch', [{'error':'SSRFProtectionError: URL resolves to a denied network'},
    {'url':'https://different.example/robots.txt'}, {'status_code':403}, {'error_type':'HTTPError'}])
def test_evidence_must_prove_exact_allowlist_failure(patch):
    s,e=inputs();e.update(patch)
    with pytest.raises(ValueError):release(s,e)


def test_exact_document_origin_does_not_allow_other_hosts_or_private_addresses():
    source=SimpleNamespace(base_url='https://jobs.example',extra={'reviewed_document_origins':['https://docs.example/']})
    hosts=allowed_hosts_for_source(source)
    policy=SafeHTTPPolicy(hosts,resolver=lambda _:['8.8.8.8'])
    assert policy.validate_url('https://docs.example/file.pdf')=='docs.example'
    with pytest.raises(SSRFProtectionError):policy.validate_url('https://evil.docs.example/file.pdf')
    policy.resolver=lambda _:['127.0.0.1']
    with pytest.raises(SSRFProtectionError):policy.validate_url('https://docs.example/file.pdf')


def test_new_allowlist_failure_never_stops_host(tmp_path):
    import json,hashlib
    from jobagg.http import JobAggHTTPClient
    from jobagg.pipelines.http_checkpoint import DurableCapture
    from jobagg.robots import load_policy
    policy=tmp_path/'robots.yaml'
    policy.write_text('default:\n  honor_robots_txt: false\n  min_delay_seconds: 0\n')
    client=JobAggHTTPClient()
    def denied(*args,**kwargs):
        raise SSRFProtectionError('URL host is not in the organization allowlist')
    client._request=denied
    capture=DurableCapture(client,load_policy(policy),tmp_path/'capture',{},lock_root=tmp_path/'hosts')
    with pytest.raises(SSRFProtectionError):capture.request('https://docs.example/file.pdf')
    state=json.loads((tmp_path/'hosts'/('host-'+hashlib.sha256(b'docs.example').hexdigest()[:24]+'.json')).read_text())
    assert not state.get('stopped') and not state.get('consecutive_transport_failures')
    record=json.loads((tmp_path/'capture/http/00001.json').read_text())
    assert record['failure_category']=='local_policy'
