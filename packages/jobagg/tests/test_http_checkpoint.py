import gzip
import hashlib
import json
import time
import urllib.error
import urllib.request

import pytest

from jobagg.http import HTTPError, HttpResponse, JobAggHTTPClient
from jobagg.pipelines.http_checkpoint import DurableCapture, HostIneligible, RedirectHop, safe_url
from jobagg.robots import load_policy


def capture(tmp_path, monkeypatch, transport, *, robots=False, **kwargs):
    monkeypatch.setattr("jobagg.pipelines.http_checkpoint.time.sleep", lambda _: None)
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        f"default:\n  honor_robots_txt: {str(robots).lower()}\n  min_delay_seconds: 0\n"
    )
    client = JobAggHTTPClient(
        default_headers={"Authorization": "private", "Cookie": "private", "X-Token": "private"}
    )
    client._request = transport
    result = DurableCapture(
        client,
        load_policy(policy_path),
        tmp_path / "evidence",
        {},
        lock_root=tmp_path / "hosts",
        default_header_origin="https://one.example",
        phase={"kind": "detail", "job_id": "123"},
        **kwargs,
    )
    return result


def response(url, text="Public body", status=200):
    return HttpResponse(url, status, {"Content-Type": "text/html"}, text, text.encode())


def test_durable_intent_precedes_transport_and_binary_receipt_is_exact(tmp_path, monkeypatch):
    def transport(url, **kwargs):
        intent = json.loads((tmp_path / "evidence/http/00001.json").read_text())
        assert intent["state"] == "dispatched_before_response"
        assert "finished_at" not in intent
        return response(url, "Exact public UTF8 é")

    guarded = capture(tmp_path, monkeypatch, transport)
    guarded.request("https://one.example/123", method="GET")
    meta = json.loads((tmp_path / "evidence/http/00001.json").read_text())
    body = gzip.decompress(open(meta["artifact"], "rb").read())
    assert body == "Exact public UTF8 é".encode()
    assert meta["body_sha256"] == hashlib.sha256(body).hexdigest()
    assert meta["state"] == "response_captured" and meta["phase"]["job_id"] == "123"


def test_first_challenge_is_durable_stop_even_after_cooldown(tmp_path, monkeypatch):
    calls = []

    def transport(url, **kwargs):
        calls.append(url)
        return response(url, "<title>Just a moment</title>Verify you are human")

    guarded = capture(tmp_path, monkeypatch, transport)
    with pytest.raises(RuntimeError, match="access challenge"):
        guarded.request("https://one.example/123", method="GET")
    state_path = next((tmp_path / "hosts").glob("*.json"))
    state = json.loads(state_path.read_text())
    assert state["stopped"] is True
    state["eligible_at"] = 0
    state_path.write_text(json.dumps(state))
    with pytest.raises(HostIneligible, match="Existing host stop"):
        guarded.request("https://one.example/456", method="GET")
    assert len(calls) == 1


def test_redirect_checks_actual_destination_robots_and_strips_credentials(tmp_path, monkeypatch):
    calls = []
    guarded = None

    def transport(url, **kwargs):
        calls.append((url, dict(guarded.client.default_headers), kwargs.get("headers") or {}))
        if url.endswith("/robots.txt"):
            return response(url, "User-agent: *\nAllow: /\n")
        if url == "https://one.example/123":
            req = urllib.request.Request(
                "https://two.example/notice",
                headers={"Authorization": "bad", "Cookie": "bad", "Accept": "text/html"},
            )
            raise RedirectHop(req, 302)
        return response(url)

    guarded = capture(tmp_path, monkeypatch, transport, robots=True)
    guarded.request("https://one.example/123", method="GET")
    assert [c[0] for c in calls] == [
        "https://one.example/robots.txt",
        "https://one.example/123",
        "https://two.example/robots.txt",
        "https://two.example/notice",
    ]
    for url, defaults, headers in calls[2:]:
        assert not {key.lower() for key in (*defaults, *headers)} & {
            "authorization",
            "cookie",
            "x-token",
        }
    assert len(list((tmp_path / "hosts").glob("*.lock"))) == 2


def test_retry_after_preserved_and_transport_called_once(tmp_path, monkeypatch):
    calls = []

    def transport(url, **kwargs):
        calls.append(url)
        try:
            raise urllib.error.HTTPError(url, 429, "Slow down", {"Retry-After": "7200"}, None)
        except urllib.error.HTTPError as original:
            raise HTTPError("rate limit") from original

    guarded = capture(tmp_path, monkeypatch, transport)
    before = time.time()
    with pytest.raises(HTTPError):
        guarded.request("https://one.example/123", method="GET")
    state = json.loads(next((tmp_path / "hosts").glob("*.json")).read_text())
    assert state["eligible_at"] >= before + 7200
    assert calls == ["https://one.example/123"] and guarded.client.max_retries == 0


def test_deadline_and_redirect_downgrade_dispatch_no_extra_request(tmp_path, monkeypatch):
    calls = []
    guarded = capture(
        tmp_path,
        monkeypatch,
        lambda *args, **kwargs: calls.append(args),
        deadline_at=time.time() - 1,
    )
    with pytest.raises(HostIneligible, match="deadline"):
        guarded.request("https://one.example/123", method="GET")
    assert not calls
    guarded.deadline_at = None

    def transport(url, **kwargs):
        calls.append(url)
        raise RedirectHop(urllib.request.Request("http://one.example/123"), 302)

    guarded.original = transport
    with pytest.raises(HostIneligible, match="downgrade"):
        guarded.request("https://one.example/123", method="GET")
    assert len(calls) == 1


def test_diagnostic_url_hides_query_secret():
    assert "actual-secret" not in safe_url("https://one.example/job?token=actual-secret&id=123")
    with pytest.raises(ValueError):
        safe_url("https://user:password@one.example/job")


@pytest.mark.parametrize('remaining', [0.345, 4.9, 5.9])
def test_under_budget_requests_do_not_dispatch_or_poison_host(tmp_path, monkeypatch, remaining):
    guarded = capture(tmp_path, monkeypatch, lambda *a, **k: pytest.fail('under-budget dispatch'),
                      deadline_at=time.time() + remaining)
    with pytest.raises(HostIneligible) as error:
        guarded.request('https://one.example/123')
    assert error.value.category == 'budget'
    assert guarded.dispatched == 0
    for path in (tmp_path / 'hosts').glob('*.json'):
        state = json.loads(path.read_text())
        assert not state.get('stopped') and not state.get('recovery')


def test_pacing_budget_does_not_charge_half_open_probe(tmp_path, monkeypatch):
    from jobagg.pipelines.host_recovery import transient_failure
    guarded = capture(tmp_path, monkeypatch, lambda *a, **k: pytest.fail('paced dispatch'))
    now = time.time()
    stem = 'host-' + hashlib.sha256(b'one.example').hexdigest()[:24]
    lock = tmp_path / 'hosts' / (stem + '.lock')
    state_path = lock.with_suffix('.json')
    state = transient_failure({}, now - 4000, 'transient_transport', 'old', 0)
    state_path.write_text(json.dumps(state))
    lock.write_text(str(now))
    guarded.deadline_at = now + 10  # Eight seconds pacing leaves < six seconds.
    with pytest.raises(HostIneligible) as error:
        guarded.request('https://one.example/123')
    assert error.value.category == 'budget'
    assert json.loads(state_path.read_text()) == state


@pytest.mark.parametrize('native', [False, True])
@pytest.mark.parametrize('headers_received', [False, True])
def test_deadline_clipped_timeout_defers_then_next_batch_succeeds(tmp_path, monkeypatch, native, headers_received):
    clock = [100.0]
    monkeypatch.setattr('jobagg.pipelines.http_checkpoint.time.monotonic', lambda: clock[0])

    def timeout(url, **kwargs):
        clock[0] += kwargs['timeout_seconds']
        # Native dispatch has no elapsed_seconds diagnostic; checkpoint timing
        # must work independently, including when a response body is incomplete.
        guarded.client.last_request_diagnostics = {'headers_received': headers_received}
        if headers_received:
            guarded.client.last_request_diagnostics.update(status_code=200, wire_bytes_read=17)
        raise TimeoutError('read')

    guarded = capture(tmp_path, monkeypatch, timeout,
                      deadline_at=time.time() + 10)
    with pytest.raises(HostIneligible) as error:
        guarded.request('https://one.example/123', **({'_native_dispatch': timeout} if native else {}))
    assert error.value.category == 'budget'
    meta = json.loads((tmp_path / 'evidence/http/00001.json').read_text())
    assert meta['failure_category'] == 'local_budget' and meta['deadline_limited']
    assert meta['transport_elapsed_seconds'] == pytest.approx(meta['effective_timeout_seconds'])
    assert meta['transport_diagnostics']['headers_received'] is headers_received
    assert meta['body_captured'] is False
    if headers_received:
        assert meta['status_code'] == 200 and meta['transport_diagnostics']['wire_bytes_read'] == 17
    state = json.loads(next((tmp_path / 'hosts').glob('*.json')).read_text())
    assert not state.get('recovery') and not state.get('stopped')
    guarded.deadline_at = time.time() + 60
    guarded.original = lambda url, **kw: response(url)
    assert guarded.request('https://one.example/123').status_code == 200


@pytest.mark.parametrize('native', [False, True])
def test_early_timeout_with_clipped_allowance_charges_host_recovery(tmp_path, monkeypatch, native):
    clock = [100.0]
    monkeypatch.setattr('jobagg.pipelines.http_checkpoint.time.monotonic', lambda: clock[0])

    def timeout(url, **kwargs):
        clock[0] += 0.25
        raise TimeoutError('early connection timeout')

    guarded = capture(tmp_path, monkeypatch, timeout, deadline_at=time.time() + 10)
    # A previous request's diagnostics cannot establish expiration or a status.
    guarded.client.last_request_diagnostics = {'elapsed_seconds': 90, 'headers_received': True, 'status_code': 403}
    with pytest.raises(TimeoutError):
        guarded.request('https://one.example/123', **({'_native_dispatch': timeout} if native else {}))
    meta = json.loads((tmp_path / 'evidence/http/00001.json').read_text())
    assert meta['deadline_limited'] and meta['failure_category'] == 'transient_transport'
    assert meta['transport_elapsed_seconds'] == 0.25
    assert 'status_code' not in meta and 'transport_diagnostics' not in meta
    state = json.loads(next((tmp_path / 'hosts').glob('*.json')).read_text())
    assert state['recovery']['failures'] == 1 and not state['stopped']


@pytest.mark.parametrize('status, category', [(403, 'access_denied'), (429, 'rate_limit')])
def test_http_error_status_wins_over_exhausted_clipped_body_timeout(tmp_path, monkeypatch, status, category):
    clock = [100.0]
    monkeypatch.setattr('jobagg.pipelines.http_checkpoint.time.monotonic', lambda: clock[0])

    def timeout(url, **kwargs):
        clock[0] += kwargs['timeout_seconds']
        guarded.client.last_request_diagnostics = {'headers_received': True, 'status_code': status}
        try:
            raise urllib.error.HTTPError(url, status, 'denied', {'Retry-After': '7200'}, None)
        except urllib.error.HTTPError as cause:
            raise TimeoutError('error body timed out') from cause

    guarded = capture(tmp_path, monkeypatch, timeout, deadline_at=time.time() + 10)
    before = time.time()
    with pytest.raises(TimeoutError):
        guarded.request('https://one.example/123')
    meta = json.loads((tmp_path / 'evidence/http/00001.json').read_text())
    assert meta['deadline_limited'] and meta['failure_category'] == category
    assert meta['status_code'] == status and meta['transport_diagnostics']['headers_received']
    state = json.loads(next((tmp_path / 'hosts').glob('*.json')).read_text())
    if status == 403:
        assert state['stopped'] and state['failure_category'] == 'access_denied'
    else:
        assert state['recovery']['failures'] == 1 and state['eligible_at'] >= before + 7200


def test_recovery_probe_requires_full_timeout_not_clipped_batch_remainder(tmp_path, monkeypatch):
    from jobagg.pipelines.host_recovery import transient_failure
    guarded = capture(tmp_path, monkeypatch, lambda *a, **k: pytest.fail('clipped recovery probe'))
    now = time.time()
    state_path = tmp_path / 'hosts' / ('host-' + hashlib.sha256(b'one.example').hexdigest()[:24] + '.json')
    state = transient_failure({}, now - 4000, 'transient_transport', 'old', 0)
    state_path.write_text(json.dumps(state))
    guarded.deadline_at = now + 10
    with pytest.raises(HostIneligible) as error:
        guarded.request('https://one.example/123')
    assert error.value.category == 'budget'
    assert json.loads(state_path.read_text()) == state
    record = json.loads((tmp_path / 'evidence/http/00001.json').read_text())
    assert record['failure_category'] == 'local_budget'
    assert record['failure_stage'] == 'local_pre_dispatch' and not record['body_captured']
