import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from test_runner_reliability import phase_config, RUNNER

pytest_plugins = ["test_runner_reliability"]
HERE = Path(__file__).parent
REPO = HERE.parents[3]
PACKAGE = REPO / "packages/jobagg"


def configure(fixture, *, publication="happy", metrics="healthy"):
    phase_config(fixture, publication=publication)
    config = json.loads(fixture.config_path.read_text())
    argv = config["worker_argv"]
    argv[1] = str(HERE / "parallel_worker.py")
    pos = argv.index("--mode")
    del argv[pos:pos + 2]
    argv += ["--parallel-sources", "{parallel_sources}", "--metrics", metrics]
    config["concurrency"] = {"initial_limit": 4, "maximum_limit": 8}
    helper_dir = fixture.root / "jobagg"
    helper_dir.mkdir(exist_ok=True)
    shutil.copy2(PACKAGE / "jobagg/remediation_concurrency.py", helper_dir)
    fixture.config_path.write_text(json.dumps(config))


def state(fixture):
    return json.loads((fixture.root / "state/concurrency.json").read_text())


def test_three_verified_incomplete_scope_batches_raise_four_to_five(fixture):
    configure(fixture)
    for count in range(3):
        code, result = fixture.invoke()
        assert code == 2, result
        assert result["publication_status"] == "published"
        assert result["concurrency"]["limit_used"] == 4
        assert result["concurrency"]["qualifying"] is True
        assert state(fixture)["inflight"] is None
        assert state(fixture)["controller_state"]["limit"] == (5 if count == 2 else 4)
    _, fourth = fixture.invoke()
    assert fourth["concurrency"]["limit_used"] == 5
    request = json.loads(Path(fourth["request_path"]).read_text())
    assert request["parallel_sources"] == 5


@pytest.mark.parametrize("metrics", ["missing", "access_block"])
def test_missing_metrics_or_new_access_block_never_promotes(fixture, metrics):
    configure(fixture, metrics=metrics)
    _, result = fixture.invoke()
    assert result["concurrency"]["qualifying"] is False
    assert state(fixture)["controller_state"]["limit"] == (3 if metrics == "access_block" else 4)
    assert state(fixture)["controller_state"]["healthy_streak"] == 0


def test_publication_failure_clears_ramp_credit(fixture):
    configure(fixture)
    fixture.invoke()
    assert state(fixture)["controller_state"]["healthy_streak"] == 1
    configure(fixture, publication="wrong_binding")
    code, result = fixture.invoke()
    assert code == 3, result
    assert state(fixture)["controller_state"]["healthy_streak"] == 0
    assert state(fixture)["controller_state"]["limit"] == 4


def test_crash_marker_clears_credit_before_next_dispatch(fixture):
    configure(fixture)
    fixture.invoke()
    fixture.invoke()
    envelope = state(fixture)
    assert envelope["controller_state"]["healthy_streak"] == 2
    envelope["inflight"] = {"run_id": "unfinalized", "limit": 4}
    (fixture.root / "state/concurrency.json").write_text(json.dumps(envelope))
    _, result = fixture.invoke()
    assert result["concurrency"]["previous_unfinished_run"] == "unfinalized"
    assert state(fixture)["controller_state"]["limit"] == 4
    assert state(fixture)["controller_state"]["healthy_streak"] == 1


def test_corrupt_persisted_limit_refuses_dispatch(fixture):
    configure(fixture)
    fixture.invoke()
    envelope = state(fixture)
    envelope["controller_state"]["limit"] = 49
    path = fixture.root / "state/concurrency.json"
    path.write_text(json.dumps(envelope))
    starts = (fixture.root / "worker_starts.txt").read_bytes()
    code, result = fixture.invoke()
    assert code == 3, result
    assert (fixture.root / "worker_starts.txt").read_bytes() == starts
    assert json.loads(path.read_text()) == envelope


def test_explicit_fixed_policy_preserves_history_and_clears_growth_credit(fixture):
    configure(fixture)
    fixture.invoke()
    before = state(fixture)["controller_state"]
    config = json.loads(fixture.config_path.read_text())
    config["concurrency"].update(mode="fixed", fixed_limit=5)
    fixture.config_path.write_text(json.dumps(config))
    _, result = fixture.invoke()
    assert result["concurrency"]["limit_used"] == 5
    assert result["concurrency"]["policy_reconfigured"] is True
    after = state(fixture)["controller_state"]
    assert after["healthy_streak"] == 0 and after["limit"] == 5
    assert after["processed_batches"] == before["processed_batches"] + 1
    assert after["recent_batch_ids"][:1] == before["recent_batch_ids"]


def test_no_ramp_credit_until_terminal_outcome_is_durable(fixture):
    configure(fixture)
    fixture.invoke()
    fixture.invoke()
    before = state(fixture)["controller_state"]
    assert before["healthy_streak"] == 2
    hook = fixture.root / "fail_outcome.py"
    hook.write_text(f'''import importlib.util,sys
spec=importlib.util.spec_from_file_location("runner",{str(RUNNER)!r})
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
original=m.immutable_json
def write(path,value):
    if path.name=="outcome.json":
        raise OSError("synthetic terminal journal failure")
    return original(path,value)
m.immutable_json=write
sys.exit(m.main(["--config",{str(fixture.config_path)!r},"--execute"]))
''')
    result = subprocess.run([sys.executable, str(hook)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 3
    envelope = state(fixture)
    assert envelope["controller_state"] == before
    assert envelope["inflight"] is not None


def test_adaptive_dry_run_does_not_initialize_or_change_state(fixture):
    configure(fixture)
    before = {str(path) for path in fixture.root.rglob("*")}
    code, result = fixture.invoke(execute=False)
    assert code == 0 and result["concurrency_policy"]["initial_limit"] == 4
    assert {str(path) for path in fixture.root.rglob("*")} == before


def test_removed_controller_state_cannot_silently_reset_previous_limit(fixture):
    configure(fixture, metrics="access_block")
    fixture.invoke()
    assert state(fixture)["controller_state"]["limit"] == 3
    path = fixture.root / "state/concurrency.json"
    path.unlink()
    starts = (fixture.root / "worker_starts.txt").read_bytes()
    code, result = fixture.invoke()
    assert code == 3, result
    assert any("history is missing" in reason for reason in result["reasons"])
    assert not path.exists()
    assert (fixture.root / "worker_starts.txt").read_bytes() == starts
