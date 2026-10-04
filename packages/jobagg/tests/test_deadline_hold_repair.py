"""Deadline corrections use captured test fixtures and temporary databases only."""
from datetime import UTC, datetime
import json

import pytest

from jobagg import remediation_repair as repair
from jobagg.deadline_review import MARKER, active_hold
from jobagg.remediation_worker import dump
from jobagg.source_health import read_worker_health
from test_remediation_repair import snapshots
from test_remediation_worker import setup


def held_task(setup):
    worker, _, calls, tmp = setup
    worker.tick(execute=True)
    with worker.db.connect() as conn:
        task = dict(conn.execute("SELECT * FROM remediation_tasks WHERE kind='detail'").fetchone())
        raw = json.loads(conn.execute("SELECT raw_json FROM jobs WHERE job_key='demo_workday:R1'").fetchone()[0])
        raw[MARKER] = dict(authority='user_requested_deadline_classification', source_id='demo_workday',
                           external_id='R1', review_id='deadline-review-1',
                           reviewed_at='2026-09-21T00:00:00+00:00', active=True)
        conn.execute("UPDATE jobs SET raw_json=?,closes_at='2020-01-01T00:00:00+00:00' WHERE job_key='demo_workday:R1'", (dump(raw),))
        conn.execute("UPDATE remediation_tasks SET status='pending',eligible_at=9999999999,last_error='retained earlier failure' WHERE task_id=?", (task['task_id'],))
        before = dict(conn.execute("SELECT * FROM remediation_tasks WHERE task_id=?", (task['task_id'],)).fetchone())
        worker.enqueue(conn, task['source_id'], 'detail', task['external_id'], json.loads(task['payload']))
        held = dict(conn.execute("SELECT * FROM remediation_tasks WHERE task_id=?", (task['task_id'],)).fetchone())
        assert held == {**before, 'status': 'past_deadline'}
        hold = active_hold(conn, task['task_id'])
        assert json.loads(hold['review_json']) == raw[MARKER]
    return worker, held, hold, calls, tmp


def withdraw_marker(worker, *, invalid=False):
    with worker.db.connect() as conn:
        raw = json.loads(conn.execute("SELECT raw_json FROM jobs WHERE job_key='demo_workday:R1'").fetchone()[0])
        if invalid:
            raw[MARKER]['authority'] = 'unverified'
        else:
            raw.pop(MARKER)
        conn.execute("UPDATE jobs SET raw_json=?,closes_at='2099-01-01T00:00:00+00:00' WHERE job_key='demo_workday:R1'", (dump(raw),))


def correction_file(tmp, task, hold):
    correction = dict(task_id=task['task_id'], hold_id=hold['hold_id'], source_id=task['source_id'],
                      external_id=task['external_id'], review_id='deadline-review-1',
                      authority='user_requested_deadline_correction', action='withdraw_classification',
                      correction_id='deadline-correction-1', reviewed_at=datetime.now(UTC).isoformat(),
                      reason='Reviewed the incorrect classification and withdrew it')
    path = tmp / 'deadline-correction.json'
    path.write_text(dump(dict(schema_version=1, kind='reviewed_deadline_corrections', corrections=[correction])))
    return path


@pytest.mark.parametrize('invalid', [False, True])
def test_marker_absence_or_invalid_authority_is_visible_but_never_automatic_release(setup, invalid):
    worker, task, hold, calls, _ = held_task(setup)
    withdraw_marker(worker, invalid=invalid)
    before_calls = list(calls)
    before = snapshots(worker)
    with worker.db.connect() as conn:
        worker.enqueue(conn, task['source_id'], 'detail', task['external_id'],
                       {'listing': {'closes_at': '2099-01-01T00:00:00+00:00'}})
        assert dict(conn.execute('SELECT * FROM remediation_tasks WHERE task_id=?', (task['task_id'],)).fetchone()) == task
        assert active_hold(conn, task['task_id']) == hold
    health = read_worker_health(worker.db.path)[task['source_id']]
    assert health['health_status'] == 'degraded'
    explanation = health['deadline_holds'][0]
    assert explanation['hold_id'] == hold['hold_id'] and explanation['review_id'] == 'deadline-review-1'
    assert explanation['deadline_utc'] == '2020-01-01T00:00:00+00:00'
    assert explanation['retained_eligible_at'] == task['eligible_at']
    assert explanation['reason'] and explanation['release_conditions'] and explanation['automatic_retry'] is False
    plan = repair.prepare(worker.workspace, worker.shared_lock)
    assert plan['candidates'] == [] and plan['held'][0]['deadline_hold'] == hold
    assert snapshots(worker) == before and calls == before_calls


@pytest.mark.parametrize('invalid', [False, True])
def test_evidenced_correction_releases_only_matching_hold_and_preserves_every_task_field(setup, invalid):
    worker, task, hold, calls, tmp = held_task(setup)
    withdraw_marker(worker, invalid=invalid)
    path = correction_file(tmp, task, hold)
    before = snapshots(worker)
    before_calls = list(calls)
    plan = repair.prepare(worker.workspace, worker.shared_lock, deadline_correction_path=path)
    assert len(plan['candidates']) == 1 and plan['candidates'][0]['category'] == 'reviewed_deadline_correction'
    assert plan['database_writes'] == plan['network_requests'] == 0
    result = repair.apply(plan)
    assert result['tasks_requeued'] == 1
    assert snapshots(worker) == before and calls == before_calls
    with worker.db.connect() as conn:
        after = dict(conn.execute('SELECT * FROM remediation_tasks WHERE task_id=?', (task['task_id'],)).fetchone())
        assert after == {**task, 'status': 'pending'}
        assert active_hold(conn, task['task_id']) is None
        evidence = dict(conn.execute('SELECT * FROM remediation_deadline_holds WHERE hold_id=?', (hold['hold_id'],)).fetchone())
        assert evidence['review_json'] == hold['review_json'] and evidence['recorded_at'] == hold['recorded_at']
        assert evidence['released_at'] and json.loads(evidence['release_json'])['correction']['correction_id'] == 'deadline-correction-1'
        assert conn.execute('SELECT count(*) FROM remediation_queue_repairs').fetchone()[0] == 1
    assert repair.apply(plan)['status'] == 'already_applied'


@pytest.mark.parametrize('field,value', [('authority','unverified'), ('hold_id','other-hold'),
                                         ('source_id','other-source'), ('external_id','other-job'),
                                         ('review_id','other-review'), ('correction_id',''),
                                         ('reviewed_at','2020-01-01T00:00:00+00:00'),
                                         ('reviewed_at','2099-01-01T00:00:00+00:00'), ('reason','')])
def test_unbound_correction_cannot_release_a_deadline_hold(setup, field, value):
    worker, task, hold, calls, tmp = held_task(setup)
    withdraw_marker(worker)
    path = correction_file(tmp, task, hold)
    data = json.loads(path.read_text()); data['corrections'][0][field] = value; path.write_text(dump(data))
    before = snapshots(worker)
    before_calls = list(calls)
    plan = repair.prepare(worker.workspace, worker.shared_lock, deadline_correction_path=path)
    assert plan['candidates'] == [] and plan['held']
    assert snapshots(worker) == before and calls == before_calls
    with worker.db.connect() as conn:
        assert dict(conn.execute('SELECT * FROM remediation_tasks WHERE task_id=?', (task['task_id'],)).fetchone()) == task


@pytest.mark.parametrize('status', ['blocked','interrupted','inflight','dead_letter','done',
                                   'unavailable_pending_inventory','listing_detail_conflict'])
def test_correction_file_does_not_select_or_reset_other_protected_states(setup, status):
    worker, task, hold, _, tmp = held_task(setup)
    withdraw_marker(worker)
    with worker.db.connect() as conn:
        conn.execute('UPDATE remediation_tasks SET status=? WHERE task_id=?', (status, task['task_id']))
    path = correction_file(tmp, task, hold)
    plan = repair.prepare(worker.workspace, worker.shared_lock, deadline_correction_path=path)
    assert plan['candidates'] == [] and plan['held']
    with worker.db.connect() as conn:
        assert dict(conn.execute('SELECT * FROM remediation_tasks WHERE task_id=?', (task['task_id'],)).fetchone()) == {**task, 'status': status}
        assert active_hold(conn, task['task_id']) == hold


@pytest.mark.parametrize('tamper', ['review_file','job','task','hold','source_hold'])
def test_apply_rechecks_review_job_task_hold_and_policy_without_partial_writes(setup, tamper):
    worker, task, hold, _, tmp = held_task(setup)
    withdraw_marker(worker)
    path = correction_file(tmp, task, hold)
    plan = repair.prepare(worker.workspace, worker.shared_lock, deadline_correction_path=path)
    if tamper == 'review_file':
        data = json.loads(path.read_text()); data['corrections'][0]['reason'] = 'different review'; path.write_text(dump(data))
    elif tamper == 'source_hold':
        (worker.shared_policy.root / 'source_holds.json').write_text(dump({'demo_workday': {'reason': 'review'}}))
    else:
        with worker.db.connect() as conn:
            if tamper == 'job':
                conn.execute("UPDATE jobs SET title='changed' WHERE job_key='demo_workday:R1'")
            elif tamper == 'task':
                conn.execute('UPDATE remediation_tasks SET attempts=attempts+1 WHERE task_id=?', (task['task_id'],))
            else:
                conn.execute("UPDATE remediation_deadline_holds SET recorded_at='changed' WHERE hold_id=?", (hold['hold_id'],))
    before = snapshots(worker)
    with worker.db.connect() as conn:
        before_task = dict(conn.execute('SELECT * FROM remediation_tasks WHERE task_id=?', (task['task_id'],)).fetchone())
        before_hold = active_hold(conn, task['task_id'])
    with pytest.raises(ValueError):
        repair.apply(plan)
    assert snapshots(worker) == before
    with worker.db.connect() as conn:
        assert dict(conn.execute('SELECT * FROM remediation_tasks WHERE task_id=?', (task['task_id'],)).fetchone()) == before_task
        assert active_hold(conn, task['task_id']) == before_hold


def test_still_active_review_or_missing_retained_evidence_requires_separate_correction(setup):
    worker, task, hold, _, tmp = held_task(setup)
    path = correction_file(tmp, task, hold)
    assert repair.prepare(worker.workspace, worker.shared_lock, deadline_correction_path=path)['candidates'] == []
    withdraw_marker(worker)
    with worker.db.connect() as conn:
        conn.execute('DELETE FROM remediation_deadline_holds WHERE hold_id=?', (hold['hold_id'],))
    plan = repair.prepare(worker.workspace, worker.shared_lock, deadline_correction_path=path)
    assert plan['candidates'] == [] and plan['held']
    assert read_worker_health(worker.db.path)[task['source_id']]['deadline_holds'][0]['hold_id'] is None


def test_positive_extension_retains_hold_history_and_rejects_another_review_identity(setup):
    worker, task, hold, _, _ = held_task(setup)
    with worker.db.connect() as conn:
        raw = json.loads(conn.execute("SELECT raw_json FROM jobs WHERE job_key='demo_workday:R1'").fetchone()[0])
        raw[MARKER]['review_id'] = 'different-review'
        conn.execute("UPDATE jobs SET raw_json=? WHERE job_key='demo_workday:R1'", (dump(raw),))
        payload = {'listing': {'closes_at': '2099-01-01T00:00:00+00:00'}}
        worker.enqueue(conn, task['source_id'], 'detail', task['external_id'], payload)
        assert dict(conn.execute('SELECT * FROM remediation_tasks WHERE task_id=?', (task['task_id'],)).fetchone()) == task
        raw[MARKER]['review_id'] = 'deadline-review-1'
        conn.execute("UPDATE jobs SET raw_json=? WHERE job_key='demo_workday:R1'", (dump(raw),))
        worker.enqueue(conn, task['source_id'], 'detail', task['external_id'], payload)
        assert active_hold(conn, task['task_id']) is None
        after = dict(conn.execute('SELECT * FROM remediation_tasks WHERE task_id=?', (task['task_id'],)).fetchone())
        assert after == {**task, 'status': 'pending', 'payload': dump(payload)}
    assert read_worker_health(worker.db.path)[task['source_id']]['deadline_holds'] == []


@pytest.mark.parametrize('missing', ['row', 'table'])
def test_positive_extension_cannot_release_proofless_legacy_hold(setup, missing):
    worker, task, hold, calls, _ = held_task(setup)
    with worker.db.connect() as conn:
        if missing == 'row':
            conn.execute('DELETE FROM remediation_deadline_holds WHERE hold_id=?', (hold['hold_id'],))
        else:
            conn.execute('DROP TABLE remediation_deadline_holds')
    before_calls = list(calls)
    before = snapshots(worker)
    with worker.db.connect() as conn:
        worker.enqueue(conn, task['source_id'], 'detail', task['external_id'],
                       {'listing': {'closes_at': '2099-01-01T00:00:00+00:00'}})
        assert dict(conn.execute('SELECT * FROM remediation_tasks WHERE task_id=?', (task['task_id'],)).fetchone()) == task
        assert active_hold(conn, task['task_id']) is None
    summary = read_worker_health(worker.db.path)[task['source_id']]['deadline_holds'][0]
    assert summary['hold_id'] is None and summary['task_status'] == 'past_deadline'
    assert summary['automatic_retry'] is False and 'reconciliation required' in summary['reason']
    assert 'reconciliation is required' in summary['release_conditions']
    assert snapshots(worker) == before and calls == before_calls


@pytest.mark.parametrize('status', ['blocked', 'interrupted', 'inflight', 'dead_letter', 'done',
                                   'unavailable_pending_inventory', 'listing_detail_conflict'])
def test_active_hold_remains_visible_when_task_enters_protected_state(setup, status):
    worker, task, hold, calls, _ = held_task(setup)
    with worker.db.connect() as conn:
        conn.execute('UPDATE remediation_tasks SET status=? WHERE task_id=?', (status, task['task_id']))
    before = snapshots(worker)
    before_calls = list(calls)
    summary = read_worker_health(worker.db.path)[task['source_id']]['deadline_holds'][0]
    assert summary['task_status'] == status and summary['hold_id'] == hold['hold_id']
    assert summary['review_id'] == 'deadline-review-1' and summary['deadline_utc'] == hold['deadline_utc']
    assert summary['retained_eligible_at'] == task['eligible_at'] and summary['automatic_retry'] is False
    with worker.db.connect() as conn:
        assert dict(conn.execute('SELECT * FROM remediation_tasks WHERE task_id=?', (task['task_id'],)).fetchone()) == {**task, 'status': status}
        assert active_hold(conn, task['task_id']) == hold
    assert snapshots(worker) == before and calls == before_calls


def test_unknown_current_date_never_releases_held_task_or_implies_review_withdrawal(setup):
    worker, task, hold, calls, tmp = held_task(setup)
    with worker.db.connect() as conn:
        conn.execute("UPDATE jobs SET closes_at=NULL WHERE job_key='demo_workday:R1'")
    before = snapshots(worker)
    before_calls = list(calls)
    with worker.db.connect() as conn:
        worker.enqueue(conn, task['source_id'], 'detail', task['external_id'], {'listing': {}})
        assert dict(conn.execute('SELECT * FROM remediation_tasks WHERE task_id=?', (task['task_id'],)).fetchone()) == task
        assert active_hold(conn, task['task_id']) == hold
    path = correction_file(tmp, task, hold)
    plan = repair.prepare(worker.workspace, worker.shared_lock, deadline_correction_path=path)
    assert plan['candidates'] == [] and plan['held']
    assert snapshots(worker) == before and calls == before_calls


@pytest.mark.parametrize('change', ['job', 'hold'])
def test_transaction_rechecks_deadline_evidence_after_fresh_preview(setup, monkeypatch, change):
    worker, task, hold, _, tmp = held_task(setup)
    withdraw_marker(worker)
    path = correction_file(tmp, task, hold)
    plan = repair.prepare(worker.workspace, worker.shared_lock, deadline_correction_path=path)
    prepare = repair.prepare

    def race(*args, **kwargs):
        fresh = prepare(*args, **kwargs)
        with worker.db.connect() as conn:
            if change == 'job':
                conn.execute("UPDATE jobs SET title='changed after preview' WHERE job_key='demo_workday:R1'")
            else:
                conn.execute("UPDATE remediation_deadline_holds SET recorded_at='changed after preview' WHERE hold_id=?", (hold['hold_id'],))
        return fresh

    monkeypatch.setattr(repair, 'prepare', race)
    with pytest.raises(ValueError, match='changed before repair transaction'):
        repair.apply(plan)
    with worker.db.connect() as conn:
        assert dict(conn.execute('SELECT * FROM remediation_tasks WHERE task_id=?', (task['task_id'],)).fetchone()) == task
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='remediation_queue_repairs'").fetchone()
