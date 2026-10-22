"""Native publication clocks and immutable export proof, without storage budget."""
import sqlite3
from pathlib import Path

import pytest

from jobagg import publication_fence as fence, runtime_authority as runtime
from jobagg.db import JobDatabase
from test_live_publication import setup as live_setup, add_detail, run


@pytest.fixture
def prepared(tmp_path):
    fixture = live_setup.__wrapped__(tmp_path)
    owner_path = tmp_path/'owner.lock'
    owner = owner_path.open('a+')
    targets = [fixture['output']/'test_jobs.sqlite3', fixture['output']/'all_jobs.sqlite3']
    for path in targets:
        with JobDatabase(path).connect() as conn:
            fence.prepare_target_schema(conn)
            fence.activate(conn, owner_path=owner_path, owner_fd=owner.fileno(), epoch='fixture')
    yield fixture, owner_path, owner, targets
    owner.close()


def test_fenced_publisher_seals_complete_scope_and_detects_destination_or_export_change(prepared):
    fixture, owner_path, owner, targets = prepared
    add_detail(fixture)
    with fence.writer_scope(owner.fileno(), owner_path, fixture['output']):
        published = run(fixture, execute=True)
    assert published['status'] == 'published', published
    boundary = published['publication_boundary']
    scope = fence.publication_scope(fixture['registry'], fixture['output'])
    assert fence.verify_boundary(boundary, expected_scope=scope) is True
    # Unadmitted mutation cannot silently change a destination's contents.
    with JobDatabase(targets[0]).connect() as conn:
        with pytest.raises(sqlite3.IntegrityError, match='not_admitted'):
            conn.execute("UPDATE jobs SET title='unreviewed'")
    with fence.writer_scope(owner.fileno(), owner_path, fixture['output']):
        with JobDatabase(targets[0]).connect() as conn:
            conn.execute("UPDATE jobs SET title='reviewed mutation'")
    assert fence.verify_boundary(boundary, expected_scope=scope) is False
    # Any chmod/write/replace of an export invalidates its sealed identity.
    export = Path(boundary['exports'][0]['path'])
    export.chmod(0o600)
    export.write_bytes(b'changed export')
    assert fence.verify_boundary(boundary, expected_scope=scope) is False


def test_native_noop_requires_all_destinations_and_exports_not_just_worker_revision(prepared):
    fixture, owner_path, owner, _ = prepared
    add_detail(fixture)
    with fence.writer_scope(owner.fileno(), owner_path, fixture['output']):
        published = run(fixture, execute=True)
    worker = fixture['worker'].path
    scope = fence.publication_scope(fixture['registry'], fixture['output'])
    with sqlite3.connect(worker) as conn:
        runtime.activate(conn, epoch='native-worker', owner_path=owner_path, owner_fd=owner.fileno(),
            bootstrap_receipt={'reconciled': True, 'source_sha256': 'a'*64,
                               'publication_boundary': published['publication_boundary']})
        runtime.begin(conn, 'quiet', 'configuration', publication_scope=scope)
        assert runtime.proven_empty(conn, 'quiet', accepted_progress=0) is True
        export = Path(published['publication_boundary']['exports'][0]['path'])
        export.chmod(0o600)
        export.write_bytes(b'new bytes with no worker progress')
        assert runtime.proven_empty(conn, 'quiet', accepted_progress=0) is False


def test_unprepared_destination_cannot_issue_a_quiescence_certificate(tmp_path):
    fixture = live_setup.__wrapped__(tmp_path)
    add_detail(fixture)
    result = run(fixture, execute=True)
    assert result['status'] == 'published'
    assert result['publication_boundary'] is None


def test_publisher_cli_grants_native_owner_scope_without_storage_budget(prepared, monkeypatch, capsys):
    from jobagg import publish_worker
    fixture, owner_path, owner, _ = prepared
    add_detail(fixture)
    monkeypatch.setenv('JOBAGG_SHARED_LOCK_FD', str(owner.fileno()))
    code = publish_worker.main(['--worker-database', str(fixture['worker'].path),
        '--registry', str(fixture['registry']), '--output-dir', str(fixture['output']),
        '--state-dir', str(fixture['state']), '--shared-lock', str(owner_path),
        '--max-seconds','10','--execute'])
    assert code == 0
    import json
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'published'
    assert fence.verify_boundary(result['publication_boundary'],
                                 expected_scope=fence.publication_scope(fixture['registry'], fixture['output']))
