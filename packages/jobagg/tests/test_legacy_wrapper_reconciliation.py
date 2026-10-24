"""A diagnostic duplicate can close without blessing its failed child."""
import hashlib
import json
import sqlite3
import time

import pytest

from jobagg import runtime_control_catalog as catalog, runtime_authority as authority
from jobagg.remediation_worker import shared_owner


def auxiliary_id(wrapper):
    return 'held-' + hashlib.sha256(str(wrapper).encode()).hexdigest()


def setup_catalog(tmp_path, conn, fd, owner):
    worker, wrapper = tmp_path / 'worker', tmp_path / 'wrapper'
    worker.mkdir()
    wrapper.mkdir()
    result = {
        'schema_version': 1, 'run_id': worker.name, 'status': 'process_failure',
        'started_at': '2026-01-01T01:00:01+00:00',
        'finished_at': '2026-01-01T01:01:00+00:00',
        'wrapper_pid': 1073741820, 'worker_exit_code': -15,
        'run_dir': str(worker), 'request_path': str(worker / 'request.json'),
        'report_path': str(worker / 'acceptance.json'),
    }
    controls = {}
    for name, value in {'request.json': {'run_id': worker.name}, 'outcome.json': result}.items():
        p = worker / name
        p.write_text(json.dumps(value))
        controls[name] = hashlib.sha256(p.read_bytes()).hexdigest()
    intent = {'schema_version': 1, 'attempt_id': wrapper.name, 'status': 'started',
              'started_at': '2026-01-01T01:00:00+00:00', 'pid': result['wrapper_pid'],
              'config': {'sha256': 'b' * 64}}
    outcome = {**intent, 'status': result['status'], 'finished_at': '2026-01-01T01:01:01+00:00',
               'result': result}
    unverified = {}
    for name, value in {'intent.json': intent, 'outcome.json': outcome}.items():
        p = wrapper / name
        p.write_text(json.dumps(value))
        s = p.stat()
        unverified[name] = [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns]
    manifest = {
        'schema': catalog.SCHEMA, 'epoch': 'fixture', 'shared_owner': str(owner),
        'complete_control_root_inventory': True, 'writers': [{'name': 'fixture', 'fenced': True}],
        'runs': [
            {'run_id': auxiliary_id(wrapper), 'kind': 'held_control_set', 'path': str(wrapper), 'controls': {},
             'unverified_files': unverified},
            {'run_id': worker.name, 'path': str(worker), 'controls': controls},
        ],
    }
    sha = catalog.prepare(conn, manifest=manifest, owner_fd=fd, owner_path=owner)
    catalog.step(conn, owner_fd=fd, owner_path=owner, limit=2,
                 deadline_at=time.monotonic() + 5, reviewed_implementations=set())
    return sha, worker, wrapper


def resolve(conn, fd, owner, sha):
    return catalog.reconcile_wrapper_reference(
        conn, auxiliary_id(owner.parent / 'wrapper'), expected_inventory_sha256=sha, owner_fd=fd, owner_path=owner,
        deadline_at=time.monotonic() + 5)


def test_reference_retains_failed_child_debt_and_original_receipt_under_native_fence(tmp_path):
    owner = tmp_path / 'owner.lock'
    with shared_owner(owner) as fd, sqlite3.connect(tmp_path / 'worker.sqlite3') as conn:
        sha, worker, wrapper = setup_catalog(tmp_path, conn, fd, owner)
        original = catalog.receipt(conn)
        child = conn.execute("SELECT * FROM jobagg_legacy_controls WHERE run_id='worker'").fetchone()
        authority.activate(conn, epoch='fixture', owner_fd=fd, owner_path=owner,
                           bootstrap_receipt={'reconciled': True, 'source_sha256': 'a' * 64,
                                              'control_catalogue_sha256': original['sha256']})
        conn.commit()
        with authority.controller(conn, run_id=None, owner_fd=fd):
            proof = resolve(conn, fd, owner, sha)
            assert resolve(conn, fd, owner, sha) == proof
            conn.commit()
        assert proof['certifies_acceptance_or_publication'] is False
        assert catalog.oldest_unresolved(conn)['run_id'] == 'worker'
        assert conn.execute("SELECT * FROM jobagg_legacy_controls WHERE run_id='worker'").fetchone() == child
        assert catalog.receipt(conn) == original
        assert (wrapper / 'outcome.json').exists() and (worker / 'outcome.json').exists()
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='jobagg_accepted_exit_runs'").fetchone()
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("UPDATE jobagg_legacy_controls SET disposition='terminal_verified'")


@pytest.mark.parametrize('change', ['bytes', 'alias', 'missing_child', 'different_child',
                                   'extra_action', 'inventory', 'chronology', 'pid', 'timeout'])
def test_reference_rejects_unbound_or_changed_controls_without_writing(tmp_path, change):
    owner = tmp_path / 'owner.lock'
    with shared_owner(owner) as fd, sqlite3.connect(':memory:') as conn:
        sha, worker, wrapper = setup_catalog(tmp_path, conn, fd, owner)
        if change == 'bytes':
            (wrapper / 'outcome.json').write_text('{}')
        elif change == 'alias':
            p = wrapper / 'outcome.json'
            p.rename(wrapper / 'saved.json')
            p.symlink_to(wrapper / 'saved.json')
        elif change == 'missing_child':
            conn.execute("DELETE FROM jobagg_legacy_controls WHERE run_id='worker'")
        elif change == 'different_child':
            p = worker / 'outcome.json'
            p.write_text(json.dumps({**json.loads(p.read_text()), 'worker_exit_code': 0}))
        elif change in {'extra_action', 'chronology', 'pid'}:
            p = wrapper / 'outcome.json'
            value = json.loads(p.read_text())
            if change == 'extra_action':
                value['result']['storage_retention'] = {'deleted_bytes': 1}
            elif change == 'chronology':
                value['finished_at'] = '2025-01-01T00:00:00+00:00'
            else:
                value['pid'] += 1
            p.write_text(json.dumps(value))
            s = p.stat()
            conn.execute('UPDATE jobagg_legacy_dependencies SET identity_json=? WHERE path=?',
                         (json.dumps([s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns]), str(p)))
        elif change == 'inventory':
            sha = 'f' * 64
        before = conn.total_changes
        with pytest.raises((ValueError, TimeoutError)):
            if change == 'timeout':
                catalog.reconcile_wrapper_reference(conn, auxiliary_id(owner.parent / 'wrapper'), expected_inventory_sha256=sha,
                    owner_fd=fd, owner_path=owner, deadline_at=time.monotonic() - 1)
            else:
                resolve(conn, fd, owner, sha)
        assert conn.total_changes == before
        assert catalog.oldest_unresolved(conn)['run_id'] == auxiliary_id(wrapper)
