"""Regression checks for stage-scoped dispatcher recovery; no live I/O."""
import hashlib
import importlib.util
import json
from pathlib import Path
import time

import pytest

SOURCE = Path(__file__).resolve().parents[1] / 'runner_observability.py'
spec = importlib.util.spec_from_file_location('scoped_probe_observability', SOURCE)
obs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(obs)


@pytest.fixture
def held(tmp_path):
    config = {'attempt_state_dir': tmp_path, '_config_sha256': 'c' * 64,
              '_stable_release_sha256': 'd' * 64, 'worker_cwd': tmp_path / 'worker',
              'publication_cwd': tmp_path / 'publisher', 'runtime_control': True}
    worker = {'stage': 'worker', 'unchanged_failures': 3, 'failure_class': 'process_exit',
              'reasons': ['worker exited 3'], 'class_episodes': {},
              'scope': obs.stable_failure_scope(config, 'worker'),
              'first_occurrence_at': '2026-10-28T19:45:02+00:00',
              'updated_at': '2026-10-30T15:15:03+00:00'}
    journal = {**worker, 'schema_version': 2, 'circuits': {'worker': worker,
               'end_to_end': {'stage': 'end_to_end', 'unchanged_failures': 1}},
               'history_refs': []}
    path = tmp_path / 'failure_condition.json'
    obs.write(path, journal)
    evidence = tmp_path / 'review.txt'
    evidence.write_text('Reviewed worker correction for one bounded natural invocation.\n')
    return config, path, evidence


def approve(held, stage='worker'):
    config, _, evidence = held
    obs.authorize_stage_probe(config, stage, evidence_path=evidence,
        evidence_sha256=hashlib.sha256(evidence.read_bytes()).hexdigest(),
        expires_at=time.time() + 60)


def test_publication_continuation_does_not_spend_worker_probe(held):
    config, path, _ = held
    assert obs.failure_hold(config, 'before', stage='projection') is None
    approve(held)
    approved = path.read_bytes()
    assert obs.failure_hold(config, 'projection', stage='projection') is None
    assert obs.failure_hold(config, 'publication', stage='publication') is None
    assert path.read_bytes() == approved
    assert obs.failure_hold(config, 'worker', stage='worker') is None
    reserved = json.loads(path.read_text())['circuits']['worker']
    assert reserved['unchanged_failures'] == 3
    assert reserved['reviewed_probe']['state'] == 'reserved'
    assert obs.failure_hold(config, 'next-worker', stage='worker')['unchanged_failures'] == 3
    with pytest.raises(ValueError, match='single probe'):
        approve(held)


@pytest.mark.parametrize('change', ['config', 'release', 'cwd', 'expired'])
def test_probe_rejects_changed_scope_or_expiration(held, change):
    config, path, _ = held
    approve(held)
    if change == 'config':
        config['_config_sha256'] = 'e' * 64
    elif change == 'release':
        config['_stable_release_sha256'] = 'e' * 64
    elif change == 'cwd':
        config['worker_cwd'] = config['worker_cwd'] / 'changed'
    else:
        value = json.loads(path.read_text())
        value['circuits']['worker']['reviewed_probe']['expires_at'] = time.time() - 1
        obs.write(path, value)
    assert obs.failure_hold(config, change, stage='worker')['stage'] == 'worker'
    assert json.loads(path.read_text())['circuits']['worker']['reviewed_probe']['state'] == 'available'


def test_failed_probe_preserves_failure_hold(held):
    config, path, _ = held
    approve(held)
    assert obs.failure_hold(config, 'worker', stage='worker') is None
    obs.record_failure(config, 'failed', {'status': 'process_failure', 'failure_stage': 'worker',
                       'worker_exit_code': 3, 'reasons': ['new failed worker']})
    state = json.loads(path.read_text())
    assert state['circuits']['worker']['unchanged_failures'] == 4
    assert state['circuits']['worker']['first_occurrence_at'] == '2026-10-28T19:45:02+00:00'
    assert state['circuits']['worker']['reviewed_probe']['state'] == 'reserved'
    assert obs.failure_hold(config, 'next', stage='worker')['unchanged_failures'] == 4


def test_old_publication_success_keeps_worker_hold_and_probe(held):
    config, path, _ = held
    approve(held)
    before = json.loads(path.read_text())['circuits']['worker']
    obs.record_failure(config, 'old-publication', {'status': 'incomplete',
        'worker_exit_code': 0, 'worker_status': 'complete', 'accepted_work_resumed': True,
        'publication_status': 'published', 'publication_exit_code': 0, 'readback_verified': True})
    assert json.loads(path.read_text())['circuits']['worker'] == before
    assert obs.failure_hold(config, 'next-worker', stage='worker') is None


def test_new_worker_success_archives_only_matching_episode(held):
    config, path, _ = held
    approve(held)
    assert obs.failure_hold(config, 'worker', stage='worker') is None
    obs.record_failure(config, 'success', {'status': 'complete', 'worker_exit_code': 0,
                       'worker_status': 'complete', 'run_id': 'successful-new-worker'})
    value = json.loads(path.read_text())
    assert 'worker' not in value['circuits']
    assert value['circuits']['end_to_end']['unchanged_failures'] == 1
    history = json.loads(Path(value['history_refs'][-1]['path']).read_text())
    assert history['episode']['unchanged_failures'] == 3
    assert history['episode']['reviewed_probe']['state'] == 'reserved'
    assert history['resolution'] == 'validated_stage_success'


@pytest.mark.parametrize('stage', ['publication', 'end_to_end'])
def test_independent_hold_cannot_use_worker_probe(held, stage):
    config, path, _ = held
    value = json.loads(path.read_text())
    value['circuits'][stage] = {'stage': stage, 'unchanged_failures': 3,
                                'reasons': ['independent retained hold']}
    obs.write(path, value)
    approve(held)
    before = path.read_bytes()
    requested = 'projection' if stage == 'end_to_end' else 'publication'
    assert obs.failure_hold(config, 'blocked', stage=requested)['stage'] == stage
    assert path.read_bytes() == before


def test_legacy_global_hold_is_preserved(held):
    config, path, _ = held
    value = json.loads(path.read_text())
    del value['circuits']
    value['schema_version'] = 1
    obs.write(path, value)
    before = path.read_bytes()
    assert obs.failure_hold(config, 'legacy', stage='projection')['unchanged_failures'] == 3
    assert path.read_bytes() == before
