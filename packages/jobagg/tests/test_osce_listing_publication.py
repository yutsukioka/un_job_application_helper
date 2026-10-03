"""Publication must retain source URLs for independent OSCE census verification."""
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json

import pytest

from jobagg.db import JobDatabase
from jobagg.pipelines.live_inventory import plan_frames
from test_osce_server_inventory import ProofFixture, minimal_html


@pytest.mark.parametrize('changed_url', [False, True])
def test_publication_rechecks_exact_source_urls_in_complete_osce_frame(tmp_path, changed_url):
    capture = tmp_path / 'capture'
    capture.mkdir()
    fixture = ProofFixture(capture, [minimal_html(1)])
    proof = fixture.proof()
    assert proof['complete'] is True
    item = asdict(fixture.jobs[0])
    if changed_url:
        item['source_url'] = 'https://vacancies.osce.org/jobs/different-999'
    observed = datetime.now(timezone.utc)
    frame = capture / 'listing.json'
    frame.write_text(json.dumps({'source_id': fixture.source.id,
                                'observed_at': observed.isoformat(), 'jobs': [item]},
                               default=lambda value: value.isoformat()))
    worker = JobDatabase(tmp_path / 'worker.sqlite3')
    source_db = JobDatabase(tmp_path / 'osce_jobs.sqlite3')
    consolidated = JobDatabase(tmp_path / 'all_jobs.sqlite3')
    for db in (worker, source_db, consolidated):
        db.initialize()
    with worker.connect() as conn:
        conn.executescript('''
            CREATE TABLE remediation_sources(source_id TEXT PRIMARY KEY,last_list_at REAL,listing_ids TEXT,listing_proof TEXT);
            CREATE TABLE remediation_tasks(source_id TEXT,kind TEXT,status TEXT,receipt TEXT);
        ''')
        conn.execute('INSERT INTO remediation_sources VALUES(?,?,?,?)', (
            fixture.source.id, observed.timestamp(), json.dumps([fixture.jobs[0].identity_key()]), json.dumps(proof)))
        receipt = {'frame_path': str(frame), 'frame_sha256': hashlib.sha256(frame.read_bytes()).hexdigest(),
                   'enumeration': proof}
        conn.execute('INSERT INTO remediation_tasks VALUES(?,?,?,?)', (
            fixture.source.id, 'listing', 'done', json.dumps(receipt)))
    with worker.connect() as worker_conn, consolidated.connect() as live_conn:
        frames, rejected = plan_frames(worker_conn, live_conn, {fixture.source.id: fixture.source},
                                       {fixture.source.id: source_db.path}, limit=5)
    assert not rejected
    assert len(frames) == 1
    assert frames[0]['inventory_complete'] is (not changed_url)
