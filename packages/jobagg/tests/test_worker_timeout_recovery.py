"""Checkpoint and worker integration: recovery never bypasses durable accounting."""
import json
import time

from test_remediation_worker import FixtureClient, setup
from jobagg.pipelines.host_recovery import transient_failure


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
