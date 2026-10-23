"""Three real fixture worker/publication cycles plus conservative downshift.

All HTTP is fixture data. These are local A8 acceptance fixtures, not evidence
that three production cron cycles or the 24-hour observation have occurred.
"""
from datetime import datetime
import sqlite3
import time

from jobagg.db import JobDatabase
from jobagg.pipelines.bundles import source_output_slug
from jobagg.pipelines.live_publication import publish_incremental
from jobagg.remediation_concurrency import ControllerPolicy, initial_state, decide
from jobagg.remediation_worker import shared_owner
from test_worker_parallel import make_worker


def test_three_cycles_only_ramp_on_qualified_history_and_publish_exact_text(tmp_path, monkeypatch):
    worker, calls, _ = make_worker(tmp_path, monkeypatch, count=4, parallel=1, max_tasks=2, delay=.1)
    output = tmp_path/'live'
    output.mkdir()
    for source in worker.by_id.values():
        JobDatabase(output/(source_output_slug(source)+'_jobs.sqlite3')).initialize()
    JobDatabase(output/'all_jobs.sqlite3').initialize()
    policy = ControllerPolicy(initial_limit=1, maximum_limit=2, healthy_batches_to_increase=2)
    state = initial_state(policy, now=time.time())
    limits, metrics = [], []
    for cycle in range(3):
        worker.parallel_sources = state['limit']
        limits.append(worker.parallel_sources)
        report = worker.tick(execute=True)
        m = report['concurrency']
        with shared_owner(worker.shared_lock):
            published = publish_incremental(worker.db.path, worker.registry, output, tmp_path/'publisher',
                                            max_jobs=20, deadline_at=time.monotonic()+10, execute=True)
        assert published['status'] == 'published', published
        assert published['readback_verified'] is True
        observed = {**{key: m[key] for key in (
            'limit_used','eligible_distinct_sources','eligible_distinct_hosts','peak_active_tasks',
            'peak_active_sources','peak_active_hosts','peak_active_scheduling_hosts','attempted_tasks',
            'accepted_progress','eligible_backlog_remaining','new_access_blocks','transport_failures',
            'runtime_errors','integrity_errors','database_errors','control_cycle_complete','killed')},
            'batch_id': str(cycle), 'started_at': datetime.fromisoformat(m['started_at']).timestamp(),
            'finished_at': time.time(), 'publication_status': 'verified'}
        decision = decide(state, observed, policy, now=observed['finished_at'])
        state = decision['state']
        metrics.append(m)
        if cycle < 2:
            assert decision['qualifying'] is True, decision
    assert limits == [1, 1, 2]
    assert metrics[2]['peak_active_sources'] == metrics[2]['peak_active_scheduling_hosts'] == 2
    with sqlite3.connect(output/'all_jobs.sqlite3') as live, worker.db.connect() as origin:
        for row in origin.execute('SELECT source_id,external_id,description FROM jobs WHERE description IS NOT NULL'):
            actual = live.execute('SELECT description FROM jobs WHERE source_id=? AND external_id=?', (row['source_id'],row['external_id'])).fetchone()
            if row['description']:
                assert actual and actual[0] == row['description']
    # A typed integrity-pressure fixture retains conservative downshift behavior;
    # completing publication must not erase the pressure or award health credit.
    original_factory = worker.client_factory
    def failing_factory(source, policy):
        client = original_factory(source, policy)
        request = client._request
        def checked(url, **kwargs):
            if source.id == 'source2' and not url.endswith('/jobs'):
                from jobagg.vacancy_outcomes import DetailIdentityMismatch
                raise DetailIdentityMismatch('fixture identity mismatch')
            return request(url, **kwargs)
        client._request = checked
        return client
    worker.client_factory = failing_factory
    report = worker.tick(execute=True)
    m = report['concurrency']
    with shared_owner(worker.shared_lock):
        pressure_publication = publish_incremental(worker.db.path, worker.registry, output, tmp_path/'publisher',
                                                  max_jobs=20, deadline_at=time.monotonic()+10, execute=True)
    assert pressure_publication['status'] == 'published'
    assert pressure_publication['readback_verified'] is True
    assert m['integrity_errors'] >= 1
    observed.update({key: m[key] for key in (
        'limit_used','eligible_distinct_sources','eligible_distinct_hosts','peak_active_tasks',
        'peak_active_sources','peak_active_hosts','peak_active_scheduling_hosts','attempted_tasks',
        'accepted_progress','eligible_backlog_remaining','new_access_blocks','transport_failures',
        'runtime_errors','integrity_errors','database_errors','control_cycle_complete','killed')})
    observed.update(batch_id='pressure', started_at=datetime.fromisoformat(m['started_at']).timestamp(), finished_at=time.time())
    downshift = decide(state, observed, policy, now=observed['finished_at'])
    assert downshift['action'] == 'decrease' and downshift['limit'] == 1
    repeated = decide(downshift['state'], observed, policy, now=observed['finished_at'])
    assert repeated['state'] == downshift['state'] and repeated['consumed'] is False
    assert calls
