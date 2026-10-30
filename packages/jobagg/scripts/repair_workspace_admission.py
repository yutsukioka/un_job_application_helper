#!/usr/bin/env python3
"""Repair one failed preflight and bind an already-reviewed worker generation.

The default mode prepares a full old/new module comparison. Execute only at a
clean publication boundary. A failed execution retains its maintenance marker.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import uuid

from repair_registry_admission import (allow_due_preflight_closure, check_failed_admission,
                                       encoded, protected, require, save, sha)


def manifest(root):
    root = Path(root).resolve(strict=True)
    entries = {str(p.relative_to(root)): sha(p.read_bytes()) for p in sorted(root.rglob('*.py'))}
    require(entries, 'Empty implementation tree')
    digest = sha(json.dumps(entries, ensure_ascii=False, sort_keys=True).encode())
    return {'root': str(root), 'modules': entries, 'implementation_sha256': digest}


def reviewed_binding(old, actual, old_manifest, new_manifest):
    require(set(old) == set(actual), 'Workspace binding fields differ')
    require(old['implementation_sha256'] == old_manifest['implementation_sha256'], 'Old implementation proof differs')
    require(actual['implementation_sha256'] == new_manifest['implementation_sha256'], 'New implementation proof differs')
    require(old['implementation_sha256'] != actual['implementation_sha256'], 'Implementation did not change')
    require({k: v for k, v in old.items() if k != 'implementation_sha256'} ==
            {k: v for k, v in actual.items() if k != 'implementation_sha256'}, 'Non-code workspace binding changed')
    return actual


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for key in ('config', 'runner', 'old-root', 'output-dir'):
        p.add_argument('--' + key, type=Path, required=True)
    p.add_argument('--run-id', required=True)
    p.add_argument('--expected-old-seal', required=True)
    p.add_argument('--expected-new-seal', required=True)
    p.add_argument('--expected-contract', required=True)
    p.add_argument('--execute', action='store_true')
    a = p.parse_args()
    config_bytes = a.config.read_bytes()
    config = json.loads(config_bytes)
    args = config['worker_argv']
    arg = lambda name: args[args.index(name) + 1]
    workspace = Path(arg('--workspace'))
    marker = workspace / 'worker_workspace.json'
    old_bytes = marker.read_bytes()
    old = json.loads(old_bytes)
    sys.path.insert(0, str(a.runner.parent))
    spec = importlib.util.spec_from_file_location('workspace_repair_bound_runner', a.runner)
    runner = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = runner
    spec.loader.exec_module(runner)
    from jobagg import remediation_worker as worker
    from jobagg import runtime_authority as authority, runtime_source_head as heads
    from jobagg import publication_work as work, runtime_containment as containment
    from jobagg import pending_publication as pending
    from jobagg.publication_fence import verify_boundary
    from inspect_pending_publication import clone_file
    old_code = manifest(a.old_root)
    new_code = manifest(Path(worker.__file__).resolve().parent)
    require(old_code['implementation_sha256'] == a.expected_old_seal, 'Unexpected old code manifest')
    require(new_code['implementation_sha256'] == a.expected_new_seal == worker.implementation_hash(), 'Unexpected new seal')
    bound_config, _ = runner.load_config(a.config)
    contract = runner.runtime_publication_contract(bound_config)
    require(contract['sha256'] == a.expected_contract, 'Runtime contract changed')
    require(contract['jobagg_implementation_sha256'] == a.expected_new_seal, 'Runtime seal differs')
    actual = {'version': worker.VERSION, 'registry_sha256': worker.sha(arg('--registry')),
              'robots_sha256': worker.sha(arg('--robots')), 'implementation_sha256': worker.implementation_hash(),
              'shared_lock': str(Path(arg('--shared-lock')).absolute())}
    reviewed_binding(old, actual, old_code, new_code)
    require(actual['shared_lock'] == config['shared_lock_path'], 'Owner differs')
    d = a.output_dir.resolve()
    d.mkdir(parents=True, exist_ok=True)
    require(not (d / 'binding-intent.json').exists(), 'Prior intent exists; inspect before retry')
    require(d.stat().st_dev == Path(config['publication_worker_database']).stat().st_dev, 'Backup needs same-volume directory')
    save(d / 'old-module-manifest.json', old_code)
    save(d / 'new-module-manifest.json', new_code)
    save(d / 'worker-marker.before.json', old_bytes)
    save(d / 'worker-marker.after.json', actual)
    delta = {'added': sorted(new_code['modules'].keys() - old_code['modules'].keys()),
             'removed': sorted(old_code['modules'].keys() - new_code['modules'].keys()),
             'changed': sorted(k for k in old_code['modules'].keys() & new_code['modules'].keys()
                               if old_code['modules'][k] != new_code['modules'][k])}
    save(d / 'module-difference.json', delta)
    if not a.execute:
        print(json.dumps({'staged': True, 'old_modules': len(old_code['modules']),
                          'new_modules': len(new_code['modules']), 'module_difference': {k: len(v) for k, v in delta.items()}}))
        return
    owner = Path(config['shared_lock_path'])
    fence = Path(config['maintenance_file'])
    run = Path(config['state_dir']) / 'runs' / a.run_id
    health_path = Path(config['attempt_state_dir']) / 'health.json'
    output = Path(config['publication_argv'][config['publication_argv'].index('--output-dir') + 1])
    token = encoded({'purpose': 'workspace-preflight-repair', 'operator': 'Codex for yutsukioka2',
                     'created_at': datetime.now(timezone.utc).isoformat(), 'token': uuid.uuid4().hex})
    with owner.open('r+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        health = json.loads(health_path.read_text())
        gate = json.loads((output / '.jobagg-publication-state.json').read_text())
        require(health['active_attempts'] == {} and health['unresolved_publication'] is False, 'Runtime not idle')
        require(gate['state'] == 'complete' and gate['readback_verified'] is True, 'Publication incomplete')
        require(pending.inspect(Path(config['state_dir'])) is None, 'Pending publication intent')
        require(a.config.read_bytes() == config_bytes and marker.read_bytes() == old_bytes, 'Config or marker changed')
        outcome = json.loads((run / 'outcome.json').read_text())
        error = json.loads((run / 'stdout.log').read_text())
        require(outcome['run_id'] == a.run_id and error.get('reason') ==
                'Workspace code/config drift requires a separately reviewed generation', 'Unexpected worker failure')
        require(not (run / 'acceptance.json').exists() and not (run / 'worker_result.json').exists(), 'Worker proof exists')
        for pid in (outcome['worker_pid'], outcome['wrapper_pid']):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue
            raise ValueError('Recorded process ID is live')
        require(not fence.exists() and not (d / 'fence-owned.json').exists(), 'Existing maintenance fence')
        save(fence, token)
        save(d / 'fence-owned.json', token)
        database = Path(config['publication_worker_database'])
        for suffix in ('', '-wal', '-shm'):
            source = Path(str(database) + suffix)
            if source.exists():
                clone_file(source, d / ('worker.before.sqlite3' + suffix))
        for name in ('request.json', 'outcome.json', 'state.json', 'stdout.log'):
            save(d / ('failed-' + name), (run / name).read_bytes())
        save(d / 'health.before.json', health_path.read_bytes())
        with closing(sqlite3.connect(database, timeout=1)) as conn:
            work.configure(conn)
            state = authority.validate(conn)
            check_failed_admission(state, outcome, conn.execute(
                'SELECT 1 FROM jobagg_accepted_events WHERE run_id=? LIMIT 1', (a.run_id,)).fetchone())
            require(state['config_sha256'] == contract['config_sha256'], 'Admission config differs')
            control = json.loads(state['control_json'])
            proof = dict(accepted_progress=0, contract_sha256=contract['sha256'],
                         config_sha256=contract['config_sha256'], implementation_sha256=a.expected_new_seal,
                         policy_location=config['policy_authority_location'])
            scoped = containment.can_close_scoped_noop(conn, a.run_id, **proof)
            if not scoped:
                allow_due_preflight_closure(work.vector(conn, contract_sha256=contract['sha256'], now=time.time()),
                    approved=containment.approved(conn, config_sha256=contract['config_sha256'],
                       implementation_sha256=a.expected_new_seal, policy_location=config['policy_authority_location']),
                    boundary_verified=control.get('publication_scope') is not None and verify_boundary(
                       control.get('publication_cursor'), expected_scope=control['publication_scope']))
            baseline = protected(conn, containment)
            baseline['containment_review'] = conn.execute('SELECT review_json FROM jobagg_legacy_review_holds WHERE run_id=?', (containment.KEY,)).fetchone()[0]
            head = Path(json.loads(state['bootstrap_json'])['restore_head_path'])
            save(d / 'source-head.before.json', head.read_bytes())
            save(d / 'binding-intent.json', {'native_before': state, 'protected_before': baseline,
                 'old_marker_sha256': sha(old_bytes), 'new_marker_sha256': sha(encoded(actual)),
                 'failed_run': a.run_id, 'worker_success_credit': False})
            temporary = marker.with_name('.workspace-reviewed-repair.tmp')
            require(not temporary.exists(), 'Unexpected workspace temporary file')
            save(temporary, encoded(actual))
            os.chmod(temporary, marker.stat().st_mode & 0o777)
            require(fence.read_bytes() == token and marker.read_bytes() == old_bytes, 'Fence or marker changed')
            temporary.replace(marker)
            instance = worker.Worker(registry=arg('--registry'), robots=arg('--robots'), workspace=workspace,
                                     shared_lock=owner, policy_authority_location=config['policy_authority_location'])
            preview = instance.preview()
            require(preview['binding'] == actual and preview['network_requests'] == 0 and preview['database_writes'] == 0, 'Worker preview failed')
            save(d / 'worker-preview.json', preview)
            require(worker.implementation_hash() == a.expected_new_seal, 'Worker seal changed during handoff')
            allowed = {'jobagg_runtime_state': {'phase', 'source_transaction_id'}}
            def authorize(action, table, column, db, trigger):
                if action in {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_DELETE}:
                    return sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_UPDATE and column not in allowed.get(table, set()):
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            with heads.grant(conn, owner_fd=lock.fileno(), control=True, authorizer=authorize):
                conn.execute('EXPLAIN UPDATE jobagg_runtime_state SET phase=? WHERE id=1', ('idle',)).fetchall()
                with conn:
                    if scoped:
                        containment.close_scoped_noop(conn, a.run_id, **proof)
                    else:
                        authority.finish_publication(conn, a.run_id)
                    after = authority.validate(conn)
                    require(after['phase'] == 'idle' and after['revision'] == state['revision'], 'Closure invariant failed')
                    final = protected(conn, containment)
                    final['containment_review'] = conn.execute('SELECT review_json FROM jobagg_legacy_review_holds WHERE run_id=?', (containment.KEY,)).fetchone()[0]
                    require(final == baseline, 'Protected work or holds changed')
            save(d / 'closure-complete.json', {'phase': after['phase'], 'revision': after['revision'],
                 'protected_unchanged': True, 'failed_run': a.run_id, 'worker_success_credit': False})
        require(fence.read_bytes() == token and json.loads(marker.read_text()) == actual, 'Fence or binding changed')
        require(a.config.read_bytes() == config_bytes and worker.implementation_hash() == a.expected_new_seal, 'Config or seal changed')
        save(d / 'activation-verified.json', {'completed_at': datetime.now(timezone.utc).isoformat(),
             'failed_run': a.run_id, 'config_sha256': contract['config_sha256'], 'contract_sha256': contract['sha256'],
             'worker_seal': a.expected_new_seal, 'holds_preserved': True, 'cron_schedule_unchanged': True})
        fence.unlink()
        print(json.dumps({'repair_complete': True, 'evidence': str(d), 'dispatch_resumed': True}))


if __name__ == '__main__':
    main()
