"""Bounded, read-only historical acceptance reconciliation for M1 preparation.

A changed later row does not erase older acceptance. Unproven listing/document/
export destinations remain explicit unresolved work, never an empty-backlog seal.
This diagnostic does not activate an authority or dispatch fetching/publication.
"""
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from contextlib import closing


def _check(deadline):
    if time.monotonic() >= deadline:
        raise TimeoutError('historical reconciliation budget exhausted')


def _control(path, deadline):
    _check(deadline)
    path = Path(path)
    if path.is_symlink() or path.stat().st_size > 16*1024**2:
        raise ValueError('reconciliation control absent/aliased/oversized')
    data = path.read_bytes()
    _check(deadline)
    return json.loads(data), hashlib.sha256(data).hexdigest()


def reconcile_run(run, worker_database, destinations, *, deadline_at):
    run = Path(run).absolute()
    request, request_sha = _control(run/'request.json', deadline_at)
    report, report_sha = _control(run/'acceptance.json', deadline_at)
    worker, worker_sha = _control(run/'worker_result.json', deadline_at)
    if (request.get('run_id') != run.name or report.get('run_id') != run.name
            or worker.get('run_id') != run.name or worker.get('worker_exit_code') != 0
            or worker.get('report_sha256') != report_sha):
        raise ValueError('historical worker acceptance lacks exact successful exit/report proof')
    intervals = report.get('concurrency', {}).get('task_intervals')
    if not isinstance(intervals, list):
        return {'run_id': run.name, 'reconciled': False, 'reason': 'accepted_attempt_identities_missing',
                'acceptance_sha256': report_sha}
    records = []
    unresolved = []
    with closing(sqlite3.connect(Path(worker_database).absolute().as_uri()+'?mode=ro', uri=True, timeout=1)) as conn:
        conn.row_factory = sqlite3.Row
        conn.set_progress_handler(lambda: int(time.monotonic() >= deadline_at), 256)
        for interval in intervals:
            _check(deadline_at)
            outcome = interval.get('outcome', {})
            if not any(key in outcome for key in ('detail_stored', 'listing_observed', 'document_captured')):
                continue
            attempt_id = interval.get('attempt_id')
            row = conn.execute('SELECT * FROM remediation_attempts WHERE attempt_id=?', (attempt_id,)).fetchone()
            if (not row or row['status'] != 'done' or row['finished_at'] is None or
                    any(row[key] != interval.get(key) for key in ('task_id', 'source_id', 'kind'))):
                unresolved.append({'attempt_id': attempt_id, 'reason': 'immutable_acceptance_identity_missing_or_changed'})
                continue
            receipt = json.loads(row['evidence'])
            if row['kind'] != 'detail':
                unresolved.append({'attempt_id': attempt_id, 'kind': row['kind'],
                                   'reason': 'exact_listing_document_export_reconciliation_required'})
                continue
            artifact, digest = _control(Path(receipt['detail_path']), deadline_at)
            if digest != receipt.get('detail_sha256'):
                unresolved.append({'attempt_id': attempt_id, 'reason': 'accepted_artifact_changed'})
                continue
            job = artifact['job']
            task = conn.execute('SELECT source_id,kind,external_id FROM remediation_tasks WHERE task_id=?', (row['task_id'],)).fetchone()
            if (not task or task['source_id'] != job['source_id'] or task['kind'] != 'detail'
                    or str(task['external_id']) != str(job['external_id'])):
                unresolved.append({'attempt_id': attempt_id, 'reason': 'immutable_task_crosswalk_differs'})
                continue
            targets = []
            for destination in destinations:
                _check(deadline_at)
                with closing(sqlite3.connect(Path(destination).absolute().as_uri()+'?mode=ro', uri=True, timeout=1)) as live:
                    live.row_factory = sqlite3.Row
                    live.set_progress_handler(lambda: int(time.monotonic() >= deadline_at), 256)
                    rows = live.execute('SELECT source_id,external_id,description,raw_json FROM jobs WHERE source_id=? AND external_id=?',
                                        (job['source_id'], str(job['external_id']))).fetchall()
                    exact = (len(rows) == 1 and rows[0]['description'] == job['description'] and
                             json.loads(rows[0]['raw_json']).get('_deterministic_fetch_observation') == artifact['proof'])
                    targets.append({'path': str(destination), 'exact_current_identity_readback': exact})
            record = {'attempt_id': attempt_id, 'task_id': row['task_id'], 'source_id': row['source_id'],
                      'external_id': str(job['external_id']), 'artifact_sha256': digest,
                      'description_sha256': hashlib.sha256(job['description'].encode()).hexdigest(), 'destinations': targets}
            records.append(record)
            if len(targets) < 2 or not all(item['exact_current_identity_readback'] for item in targets):
                unresolved.append({'attempt_id': attempt_id, 'reason': 'exact_published_identity_unproven_or_superseded'})
    return {'run_id': run.name, 'request_sha256': request_sha, 'acceptance_sha256': report_sha,
            'worker_result_sha256': worker_sha, 'accepted_details': records, 'unresolved': unresolved,
            'reconciled': not unresolved, 'exports_reconciled': False,
            'scope': 'Exact accepted detail/current per-destination identity; historical supersession and exports need separate proof.'}
