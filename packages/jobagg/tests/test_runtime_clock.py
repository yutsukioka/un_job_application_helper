"""No UTC correction or cross-boot raw clock can extend an admitted budget."""

import json
from pathlib import Path

import pytest

from jobagg import publication_deadline, runtime_clock


def test_sleep_and_backward_utc_cannot_extend_entry_or_nested_phase(monkeypatch):
    continuous = [100.0]
    monkeypatch.setattr(runtime_clock, "now", lambda: continuous[0])
    monkeypatch.setattr(publication_deadline.time, "monotonic", lambda: 10.0)
    monkeypatch.setattr(publication_deadline, "wall_now", lambda: 1000.0)
    parent = runtime_clock.budget(60)
    monkeypatch.setenv(runtime_clock.ENVIRONMENT, json.dumps(parent))
    with publication_deadline.entry_deadlines(70, 1060):
        assert not publication_deadline.expired(None)
        # Sleep consumed 120 seconds; a simultaneous UTC correction masked it.
        continuous[0] += 120
        assert publication_deadline.expired(None)
        with publication_deadline.entry_deadlines(1000, 2000):
            assert publication_deadline.expired(None)


@pytest.mark.parametrize("component", ["boot", "host", "provider"])
def test_cross_boot_and_host_budgets_are_repair_and_elapsed_regression_is_rejected(
    monkeypatch, component
):
    original = runtime_clock.budget(60)
    changed = {**original, "domain": {**original["domain"], component: "previous-domain"}}
    monkeypatch.setenv(runtime_clock.ENVIRONMENT, json.dumps(changed))
    assert publication_deadline.expired(None)
    with pytest.raises(ValueError, match="domain"):
        runtime_clock.budget(60, parent=changed)
    monkeypatch.setattr(runtime_clock, "now", lambda: original["started"] - 1)
    with pytest.raises(ValueError, match="regressed"):
        runtime_clock.remaining(original)


def test_bootstrap_and_selected_package_clock_implementations_are_identical():
    package = Path(runtime_clock.__file__)
    dispatcher = (
        package.parents[3]
        / "reports/jobagg-remediation-plan-2026-09-10/proposed_runner/runtime_clock.py"
    )
    assert package.read_bytes() == dispatcher.read_bytes()


def test_native_clock_runs_and_inherited_budget_never_resets():
    first = runtime_clock.budget(5)
    next_phase = runtime_clock.budget(500, parent=first)
    assert next_phase["deadline"] == first["deadline"]
    assert next_phase["started"] == first["started"]
    assert 0 < runtime_clock.remaining(next_phase) <= 5


def test_expired_boot_bound_entry_never_loads_configuration_or_spawns(monkeypatch, capsys):
    import importlib.util
    from test_budgeted_dispatcher import runner

    monkeypatch.setenv(runtime_clock.ENVIRONMENT, json.dumps(runtime_clock.budget(0)))
    monkeypatch.setattr(runner, "read_json", lambda *_: pytest.fail("expired config read"))
    assert runner.main(["--config", "unused.json", "--execute"]) == 3
    assert "process_failure" in capsys.readouterr().out
    path = Path(runner.__file__).with_name("supervisor.py")
    spec = importlib.util.spec_from_file_location("expired_supervisor", path)
    supervisor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(supervisor)
    monkeypatch.setattr(
        supervisor.subprocess, "Popen", lambda *a, **k: pytest.fail("expired child spawn")
    )
    assert supervisor.main(["--", "--config", "unused.json"]) == 3
    assert "entry_watchdog_expired" in capsys.readouterr().out


def test_phase_clock_counts_configuration_and_cannot_reset_after_masked_sleep(monkeypatch):
    from test_budgeted_dispatcher import runner

    clock = [100.0]
    monkeypatch.setattr(runtime_clock, "now", lambda: clock[0])
    monkeypatch.setattr(runner.RUNTIME_CLOCK, "now", lambda: clock[0])
    clock[0] = 120
    phase = runtime_clock.budget(30, started=100)
    assert runtime_clock.remaining(phase) == 10
    monkeypatch.setattr(runner.time, "monotonic", lambda: 10)
    monkeypatch.setattr(runner.time, "time", lambda: 1000)
    config = {
        "total_timeout_seconds": 840,
        "publication_timeout_seconds": 360,
        "_publication_clock": phase,
        "phase_budgets": {
            "projection_max_seconds": 1,
            "projection_min_seconds": 0.01,
            "publisher_min_seconds": 0.01,
            "cleanup_report_seconds": 0.01,
            "measurement_sha256": "a" * 64,
        },
    }
    assert runner.phase_remaining(config, 100, 1100, "projection") == 1
    clock[0] = 131
    with pytest.raises(ValueError, match="insufficient viable"):
        runner.phase_remaining(config, 100, 1100, "publisher")
    with publication_deadline.continuous_budget(phase):
        assert publication_deadline.expired(None)


def test_child_wait_cannot_extend_after_sleep_masked_by_utc(monkeypatch):
    import subprocess
    from test_budgeted_dispatcher import runner

    samples = iter([100.0, 120.0])
    monkeypatch.setattr(runner.RUNTIME_CLOCK, "now", lambda: next(samples))
    monkeypatch.setattr(runner.time, "monotonic", lambda: 10)
    monkeypatch.setattr(runner.time, "time", lambda: 1000)

    class Process:
        args = ["offline fixture"]

        def poll(self):
            return None

        def wait(self, **_):
            pytest.fail("expired continuous budget reached child wait")

    with pytest.raises(subprocess.TimeoutExpired):
        runner.wait_with_deadlines(Process(), 10)


def test_logged_pipe_wait_checks_continuous_deadline_before_io(tmp_path, monkeypatch):
    from contextlib import nullcontext
    import os
    import subprocess
    from jobagg import dispatcher_storage

    samples = iter([100.0, 120.0])
    monkeypatch.setattr(runtime_clock, "now", lambda: next(samples))
    monkeypatch.setattr(dispatcher_storage.time, "monotonic", lambda: 10)
    monkeypatch.setattr(dispatcher_storage.time, "time", lambda: 1000)
    monkeypatch.setattr(dispatcher_storage, "charged_write", lambda *_: nullcontext())

    class Process:
        args = ["offline pipe fixture"]

        def wait(self, **_):
            pytest.fail("expired logged child reached wait")

    process = Process()
    streams = []
    for _ in range(2):
        reader, writer = os.pipe()
        os.close(writer)
        streams.append(os.fdopen(reader, "rb"))
    process.stdout, process.stderr = streams
    with pytest.raises(subprocess.TimeoutExpired):
        dispatcher_storage.wait_logged(process, [tmp_path / "out", tmp_path / "err"], None, 10)


def test_unbounded_legacy_verifier_keeps_the_finite_ambient_clock(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(runtime_clock, "now", lambda: clock[0])
    with publication_deadline.entry_deadlines(float("inf"), float("inf")):
        assert not publication_deadline.expired(None)
    with publication_deadline.continuous_budget(runtime_clock.budget(10)):
        with publication_deadline.entry_deadlines(float("inf"), float("inf")):
            clock[0] = 111
            assert publication_deadline.expired(None)
