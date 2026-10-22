"""Budget-independent active authority and pre-intent continuation."""
import importlib.util
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from jobagg import pending_publication, runtime_authority as authority
from test_budgeted_dispatcher import configured, runner  # noqa: F401

# ruff: noqa: F811


@pytest.fixture
def runtime(configured, monkeypatch):
    config, calls = configured
    config['_fixture_storage_budget'] = config.pop('storage_budget')
    config['runtime_control'] = True
    config['phase_budgets'] = dict(projection_max_seconds=3, projection_min_seconds=.01,
                                    publisher_min_seconds=.01, cleanup_report_seconds=.01,
                                    measurement_sha256='a'*64)
    db = config['publication_worker_database']
    db.unlink()
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE jobs(id TEXT PRIMARY KEY, body TEXT)')
        authority.activate(conn, epoch='fixture', owner_path=config['shared_lock_path'],
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
    with sqlite3.connect(config['publication_worker_database']) as conn:
        authority.begin(conn, 'run', config['_config_sha256'])
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO jobs VALUES('1','unadmitted')")
        with authority.writer(conn, run_id='run', owner_path=config['shared_lock_path']):
            conn.execute("INSERT INTO jobs VALUES('1','retained')")
        assert authority.validate(conn)['revision'] == 1
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM jobs")
    with sqlite3.connect(config['publication_worker_database']) as conn:
        assert conn.execute('SELECT body FROM jobs').fetchone()[0] == 'retained'


def test_changed_revision_holds_pending_without_refetch(runtime, monkeypatch):
    config, calls = runtime
    monkeypatch.setattr(runner, 'snapshot_helper', lambda _: SimpleNamespace(create_publication_snapshot=lambda *a, **k: (_ for _ in ()).throw(TimeoutError('fixture'))))
    first = runner.tick(config, ['fixture'])
    with sqlite3.connect(config['publication_worker_database']) as conn:
        with authority.writer(conn, run_id=first['run_id'], owner_path=config['shared_lock_path']):
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
             'publication_ok': True, 'readback_verified': True, 'controller_approved': True,
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
