"""Checkpoint and worker integration: recovery never bypasses durable accounting."""
import json
import time
import ssl
import gzip
import hashlib
from pathlib import Path

import pytest
from jobagg.http import HTTPError, HttpResponse

import test_remediation_worker as fixtures

from jobagg.pipelines.host_recovery import transient_failure

FixtureClient = fixtures.FixtureClient
setup = fixtures.setup


def task_row(worker):
    with worker.db.connect() as db:
        return dict(db.execute("SELECT * FROM remediation_tasks WHERE kind='detail'").fetchone())


def test_second_timeout_then_success_preserves_all_three_charged_attempts(setup, monkeypatch):
    worker, replies, calls, _ = setup
    worker.max_tasks = 1
    worker.tick(execute=True)  # Listing only.
    url = next(url for url in replies if not url.endswith('/jobs'))
    good = replies[url]
    replies[url] = TimeoutError('read timeout')
    now = [time.time()]
    monkeypatch.setattr(time, 'time', lambda: now[0])
    for expected in (1, 2):
        worker.tick(execute=True)
        task = task_row(worker)
        assert task['status'] == 'pending' and task['attempts'] == expected
        assert not worker.host_state('demo.example')['stopped']
        now[0] = task['eligible_at'] + 1
    assert worker.host_state('demo.example')['recovery']['failures'] == 2
    replies[url] = good
    worker.tick(execute=True)
    task = task_row(worker)
    assert task['status'] == 'done' and task['attempts'] == 3
    events = worker.shared_policy.events('demo_workday')
    assert len(events) == 3
    assert 'Qualifications' in worker.db.get_job('demo_workday:R1')['description']
    assert 'recovery' not in worker.host_state('demo.example')
    assert len(calls) == 4


def test_http200_headers_then_body_timeout_remains_pending(setup):
    worker, replies, calls, _ = setup
    worker.max_tasks = 1
    worker.tick(execute=True)
    class PartialClient(FixtureClient):
        def _request(self, url, **kwargs):
            self.last_request_diagnostics = {'stage': 'body_read', 'headers_received': True,
                                             'status_code': 200, 'wire_bytes_read': 200}
            raise TimeoutError('body read')
    worker.client_factory = lambda source, policy: PartialClient(replies, calls)
    worker.tick(execute=True)
    task = task_row(worker)
    assert task['status'] == 'pending'
    receipt = json.loads(task['receipt'])
    from pathlib import Path
    capture = json.loads(Path(receipt['retry_decision']['capture']['path']).read_text())
    assert capture['status_code'] == 200 and capture['body_captured'] is False
    assert capture['failure_category'] == 'transient_transport'


def test_due_recovery_listing_is_preferred_without_charging_detail(setup):
    worker, _, _, _ = setup
    worker.max_tasks = 1
    worker.tick(execute=True)
    import hashlib
    path = worker.shared_policy.root / 'hosts' / ('host-' + hashlib.sha256(b'demo.example').hexdigest()[:24] + '.json')
    path.write_text(json.dumps(transient_failure({}, time.time() - 4000, 'transient_transport', 'old.json', 0)))
    with worker.db.connection_scope() as db:
        db.execute("UPDATE remediation_sources SET next_list_at=0 WHERE source_id='demo_workday'")
    worker.seed_listings()
    assert worker.choose()['kind'] == 'listing'
    assert worker.shared_policy.events('demo_workday') == []


def test_ssl_eof_retries_with_durable_cooldown_and_preserved_attempt(setup):
    worker, replies, calls, _ = setup
    worker.max_tasks = 1
    worker.tick(execute=True)
    url = next(url for url in replies if not url.endswith('/jobs'))
    replies[url] = ssl.SSLEOFError(8, 'Unexpected EOF while reading')
    worker.tick(execute=True)
    task = task_row(worker)
    state = worker.host_state('demo.example')
    assert task['status'] == 'pending' and task['attempts'] == 1
    assert not state.get('stopped', False)
    assert state['recovery']['failure_kind'] == 'transient_transport'
    assert task['eligible_at'] > time.time()
    assert len(worker.shared_policy.events('demo_workday')) == 1


def test_certificate_failure_retains_host_review_hold(setup):
    worker, replies, calls, _ = setup
    worker.max_tasks = 1
    worker.tick(execute=True)
    url = next(url for url in replies if not url.endswith('/jobs'))
    replies[url] = ssl.SSLCertVerificationError(1, 'Certificate verify failed')
    worker.tick(execute=True)
    assert task_row(worker)['status'] == 'blocked'
    assert worker.host_state('demo.example')['stopped'] is True


def test_existing_review_hold_excludes_recovery_even_when_listing_due(setup):
    worker, _, _, _ = setup
    worker.max_tasks = 1
    worker.tick(execute=True)
    import hashlib
    path = worker.shared_policy.root / 'hosts' / ('host-' + hashlib.sha256(b'demo.example').hexdigest()[:24] + '.json')
    path.write_text(json.dumps({'stopped': True, 'eligible_at': 0, 'reason': 'Explicit access review'}))
    with worker.db.connection_scope() as db:
        db.execute("UPDATE remediation_sources SET next_list_at=0 WHERE source_id='demo_workday'")
    worker.seed_listings()
    assert worker.choose() is None
    assert worker.shared_policy.events('demo_workday') == []


def test_listing_budget_is_checked_before_durable_reservation(setup):
    from dataclasses import replace
    from jobagg.pipelines.http_checkpoint import HostIneligible
    import pytest
    worker, _, _, _ = setup
    worker.initialize()
    worker.seed_listings()
    source=worker.by_id['demo_workday']
    worker.by_id[source.id]=replace(source,extra={**source.extra,'listing_min_budget_seconds':180})
    task=worker.choose(deadline=time.time()+200)
    before=worker.shared_policy.events(source.id)
    with pytest.raises(HostIneligible) as result:
        worker.admit(task,time.time()+30)
    assert result.value.category=='budget'
    assert worker.shared_policy.events(source.id)==before
    with worker.db.connect() as conn:
        row=conn.execute('SELECT status,attempts FROM remediation_tasks WHERE task_id=?',(task['task_id'],)).fetchone()
    assert row['status']=='pending' and row['attempts']==0


def test_unchanged_failed_inputs_do_not_reenter_on_new_listing_frame(setup):
    from copy import deepcopy
    worker, _, _, _ = setup
    worker.max_tasks = 1
    worker.tick(execute=True)
    task = task_row(worker)
    payload = json.loads(task['payload'])
    for status in ('dead_letter', 'blocked'):
        receipt = {'retry_input_sha256': worker.retry_input_fingerprint(payload)}
        with worker.db.connection_scope() as conn:
            conn.execute('UPDATE remediation_tasks SET status=?,receipt=? WHERE task_id=?',
                         (status, json.dumps(receipt), task['task_id']))
            fresh = deepcopy(payload)
            fresh['frame_path'] = '/new-frame/listing.json'
            fresh['frame_sha256'] = 'new-frame'
            fresh['listing']['last_seen_at'] = '2026-10-01T00:00:00+00:00'
            worker.enqueue(conn, task['source_id'], 'detail', task['external_id'], fresh, refresh=True)
        assert task_row(worker)['status'] == status
        with worker.db.connection_scope() as conn:
            fresh['listing']['source_url'] = 'https://demo.example/corrected/123'
            worker.enqueue(conn, task['source_id'], 'detail', task['external_id'], fresh, refresh=True)
        assert task_row(worker)['status'] == 'pending'
        with worker.db.connection_scope() as conn:
            conn.execute('UPDATE remediation_tasks SET payload=? WHERE task_id=?', (json.dumps(payload), task['task_id']))


def test_changed_reviewed_implementation_allows_one_dead_letter_retry(setup):
    worker, _, _, _ = setup
    worker.max_tasks = 1
    worker.tick(execute=True)
    task = task_row(worker)
    payload = json.loads(task['payload'])
    receipt = {'retry_input_sha256': worker.retry_input_fingerprint(payload)}
    with worker.db.connection_scope() as conn:
        conn.execute("UPDATE remediation_tasks SET status='dead_letter',receipt=? WHERE task_id=?", (json.dumps(receipt), task['task_id']))
        worker.binding['implementation_sha256'] = 'reviewed-parser-repair'
        worker.enqueue(conn, task['source_id'], 'detail', task['external_id'], payload, refresh=True)
    assert task_row(worker)['status'] == 'pending'


def test_wrapped_typed_budget_deferral_is_not_a_permanent_failure(setup):
    from jobagg.pipelines.http_checkpoint import HostIneligible
    worker, _, _, _ = setup
    try:
        try:
            raise HostIneligible('deadline', category='budget')
        except HostIneligible as exc:
            raise RuntimeError('adapter wrapper') from exc
    except RuntimeError as exc:
        assert worker.retry_after_error({}, worker.workspace, exc)['category'] == 'eligibility_deferred'


@pytest.mark.parametrize('status,category', [(429, 'rate_limit'), *[(x, 'transient_transport') for x in (408, 500, 502, 503, 504)]])
@pytest.mark.parametrize('retry_header', ['Retry-After', 'retry-after', 'RETRY-AFTER'])
def test_captured_http_error_body_requeues_with_retry_after(setup, status, category, retry_header):
    worker, replies, calls, _ = setup
    worker.max_tasks = 1
    worker.tick(execute=True)  # A complete listing creates the detail task.
    body = b'<html><title>Temporary service failure</title>Please try later.</html>'

    class ErrorBodyClient(FixtureClient):
        def _request(self, url, **kwargs):
            self.calls.append((url, kwargs))
            response = HttpResponse(
                url, status,
                {'Content-Type': 'text/html', retry_header: '7200'},
                body.decode(), body,
            )
            # Match the real HTTP client's bounded-response exception, so the
            # actual checkpoint persists the bytes and classifies the status.
            raise HTTPError(f'HTTP {status}', response=response)

    worker.client_factory = lambda source, policy: ErrorBodyClient(replies, calls)
    before_attempt = time.time()
    worker.tick(execute=True)
    task = task_row(worker)
    receipt = json.loads(task['receipt'])
    capture_path = Path(receipt['capture_directory']) / 'http' / '00001.json'
    capture = json.loads(capture_path.read_text())
    state = worker.host_state('demo.example')

    assert capture['status_code'] == status
    assert capture['state'] == 'failed' and capture['body_captured'] is True
    assert capture['failure_category'] == category
    assert capture['response_headers'][retry_header] == '7200'
    assert capture['retry_after'] == '7200'
    assert gzip.decompress(Path(capture['artifact']).read_bytes()) == body
    assert capture['body_sha256'] == hashlib.sha256(body).hexdigest()
    assert not state['stopped'] and state['recovery']['failure_kind'] == category
    assert state['eligible_at'] >= before_attempt + 7200
    assert task['attempts'] == 1
    assert len(worker.shared_policy.events('demo_workday')) == 1
    assert len(calls) == 2  # One listing and one detail; no hidden HTTP retries.

    # The deployed gap blocked a captured transient response despite its exact
    # host evidence. The integration must retain a bounded future retry.
    assert task['status'] == 'pending'
    assert receipt['retry_decision']['capture']['path'] == str(capture_path)
    assert task['eligible_at'] >= state['eligible_at']
    worker.tick(execute=True)
    assert len(calls) == 2  # Retry-After and durable host cooldown remain binding.



@pytest.mark.parametrize("change", ["status", "category", "source", "job", "phase", "body_hash", "body_size", "artifact", "body_corrupt"])
def test_captured_error_retry_rejects_unbound_or_changed_evidence(setup, change):
    import urllib.error
    worker, _, _, _ = setup
    test_captured_http_error_body_requeues_with_retry_after(setup, 503, "transient_transport", "Retry-After")
    task = task_row(worker)
    path = Path(json.loads(task["receipt"])["capture_directory"]) / "http" / "00001.json"
    meta = json.loads(path.read_text())
    if change == "status":
        meta["status_code"] = 403
    elif change == "category":
        meta["failure_category"] = "access_denied"
    elif change == "source":
        meta["source_binding"]["source_id"] = "another_source"
    elif change == "job":
        meta["phase"]["job_id"] = "another_job"
    elif change == "phase":
        meta["phase"]["kind"] = "listing"
    elif change == "body_hash":
        meta["body_sha256"] = "0" * 64
    elif change == "body_size":
        meta["body_bytes"] += 1
    elif change == "artifact":
        meta["artifact"] = str(path.parent / "unrelated.body.gz")
    else:
        Path(meta["artifact"]).write_bytes(b"corrupt")
    path.write_text(json.dumps(meta))
    error = urllib.error.HTTPError(meta["url"], 503, "captured failure", {}, None)
    assert worker.retry_after_error(task, path.parent.parent, error) is None


def test_captured_error_restart_preserves_probe_ceiling_then_recovers(setup, monkeypatch):
    from jobagg.remediation_worker import Worker
    from jobagg.pipelines.host_recovery import host_eligibility
    worker, replies, calls, _ = setup
    test_captured_http_error_body_requeues_with_retry_after(setup, 503, "transient_transport", "Retry-After")
    error_factory = worker.client_factory
    now = [task_row(worker)["eligible_at"] + 1]
    monkeypatch.setattr(time, "time", lambda: now[0])
    first_probe = now[0]
    for expected in (2, 3, 4):
        worker = Worker(registry=worker.registry, robots=worker.robots_path,
                        workspace=worker.workspace, shared_lock=worker.shared_lock,
                        max_tasks=1, client_factory=error_factory)
        worker.initialize()
        selected = worker.choose(excluded_kinds=("listing",))
        assert selected is not None
        worker.perform(selected, time.time() + 60)
        task = task_row(worker)
        assert task["status"] == "pending" and task["attempts"] == expected
        now[0] = task["eligible_at"] + 1
    state = worker.host_state("demo.example")
    assert len(state["recovery"]["probe_attempts"]) == 3
    assert task["eligible_at"] >= first_probe + 86400
    assert not host_eligibility(state, first_probe + 86399)["allowed"]
    now[0] = first_probe + 86399
    assert worker.choose(excluded_kinds=("listing",)) is None
    assert task_row(worker)["attempts"] == 4 and len(calls) == 5
    now[0] = task["eligible_at"] + 1
    worker.client_factory = lambda source, policy: FixtureClient(replies, calls)
    worker.perform(worker.choose(excluded_kinds=("listing",)), time.time() + 60)
    assert task_row(worker)["status"] == "done" and task_row(worker)["attempts"] == 5
    assert "recovery" not in worker.host_state("demo.example")
    with worker.db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM remediation_attempts WHERE kind='detail'").fetchone()[0] == 5
    assert len(worker.shared_policy.events("demo_workday")) == 5
