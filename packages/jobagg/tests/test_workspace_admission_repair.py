"""A reviewed generation change must preserve the other workspace bindings."""
import importlib.util
import sys
from pathlib import Path
import pytest

scripts = Path(__file__).parents[1] / 'scripts'
sys.path.insert(0, str(scripts))
spec = importlib.util.spec_from_file_location('workspace_repair', scripts / 'repair_workspace_admission.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def test_reviewed_generation_binding_and_manifest(tmp_path):
    old, new = tmp_path / 'old', tmp_path / 'new'
    old.mkdir(); new.mkdir()
    (old / 'worker.py').write_text('old')
    (new / 'worker.py').write_text('new')
    old_manifest, new_manifest = m.manifest(old), m.manifest(new)
    previous = {'implementation_sha256': old_manifest['implementation_sha256'], 'registry_sha256': 'registry', 'robots_sha256': 'robots', 'shared_lock': '/owner', 'version': 'v1'}
    current = {**previous, 'implementation_sha256': new_manifest['implementation_sha256']}
    assert m.reviewed_binding(previous, current, old_manifest, new_manifest) == current
    for k in ('registry_sha256', 'robots_sha256', 'shared_lock', 'version'):
        with pytest.raises(ValueError, match='Non-code'):
            m.reviewed_binding(previous, {**current, k: 'changed'}, old_manifest, new_manifest)
    with pytest.raises(ValueError, match='Old implementation proof'):
        m.reviewed_binding(previous, current, new_manifest, new_manifest)
    with pytest.raises(ValueError, match='New implementation proof'):
        m.reviewed_binding(previous, current, old_manifest, old_manifest)
