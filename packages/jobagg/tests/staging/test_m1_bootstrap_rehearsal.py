import importlib
import json
from pathlib import Path

import pytest

from test_m1_stage_snapshot import fixture_config, staging


@pytest.fixture
def helper(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]/'scripts'))
    return importlib.import_module('m1_bootstrap_rehearsal')


def test_rehearsal_refuses_to_mutate_without_os_production_write_denial(tmp_path, helper):
    config, fence, _ = fixture_config(tmp_path)
    root = tmp_path/'rehearsal'
    staging.snapshot(config, root, fence, max_bytes=10000, max_files=20, seconds=10)
    original = (root/'worker/jobs.sqlite3').read_bytes()
    with pytest.raises(ValueError, match='sandbox does not deny production writes'):
        helper.rehearse(root, tmp_path/'registry', 10, 1)
    assert (root/'worker/jobs.sqlite3').read_bytes() == original
    assert not (root/'rehearsal.json').exists()


def test_rehearsal_rejects_aliased_mutable_database_before_opening_it(tmp_path, helper):
    config, fence, production = fixture_config(tmp_path)
    root = tmp_path/'rehearsal'
    staging.snapshot(config, root, fence, max_bytes=10000, max_files=20, seconds=10)
    (root/'worker/jobs.sqlite3').unlink()
    (root/'worker/jobs.sqlite3').symlink_to(production/'worker/jobs.sqlite3')
    with pytest.raises(ValueError, match='mutable rehearsal path is aliased'):
        helper.rehearse(root, tmp_path/'registry', 10, 1)


def test_rehearsal_rejects_production_configuration_drift(tmp_path, helper):
    config, fence, _ = fixture_config(tmp_path)
    root = tmp_path/'rehearsal'
    staging.snapshot(config, root, fence, max_bytes=10000, max_files=20, seconds=10)
    config.write_text(json.dumps({'changed':True}))
    with pytest.raises(ValueError, match='production configuration changed'):
        helper.rehearse(root, tmp_path/'registry', 10, 1)
