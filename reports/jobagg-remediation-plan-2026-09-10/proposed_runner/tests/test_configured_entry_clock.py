"""Configured cycle budgets survive bootstrap without extending inherited clocks."""
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.mark.parametrize("configured,parent,expected", [
    (1500, None, 1500),
    (1800, None, 1800),
    (None, None, 840),
    (600, None, 600),
    (1500, 900, 900),
])
def test_configured_entry_clock_and_deadline(monkeypatch, tmp_path, configured, parent, expected):
    path = Path(__file__).resolve().parents[1] / "runner.py"
    spec = importlib.util.spec_from_file_location("configured_entry_runner", path)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    elapsed = [0.]
    monkeypatch.setattr(runner.RUNTIME_CLOCK, "now", lambda: 1000. + elapsed[0])
    monkeypatch.setattr(runner.time, "monotonic", lambda: 1000. + elapsed[0])
    monkeypatch.setattr(runner.time, "time", lambda: 100000. + elapsed[0])
    monkeypatch.delenv(runner.RUNTIME_CLOCK.ENVIRONMENT, raising=False)
    monkeypatch.delenv("JOBAGG_ENTRY_MONOTONIC_DEADLINE", raising=False)
    monkeypatch.delenv("JOBAGG_ENTRY_WALL_DEADLINE", raising=False)
    if parent is not None:
        monkeypatch.setenv(runner.RUNTIME_CLOCK.ENVIRONMENT,
                           json.dumps(runner.RUNTIME_CLOCK.budget(parent)))
    original = runner.os.environ.get(runner.RUNTIME_CLOCK.ENVIRONMENT)
    config = tmp_path / "dispatcher.json"
    config.write_text(json.dumps({} if configured is None else {"total_timeout_seconds": configured}))

    class ConfigurationChecked(BaseException):
        pass

    def inspect_config(*args, **kwargs):
        assert runner.RUNTIME_CLOCK.remaining(runner._ENTRY_CLOCK) == pytest.approx(expected)
        elapsed[0] = expected - 1
        runner.check_entry_budget()
        elapsed[0] = expected
        with pytest.raises(TimeoutError):
            runner.check_entry_budget()
        raise ConfigurationChecked

    monkeypatch.setattr(runner, "load_config", inspect_config)
    with pytest.raises(ConfigurationChecked):
        runner.main(["--config", str(config)])
    assert runner._ENTRY_CLOCK is None
    assert runner.os.environ.get(runner.RUNTIME_CLOCK.ENVIRONMENT) == original
