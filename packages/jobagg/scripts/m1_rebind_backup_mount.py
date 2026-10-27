#!/usr/bin/env python3
"""Renew a rebooted backup locator map on an idle fenced copy only.

Verify every retained hash in place; copy no HDD payloads. An immutable plan and
verified receipt permit the same interrupted head/map handoff to resume. Ordinary
policy guards and the original migration receipts remain unchanged.
"""
from contextlib import closing
from copy import deepcopy
import argparse
import json
import math
from pathlib import Path
import sqlite3
import time
from types import SimpleNamespace

from m1_stage_snapshot import disjoint, fenced_owner, sha
from m1_reboot_reattest import digest, write
from jobagg import runtime_authority
from jobagg.runtime_source_head import owner
from jobagg.policy_authority import PolicyAuthority
from jobagg.policy_evidence import EvidenceLocations, _identity, digest as file_digest
from jobagg.publication_fence import _file_identity


def verify_mapping(mapping, expected_hashes, *, deadline_at, maximum_bytes):
    if (not math.isfinite(deadline_at) or type(maximum_bytes) is not int
            or not 0 < maximum_bytes <= 16 * 1024**3):
        raise ValueError('finite backup verification bounds required')
    root = Path(mapping['location_root'])
    if root.resolve() != root:
        raise ValueError('backup root aliased')
    renewed = deepcopy(mapping)
    old_devices, new_devices = set(), set()
    total = 0
    def check():
        if time.monotonic() >= deadline_at:
            raise TimeoutError('backup re-attestation deadline')
    if not 0 < len(mapping['files']) <= 1024:
        raise ValueError('bounded backup reference set required')
    for original, row in mapping['files'].items():
        check()
        p = Path(row['path'])
        old = row['identity']
        actual = _identity(p)
        if (not p.is_relative_to(root) or p.resolve() != p
                or row['sha256'] != expected_hashes.get(original)
                or len(old) != 5 or old[1:] != actual[1:]):
            raise ValueError('backup change exceeds device-only renewal')
        total += old[2]
        if total > maximum_bytes:
            raise ValueError('backup verification byte bound exceeded')
        if file_digest(p, check, old[2]) != row['sha256'] or _identity(p) != actual:
            raise ValueError('backup evidence differs from retained original hash')
        old_devices.add(old[0])
        new_devices.add(actual[0])
        renewed['files'][original]['identity'] = actual
    if len(old_devices) != 1 or new_devices != {root.stat().st_dev}:
        raise ValueError('backup device transition is not one bounded mount')
    return renewed, {'files': len(mapping['files']), 'bytes': total,
                     'old_device': next(iter(old_devices)), 'new_device': next(iter(new_devices))}


def finish_handoff(policy, locations_path, plan, verified, *, owner_fd, fault=lambda _: None):
    owner(owner_fd, policy.policy.owner)
    renewed = verified['mapping']
    old = plan['head']
    pending = {**old, 'pending': True, 'backup_device_handoff': plan['sha256']}
    final = deepcopy(old)
    final['backup_device_renewal'] = {'plan_sha256': plan['sha256'], 'verified_sha256': verified['sha256']}
    current_head = json.loads(policy.head.read_text())
    current_map = json.loads(locations_path.read_text())
    if current_map not in (plan['mapping'], renewed):
        raise ValueError('backup mapping changed outside handoff')
    if current_head not in (old, pending):
        # The only admissible completed resume is this exact new head.
        final['immutable_controls'][str(locations_path)] = _file_identity(locations_path)
        if current_head != final or current_map != renewed:
            raise ValueError('backup policy head changed outside handoff')
    else:
        if current_head == old and current_map != plan['mapping']:
            raise ValueError('backup map changed before pending head')
        write(policy.head, pending)
        fault('after_pending_head')
        if current_map != renewed:
            write(locations_path, renewed)
            locations_path.chmod(0o400)
            fault('after_mapping')
        final['immutable_controls'][str(locations_path)] = _file_identity(locations_path)
        write(policy.head, final)
    return final


def execute(root, operation, *, seconds, maximum_bytes, resume=False):
    root, operation = Path(root).absolute(), Path(operation).absolute()
    if (not math.isfinite(seconds) or not 0 < seconds <= 600
            or type(maximum_bytes) is not int or not 0 < maximum_bytes <= 16 * 1024**3
            or root.resolve() != root or operation.parent != root or operation.resolve() != operation):
        raise ValueError('bounded exact copy-local operation required')
    deadline = time.monotonic() + seconds
    started = time.monotonic()
    snapshot = json.loads((root / 'snapshot.json').read_text())
    config_path = Path(snapshot['production_config'])
    if (snapshot.get('complete') is not True or snapshot['snapshot_root'] != str(root)
            or sha(config_path) != snapshot['production_config_sha256']):
        raise ValueError('completed unchanged fenced snapshot required')
    config = json.loads(config_path.read_text())
    production = Path(config['publication_worker_database'])
    policy_root = root / 'policy'
    disjoint(root, [production.parent, Path(config['shared_lock_path']).parent,
                    Path(config['state_dir']), Path(config['attempt_state_dir'])])
    policy = PolicyAuthority(SimpleNamespace(root=policy_root, owner=Path(config['shared_lock_path'])))
    locations_path = policy_root / 'evidence_locations.json'
    # A copied policy database must also be physically independent.
    original_policy = Path(config['shared_lock_path'] + '.worker-policy') / 'authority.sqlite3'
    database = root / 'worker/jobs.sqlite3'
    if (database.resolve() != database or database.samefile(production) or database.stat().st_nlink != 1
            or policy.path.resolve() != policy.path or locations_path.resolve() != locations_path
            or locations_path.stat().st_nlink != 1 or policy.head.stat().st_nlink != 1
            or policy.path.stat().st_nlink != 1
            or (original_policy.exists() and policy.path.samefile(original_policy))):
        raise ValueError('policy copy aliased or hard linked')
    with fenced_owner(config, snapshot['fence_sha256']) as fd:
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as conn:
            state = runtime_authority.validate(conn)
            if state['phase'] != 'idle' or state['owner_path'] != config['shared_lock_path']:
                raise ValueError('idle copied source with actual owner required')
        if resume:
            plan = json.loads((operation / 'plan.json').read_text())
            if plan['sha256'] != digest({k: v for k, v in plan.items() if k != 'sha256'}):
                raise ValueError('backup renewal plan changed')
        else:
            # Validate the old authority before opening any handoff; the stale
            # nested HDD device binding does not bypass any ordinary guard.
            with policy.connect() as conn:
                expected = dict(conn.execute('SELECT path,sha256 FROM evidence'))
            EvidenceLocations(policy.policy)
            plan = {'schema': 'copy-backup-device-renewal-1', 'copy_root': str(root),
                    'snapshot_sha256': sha(root / 'snapshot.json'),
                    'head': json.loads(policy.head.read_text()),
                    'mapping': json.loads(locations_path.read_text()),
                    'database_sha256': sha(policy.path)}
            plan['expected_hashes'] = {k: expected[k] for k in plan['mapping']['files']}
            plan['sha256'] = digest(plan)
            operation.mkdir(mode=0o700, exist_ok=False)
            write(operation / 'plan.json', plan)
            (operation / 'plan.json').chmod(0o400)
        if (plan['copy_root'] != str(root) or plan['snapshot_sha256'] != sha(root / 'snapshot.json')
                or sha(policy.path) != plan['database_sha256']):
            raise ValueError('backup renewal copy or policy database changed')
        verified_path = operation / 'verified.json'
        if verified_path.exists():
            verified = json.loads(verified_path.read_text())
            if (verified.get('plan_sha256') != plan['sha256']
                    or verified.get('sha256') != digest({k:v for k,v in verified.items() if k != 'sha256'})):
                raise ValueError('backup renewal verification receipt changed')
            renewed = verified['mapping']
            for row in renewed['files'].values():
                if Path(row['path']).resolve() != Path(row['path']) or _identity(row['path']) != row['identity']:
                    raise ValueError('verified backup identity changed before resume')
        else:
            renewed, result = verify_mapping(plan['mapping'], plan['expected_hashes'],
                                             deadline_at=deadline, maximum_bytes=maximum_bytes)
            verified = {'plan_sha256': plan['sha256'], 'mapping': renewed, 'result': result}
            verified['sha256'] = digest(verified)
            write(verified_path, verified)
            verified_path.chmod(0o400)
        for name, identity in plan['head']['immutable_controls'].items():
            if name != str(locations_path) and _file_identity(name) != identity:
                raise ValueError('immutable policy control changed during backup renewal')
        if time.time() < plan['head']['last_wall_at']:
            raise ValueError('clock regressed during backup renewal')
        owner(fd, policy.policy.owner)
        if sha(config_path) != snapshot['production_config_sha256']:
            raise ValueError('production configuration changed during renewal')
        old = plan['head']
        finish_handoff(policy, locations_path, plan, verified, owner_fd=fd)
        if policy.revision() != old['revision'] or sha(policy.path) != plan['database_sha256']:
            raise ValueError('backup renewal changed policy history')
        EvidenceLocations(policy.policy)
        receipt = {'schema': plan['schema'], 'plan_sha256': plan['sha256'],
                   'verified_sha256': verified['sha256'], 'result': verified['result'],
                   'policy_revision': old['revision'], 'database_unchanged_sha256': plan['database_sha256'],
                   'payloads_copied': 0, 'production_activated': False,
                   'elapsed_seconds': time.monotonic() - started}
        write(operation / 'receipt.json', receipt)
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--operation', type=Path, required=True)
    parser.add_argument('--seconds', type=float, required=True)
    parser.add_argument('--maximum-bytes', type=int, required=True)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    print(json.dumps(execute(args.snapshot, args.operation, seconds=args.seconds,
                             maximum_bytes=args.maximum_bytes, resume=args.resume)))
