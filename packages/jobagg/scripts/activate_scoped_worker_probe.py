#!/usr/bin/env python3
"""One scoped dispatcher handoff. Default is read-only preview.

Run only with the pinned Python and deployed worker package. Failure after the
journal starts retains this operation's maintenance fence for explicit recovery.
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

from jobagg import runtime_authority as authority, runtime_source_head as heads
from jobagg import runtime_contract, publication_work as work
from jobagg import remediation_worker as worker
from jobagg.publication_fence import verify_boundary

LIVE = Path('/Users/yutsukioka2/git/un_job_application_helper/reports/jobagg-deterministic-fetch-2026-09-14/dispatcher.proposed.json')
BASE = Path('/Users/yutsukioka2/git/un_job_application_helper-releases/pr143-075cd7ac-dispatcher-timeouts/source/reports/jobagg-remediation-plan-2026-09-10/proposed_runner')
OLD_CONTRACT = Path('/Volumes/MacbookAirM2/un_job_application_helper/jobagg-operations/audit-independent-20261030/contract.after.json')
D = Path('/Volumes/MacbookAirM2/un_job_application_helper/jobagg-operations/scoped-probe-20261030')
CONFIG_SHA = '566e5a7233d0c6eb10c158235b297c81aa65ecc3cf627e59ae492a352933ecab'
SEAL = 'dba19225ae326c3cc00b655203f23f0af3b5eab2b093a0ab22302346df590e85'
RUN = 'd42856c6-5576-42cf-ae66-9db40640f40e'


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def file_sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def save(path, value):
    data = value if isinstance(value, bytes) else (json.dumps(value, sort_keys=True, indent=2)+'\n').encode()
    with path.open('xb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def load_runner():
    spec = importlib.util.spec_from_file_location('scoped_operator_runner', BASE/'runner.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def protected(conn):
    result = {}
    tables = ('jobagg_dirty_current', 'jobagg_dirty_versions', 'jobagg_dirty_cohorts',
              'jobagg_dirty_transitions', 'jobagg_legacy_controls',
              'jobagg_legacy_dependencies', 'jobagg_legacy_review_holds')
    for table in tables:
        h = hashlib.sha256()
        n = 0
        for row in conn.execute('SELECT * FROM '+table+' ORDER BY 1'):
            h.update(json.dumps(row, default=str).encode())
            n += 1
        result[table] = [n, h.hexdigest()]
    for table in ('jobs', 'remediation_tasks', 'remediation_attempts',
                  'remediation_observations', 'jobagg_accepted_events', 'jobagg_accepted_evidence'):
        result[table] = conn.execute('SELECT count(*) FROM '+table).fetchone()[0]
    result['next_transition_at'] = conn.execute('SELECT next_transition_at FROM jobagg_dirty_state WHERE id=1').fetchone()[0]
    return result


def policy_files(config):
    root = Path(config['policy_authority_location']['root'])
    paths = [root/'authority.sqlite3', root/'authority.json', root/'source_holds.json']
    paths += [Path(str(root/'authority.sqlite3')+s) for s in ('-wal', '-shm')]
    return {str(p): file_sha(p) if p.exists() else None for p in paths}


def replace_reviewed(path, data):
    directory_mode = path.parent.stat().st_mode & 0o777
    file_mode = path.stat().st_mode & 0o777
    temporary = path.with_name('.scoped-probe-'+uuid.uuid4().hex+'.tmp')
    try:
        os.chmod(path.parent, directory_mode | 0o200)
        save(temporary, data)
        os.chmod(temporary, file_mode)
        temporary.replace(path)
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        os.chmod(path.parent, directory_mode)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', required=True, type=Path)
    parser.add_argument('--candidate-sha256', required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    candidate = args.candidate.read_bytes()
    require(sha(candidate) == args.candidate_sha256, 'Candidate bytes differ')
    target = BASE/'runner_observability.py'
    original = target.read_bytes()
    expected_old = b'''    # Preserve pre-M1 holds until explicitly reviewed; a release change isn't recovery.
    if circuit is None and prior.get("unchanged_failures", 0) >= 3:
'''
    expected_new = b'''    # The structured circuit table is authoritative. Its top-level summary is
    # not a hold on other stages. Keep the fallback only for pre-M1 records.
    if "circuits" not in prior and circuit is None and prior.get("unchanged_failures", 0) >= 3:
'''
    require(original.count(expected_old) == 1 and original.replace(expected_old, expected_new) == candidate,
            'Candidate must contain only the reviewed fallback correction')
    require(file_sha(LIVE) == CONFIG_SHA and worker.implementation_hash() == SEAL, 'Live config or worker seal differs')
    runner = load_runner()
    config, expected_sources = runner.load_config(LIVE)
    before = runner.runtime_publication_contract(config)
    require(before == json.loads(OLD_CONTRACT.read_text()), 'Existing runtime contract differs')
    after = json.loads(json.dumps(before))
    after['dispatcher'][str(target)] = sha(candidate)
    after.pop('sha256')
    after['sha256'] = runtime_contract._digest(after)
    require({k for k in before if before[k] != after[k]} == {'dispatcher', 'sha256'}, 'Contract scope differs')
    require(not (D/'binding-intent.json').exists(), 'Previous handoff intent exists; inspect before retry')
    if not args.execute:
        print(json.dumps({'preview': True, 'old_contract': before['sha256'], 'new_contract': after['sha256'],
                          'worker_seal': SEAL, 'probe_stage': 'worker', 'candidate_sha256': sha(candidate)}))
        return
    from inspect_pending_publication import clone_file
    lock_path, fence = config['shared_lock_path'], config['maintenance_file']
    health_path = config['attempt_state_dir']/'health.json'
    condition_path = config['attempt_state_dir']/'failure_condition.json'
    database = config['publication_worker_database']
    output = Path(config['publication_argv'][config['publication_argv'].index('--output-dir')+1])
    run = config['state_dir']/'runs'/RUN
    token = {'purpose': 'scoped-worker-probe', 'token': uuid.uuid4().hex,
             'created_at': datetime.now(timezone.utc).isoformat()}
    with lock_path.open('r+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        health = json.loads(health_path.read_text())
        gate = json.loads((output/'.jobagg-publication-state.json').read_text())
        require(health.get('active_attempts') == {} and health.get('unresolved_publication') is False, 'Runtime not idle')
        require(gate['state'] == 'complete' and gate['readback_verified'] is True, 'Publication boundary incomplete')
        require(not (run/'publication_intent.json').exists(), 'Immutable publication intent exists')
        require(file_sha(LIVE) == CONFIG_SHA and target.read_bytes() == original, 'Config or dispatcher changed')
        require(not fence.exists(), 'Existing maintenance fence')
        condition_bytes = condition_path.read_bytes()
        condition_before = json.loads(condition_bytes)
        require(set(condition_before['circuits']) == {'worker', 'end_to_end'}, 'Failure circuits changed')
        held = condition_before['circuits']['worker']
        require(held['unchanged_failures'] == 3 and held.get('reviewed_probe') is None, 'Worker hold changed')
        require(condition_before['circuits']['end_to_end']['unchanged_failures'] < 3, 'Independent whole-cycle hold')
        D.mkdir(parents=True, exist_ok=True)
        require(D.stat().st_dev == database.stat().st_dev, 'Backups must share source volume')
        save(fence, token)
        save(D/'fence-owned.json', fence.read_bytes())
        save(D/'dispatcher.before.py', original)
        save(D/'dispatcher.after.py', candidate)
        save(D/'failure-condition.before.json', condition_bytes)
        save(D/'config.before.json', LIVE.read_bytes())
        save(D/'contract.before.json', before)
        save(D/'contract.after.json', after)
        for suffix in ('', '-wal', '-shm'):
            source = Path(str(database)+suffix)
            if source.exists():
                clone_file(source, D/('worker.before.sqlite3'+suffix))
        policy_before = policy_files(config)
        proof_before = {name: file_sha(run/name) for name in ('acceptance.json', 'worker_result.json', 'request.json')}
        with closing(sqlite3.connect(database, timeout=1)) as conn:
            work.configure(conn)
            state = authority.validate(conn)
            require(state['phase'] == 'accepted' and state['run_id'] == RUN and state['revision'] == 21480,
                    'Native accepted publication state changed')
            require(state['config_sha256'] == CONFIG_SHA, 'Native config binding differs')
            control = json.loads(state['control_json'])
            require(verify_boundary(control['publication_cursor'], expected_scope=control['publication_scope']),
                    'Destination boundary differs')
            dirty_before = conn.execute('SELECT contract_sha256 FROM jobagg_dirty_state WHERE id=1').fetchone()
            revalidation_before = conn.execute('SELECT contract_sha256,complete FROM jobagg_dirty_revalidation WHERE id=1').fetchone()
            require(dirty_before == (before['sha256'],), 'Dirty contract binding differs')
            require(revalidation_before == (before['sha256'], 1), 'Unfinished or changed revalidation')
            baseline = protected(conn)
            save(D/'source-head.before.json', Path(json.loads(state['bootstrap_json'])['restore_head_path']).read_bytes())
            save(D/'binding-intent.json', {'native_before': state, 'protected_before': baseline,
                 'policy_before': policy_before, 'proof_before': proof_before,
                 'old_contract': before['sha256'], 'new_contract': after['sha256'],
                 'candidate_sha256': sha(candidate), 'probe_stage': 'worker',
                 'scope': 'Correct cross-stage summary fallback; one reviewed worker admission; no hold reset'})
            receipt = file_sha(D/'binding-intent.json')
            statements = [
                ('UPDATE jobagg_dirty_state SET contract_sha256=? WHERE id=1 AND contract_sha256=?', (after['sha256'], before['sha256'])),
                ('UPDATE jobagg_dirty_revalidation SET contract_sha256=?,receipt_sha256=? WHERE id=1 AND complete=1 AND contract_sha256=?', (after['sha256'], receipt, before['sha256']))]
            allowed = {'jobagg_dirty_state': {'contract_sha256'},
                       'jobagg_dirty_revalidation': {'contract_sha256', 'receipt_sha256'},
                       'jobagg_runtime_state': {'source_transaction_id'}}
            def authorize(action, table, column, db, trigger):
                if action in {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_DELETE}:
                    return sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_UPDATE and column not in allowed.get(table, set()):
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            with heads.grant(conn, owner_fd=lock.fileno(), control=True, authorizer=authorize):
                for sql, values in statements:
                    conn.execute('EXPLAIN '+sql, values).fetchall()
                with conn:
                    for sql, values in statements:
                        require(conn.execute(sql, values).rowcount == 1, 'Contract update missed row')
                    current = authority.validate(conn)
                    require({k:v for k,v in current.items() if k != 'source_transaction_id'} ==
                            {k:v for k,v in state.items() if k != 'source_transaction_id'}, 'Native state changed')
                    require(protected(conn) == baseline, 'Protected data or holds changed')
            save(D/'binding-sql-complete.json', {'revision': current['revision'], 'phase': current['phase'],
                 'run_id': current['run_id'], 'protected_unchanged': True, 'contract_sha256': after['sha256']})
        require(json.loads(fence.read_text()) == token, 'Fence changed')
        replace_reviewed(target, candidate)
        runner = load_runner()
        current_config, _ = runner.load_config(LIVE)
        require(runner.runtime_publication_contract(current_config) == after, 'Activated contract differs')
        obs = runner.OBSERVABILITY
        obs.condition_fingerprint(current_config, BASE/'runner.py')
        # Probe is not consumed by projection-only continuation. No failure
        # episode/counter is erased; natural verified worker success archives it.
        require(condition_path.read_bytes() == condition_bytes, 'Failure evidence changed')
        expires = time.time()+86400
        review = {'operation': 'one reviewed worker probe after audit-independent repair',
                  'binding_intent_sha256': receipt, 'config_sha256': CONFIG_SHA,
                  'worker_seal': SEAL, 'runtime_contract_sha256': after['sha256'],
                  'scope': obs.stable_failure_scope(current_config, 'worker'),
                  'expires_at': expires, 'hold_before_sha256': sha(condition_bytes)}
        save(D/'probe-review.json', review)
        obs.authorize_stage_probe(current_config, 'worker', evidence_path=D/'probe-review.json',
                                  evidence_sha256=file_sha(D/'probe-review.json'), expires_at=expires)
        condition_after = json.loads(condition_path.read_text())
        trimmed = json.loads(json.dumps(condition_after))
        for key in ('reviewed_probe', 'probe_review_history'):
            trimmed['circuits']['worker'][key] = held[key]
        require(trimmed == condition_before, 'Probe changed existing hold evidence')
        require(condition_after['circuits']['worker']['reviewed_probe']['state'] == 'available', 'Probe not available')
        require(policy_files(config) == policy_before, 'Policy charges or source holds changed')
        require(proof_before == {name:file_sha(run/name) for name in proof_before}, 'Accepted proof changed')
        require(worker.implementation_hash() == SEAL and file_sha(LIVE) == CONFIG_SHA, 'Worker seal or config changed')
        save(D/'failure-condition.after.json', condition_path.read_bytes())
        save(D/'activation-verified.json', {'activated_at': datetime.now(timezone.utc).isoformat(),
             'worker_seal': SEAL, 'config_sha256': CONFIG_SHA, 'runtime_contract_sha256': after['sha256'],
             'policy_charges_and_holds_preserved': True, 'accepted_proof_preserved': True,
             'probe_stage': 'worker', 'probe_state': 'available', 'expires_at': expires,
             'dispatcher_sha256': file_sha(target), 'cron_unchanged': True})
        require(json.loads(fence.read_text()) == token, 'Fence changed')
        fence.unlink()
        print(json.dumps({'activated': True, 'probe': 'available', 'evidence': str(D)}))


if __name__ == '__main__':
    main()
