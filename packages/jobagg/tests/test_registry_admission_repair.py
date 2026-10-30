"""Path-only repair must not excuse content changes or accepted worker effects."""
import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('registry_repair', Path(__file__).parents[1] / 'scripts/repair_registry_admission.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def test_registry_path_only(tmp_path):
    original, pinned = tmp_path / 'original', tmp_path / 'pinned'
    original.write_bytes(b'same registry')
    pinned.write_bytes(original.read_bytes())
    manifest = {'registry': {'path': str(original), 'sha256': m.sha(original.read_bytes())}, 'sources': [{'source_id': 'one'}]}
    corrected = m.corrected_manifest(manifest, pinned)
    assert corrected['registry']['path'] == str(pinned)
    assert corrected['sources'] == manifest['sources']
    assert manifest['registry']['path'] == str(original)
    pinned.write_bytes(b'changed registry')
    with pytest.raises(ValueError, match='contents differ'):
        m.corrected_manifest(manifest, pinned)


@pytest.mark.parametrize('change,accepted', [({'revision': 11}, False), ({'phase': 'publisher'}, False), ({'run_id': 'other'}, False), ({}, True)])
def test_rejects_changed_or_accepted_admission(change, accepted):
    state = dict(phase='worker', run_id='failed', revision=10, start_revision=10)
    outcome = dict(run_id='failed', worker_exit_code=3, status='process_failure', publication_status='not_started')
    m.check_failed_admission(state, outcome, False)
    with pytest.raises(ValueError):
        m.check_failed_admission({**state, **change}, outcome, accepted)
