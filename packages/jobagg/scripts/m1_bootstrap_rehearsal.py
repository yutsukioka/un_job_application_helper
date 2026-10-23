#!/usr/bin/env python3
"""Rehearse native schema/policy preparation on an independently copied boundary.

Run only inside an OS sandbox denying network and production writes. This tool
never calls worker, publisher, runtime activation, recovery, or maintenance APIs.
Historical proof paths/owner identities are preserved, never rewritten.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import socket
import sqlite3
import time
import traceback
from urllib.parse import urlsplit

from m1_stage_snapshot import disjoint, fenced_owner, sha


def rehearse(root: Path, registry: Path, seconds: float, page: int):
    if not math.isfinite(seconds) or seconds <= 0 or page <= 0:
        raise ValueError('positive finite rehearsal limits required')
    root = root.resolve(strict=True)
    snapshot = json.loads((root/'snapshot.json').read_text())
    if snapshot.get('complete') is not True or snapshot['snapshot_root'] != str(root):
        raise ValueError('complete exact snapshot required')
    config = json.loads((root/'production-config.original.json').read_text())
    disjoint(root, [Path(config['publication_worker_database']).parent,
                    Path(config['shared_lock_path']).parent, Path(config['state_dir']),
                    Path(config['attempt_state_dir']),
                    Path(config['publication_argv'][config['publication_argv'].index('--output-dir')+1])])
    for mutable in [root/'worker/jobs.sqlite3', root/'policy', root/'policy-marker.json',
                    *sorted((root/'output').glob('*_jobs.sqlite3'))]:
        if mutable.resolve(strict=True) != mutable:
            raise ValueError('mutable rehearsal path is aliased')
    production_config = Path(snapshot['production_config'])
    if sha(production_config) != snapshot['production_config_sha256']:
        raise ValueError('production configuration changed')
    # Probe permissions without performing any production write or network request.
    try:
        fd = os.open(production_config, os.O_WRONLY)
    except PermissionError:
        pass
    else:
        os.close(fd)
        raise ValueError('OS sandbox does not deny production writes')
    with socket.socket() as probe:
        try:
            probe.bind(('127.0.0.1', 0))
        except PermissionError:
            pass
        else:
            raise ValueError('OS sandbox does not deny network')
    from jobagg import accepted_evidence, publication_work, task_headers, publication_fence
    from jobagg import policy_migration
    from jobagg.pipelines.worker_policy import SharedPolicy
    from jobagg.pipelines.sync_source import load_sources, fetch_schedule_policy
    started = time.monotonic()
    deadline = started + seconds
    def check():
        if time.monotonic() >= deadline:
            raise TimeoutError('rehearsal deadline; retain checkpoint and resume same copy')
    sources = load_sources(registry)
    sources_by_id = {source.id: source for source in sources}
    bootstrap = json.loads((root/'policy/bootstrap.json').read_text())
    source_ids = set(bootstrap['scope_source_ids'])
    if not source_ids.issubset(sources_by_id):
        raise ValueError('historical source pacing requires explicit reviewed mapping')
    pauses = {}
    for source_id in source_ids:
        policy = fetch_schedule_policy(sources_by_id[source_id])
        pauses[source_id] = float(policy.get('detail_batch_pause_seconds') or policy.get('oracle_detail_batch_pause_seconds') or 0)
    previous = root/'rehearsal.json'
    if previous.exists():
        if previous.is_symlink() or not previous.is_file():
            raise ValueError('prior rehearsal journal is aliased or nonregular')
        history = root/'rehearsal-history'
        history.mkdir(exist_ok=True)
        if history.resolve() != history:
            raise ValueError('rehearsal journal archive is aliased')
        os.link(previous, history/(str(time.time_ns())+'.json'), follow_symlinks=False)
    report = dict(schema='jobagg-m1-isolated-rehearsal-1', snapshot_sha256=sha(root/'snapshot.json'),
                  started_at_utc=datetime.now(timezone.utc).isoformat(), network_denied=True,
                  production_writes_denied=True, runtime_activated=False, production_activated=False,
                  registry_sha256=sha(registry), page_limit=page, seconds_limit=seconds,
                  checkpoints=[], blockers=[], publication_fences=[])
    def save():
        report['elapsed_seconds'] = time.monotonic()-started
        temporary = root/'rehearsal.pending.json'
        temporary.write_text(json.dumps(report, indent=2, sort_keys=True)+'\n')
        temporary.replace(root/'rehearsal.json')
    save()
    with fenced_owner(config, snapshot['fence_sha256']) as owner_fd:
        try:
            # Every mutable SQLite pathname is derived solely from the copy root.
            worker = root/'worker/jobs.sqlite3'
            with closing(sqlite3.connect(worker)) as conn:
                conn.set_progress_handler(lambda: 1 if time.monotonic() >= deadline else 0, 1000)
                preserved = ['jobs','remediation_tasks','remediation_attempts','remediation_observations']
                report['worker_counts_before'] = {name:conn.execute('SELECT count(*) FROM '+name).fetchone()[0] for name in preserved}
                with conn:
                    accepted_evidence.prepare(conn)
                    hosts = dict(conn.execute('SELECT source_id,? FROM remediation_sources', ('',)))
                    for source_id in hosts:
                        if source_id not in sources_by_id:
                            raise ValueError('historical task source host requires reviewed mapping')
                        hosts[source_id] = urlsplit(sources_by_id[source_id].base_url).hostname.lower()
                    task_headers.prepare(conn, hosts)
                    publication_work.prepare(conn, contract_sha256=report['registry_sha256'],
                                             epoch='pr142-copy-only-rehearsal')
                complete = False
                while not complete:
                    check()
                    with conn:
                        complete = publication_work.bootstrap(conn, limit=page, deadline_at=deadline)
                report['worker_counts_after'] = {name:conn.execute('SELECT count(*) FROM '+name).fetchone()[0] for name in preserved}
                if report['worker_counts_after'] != report['worker_counts_before']:
                    raise ValueError('bootstrap changed retained worker data counts')
                report['dirty_units'] = conn.execute('SELECT count(*) FROM jobagg_dirty_current').fetchone()[0]
                report['worker_integrity'] = conn.execute('PRAGMA quick_check').fetchone()[0]
                report['checkpoints'].append('worker_native_schema_and_dirty_bootstrap')
                # This deliberately is not a deployment contract or a reconciled
                # activation receipt. No invented successful-exit proof is used.
                report['blockers'].append('dirty contract uses rehearsal-only registry seal; rebuild against final measured deployment runtime contract before activation')
            for path in sorted((root/'output').glob('*_jobs.sqlite3')):
                check()
                with closing(sqlite3.connect(path)) as conn:
                    conn.set_progress_handler(lambda: 1 if time.monotonic() >= deadline else 0, 1000)
                    before = conn.execute('SELECT count(*) FROM jobs').fetchone()[0]
                    with conn:
                        publication_fence.prepare_target_schema(conn)
                        publication_fence.activate(conn, owner_path=config['shared_lock_path'], owner_fd=owner_fd,
                                                   epoch='pr142-copy-only-rehearsal')
                    after = conn.execute('SELECT count(*) FROM jobs').fetchone()[0]
                    if before != after:
                        raise ValueError('publication preparation changed retained job count')
                    report['publication_fences'].append(dict(database=path.name, jobs=after,
                        schema=publication_fence.binding(path)['schema']))
                save()
            policy = SharedPolicy(config['shared_lock_path'], source_ids)
            # Keep original owner proof; redirect only the mutable store objects.
            policy.root, policy.marker = root/'policy', root/'policy-marker.json'
            complete = False
            while not complete:
                check()
                result = policy_migration.step(policy, owner_fd=owner_fd, epoch='pr142-copy-only-rehearsal',
                    pauses=pauses, reviewed_writers=[dict(name='copy-only rehearsal; original owner held and maintenance preserved', fenced=True)],
                    limit=page, check_deadline=check)
                report['policy_checkpoint'] = result
                save()
                complete = result['complete']
            report['checkpoints'].append('policy_authority_3_import_4_complete')
            with closing(sqlite3.connect(root/'policy/authority.sqlite3')) as conn:
                report['policy_attempts'] = conn.execute('SELECT count(*) FROM attempts').fetchone()[0]
                report['policy_integrity'] = conn.execute('PRAGMA quick_check').fetchone()[0]
            report['blockers'].extend([
                'complete historical manual/browser/recovery control-root and writer inventory remains subject to explicit review',
                'successful-exit acceptance import and same-generation accepted identity/export reconciliation not certified',
                'phase budgets and publication admission require measured viable-unit evidence and review; deployment remains disabled'])
        except Exception as error:
            report['failure'] = dict(type=type(error).__name__, message=str(error), traceback=traceback.format_exc())
            report['blockers'].append('resume or repair the recorded copy-only checkpoint before any production bootstrap')
        finally:
            save()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--registry', type=Path, required=True)
    parser.add_argument('--seconds', type=float, required=True)
    parser.add_argument('--page', type=int, required=True)
    args = parser.parse_args()
    report = rehearse(args.snapshot, args.registry, args.seconds, args.page)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if report.get('failure') else 0


if __name__ == '__main__':
    raise SystemExit(main())
