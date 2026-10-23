"""A reboot changes physical locators, never the retained expected file hashes."""
import hashlib
import importlib.util
import json
from pathlib import Path
import time

import pytest


@pytest.fixture
def renewal(monkeypatch):
    scripts = Path(__file__).resolve().parents[1] / 'scripts'
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location('tested_backup_renewal', scripts / 'm1_rebind_backup_mount.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def mapping(tmp_path, renewal):
    path = tmp_path / 'retained'
    path.write_bytes(b'original certified evidence')
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    original = str(tmp_path / 'old-location')
    identity = renewal._identity(path)
    identity[0] += 1
    value = {'location_root': str(tmp_path), 'files': {original: {
        'path': str(path), 'sha256': sha, 'identity': identity}}}
    return path, original, value


def test_only_device_is_renewed_after_verifying_retained_original_hash(renewal, tmp_path):
    path, original, value = mapping(tmp_path, renewal)
    before = json.dumps(value, sort_keys=True)
    renewed, report = renewal.verify_mapping(value, {original: value['files'][original]['sha256']},
        deadline_at=time.monotonic() + 5, maximum_bytes=1024)
    assert json.dumps(value, sort_keys=True) == before
    assert renewed['files'][original]['identity'] == renewal._identity(path)
    assert renewed['files'][original]['sha256'] == value['files'][original]['sha256']
    assert report['files'] == 1 and report['bytes'] == path.stat().st_size
    assert path.read_bytes() == b'original certified evidence'


@pytest.mark.parametrize('change', ['hash', 'inode', 'size', 'time', 'bytes', 'alias', 'limit', 'deadline'])
def test_changed_or_unverified_backup_cannot_be_recertified(renewal, tmp_path, change):
    path, original, value = mapping(tmp_path, renewal)
    expected = {original: value['files'][original]['sha256']}
    bound, deadline = 1024, time.monotonic() + 5
    if change == 'hash':
        expected[original] = 'a' * 64
    elif change in {'inode', 'size', 'time'}:
        value['files'][original]['identity'][{'inode':1, 'size':2, 'time':3}[change]] += 1
    elif change == 'bytes':
        path.write_bytes(b'wrong bytes')
        value['files'][original]['identity'][1:] = renewal._identity(path)[1:]
    elif change == 'alias':
        alias = tmp_path / 'alias'
        alias.symlink_to(path)
        value['files'][original]['path'] = str(alias)
    elif change == 'limit':
        bound = 1
    elif change == 'deadline':
        deadline = time.monotonic() - 1
    before = json.dumps(value, sort_keys=True)
    with pytest.raises((ValueError, TimeoutError)):
        renewal.verify_mapping(value, expected, deadline_at=deadline, maximum_bytes=bound)
    assert json.dumps(value, sort_keys=True) == before


@pytest.mark.parametrize('window', ['after_pending_head', 'after_mapping'])
def test_interrupted_exact_head_map_handoff_resumes_without_losing_original_receipt(renewal, tmp_path, window):
    from types import SimpleNamespace
    from jobagg.remediation_worker import shared_owner
    path, original, value = mapping(tmp_path, renewal)
    renewed, _ = renewal.verify_mapping(value, {original: value['files'][original]['sha256']},
        deadline_at=time.monotonic() + 5, maximum_bytes=1024)
    map_path, head_path = tmp_path / 'map.json', tmp_path / 'head.json'
    map_path.write_text(json.dumps(value))
    map_path.chmod(0o400)
    old = {'pending': False, 'revision': 17, 'epoch': 'retained',
           'immutable_controls': {str(map_path): renewal._file_identity(map_path)}}
    head_path.write_text(json.dumps(old))
    owner = tmp_path / 'owner.lock'
    policy = SimpleNamespace(head=head_path, policy=SimpleNamespace(owner=owner))
    plan = {'head': old, 'mapping': value, 'sha256': 'a' * 64}
    verified = {'mapping': renewed, 'sha256': 'b' * 64}
    def fail(where):
        if where == window:
            raise RuntimeError('power interruption')
    with shared_owner(owner) as fd:
        with pytest.raises(RuntimeError, match='power interruption'):
            renewal.finish_handoff(policy, map_path, plan, verified, owner_fd=fd, fault=fail)
        assert json.loads(head_path.read_text())['pending'] is True
        final = renewal.finish_handoff(policy, map_path, plan, verified, owner_fd=fd)
        assert renewal.finish_handoff(policy, map_path, plan, verified, owner_fd=fd) == final
    assert final['revision'] == 17 and final['epoch'] == old['epoch']
    assert json.loads(map_path.read_text()) == renewed
    assert plan['head'] == old and plan['mapping'] == value
    assert path.exists()
    head_path.write_text(json.dumps({**final, 'revision': 0}))
    with shared_owner(owner) as fd, pytest.raises(ValueError, match='outside handoff'):
        renewal.finish_handoff(policy, map_path, plan, verified, owner_fd=fd)
