#!/usr/bin/env python3
"""Explicit operator repair for a registry-path preflight failure; never a worker.

Uses the deployed native scoped-noop closure. Original failure evidence and all
work/holds survive. Default stages inputs only; --execute fences dispatch and
leaves that fence in place on any failure. No automatic retry after SQL intent.
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
import uuid


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, indent=2, sort_keys=True) + '\n').encode()


def save(path, data):
    payload = data if isinstance(data, bytes) else encoded(data)
    if path.exists():
        require(path.read_bytes() == payload, 'Existing evidence differs: ' + str(path))
        return
    with path.open('xb') as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def corrected_manifest(manifest, registry):
    original = Path(manifest['registry']['path']).resolve(strict=True)
    target = Path(registry).resolve(strict=True)
    require(original != target, 'Registry paths already agree')
    require(sha(original.read_bytes()) == sha(target.read_bytes()) == manifest['registry']['sha256'],
            'Registry contents differ; path-only repair refused')
    result = json.loads(json.dumps(manifest))
    result['registry']['path'] = str(target)
    return result


def check_failed_admission(state, outcome, accepted):
    require(state['phase'] == 'worker' and state['run_id'] == outcome['run_id'], 'Admission changed')
    require(state['revision'] == state['start_revision'], 'Worker changed authoritative data')
    require(not accepted, 'Accepted work exists')
    require(outcome['worker_exit_code'] == 3 and outcome['status'] == 'process_failure'
            and outcome['publication_status'] == 'not_started', 'Not a preflight process failure')


def allow_due_preflight_closure(vector, *, approved, boundary_verified):
    require(approved and boundary_verified, 'Containment or publication boundary invalid')
    require(vector['pending'] is None and vector.get('unscoped_held') is None, 'Publication work requires repair')
    require(vector['revalidation_reasons'] == ['time_transition_due'], 'Unexpected revalidation debt')


def protected(conn, containment):
    result = {}
    for table in ('jobagg_dirty_current', 'jobagg_dirty_versions', 'jobagg_dirty_cohorts',
                  'jobagg_legacy_controls', 'jobagg_legacy_dependencies', 'jobagg_dirty_transitions'):
        h = hashlib.sha256()
        count = 0
        for row in conn.execute('SELECT * FROM ' + table + ' ORDER BY 1'):
            h.update(json.dumps(row, default=str).encode())
            count += 1
        result[table] = {'rows': count, 'sha256': h.hexdigest()}
    rows = conn.execute('SELECT * FROM jobagg_legacy_review_holds WHERE run_id!=? ORDER BY run_id',
                        (containment.KEY,)).fetchall()
    result['next_transition_at'] = conn.execute('SELECT next_transition_at FROM jobagg_dirty_state WHERE id=1').fetchone()[0]
    result['unrelated_holds'] = sha(json.dumps(rows).encode())
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('config', 'runner', 'old-contract', 'output-dir'):
        p.add_argument('--' + name, type=Path, required=True)
    p.add_argument('--run-id', required=True)
    p.add_argument('--expected-seal', required=True)
    p.add_argument('--execute', action='store_true')
    a = p.parse_args()
    d = a.output_dir.resolve()
    d.mkdir(parents=True, exist_ok=True)
    require(not (d / 'binding-intent.json').exists(), 'Prior intent exists; inspect rather than repeat')
    before_bytes = a.config.read_bytes()
    config = json.loads(before_bytes)
    old_manifest = Path(config['source_manifest_path'])
    registry = config['worker_argv'][config['worker_argv'].index('--registry') + 1]
    manifest = corrected_manifest(json.loads(old_manifest.read_text()), registry)
    after_config = {**config, 'source_manifest_path': str(d / 'source_manifest.json')}
    for name, data in [('dispatcher.before.json', before_bytes), ('manifest.before.json', old_manifest.read_bytes()),
                       ('source_manifest.json', encoded(manifest)), ('dispatcher.after.json', encoded(after_config))]:
        target = d / name
        if target.exists():
            require(target.read_bytes() == data, 'Staged input differs: ' + name)
        else:
            save(target, data)
    sys.path.insert(0, str(a.runner.parent))
    spec = importlib.util.spec_from_file_location('repair_bound_runner', a.runner)
    runner = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = runner
    spec.loader.exec_module(runner)
    old, _ = runner.load_config(a.config)
    new, _ = runner.load_config(d / 'dispatcher.after.json')
    old_contract = runner.runtime_publication_contract(old)
    contract = runner.runtime_publication_contract(new)
    require(old_contract == json.loads(a.old_contract.read_text()), 'Existing runtime contract changed')
    require(contract['jobagg_implementation_sha256'] == a.expected_seal, 'Worker seal changed')
    require(runner.runtime_publication_scope(old) == runner.runtime_publication_scope(new), 'Publication scope changed')
    changed = {k for k in contract if contract[k] != old_contract[k]}
    require(changed == {'sha256', 'config_sha256', 'manifest_sha256'}, 'Unexpected contract changes')
    for name, data in [('contract.before.json', old_contract), ('contract.after.json', contract)]:
        if not (d / name).exists():
            save(d / name, data)
        else:
            require((d / name).read_bytes() == encoded(data), 'Contract staging changed')
    if not a.execute:
        print(json.dumps({'staged': True, 'output': str(d), 'worker_seal': a.expected_seal}))
        return
    from jobagg import runtime_authority as authority, runtime_source_head as heads
    from jobagg import publication_work as work, runtime_containment as containment
    from jobagg import pending_publication as pending
    from inspect_pending_publication import clone_file
    owner = Path(config['shared_lock_path'])
    fence = Path(config['maintenance_file'])
    run = Path(config['state_dir']) / 'runs' / a.run_id
    health_path = Path(config['attempt_state_dir']) / 'health.json'
    output = Path(config['publication_argv'][config['publication_argv'].index('--output-dir') + 1])
    token = encoded({'purpose': 'registry-preflight-repair', 'operator': 'Codex for yutsukioka2',
                     'created_at': datetime.now(timezone.utc).isoformat(), 'token': uuid.uuid4().hex})
    with owner.open('r+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        health = json.loads(health_path.read_text())
        gate = json.loads((output / '.jobagg-publication-state.json').read_text())
        require(health['active_attempts'] == {} and health['unresolved_publication'] is False,
                'Runtime not idle')
        require(gate['state'] == 'complete' and gate['readback_verified'] is True, 'Publication incomplete')
        require(pending.inspect(Path(config['state_dir'])) is None, 'Pending publication intent')
        require(a.config.read_bytes() == before_bytes, 'Live config changed')
        outcome = json.loads((run / 'outcome.json').read_text())
        error = json.loads((run / 'stdout.log').read_text())
        require(error.get('reason') == 'Registry differs from dispatcher request', 'Unexpected worker failure')
        require(not (run / 'acceptance.json').exists() and not (run / 'worker_result.json').exists(), 'Worker proof exists')
        # A reused live PID is conservatively rejected; no signals are sent.
        for pid in (outcome['worker_pid'], outcome['wrapper_pid']):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue
            raise ValueError('Recorded worker/wrapper PID is live; inspect boot/process identity')
        require(d.stat().st_dev == Path(config['publication_worker_database']).stat().st_dev, 'Backup requires same-volume operation directory')
        if fence.exists():
            require((d / 'fence-owned.json').exists(), 'Existing fence belongs to another operation')
            token = (d / 'fence-owned.json').read_bytes()
            require(fence.read_bytes() == token, 'Fence ownership changed')
        else:
            require(not (d / 'fence-owned.json').exists(), 'Previous operation fence was removed; inspect first')
            save(fence, token)  # Exclusive creation; never replace another operator's fence.
            save(d / 'fence-owned.json', token)
        database = Path(config['publication_worker_database'])
        for suffix in ('', '-wal', '-shm'):
            source = Path(str(database) + suffix)
            if source.exists() and not (d / ('worker.before.sqlite3' + suffix)).exists():
                clone_file(source, d / ('worker.before.sqlite3' + suffix))
        for name in ('request.json', 'outcome.json', 'state.json', 'stdout.log'):
            save(d / ('failed-' + name), (run / name).read_bytes())
        if not (d / 'health.before.json').exists():
            save(d / 'health.before.json', health_path.read_bytes())
        with closing(sqlite3.connect(database, timeout=1)) as conn:
            work.configure(conn)
            state = authority.validate(conn)
            check_failed_admission(state, outcome, conn.execute(
                'SELECT 1 FROM jobagg_accepted_events WHERE run_id=? LIMIT 1', (a.run_id,)).fetchone())
            require(state['config_sha256'] == old_contract['config_sha256'], 'Admission configuration differs')
            proof = dict(accepted_progress=0, contract_sha256=old_contract['sha256'],
                         config_sha256=old_contract['config_sha256'], implementation_sha256=a.expected_seal,
                         policy_location=config['policy_authority_location'])
            scoped_noop = containment.can_close_scoped_noop(conn, a.run_id, **proof)
            if not scoped_noop:
                import time
                from jobagg.publication_fence import verify_boundary
                control = json.loads(state['control_json'])
                allow_due_preflight_closure(work.vector(conn, contract_sha256=old_contract['sha256'], now=time.time()),
                    approved=containment.approved(conn, config_sha256=old_contract['config_sha256'],
                        implementation_sha256=a.expected_seal, policy_location=config['policy_authority_location']),
                    boundary_verified=control.get('publication_scope') is not None and verify_boundary(
                        control.get('publication_cursor'), expected_scope=control['publication_scope']))
            before = protected(conn, containment)
            review_text = conn.execute('SELECT review_json FROM jobagg_legacy_review_holds WHERE run_id=?', (containment.KEY,)).fetchone()[0]
            review = json.loads(review_text)
            require(conn.execute('SELECT contract_sha256 FROM jobagg_dirty_state WHERE id=1').fetchone()[0] == old_contract['sha256'], 'Dirty contract differs')
            require(conn.execute('SELECT complete FROM jobagg_dirty_revalidation WHERE id=1 AND contract_sha256=?', (old_contract['sha256'],)).fetchone() == (1,), 'Revalidation incomplete')
            head = Path(json.loads(state['bootstrap_json'])['restore_head_path'])
            save(d / 'source-head.before.json', head.read_bytes())
            save(d / 'binding-intent.json', {'native_before': state, 'protected_before': before,
                 'old_contract': old_contract['sha256'], 'new_contract': contract['sha256'],
                 'reason': 'Close failed registry preflight with zero accepted work; identical registry bytes, corrected path only'})
            receipt = sha((d / 'binding-intent.json').read_bytes())
            review_after = {**review, 'config_sha256': contract['config_sha256'],
                            'registry_path_handoff': {'receipt': str(d / 'binding-intent.json'), 'sha256': receipt}}
            statements = [
                ('UPDATE jobagg_runtime_state SET config_sha256=? WHERE id=1', (contract['config_sha256'],)),
                ('UPDATE jobagg_dirty_state SET contract_sha256=? WHERE id=1', (contract['sha256'],)),
                ('UPDATE jobagg_dirty_revalidation SET contract_sha256=?,receipt_sha256=? WHERE id=1', (contract['sha256'], receipt)),
                ('UPDATE jobagg_legacy_review_holds SET review_json=? WHERE run_id=?', (json.dumps(review_after, sort_keys=True), containment.KEY)),
            ]
            allowed = {'jobagg_runtime_state': {'phase', 'config_sha256', 'source_transaction_id'},
                       'jobagg_dirty_state': {'contract_sha256'},
                       'jobagg_dirty_revalidation': {'contract_sha256', 'receipt_sha256'},
                       'jobagg_legacy_review_holds': {'review_json'}}
            def authorize(action, table, column, database, trigger):
                if action in {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_DELETE}:
                    return sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_UPDATE and column not in allowed.get(table, set()):
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            with heads.grant(conn, owner_fd=lock.fileno(), control=True, authorizer=authorize):
                for sql, args in statements + [('UPDATE jobagg_runtime_state SET phase=? WHERE id=1', ('idle',))]:
                    conn.execute('EXPLAIN ' + sql, args).fetchall()
                with conn:
                    if scoped_noop:
                        containment.close_scoped_noop(conn, a.run_id, **proof)
                    else:
                        # Retire only the proven zero-effects failed admission.
                        # Due work stays untouched for the next natural publication.
                        authority.finish_publication(conn, a.run_id)
                    for sql, args in statements:
                        require(conn.execute(sql, args).rowcount == 1, 'Binding update missed row')
                    after = authority.validate(conn)
                    require(after['phase'] == 'idle' and after['revision'] == state['revision'], 'Closure invariant failed')
                    require(protected(conn, containment) == before, 'Protected history changed')
                    require(containment.approved(conn, config_sha256=contract['config_sha256'],
                            implementation_sha256=a.expected_seal, policy_location=config['policy_authority_location']), 'New containment binding failed')
            save(d / 'binding-sql-complete.json', {'complete': True, 'phase': after['phase'],
                 'revision': after['revision'], 'protected_unchanged': True, 'failed_run': a.run_id,
                 'worker_success_credit': False, 'due_transitions_preserved': True, 'new_contract': contract['sha256']})
        require(fence.read_bytes() == token, 'Fence ownership changed')
        temporary = a.config.with_name('.registry-repair-config.tmp')
        save(temporary, (d / 'dispatcher.after.json').read_bytes())
        temporary.replace(a.config)
        actual = runner.runtime_publication_contract(runner.load_config(a.config)[0])
        require(actual == contract, 'Activated runtime contract differs')
        require(fence.read_bytes() == token, 'Fence ownership changed')
        save(d / 'activation-verified.json', {'completed_at': datetime.now(timezone.utc).isoformat(),
             'config_sha256': contract['config_sha256'], 'contract_sha256': contract['sha256'],
             'worker_seal_unchanged': a.expected_seal, 'holds_preserved': True, 'cron_schedule_unchanged': True})
        fence.unlink()
        print(json.dumps({'repair_complete': True, 'evidence': str(d), 'dispatch_resumed': True}))


if __name__ == '__main__':
    main()
