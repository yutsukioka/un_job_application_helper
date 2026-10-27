#!/usr/bin/env python3
"""Explicit copy-only reconciliation of reviewed diagnostic wrapper duplicates.

Run under a copy-only OS sandbox and finite outer supervisor. This tool retains
worker holds, all payloads, the real owner inode and the maintenance marker.
"""
from contextlib import closing
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import time
import uuid

from m1_stage_snapshot import disjoint, fenced_owner, sha
from jobagg import runtime_authority, runtime_control_catalog


def reconcile(snapshot_root, review_path, review_sha256, *, seconds, max_records):
    if not math.isfinite(seconds) or not 0 < seconds <= 300:
        raise ValueError('finite reconciliation deadline of at most 300 seconds required')
    if type(max_records) is not int or not 0 < max_records <= 4096:
        raise ValueError('finite reconciliation record bound required')
    started = time.monotonic()
    deadline = started + seconds
    root, review_path = Path(snapshot_root).absolute(), Path(review_path).absolute()
    if root.resolve() != root or review_path.resolve() != review_path:
        raise ValueError('aliased reconciliation input')
    if not review_path.is_relative_to(root) or review_path.stat().st_size > 1024**2:
        raise ValueError('review must be a bounded copy-local control')
    raw = review_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != review_sha256:
        raise ValueError('review hash differs')
    review = json.loads(raw)
    ids = review.get('run_ids')
    if (review.get('schema') != 'legacy-wrapper-reference-review-1'
            or not isinstance(ids, list) or not ids or len(ids) > max_records
            or any(not isinstance(v, str) for v in ids) or len(ids) != len(set(ids))):
        raise ValueError('exact finite unique reviewed wrapper set required')
    snapshot = json.loads((root / 'snapshot.json').read_text())
    config_path = Path(snapshot['production_config'])
    if (snapshot.get('complete') is not True or snapshot.get('snapshot_root') != str(root)
            or sha(config_path) != snapshot['production_config_sha256']):
        raise ValueError('completed unchanged fenced snapshot required')
    config = json.loads(config_path.read_text())
    original = json.loads((root / 'production-config.original.json').read_text())
    if config != original:
        raise ValueError('original production configuration differs')
    database = root / 'worker/jobs.sqlite3'
    production = Path(config['publication_worker_database'])
    disjoint(root, [production.parent, Path(config['state_dir']),
                    Path(config['attempt_state_dir']), Path(config['shared_lock_path']).parent])
    if database.resolve() != database or os.path.samefile(database, production):
        raise ValueError('copied worker aliases production')
    resolved = []
    # Commit one bounded record at a time. Repeating the same reviewed set is
    # idempotent even after a crash between database commit and report creation.
    with fenced_owner(config, snapshot['fence_sha256']) as fd:
        with closing(sqlite3.connect(database)) as conn:
            state = runtime_authority.validate(conn)
            if state['phase'] != 'idle' or state['owner_path'] != config['shared_lock_path']:
                raise ValueError('idle copied native authority with actual owner required')
            before = dict(conn.execute(
                'SELECT disposition,count(*) FROM jobagg_legacy_controls GROUP BY disposition'))
            for run_id in ids:
                if time.monotonic() >= deadline:
                    raise TimeoutError('wrapper reconciliation deadline')
                with runtime_authority.controller(conn, run_id=None, owner_fd=fd):
                    with conn:
                        proof = runtime_control_catalog.reconcile_wrapper_reference(
                            conn, run_id, expected_inventory_sha256=review['inventory_sha256'],
                            owner_fd=fd, owner_path=config['shared_lock_path'], deadline_at=deadline)
                resolved.append({'run_id': run_id, 'proof': proof})
            after = dict(conn.execute(
                'SELECT disposition,count(*) FROM jobagg_legacy_controls GROUP BY disposition'))
            oldest = runtime_control_catalog.oldest_unresolved(conn)
            runtime_authority.validate(conn)
    report = {'schema': 'legacy-wrapper-reference-reconciliation-1',
              'review_sha256': review_sha256, 'before': before, 'after': after,
              'resolved': resolved, 'oldest_unresolved': oldest,
              'elapsed_seconds': time.monotonic() - started,
              'production_activated': False, 'certifies_worker_completion': False}
    report_path = root / ('wrapper-reconciliation-' + str(uuid.uuid4()) + '.json')
    with report_path.open('x') as stream:
        json.dump(report, stream, sort_keys=True, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    return {'report': str(report_path), 'sha256': sha(report_path), 'before': before,
            'after': after, 'resolved': len(resolved), 'elapsed_seconds': report['elapsed_seconds']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--review', type=Path, required=True)
    parser.add_argument('--review-sha256', required=True)
    parser.add_argument('--seconds', type=float, required=True)
    parser.add_argument('--max-records', type=int, required=True)
    args = parser.parse_args()
    print(json.dumps(reconcile(args.snapshot, args.review, args.review_sha256,
                               seconds=args.seconds, max_records=args.max_records)))


if __name__ == '__main__':
    main()
