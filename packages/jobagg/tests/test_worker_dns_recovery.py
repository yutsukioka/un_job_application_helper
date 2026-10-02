"""Known resolver outages stay retryable without relaxing network admission."""
import json
import socket
import time

from jobagg.http_safe import SafeHTTPPolicy, SSRFProtectionError
from jobagg.pipelines.host_recovery import classify_failure, host_eligibility
from test_remediation_worker import FixtureClient, setup


def row(worker):
    with worker.db.connect() as conn:
        return dict(conn.execute("SELECT * FROM remediation_tasks WHERE kind='listing'").fetchone())


def resolver_worker(fixture, monkeypatch):
    worker, replies, calls, _ = fixture
    worker.max_tasks = 1
    now = [time.time()]
    monkeypatch.setattr(time, 'time', lambda: now[0])
    resolution = [socket.gaierror(socket.EAI_NONAME, 'nodename nor servname provided, or not known')]
    lookups = []
    def resolve(host):
        lookups.append(host)
        if isinstance(resolution[0], Exception):
            raise resolution[0]
        return resolution[0]
    class PolicyClient(FixtureClient):
        def _request(self, url, **kwargs):
            self.safe_policy.validate_url(url)
            return super()._request(url, **kwargs)
    def factory(source, policy):
        client = PolicyClient(replies, calls)
        client.safe_policy = SafeHTTPPolicy(allowed_hosts={'demo.example'}, resolver=resolve)
        return client
    worker.client_factory = factory
    return worker, calls, now, resolution, lookups


def test_eai_noname_worker_stays_pending_then_recovers_without_refunding_attempt(setup, monkeypatch):
    worker, calls, now, resolution, lookups = resolver_worker(setup, monkeypatch)
    worker.tick(execute=True)
    task = row(worker)
    assert task['status'] == 'pending' and task['attempts'] == 1
    assert not calls and lookups == ['demo.example']
    state = worker.host_state('demo.example')
    assert state['stopped'] is False and state['failure_category'] == 'transient_transport'
    assert task['eligible_at'] >= now[0] + 1800
    receipt = json.loads(task['receipt'])
    assert receipt['retry_decision']['category'] == 'guarded_transient_transport'
    now[0] = task['eligible_at'] + 1
    resolution[0] = ['93.184.216.34']
    worker.tick(execute=True)
    assert row(worker)['status'] == 'done' and row(worker)['attempts'] == 2
    assert len(calls) == 1 and lookups == ['demo.example', 'demo.example']
    assert 'recovery' not in worker.host_state('demo.example')


def test_retry_revalidates_dns_and_rejects_private_address_before_transport(setup, monkeypatch):
    worker, calls, now, resolution, lookups = resolver_worker(setup, monkeypatch)
    worker.tick(execute=True)
    now[0] = row(worker)['eligible_at'] + 1
    resolution[0] = ['127.0.0.1']
    worker.tick(execute=True)
    task = row(worker)
    assert task['status'] == 'dead_letter' and task['attempts'] == 2
    assert 'SSRFProtectionError' in task['last_error']
    assert json.loads(task['receipt'])['retry_decision'] is None
    assert not calls and lookups == ['demo.example', 'demo.example']


def test_permanent_nxdomain_retains_three_probe_daily_bound(setup, monkeypatch):
    worker, calls, now, _, lookups = resolver_worker(setup, monkeypatch)
    worker.tick(execute=True)  # Initial failed lookup, before the recovery circuit.
    first_probe = None
    for expected in (2, 3, 4):
        now[0] = row(worker)['eligible_at'] + 1
        first_probe = now[0] if first_probe is None else first_probe
        worker.tick(execute=True)
        assert row(worker)['status'] == 'pending' and row(worker)['attempts'] == expected
    state = worker.host_state('demo.example')
    assert len(state['recovery']['probe_attempts']) == 3
    assert row(worker)['eligible_at'] >= first_probe + 86400
    assert not host_eligibility(state, now[0] + 1)['allowed']
    now[0] += 1
    worker.tick(execute=True)
    assert row(worker)['attempts'] == 4 and len(lookups) == 4 and not calls


def test_dns_failure_does_not_override_an_explicit_local_policy_rejection():
    error = SSRFProtectionError('URL host is not in the organization allowlist')
    error.__cause__ = socket.gaierror(socket.EAI_NONAME, 'not found')
    assert classify_failure(error) == 'local_policy'
