"""Budget-independent active authority and pre-intent continuation."""
import importlib.util
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from jobagg import pending_publication, runtime_authority as authority
from jobagg.remediation_worker import shared_owner
from test_budgeted_dispatcher import configured, runner  # noqa: F401

# ruff: noqa: F811


@pytest.fixture
def runtime(configured, monkeypatch):
    config, calls = configured
    # This routing fixture has synthetic SQL/children; native capability coverage
    # lives in real selected-publication tests rather than a production bypass.
    monkeypatch.setattr(runner, 'require_native_capabilities', lambda *_: None)
    monkeypatch.setattr(runner,'runtime_publication_contract',lambda cfg:{'sha256':cfg['_config_sha256'],'jobagg_implementation_sha256':'f'*64})
    config['_fixture_storage_budget'] = config.pop('storage_budget')
    config['runtime_control'] = True
    config['native_publication_escrow'] = 'per_bounded_batch'
    config['phase_budgets'] = dict(projection_max_seconds=3, projection_min_seconds=.01,
                                    publisher_min_seconds=.01, cleanup_report_seconds=.01,
                                    measurement_sha256='a'*64)
    db = config['publication_worker_database']
    db.unlink()
    owner = config['shared_lock_path'].open('a+')
    with owner, sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE jobs(id TEXT PRIMARY KEY, body TEXT)')
        authority.activate(conn, epoch='fixture', owner_path=config['shared_lock_path'], owner_fd=owner.fileno(),
                           bootstrap_receipt={'reconciled': True, 'source_sha256': 'b'*64,
                                              'unresolved_runs': [], 'unpublished_identities': []})
    monkeypatch.setattr(pending_publication, 'database_binding', lambda path: {'files': [], 'semantic': authority.binding(path)})
    return config, calls


@pytest.mark.parametrize('budget', ['absent', False, True])
@pytest.mark.parametrize('window', ['worker_result', 'projection'])
def test_both_pre_intent_windows_resume_exact_original_without_worker(runtime, monkeypatch, window, budget):
    config, calls = runtime
    if budget != 'absent':
        config['storage_budget'] = {**config['_fixture_storage_budget'], 'enabled': budget}
    immutable = runner.immutable_json
    def interrupted(path, value):
        immutable(path, value)
        if path.name == 'worker_result.json' and window == 'worker_result':
            raise RuntimeError('injected worker-result to marker crash')
    def failed(*args, **kwargs):
        raise TimeoutError('injected marker to intent projection crash')
    monkeypatch.setattr(runner, 'immutable_json', interrupted)
    monkeypatch.setattr(runner, 'snapshot_helper', lambda _: SimpleNamespace(create_publication_snapshot=failed))
    first = runner.tick(config, ['fixture'])
    run = Path(first['run_dir'])
    assert len(calls) == 1
    assert not (run / 'publication_intent.json').exists()
    original = {name: (run / name).read_bytes() for name in ('request.json', 'acceptance.json', 'worker_result.json')}
    monkeypatch.setattr(runner, 'immutable_json', immutable)
    def snapshot(source, target, acceptance_sha, *args, **kwargs):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'bounded projection')
        return {'path': str(target), 'sha256': runner.digest(target),
                'source_database': str(source), 'worker_acceptance_sha256': acceptance_sha}
    monkeypatch.setattr(runner, 'snapshot_helper', lambda _: SimpleNamespace(create_publication_snapshot=snapshot))
    resumed = runner.tick(config, ['fixture'])
    assert resumed['publication_status'] == 'published', resumed
    assert resumed['run_id'] == first['run_id']
    assert len(calls) == 2
    assert all((run / name).read_bytes() == data for name, data in original.items())
    with sqlite3.connect(config['publication_worker_database']) as conn:
        assert authority.validate(conn)['phase'] == 'idle'


def test_unadmitted_writers_fail_and_revision_is_transactional(runtime):
    config, _ = runtime
    with shared_owner(config['shared_lock_path']) as owner_fd, sqlite3.connect(config['publication_worker_database']) as conn:
        authority.begin(conn, 'run', config['_config_sha256'])
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO jobs VALUES('1','unadmitted')")
        with authority.writer(conn, run_id='run', owner_path=config['shared_lock_path'], owner_fd=owner_fd):
            conn.execute("INSERT INTO jobs VALUES('1','retained')")
        assert authority.validate(conn)['revision'] == 1
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM jobs")
    with shared_owner(config['shared_lock_path']) as owner_fd, sqlite3.connect(config['publication_worker_database']) as conn:
        assert conn.execute('SELECT body FROM jobs').fetchone()[0] == 'retained'


def test_changed_revision_holds_pending_without_refetch(runtime, monkeypatch):
    config, calls = runtime
    monkeypatch.setattr(runner, 'snapshot_helper', lambda _: SimpleNamespace(create_publication_snapshot=lambda *a, **k: (_ for _ in ()).throw(TimeoutError('fixture'))))
    first = runner.tick(config, ['fixture'])
    with shared_owner(config['shared_lock_path']) as owner_fd, sqlite3.connect(config['publication_worker_database']) as conn:
        with authority.writer(conn, run_id=first['run_id'], owner_path=config['shared_lock_path'], owner_fd=owner_fd):
            conn.execute("INSERT INTO jobs VALUES('new','new revision')")
    held = runner.tick(config, ['fixture'])
    assert held['review_required'] is True
    assert len(calls) == 1


def test_missing_authority_holds_instead_of_scanning_or_fetching(runtime):
    config, calls = runtime
    with sqlite3.connect(config['publication_worker_database']) as conn:
        conn.execute('DROP TABLE jobagg_runtime_state')
    held = runner.tick(config, ['fixture'])
    assert held['review_required'] is True
    assert not calls


def test_proven_empty_skips_request_projection_and_publisher(runtime, monkeypatch):
    config, calls = runtime
    # Routing fixture supplies a certified publication boundary. Source revision
    # alone cannot certify other databases, exports or unresolved generations.
    check = authority.proven_empty
    monkeypatch.setattr(authority, 'proven_empty', lambda conn, run_id, **kwargs:
                        check(conn, run_id, **{**kwargs, 'publication_empty_check': lambda _state: True}))
    child = runner.subprocess.Popen
    def empty(argv, **kwargs):
        process = child(argv, **kwargs)
        report = Path(argv[argv.index('--report')+1])
        value = json.loads(report.read_text())
        value['concurrency'] = {'accepted_progress': 0}
        report.write_text(json.dumps(value))
        return process
    monkeypatch.setattr(runner.subprocess, 'Popen', empty)
    monkeypatch.setattr(runner, 'publication_request', lambda *a, **k: pytest.fail('empty backlog projected'))
    result = runner.tick(config, ['fixture'])
    assert result['publication_status'] == 'proven_empty', result
    assert len(calls) == 1
    assert not (Path(result['run_dir'])/'publication_intent.json').exists()
    with sqlite3.connect(config['publication_worker_database']) as conn:
        assert authority.validate(conn)['phase'] == 'idle'


def test_phase_viability_defers_before_projection(runtime, monkeypatch):
    config, calls = runtime
    config['phase_budgets']['publisher_min_seconds'] = 14.5
    config['phase_budgets']['cleanup_report_seconds'] = 1
    monkeypatch.setattr(runner, 'publication_request', lambda *a, **k: pytest.fail('no viable projection'))
    result = runner.tick(config, ['fixture'])
    assert result['status'] == 'accepted_work_pending', result
    assert len(calls) == 1
    assert pending_publication.inspect(config['state_dir'])


def test_entry_expiry_cannot_dispatch(runtime):
    config, calls = runtime
    config['_entry_wall_deadline'] = 0
    with pytest.raises(runner.InvalidReport, match='entry budget'):
        runner.tick(config, ['fixture'])
    assert not calls


def test_shared_policy_two_audits_not_per_claim(tmp_path, monkeypatch):
    from test_remediation_worker import setup
    from jobagg.pipelines.worker_policy import SharedPolicy
    worker, _, calls, _ = setup.__wrapped__(tmp_path, monkeypatch)
    audits = []
    original = SharedPolicy.event_snapshot
    def audit(self, **kwargs):
        audits.append(1)
        return original(self, **kwargs)
    monkeypatch.setattr(SharedPolicy, 'event_snapshot', audit)
    report = worker.tick(execute=True)
    assert len(audits) == 2
    assert report['concurrency']['eligibility_snapshot_complete'] is True
    # Reporting consumes audited state/health and never chooses another task.
    monkeypatch.setattr(worker, 'choose', lambda **kw: pytest.fail('report selected work'))
    assert worker.report()['sources']


def test_monitor_observation_deduplicates_and_recovers_without_production_writes(tmp_path):
    source = Path(__file__).resolve().parents[3] / 'reports/jobagg-cron-monitor-2026-10-22/monitor.py'
    spec = importlib.util.spec_from_file_location('m1_monitor', source)
    monitor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(monitor)
    production = tmp_path/'production'
    attempt = production/'attempts'/'one'
    attempt.mkdir(parents=True)
    stamp = monitor.iso(monitor.utcnow())
    (attempt/'intent.json').write_text(json.dumps({'started_at': stamp}))
    (production/'health.json').write_text(json.dumps({'last_invocation_at': stamp, 'accepted_work_pending': True}))
    (attempt/'outcome.json').write_text(json.dumps({'finished_at': stamp, 'status': 'accepted_work_pending', 'result': {'status': 'accepted_work_pending'}}))
    cfg = {'state_dir': str(production), 'attempt_state_dir': str(production),
           'worker_cwd': str(production), 'publication_cwd': str(production)}
    path = production/'config.json'
    path.write_text(json.dumps(cfg))
    before = {p: p.read_bytes() for p in production.rglob('*') if p.is_file()}
    output = tmp_path/'monitor'
    first = monitor.sample(cfg, path, output)
    second = monitor.sample(cfg, path, output)
    assert len(first['cycles']) == len(second['cycles']) == 1
    assert second['a8']['ready'] is False
    (production/'health.json').write_text(json.dumps({'last_invocation_at': stamp}))
    recovery = monitor.sample(cfg, path, output)
    assert recovery['alert_changes']['resolved'] == ['accepted_work_pending']
    assert all(p.read_bytes() == data for p, data in before.items() if p.name != 'health.json')
    with pytest.raises(ValueError, match='outside production'):
        monitor.sample(cfg, path, production/'bad-output')
    rows = [{'terminal': True, 'started_at': str(i), 'origin': 'natural', 'worker_exit_code': 0,
             'publication_ok': True, 'readback_verified': True, 'controller_approved': True, 'pressure_free': True,
             'parallel_sources': 2 if i == 2 else 1, 'peak_active_sources': 2 if i == 2 else 1,
             'safe_downshift_verified': i == 2} for i in range(3)]
    assert monitor.a8_readiness(rows)['ready'] is True
    rows[-1]['publication_ok'] = False
    assert monitor.a8_readiness(rows)['ready'] is False


def test_real_database_family_binding_survives_pending_replay(runtime, monkeypatch):
    config, calls = runtime
    spec = importlib.util.spec_from_file_location('m1_real_pending', pending_publication.__file__)
    real = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(real)
    monkeypatch.setattr(pending_publication, 'database_binding', real.database_binding)
    monkeypatch.setattr(runner, 'snapshot_helper', lambda _: SimpleNamespace(create_publication_snapshot=lambda *a, **k: (_ for _ in ()).throw(TimeoutError('fixture'))))
    first = runner.tick(config, ['fixture'])
    again = runner.tick(config, ['fixture'])
    assert again['status'] == 'accepted_work_pending', again
    assert again.get('review_required') is not True, again
    assert again['run_id'] == first['run_id']
    assert len(calls) == 1


def test_transactional_summary_survives_update_delete_and_rollback(tmp_path):
    from jobagg.worker_summaries import ensure, counts
    conn = sqlite3.connect(tmp_path/'counts.sqlite3')
    conn.row_factory = sqlite3.Row
    with conn:
        conn.execute('CREATE TABLE remediation_tasks(task_id TEXT PRIMARY KEY,source_id TEXT,kind TEXT,status TEXT)')
        conn.executemany('INSERT INTO remediation_tasks VALUES(?,?,?,?)', [('one','a','detail','pending'), ('two','a','detail','done')])
        ensure(conn)
    def projected():
        return sorted(tuple(row) for row in counts(conn))
    before = projected()
    with pytest.raises(RuntimeError):
        with conn:
            conn.execute("UPDATE remediation_tasks SET status='done' WHERE task_id='one'")
            conn.execute("DELETE FROM remediation_tasks WHERE task_id='two'")
            raise RuntimeError('rollback fixture')
    assert projected() == before
    with conn:
        conn.execute("UPDATE remediation_tasks SET source_id='b',kind='listing',status='blocked' WHERE task_id='one'")
        conn.execute("DELETE FROM remediation_tasks WHERE task_id='two'")
    assert projected() == [('b', 'listing', 'blocked', 1)]
    conn.close()


def test_lineage_work_is_selective_and_timings_separate_artifact_costs(tmp_path):
    import time
    from jobagg.accepted_detail_lineage import DETAIL_ATTEMPTS_QUERY, ensure_detail_lineage_indexes, matching_detail_attempts
    from test_live_publication import setup, add_detail
    fixture = setup.__wrapped__(tmp_path)
    record, proof = add_detail(fixture)
    measurements = []
    with fixture['worker'].connect() as conn:
        ensure_detail_lineage_indexes(conn)
        conn.execute('CREATE INDEX idx_remediation_attempts_kind_started ON remediation_attempts(kind,started_at DESC)')
        conn.execute('CREATE INDEX idx_remediation_attempts_task_started ON remediation_attempts(task_id,started_at DESC)')
        for size in (128, 2048):
            conn.execute("DELETE FROM remediation_attempts WHERE attempt_id LIKE 'noise-%'")
            conn.executemany('INSERT INTO remediation_attempts VALUES(?,?,?,?,?,?,?,?)', [
                (f'noise-{i}', f'orphan-{i}', 'other', 'detail', i, i+1, 'done', '{}') for i in range(size)])
            work = [0]
            def progress():
                work[0] += 1
                return 0
            conn.set_progress_handler(progress, 1)
            rows = list(conn.execute(DETAIL_ATTEMPTS_QUERY, (record.source_id, record.external_id)))
            conn.set_progress_handler(None, 0)
            assert [row['attempt_id'] for row in rows] == ['accepted-001']
            measurements.append(work[0])
        assert measurements[1] <= measurements[0] * 2
        metrics = {}
        row = conn.execute('SELECT * FROM jobs').fetchone()
        result = matching_detail_attempts(conn, row, proof, deadline_at=time.monotonic()+2, timings=metrics)
        assert result[0]['attempt']['attempt_id'] == 'accepted-001'
        assert {'sql_seconds','artifact_open_seconds','artifact_hash_read_seconds','artifact_hash_cpu_seconds',
                'artifact_metadata_seconds','artifact_text_read_seconds','artifact_parse_seconds','proof_check_seconds'} <= metrics.keys()
        assert all(value >= 0 for value in metrics.values())


@pytest.mark.parametrize('storage_enabled', [False, True])
def test_real_worker_respects_runtime_fence_in_nested_transactions(tmp_path, monkeypatch, storage_enabled):
    import time
    from datetime import UTC, datetime
    from test_remediation_worker import setup
    from test_worker_storage_ingress import enable_storage
    worker, replies, calls, root = setup.__wrapped__(tmp_path, monkeypatch)
    if storage_enabled:
        worker, replies, calls, root, request, _ledger = enable_storage((worker, replies, calls, root), monkeypatch)
    else:
        worker.initialize()
        request = {'run_id': 'native-worker', 'deadline_at': datetime.fromtimestamp(time.time()+30, UTC).isoformat(),
                   'source_manifest_sha256': worker.binding['registry_sha256']}
    if storage_enabled:
        from jobagg.remediation_worker import WorkerStorage, shared_owner
        with shared_owner(worker.shared_lock):
            WorkerStorage(worker, _ledger.for_writer('worker').wire(), request['storage_limits']).activate(request['semantic_history_policy'])
    with worker.shared_lock.open('a+') as owner, sqlite3.connect(worker.db.path) as conn:
        authority.activate(conn, epoch='native-fixture', owner_path=worker.shared_lock, owner_fd=owner.fileno(),
                           bootstrap_receipt={'reconciled': True, 'source_sha256': 'c'*64})
        authority.begin(conn, request['run_id'], 'configuration')
    report = worker.tick(execute=True, request=request)
    assert report['tick_outcomes'][0]['independent_enumeration'] is True, report['tick_outcomes']
    if not storage_enabled:
        assert any('detail_stored' in item for item in report['tick_outcomes']), report['tick_outcomes']
    assert authority.binding(worker.db.path)['revision'] > 0
    assert calls


def test_historical_reconciliation_uses_attempt_identity_and_both_destinations(tmp_path):
    import hashlib
    import time
    from jobagg.runtime_reconciliation import reconcile_run
    from test_live_publication import setup, add_detail, run as publish
    fixture = setup.__wrapped__(tmp_path)
    record, _ = add_detail(fixture)
    history = tmp_path/'historical-run'
    history.mkdir()
    (history/'request.json').write_text(json.dumps({'run_id': history.name}))
    report = {'run_id': history.name, 'concurrency': {'task_intervals': [{
        'attempt_id': 'accepted-001', 'task_id': 'detail-001', 'source_id': record.source_id,
        'kind': 'detail', 'outcome': {'detail_stored': record.identity_key()}}]}}
    payload = json.dumps(report).encode()
    (history/'acceptance.json').write_bytes(payload)
    (history/'worker_result.json').write_text(json.dumps({'run_id': history.name, 'worker_exit_code': 0,
        'report_sha256': hashlib.sha256(payload).hexdigest()}))
    targets = [fixture['output']/'test_jobs.sqlite3', fixture['output']/'all_jobs.sqlite3']
    before = reconcile_run(history, fixture['worker'].path, targets, deadline_at=time.monotonic()+2)
    assert before['reconciled'] is False
    assert publish(fixture, execute=True)['status'] == 'published'
    after = reconcile_run(history, fixture['worker'].path, targets, deadline_at=time.monotonic()+2)
    assert after['reconciled'] is True
    assert after['accepted_details'][0]['attempt_id'] == 'accepted-001'
    assert after['exports_reconciled'] is False  # Per-target text isn't export parity.
    (tmp_path/'capture/001/detail.json').write_text('{}')
    damaged = reconcile_run(history, fixture['worker'].path, targets, deadline_at=time.monotonic()+2)
    assert damaged['reconciled'] is False
    assert damaged['unresolved'][0]['reason'] == 'accepted_artifact_changed'


def test_watchdog_anchors_import_and_configuration_time_without_real_dispatch(tmp_path, monkeypatch, capsys):
    source = Path(runner.__file__).with_name('supervisor.py')
    spec = importlib.util.spec_from_file_location('m1_supervisor', source)
    supervisor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(supervisor)
    clock = [100.0]
    monkeypatch.setattr(supervisor.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(supervisor.time, 'time', lambda: clock[0])
    children, signals = [], []
    class Child:
        pid = 99999999
        returncode = None
        def __init__(self, argv, **kwargs):
            children.append((argv, kwargs))
            clock[0] += 2  # Import/configuration consumed the whole entry window.
        def poll(self):
            return self.returncode
        def wait(self, **kwargs):
            self.returncode = -15
            return self.returncode
    monkeypatch.setattr(supervisor.subprocess, 'Popen', Child)
    monkeypatch.setattr(supervisor.os, 'killpg', lambda *args: signals.append(args))
    assert supervisor.main(['--entry-timeout-seconds','1','--','--config',str(tmp_path/'unused')]) == 3
    assert children[0][1]['env']['JOBAGG_ENTRY_MONOTONIC_DEADLINE'] == '101.0'
    assert signals
    assert json.loads(capsys.readouterr().out)['accepted_work_unknown'] is True


def test_reviewed_stage_probe_is_single_use_and_success_archives_episode(configured, tmp_path):
    import hashlib
    import time
    config, _ = configured
    obs = runner.OBSERVABILITY
    config['attempt_state_dir'].mkdir(parents=True, exist_ok=True)
    failure = {'status': 'process_failure', 'failure_stage': 'worker', 'reasons': ['typed timeout']}
    for index in range(3):
        obs.record_failure(config, str(index), failure)
    assert obs.failure_hold(config, 'new-wal') is not None
    evidence = tmp_path/'review.txt'
    evidence.write_text('Reviewed fixture correction for this exact stage and configuration')
    digest = hashlib.sha256(evidence.read_bytes()).hexdigest()
    obs.authorize_stage_probe(config, 'worker', evidence_path=evidence, evidence_sha256=digest, expires_at=time.time()+60)
    assert obs.failure_hold(config, 'another-wal') is None
    assert obs.failure_hold(config, 'another-wal') is not None
    with pytest.raises(ValueError, match='single probe'):
        obs.authorize_stage_probe(config, 'worker', evidence_path=evidence, evidence_sha256=digest, expires_at=time.time()+60)
    obs.record_failure(config, 'success', {'status': 'incomplete', 'worker_exit_code': 0,
        'worker_status': 'incomplete', 'run_id': 'verified-success'})
    assert obs.failure_hold(config, 'success')['stage'] == 'end_to_end'
    obs.record_failure(config, 'published', {'status':'incomplete', 'publication_status':'published',
        'publication_exit_code':0, 'readback_verified':True, 'run_id':'verified-service-success'})
    assert obs.failure_hold(config, 'published') is None
    histories = list((config['attempt_state_dir']/'circuit-history').glob('*.json'))
    assert len(histories) == 2
    assert all(json.loads(path.read_text())['episode']['unchanged_failures'] == 3 for path in histories)


def test_monitor_uses_compact_receipt_index_with_large_retained_history(tmp_path):
    source = Path(__file__).resolve().parents[3] / 'reports/jobagg-cron-monitor-2026-10-22/monitor.py'
    spec = importlib.util.spec_from_file_location('m1_index_monitor', source)
    monitor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(monitor)
    production = tmp_path/'production'
    production.mkdir()
    config = {'state_dir': str(production), 'attempt_state_dir': str(production),
              'worker_cwd': str(production), 'publication_cwd': str(production)}
    path = production/'config.json'
    path.write_text(json.dumps(config))
    for index in range(300):
        (production/'attempts'/f'retained-{index}').mkdir(parents=True)
    attempt, intent = runner.OBSERVABILITY.start_attempt(path, config)
    runner.OBSERVABILITY.finish_attempt(attempt, intent, {'status': 'incomplete', 'origin': 'natural',
        'publication_status': 'proven_empty', 'worker_exit_code': 0, 'backlog_authoritatively_empty': True})
    snapshot = monitor.sample(config, path, tmp_path/'monitor', max_attempts=1)
    assert snapshot['discovery_complete'] is True
    assert snapshot['quiet_proven_empty'] is True
    assert len(snapshot['cycles']) == 1
    assert snapshot['cycles'][0]['publication_ok'] is False
    assert snapshot['a8']['ready'] is False
    (attempt/'outcome.json').write_text('{}')
    damaged = monitor.sample(config, path, tmp_path/'monitor', max_attempts=1)
    assert damaged['discovery_complete'] is False


def test_alternating_stage_failures_and_zero_task_deadline_do_not_erase_service_episode(runtime):
    config, _ = runtime
    observer = runner.OBSERVABILITY
    for index, stage in enumerate(('projection', 'publication', 'worker')):
        observer.record_failure(config, str(index), {'status':'process_failure', 'failure_stage':stage,
            'failure_type':'TimeoutError', 'worker_exit_code':0, 'worker_status':'incomplete',
            'worker_stop_reason':'work_deadline', 'worker_progress':0, 'reasons':['fixture deadline']})
    held = observer.failure_hold(config, 'changed-WAL', stage='projection')
    assert held['stage'] == 'end_to_end'
    assert held['unchanged_failures'] == 3
    value = json.loads((config['attempt_state_dir']/'failure_condition.json').read_text())
    assert value['circuits']['worker']['failure_class'] == 'zero_task_work_deadline'
    observer.record_failure(config, 'new', {'status':'incomplete', 'worker_exit_code':0,
        'worker_status':'incomplete', 'worker_stop_reason':'work_deadline','worker_progress':0})
    assert observer.failure_hold(config, 'new', stage='projection')
