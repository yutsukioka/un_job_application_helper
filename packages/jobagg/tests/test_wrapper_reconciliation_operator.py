"""The historical operator may mutate only an idle, independent snapshot."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest

from jobagg import runtime_authority as authority, runtime_control_catalog as catalog
from jobagg.remediation_worker import shared_owner
from test_legacy_wrapper_reconciliation import setup_catalog, auxiliary_id


@pytest.fixture
def operator(monkeypatch):
    scripts = Path(__file__).resolve().parents[1] / 'scripts'
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location('tested_wrapper_operator', scripts / 'm1_reconcile_wrappers.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prepare_copy(tmp_path):
    prod, root = tmp_path / 'production', tmp_path / 'snapshot'
    prod.mkdir()
    root.mkdir()
    owner, marker = prod / 'owner.lock', prod / 'maintenance'
    marker.write_bytes(b'operator-owned fence')
    production_db = prod / 'worker.sqlite3'
    production_db.write_bytes(b'never open this production fixture as SQLite')
    (root / 'worker').mkdir()
    (root / 'heads').mkdir(mode=0o700)
    evidence = tmp_path / 'evidence'
    evidence.mkdir()
    with shared_owner(owner) as fd, sqlite3.connect(root / 'worker/jobs.sqlite3') as conn:
        inventory, worker, wrapper = setup_catalog(evidence, conn, fd, owner)
        authority.activate(conn, epoch='fixture', owner_fd=fd, owner_path=owner,
            bootstrap_receipt={'reconciled': True, 'source_sha256': 'a' * 64,
                               'control_catalogue_sha256': catalog.receipt(conn)['sha256']},
            restore_head_root=root / 'heads')
    config = {'publication_worker_database': str(production_db),
              'state_dir': str(prod / 'state'), 'attempt_state_dir': str(tmp_path / 'status'),
              'shared_lock_path': str(owner), 'maintenance_file': str(marker)}
    config_path = tmp_path / 'production-config.json'
    config_path.write_text(json.dumps(config))
    (root / 'production-config.original.json').write_bytes(config_path.read_bytes())
    (root / 'snapshot.json').write_text(json.dumps({
        'complete': True, 'snapshot_root': str(root), 'production_config': str(config_path),
        'production_config_sha256': hashlib.sha256(config_path.read_bytes()).hexdigest(),
        'fence_sha256': hashlib.sha256(marker.read_bytes()).hexdigest()}))
    review = root / 'review.json'
    review.write_text(json.dumps({'schema': 'legacy-wrapper-reference-review-1',
        'run_ids': [auxiliary_id(wrapper)], 'inventory_sha256': inventory}))
    return root, review, production_db, marker


def test_operator_retains_production_bytes_and_worker_debt_on_restart(operator, tmp_path):
    root, review, production_db, marker = prepare_copy(tmp_path)
    before = production_db.read_bytes(), marker.read_bytes()
    sha = operator.sha(review)
    result = operator.reconcile(root, review, sha, seconds=5, max_records=1)
    assert result['after']['reference_preserved'] == 1
    assert result['after']['worker_claim_uncertain'] == 1
    again = operator.reconcile(root, review, sha, seconds=5, max_records=1)
    assert again['before'] == again['after']
    assert (production_db.read_bytes(), marker.read_bytes()) == before


@pytest.mark.parametrize('change', ['symlink', 'hardlink', 'review_hash', 'marker', 'config'])
def test_operator_refuses_changed_boundary_before_reconciliation(operator, tmp_path, change):
    root, review, production_db, marker = prepare_copy(tmp_path)
    sha = operator.sha(review)
    if change in {'symlink', 'hardlink'}:
        database = root / 'worker/jobs.sqlite3'
        database.unlink()
        if change == 'symlink':
            database.symlink_to(production_db)
        else:
            database.hardlink_to(production_db)
    elif change == 'review_hash':
        sha = 'a' * 64
    elif change == 'marker':
        marker.write_bytes(b'another operator')
    else:
        (tmp_path / 'production-config.json').write_text('{}')
    before = production_db.read_bytes()
    with pytest.raises(ValueError):
        operator.reconcile(root, review, sha, seconds=5, max_records=1)
    assert production_db.read_bytes() == before


@pytest.mark.parametrize('seconds,count', [(float('nan'),1),(float('inf'),1),(0,1),(5,0),(5,4097)])
def test_operator_requires_finite_explicit_bounds(operator, tmp_path, seconds, count):
    with pytest.raises(ValueError, match='finite'):
        operator.reconcile(tmp_path, tmp_path / 'absent', '', seconds=seconds, max_records=count)
