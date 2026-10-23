"""Implementation provenance can advance acceptance, never publication proof."""
import hashlib
import json
import sqlite3
import time

import pytest

from jobagg import runtime_authority as authority, runtime_control_catalog as catalog
from jobagg.remediation_worker import shared_owner
from test_runtime_control_catalog import _accepted_bundle, _manifest


@pytest.mark.parametrize('publication,expected', [(None, 'accepted_pending'),
    ('terminal', 'publication_reconciliation_required'), ('unresolved', 'generation_pending')])
def test_historical_review_preserves_next_proof_gate_under_native_authority(tmp_path, monkeypatch, publication, expected):
    from jobagg import terminal_publication
    owner, root = tmp_path / 'owner.lock', tmp_path / 'historical'
    controls = _accepted_bundle(root, owner)
    if publication:
        p = root / 'publication_intent.json'
        p.write_text('{}')
        controls[p.name] = hashlib.sha256(p.read_bytes()).hexdigest()
        monkeypatch.setattr(terminal_publication, 'classify_run', lambda *a,**kw: {
            'disposition': publication, 'controls': [p], 'generation_ids': [],
            'control_sha256': {str(p): controls[p.name]}})
    with shared_owner(owner) as fd, sqlite3.connect(tmp_path / 'worker.sqlite3') as conn:
        inventory = catalog.prepare(conn, manifest=_manifest(root, owner, controls), owner_fd=fd, owner_path=owner)
        catalog.step(conn, owner_fd=fd, owner_path=owner, limit=1,
                     deadline_at=time.monotonic()+5, reviewed_implementations=set())
        receipt = catalog.receipt(conn)
        authority.activate(conn, epoch='epoch', owner_fd=fd, owner_path=owner,
            bootstrap_receipt={'reconciled': True, 'source_sha256': 'a'*64,
                               'control_catalogue_sha256': receipt['sha256']})
        conn.commit()
        with authority.controller(conn, run_id=None, owner_fd=fd):
            with conn:
                result = catalog.review_accepted_exit(conn, root.name,
                    reviewed_implementations={'f'*64}, review_sha256='c'*64,
                    expected_inventory_sha256=inventory, owner_fd=fd, owner_path=owner,
                    deadline_at=time.monotonic()+5)
        assert result['disposition'] == expected
        assert catalog.oldest_unresolved(conn)['disposition'] == expected
        assert conn.execute('SELECT count(*) FROM jobagg_accepted_exits').fetchone()[0] == 1
        proof = json.loads(conn.execute('SELECT proof_json FROM jobagg_legacy_controls').fetchone()[0])
        assert proof['acceptance_review']['original_hold'] == ['accepted_exit_implementation_review_required']
        assert proof['controls'] == {str(root / n): sha for n, sha in controls.items()}
        assert catalog.receipt(conn) == receipt


@pytest.mark.parametrize('failure', ['missing_intervals', 'unreviewed', 'changed_control', 'inventory', 'review'])
def test_implementation_review_cannot_remove_other_debt_or_changed_proofs(tmp_path, failure):
    owner, root = tmp_path / 'owner.lock', tmp_path / 'historical'
    controls = _accepted_bundle(root, owner, intervals=failure != 'missing_intervals')
    with shared_owner(owner) as fd, sqlite3.connect(':memory:') as conn:
        inventory = catalog.prepare(conn, manifest=_manifest(root, owner, controls), owner_fd=fd, owner_path=owner)
        catalog.step(conn, owner_fd=fd, owner_path=owner, limit=1,
                     deadline_at=time.monotonic()+5, reviewed_implementations=set())
        if failure == 'changed_control':
            (root / 'request.json').write_text('{}')
        before = conn.total_changes
        with pytest.raises(ValueError):
            catalog.review_accepted_exit(conn, root.name,
                reviewed_implementations=set() if failure == 'unreviewed' else {'f'*64},
                review_sha256='' if failure == 'review' else 'c'*64,
                expected_inventory_sha256='a'*64 if failure == 'inventory' else inventory,
                owner_fd=fd, owner_path=owner, deadline_at=time.monotonic()+5)
        assert conn.total_changes == before
        assert catalog.oldest_unresolved(conn)['disposition'] == 'repair_required'
