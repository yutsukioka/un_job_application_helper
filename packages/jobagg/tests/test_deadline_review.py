import json
from datetime import UTC, datetime
from jobagg.deadline_review import MARKER, apply_review, blocks_detail
from jobagg.models import JobRecord

NOW = datetime(2026, 9, 21, tzinfo=UTC)


def current():
    return dict(source_id='test', external_id='1', closes_at='2026-09-18T22:59:00+00:00', raw_json=json.dumps({MARKER:dict(authority='user_requested_deadline_classification',source_id='test',external_id='1',review_id='test-review',reviewed_at=NOW.isoformat(),active=True,portal_status='Open')}))


def job(deadline):
    return JobRecord(source_id='test',org_id='test',ats_family='static_html',external_id='1',title='Role',apply_url='https://example.org/1',closes_at=deadline)


def test_old_open_label_cannot_reopen_reviewed_deadline():
    j=job(datetime(2026,9,18,tzinfo=UTC))
    apply_review(j,current(),now=NOW)
    assert j.status=='expired'
    assert j.raw[MARKER]['portal_status']=='Open'
    assert blocks_detail(current(),now=NOW)


def test_future_extension_allows_refetch_and_reopening():
    future=datetime(2026,10,1,tzinfo=UTC)
    j=job(future);apply_review(j,current(),now=NOW)
    assert j.status=='open' and j.raw[MARKER]['active'] is False
    assert not blocks_detail(current(),future.isoformat(),now=NOW)


def test_unreviewed_or_other_identity_is_not_retired():
    c=current();c['external_id']='2'
    assert not blocks_detail(c,now=NOW)
    j=job(datetime(2026,9,18,tzinfo=UTC));j.external_id='2';apply_review(j,current(),now=NOW)
    assert j.status=='open'
    c=current();c['raw_json']='{}';assert not blocks_detail(c,now=NOW)


def test_database_retains_review_and_reopens_on_extension(tmp_path):
    from jobagg.db import JobDatabase
    db = JobDatabase(tmp_path / 'jobs.sqlite3')
    db.initialize()
    baseline = job(datetime(2020, 1, 1, tzinfo=UTC))
    db.upsert_job(baseline)
    with db.connect() as conn:
        conn.execute("UPDATE jobs SET status='expired',raw_json=? WHERE job_key='test:1'", (current()['raw_json'],))
    db.upsert_job(job(datetime(2020, 1, 1, tzinfo=UTC)))
    record = db.get_job('test:1')
    assert record['status'] == 'expired' and record['deadline_state'] == 'expired'
    assert record['application_ready'] == 0
    db.upsert_job(job(datetime(2099, 1, 1, tzinfo=UTC)))
    record = db.get_job('test:1')
    assert record['status'] == 'open' and record['deadline_state'] == 'future'
    assert record['raw'][MARKER]['active'] is False


def _queue():
    import sqlite3
    from jobagg.remediation_worker import task_key
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE jobs(source_id, external_id, closes_at, raw_json);
        CREATE TABLE remediation_tasks(task_id,source_id,kind,external_id,payload,status,eligible_at,discovered_at,last_error,receipt,attempts,claim);
    """)
    c = current(); c['closes_at'] = '2020-01-01T00:00:00+00:00'
    conn.execute('INSERT INTO jobs VALUES(?,?,?,?)', tuple(c.values()))
    key = task_key('test', 'detail', '1')
    return conn, key


def test_scheduler_preserves_all_protected_task_fields():
    from jobagg.remediation_worker import Worker
    for status in ['blocked', 'interrupted', 'inflight', 'dead_letter', 'done', 'unavailable_pending_inventory', 'listing_detail_conflict']:
        conn, key = _queue()
        conn.execute("INSERT INTO remediation_tasks(task_id,status,payload,eligible_at,last_error,receipt,attempts,claim) VALUES(?,?,?,9999999999,?,?,4,?)",
                     (key, status, '{}', 'old failure', '{"evidence":"retained"}', 'claim'))
        before = dict(conn.execute('SELECT * FROM remediation_tasks').fetchone())
        Worker.enqueue(None, conn, 'test', 'detail', '1', {'listing': {}})
        assert dict(conn.execute('SELECT * FROM remediation_tasks').fetchone()) == before
        conn.close()


def test_pending_deadline_hold_keeps_accounting_and_only_future_extension_releases():
    from jobagg.remediation_worker import Worker
    conn, key = _queue()
    conn.execute("INSERT INTO remediation_tasks(task_id,status,payload,eligible_at,last_error,receipt,attempts,claim) VALUES(?,'pending','{}',9999999999,'old failure','{\"evidence\":\"retained\"}',4,'claim')", (key,))
    before = dict(conn.execute('SELECT * FROM remediation_tasks').fetchone())
    Worker.enqueue(None, conn, 'test', 'detail', '1', {'listing': {}})
    held = dict(conn.execute('SELECT * FROM remediation_tasks').fetchone())
    assert held == {**before, 'status': 'past_deadline'}
    Worker.enqueue(None, conn, 'test', 'detail', '1', {'listing': {'closes_at':'invalid'}})
    assert dict(conn.execute('SELECT * FROM remediation_tasks').fetchone()) == held
    Worker.enqueue(None, conn, 'test', 'detail', '1', {'listing': {'closes_at':'2099-01-01T00:00:00+00:00'}})
    released = dict(conn.execute('SELECT * FROM remediation_tasks').fetchone())
    assert released['status'] == 'pending'
    for field in ['attempts','claim','last_error','receipt','eligible_at']:
        assert released[field] == before[field]
    conn.close()


def test_new_reviewed_past_deadline_task_is_explicitly_recorded():
    from jobagg.remediation_worker import Worker
    conn, key = _queue()
    Worker.enqueue(None, conn, 'test', 'detail', '1', {'listing': {}})
    row = dict(conn.execute('SELECT * FROM remediation_tasks').fetchone())
    assert row['task_id'] == key and row['status'] == 'past_deadline'
    conn.close()


def test_missing_worker_date_uses_reviewed_live_deadline():
    c=current();raw=json.loads(c['raw_json']);raw[MARKER]['deadline_utc']=c['closes_at']
    c['raw_json']=json.dumps(raw);c['closes_at']=None
    j=job(None);apply_review(j,c,now=NOW)
    assert j.status=='expired' and blocks_detail(c,now=NOW)


def test_future_extension_does_not_release_legacy_block_or_clear_its_evidence():
    from jobagg.remediation_worker import Worker
    conn, key = _queue()
    conn.execute("INSERT INTO remediation_tasks(task_id,status,payload,eligible_at,last_error,receipt,attempts,claim) VALUES(?,'blocked','{}',9999999999,'old failure','{}',4,'claim')", (key,))
    before = dict(conn.execute('SELECT * FROM remediation_tasks').fetchone())
    Worker.enqueue(None, conn, 'test', 'detail', '1', {'listing': {'closes_at':'2099-01-01T00:00:00+00:00'}})
    assert dict(conn.execute('SELECT * FROM remediation_tasks').fetchone()) == before
    conn.close()
