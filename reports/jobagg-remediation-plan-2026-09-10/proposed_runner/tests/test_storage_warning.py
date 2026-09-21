import importlib.util
import json
from pathlib import Path
import time

spec = importlib.util.spec_from_file_location(
    "storage_observability", Path(__file__).parents[1] / "runner_observability.py"
)
obs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(obs)


def test_storage_warning_transitions_clear_and_runway(tmp_path):
    config = {
        "attempt_state_dir": tmp_path,
        "storage_guard": {
            "warning_free_bytes": 3000,
            "publication_headroom_bytes": 500,
            "warning_runway_seconds": 3600,
        },
    }
    assert obs.record_storage_health(config, 10000, 1000)["status"] == "ok"
    low = obs.record_storage_health(config, 2500, 1000)
    assert low["status"] == "warning" and len(low["transitions"]) == 2
    assert len(obs.record_storage_health(config, 2400, 1000)["transitions"]) == 2
    assert obs.record_storage_health(config, 1400, 1000)["status"] == "critical"
    assert obs.record_storage_health(config, 10000, 1000)["status"] == "ok"
    path = tmp_path / "storage-health.json"
    state = json.loads(path.read_text())
    state["samples"] = [{"at": time.time() - 3600, "available_bytes": 100000}]
    path.write_text(json.dumps(state))
    value = obs.record_storage_health(config, 10000, 1000)
    assert value["status"] == "warning" and 0 < value["estimated_runway_seconds"] < 3600
    assert "includes other writers" in value["estimate_basis"]


def test_preview_computes_warning_without_creating_any_receipts(tmp_path):
    directory = tmp_path / 'not-created'
    config = {'attempt_state_dir': directory, 'storage_guard': {}}
    assert obs.record_storage_health(config, 1000, 900, persist=False)['status'] == 'critical'
    assert not directory.exists()
    obs.record_storage_health(config, 1000, 900)
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    obs.record_storage_health(config, 100000000000, 900, persist=False)
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == before
