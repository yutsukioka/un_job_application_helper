import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/m1_stage_snapshot.py'
spec = importlib.util.spec_from_file_location('m1_stage_snapshot', SCRIPT)
staging = importlib.util.module_from_spec(spec)
spec.loader.exec_module(staging)


def test_destination_cannot_alias_or_overlap_production(tmp_path):
    source = tmp_path / 'production'
    source.mkdir()
    alias = tmp_path / 'alias'
    alias.symlink_to(source, target_is_directory=True)
    for destination in [source, source/'nested', tmp_path, alias/'nested']:
        with pytest.raises(ValueError):
            staging.disjoint(destination, [source])
    staging.disjoint(tmp_path/'rehearsal', [source])


def test_existing_owner_and_exact_operator_fence_are_required(tmp_path):
    owner, marker = tmp_path/'owner', tmp_path/'maintenance'
    owner.write_bytes(b'')
    marker.write_text(json.dumps({'operation':'test'}))
    config = {'shared_lock_path':str(owner), 'maintenance_file':str(marker)}
    digest = hashlib.sha256(marker.read_bytes()).hexdigest()
    inode = owner.stat().st_ino
    with staging.fenced_owner(config, digest) as fd:
        assert os.fstat(fd).st_ino == inode
    assert owner.stat().st_ino == inode
    with pytest.raises(ValueError, match='fence differs'):
        with staging.fenced_owner(config, '0'*64):
            pytest.fail('wrong fence admitted')
    assert marker.exists()


def test_symlink_evidence_is_not_copied(tmp_path):
    source = tmp_path/'source'
    source.mkdir()
    (source/'alias').symlink_to(tmp_path/'elsewhere')
    with pytest.raises(ValueError, match='nonregular'):
        list(staging.files_in(source))


def fixture_config(tmp_path):
    root = tmp_path / 'production'
    for directory in ['worker', 'output', 'remediation/owner.worker-policy/attempts', 'dispatcher', 'status']:
        (root / directory).mkdir(parents=True)
    owner = root / 'remediation/owner'
    owner.write_bytes(b'')
    marker = root / 'remediation/maintenance'
    marker.write_bytes(b'operator-owned')
    (root / 'worker/jobs.sqlite3').write_bytes(b'opaque database bytes')
    (root / 'output/all_jobs.sqlite3').write_bytes(b'opaque published bytes')
    (root / 'output/.jobagg-publication-state.json').write_text('{}')
    (root / 'status/health.json').write_text('{}')
    Path(str(owner)+'.worker-policy.json').write_text('{}')
    config = tmp_path / 'config.json'
    config.write_text(json.dumps(dict(publication_worker_database=str(root/'worker/jobs.sqlite3'),
        publication_argv=['--output-dir', str(root/'output')], maintenance_file=str(marker),
        shared_lock_path=str(owner), state_dir=str(root/'dispatcher'), attempt_state_dir=str(root/'status'))))
    return config, hashlib.sha256(marker.read_bytes()).hexdigest(), root


def test_copy_is_independent_and_existing_destination_is_never_overwritten(tmp_path):
    config, fence, root = fixture_config(tmp_path)
    destination = tmp_path / 'rehearsal'
    result = staging.snapshot(config, destination, fence, max_bytes=10000, max_files=20, seconds=10)
    assert result['complete']
    assert not os.path.samefile(root/'worker/jobs.sqlite3', destination/'worker/jobs.sqlite3')
    (destination/'worker/jobs.sqlite3').write_bytes(b'changed clone')
    assert (root/'worker/jobs.sqlite3').read_bytes() == b'opaque database bytes'
    with pytest.raises(FileExistsError):
        staging.snapshot(config, destination, fence, max_bytes=10000, max_files=20, seconds=10)
    assert (destination/'worker/jobs.sqlite3').read_bytes() == b'changed clone'


def test_scratch_limit_fails_before_creating_snapshot(tmp_path):
    config, fence, root = fixture_config(tmp_path)
    destination = tmp_path / 'rehearsal'
    with pytest.raises(ValueError, match='scratch budget'):
        staging.snapshot(config, destination, fence, max_bytes=1, max_files=20, seconds=10)
    assert not destination.exists()
    assert (root/'remediation/maintenance').read_bytes() == b'operator-owned'


@pytest.mark.parametrize('seconds', [float('inf'), float('nan'), -1, 0])
def test_snapshot_rejects_nonfinite_or_expired_ceiling(tmp_path, seconds):
    config, fence, _ = fixture_config(tmp_path)
    with pytest.raises(ValueError, match='finite positive'):
        staging.snapshot(config, tmp_path/'rehearsal', fence, max_bytes=10000, max_files=20, seconds=seconds)


def test_fence_mutation_invalidates_durable_snapshot_receipt(tmp_path, monkeypatch):
    config, fence, root = fixture_config(tmp_path)
    original = staging.sha
    def mutate_after_copy(path, check=lambda:None):
        digest = original(path, check)
        if path == tmp_path/'rehearsal/worker/jobs.sqlite3':
            (root/'remediation/maintenance').write_bytes(b'replaced fence')
        return digest
    monkeypatch.setattr(staging, 'sha', mutate_after_copy)
    destination = tmp_path/'rehearsal'
    with pytest.raises(ValueError, match='fence/owner changed'):
        staging.snapshot(config, destination, fence, max_bytes=10000, max_files=20, seconds=10)
    assert json.loads((destination/'snapshot.json').read_text())['complete'] is False
