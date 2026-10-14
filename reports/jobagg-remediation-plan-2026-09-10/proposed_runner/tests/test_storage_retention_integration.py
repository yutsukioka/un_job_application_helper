"""The dispatcher keeps retention opt-in and separate from fetch health."""

import fcntl
import importlib.util
import json
import os
from types import SimpleNamespace

import pytest

from test_runner_reliability import RUNNER


spec = importlib.util.spec_from_file_location("retention_runner", RUNNER)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


@pytest.fixture
def fixture():
    from test_runner import RunnerTests

    value = RunnerTests()
    value.setUp()
    try:
        yield value
    finally:
        value.tearDown()


def test_storage_retention_config_is_disabled_by_default_and_bounded(fixture):
    config, _ = runner.load_config(fixture.config_path)
    assert config["storage_retention"] == {
        "enabled": False, "max_seconds": 20, "max_groups": 2, "keep_completed": 2,
    }
    fixture.configure(storage_retention={
        "enabled": True, "max_seconds": 10, "max_groups": 1, "keep_completed": 3,
    })
    config, _ = runner.load_config(fixture.config_path)
    assert config["storage_retention"]["enabled"] is True


@pytest.mark.parametrize("policy", [
    {"enabled": 1}, {"max_seconds": 0}, {"max_seconds": 121},
    {"max_seconds": True}, {"max_groups": 0}, {"max_groups": 9},
    {"max_groups": True}, {"keep_completed": 1}, {"keep_completed": True},
    {"unknown": 1},
])
def test_storage_retention_config_rejects_unsafe_values(fixture, policy):
    fixture.configure(storage_retention=policy)
    with pytest.raises(ValueError, match="storage_retention"):
        runner.load_config(fixture.config_path)


def _mock_tick(monkeypatch, tmp_path, *, enabled=True, prune=None, unresolved=False,
               pause_during_work=False):
    config = {
        "state_dir": tmp_path / "state",
        "shared_lock_path": tmp_path / "owner.lock",
        "maintenance_file": tmp_path / "maintenance",
        "publication_argv": [],
        "storage_retention": {
            "enabled": enabled, "max_seconds": 5, "max_groups": 2,
            "keep_completed": 2,
        },
    }
    health = []
    monkeypatch.setattr(runner.OBSERVABILITY, "storage_check", lambda _, **_kw: {"ready": True})
    monkeypatch.setattr(runner.OBSERVABILITY, "condition_fingerprint", lambda *_: "condition")
    monkeypatch.setattr(runner.OBSERVABILITY, "failure_hold", lambda *_: None)
    monkeypatch.setattr(runner.OBSERVABILITY, "record_failure", lambda _c, _fingerprint, result: health.append(dict(result)))
    monkeypatch.setattr(runner, "recover_stale_runs", lambda *_args, **_kw: [])

    def work(*_args):
        if pause_during_work:
            config["maintenance_file"].write_text("pause")
        return {"status": "complete", "publication_status": "published"}

    monkeypatch.setattr(runner, "locked_tick", work)
    monkeypatch.setattr(runner, "unresolved_publications", lambda *_: ["pending"] if unresolved else [])
    if prune is not None:
        monkeypatch.setattr(runner, "retention_helper", lambda _config: SimpleNamespace(prune_completed=prune))
    return config, health


def test_retention_runs_under_owner_after_terminal_health_record(monkeypatch, tmp_path):
    called = []

    def prune(root, **kwargs):
        assert health == [{"status": "complete", "publication_status": "published"}]
        assert root == config["state_dir"].parent
        assert kwargs["execute"] is True
        assert kwargs["shared_lock"] == config["shared_lock_path"]
        assert kwargs["keep_completed"] == 2 and kwargs["max_groups"] == 2
        assert kwargs["deadline_at"] > runner.time.monotonic()
        assert os.fstat(kwargs["owner_fd"]).st_ino == config["shared_lock_path"].stat().st_ino
        with config["shared_lock_path"].open("r") as probe:
            with pytest.raises(BlockingIOError):
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        called.append(True)
        return {"deadline_deferred": False, "removed_groups": 1}

    config, health = _mock_tick(monkeypatch, tmp_path, prune=prune)
    result = runner.tick(config, [])
    assert called == [True]
    assert result["status"] == "complete"
    assert result["publication_status"] == "published"
    assert result["storage_retention"]["status"] == "complete"


def test_retention_disabled_or_unresolved_never_calls_helper(monkeypatch, tmp_path):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("retention helper was called")

    config, _ = _mock_tick(monkeypatch, tmp_path, enabled=False, prune=forbidden)
    assert "storage_retention" not in runner.tick(config, [])
    config, _ = _mock_tick(monkeypatch, tmp_path, unresolved=True, prune=forbidden)
    result = runner.tick(config, [])
    assert result["storage_retention"] == {
        "status": "skipped", "reason": "unresolved_publication",
    }


def test_retention_skips_maintenance_pause_after_work(monkeypatch, tmp_path):
    config, _ = _mock_tick(monkeypatch, tmp_path, pause_during_work=True,
                           prune=lambda *_a, **_k: pytest.fail("unexpected prune"))
    result = runner.tick(config, [])
    assert result["status"] == "complete"
    assert result["storage_retention"] == {
        "status": "skipped", "reason": "maintenance_paused",
    }


@pytest.mark.parametrize("failure, expected", [
    (TimeoutError("budget exhausted"), "deferred"),
    (ValueError("bad receipt"), "error"),
])
def test_retention_failure_does_not_change_publication_or_health(monkeypatch, tmp_path,
                                                                  failure, expected):
    def prune(*_args, **_kwargs):
        raise failure

    config, health = _mock_tick(monkeypatch, tmp_path, prune=prune)
    result = runner.tick(config, [])
    assert result["status"] == "complete"
    assert result["publication_status"] == "published"
    assert result["storage_retention"]["status"] == expected
    assert health == [{"status": "complete", "publication_status": "published"}]


def test_retention_partial_deadline_is_reported_separately(monkeypatch, tmp_path):
    config, _ = _mock_tick(
        monkeypatch, tmp_path,
        prune=lambda *_args, **_kwargs: {"deadline_deferred": True, "removed_groups": 1},
    )
    result = runner.tick(config, [])
    assert result["status"] == "complete"
    assert result["storage_retention"]["status"] == "deferred"


def test_enabled_dry_run_does_not_load_retention_helper(monkeypatch, fixture, capsys):
    fixture.configure(storage_retention={"enabled": True})
    monkeypatch.setattr(runner, "retention_helper", lambda *_: pytest.fail("dry run loaded helper"))
    before = set(fixture.root.rglob("*"))
    assert runner.main(["--config", str(fixture.config_path), "--dry-run"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["storage_retention"]["enabled"] is True
    assert result["writes_performed"] is False
    assert set(fixture.root.rglob("*")) == before


def test_retention_error_is_visible_in_persistent_health_without_failure_hold(tmp_path):
    value = {
        "started_at": "2026-09-29T00:00:00+00:00",
        "finished_at": "2026-09-29T00:01:00+00:00",
        "attempt_id": "attempt-1",
        "status": "complete",
        "result": {
            "status": "complete", "publication_status": "published",
            "storage_retention": {"status": "error", "reason": "ValueError: bad receipt"},
        },
    }
    runner.OBSERVABILITY.update_health(tmp_path, value)
    health = runner.OBSERVABILITY.health_view(tmp_path)
    assert health["last_attempt_status"] == "complete"
    assert health["publication_currently_confirmed"] is True
    assert health["last_retention_status"] == "error"
    assert health["last_retention_reason"] == "ValueError: bad receipt"
    assert health["last_retention_event_at"] == value["finished_at"]


def test_explicit_larger_maintenance_budget_remains_bounded(fixture):
    fixture.configure(storage_retention={
        "enabled": True, "max_seconds": 120, "max_groups": 8,
    })
    config, _ = runner.load_config(fixture.config_path)
    assert config["storage_retention"]["max_seconds"] == 120
    assert config["storage_retention"]["max_groups"] == 8


def _under_reserve(monkeypatch, *, freed=False):
    calls = []

    def check(_config, *, require_reserve=True):
        calls.append(require_reserve)
        if require_reserve and not freed:
            raise ValueError("Configured storage free space is below static reserve")
        return {"ready": bool(require_reserve and freed), "available_bytes": 1}

    monkeypatch.setattr(runner.OBSERVABILITY, "storage_check", check)
    return calls


def test_low_reserve_runs_only_retention_under_owner_then_retries_next_tick(monkeypatch, tmp_path):
    calls = []

    def prune(_root, **kwargs):
        with config["shared_lock_path"].open("r") as probe:
            with pytest.raises(BlockingIOError):
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        calls.append(True)
        return {"deadline_deferred": True, "removed_groups": 0}

    config, health = _mock_tick(monkeypatch, tmp_path, prune=prune)
    checks = _under_reserve(monkeypatch)
    monkeypatch.setattr(runner, "locked_tick", lambda *_: pytest.fail("fetch under reserve"))
    monkeypatch.setattr(runner.OBSERVABILITY, "failure_hold", lambda *_: pytest.fail("maintenance must not be held by prior fetch failures"))
    for _ in range(2):
        result = runner.tick(config, [])
        assert result["status"] == "process_failure"
        assert result["phase"] == "storage_maintenance"
        assert result["storage_retention"]["status"] == "deferred"
    assert calls == [True, True]
    assert checks == [False, False, True] * 2
    assert health == []  # A capacity deferral creates no unchanged-fetch-failure hold.


def test_reclaimed_reserve_is_rechecked_before_work_and_maintenance_is_not_doubled(monkeypatch, tmp_path):
    order = []

    def prune(_root, **_kwargs):
        order.append("prune")
        return {"deadline_deferred": False, "removed_groups": 1}

    config, _ = _mock_tick(monkeypatch, tmp_path, prune=prune)
    checks = _under_reserve(monkeypatch, freed=True)
    monkeypatch.setattr(runner, "locked_tick", lambda *_: order.append("work") or {"status": "complete"})
    result = runner.tick(config, [])
    assert order == ["prune", "work"]
    assert checks == [False, False, True]
    assert result["storage_retention"]["status"] == "complete"


@pytest.mark.parametrize("gate", ["maintenance", "unresolved", "owner"])
def test_low_reserve_preserves_maintenance_publication_and_owner_gates(monkeypatch, tmp_path, gate):
    config, _ = _mock_tick(monkeypatch, tmp_path, unresolved=gate == "unresolved",
                          prune=lambda *_a, **_k: pytest.fail("protected cleanup"))
    _under_reserve(monkeypatch)
    monkeypatch.setattr(runner, "locked_tick", lambda *_: pytest.fail("protected worker"))
    if gate == "maintenance":
        config["maintenance_file"].write_text("operator pause")
        assert runner.tick(config, [])["status"] == "maintenance_paused"
    elif gate == "unresolved":
        result = runner.tick(config, [])
        assert result["storage_retention"]["reason"] == "unresolved_publication"
        assert result["unresolved_publication"] is True
    else:
        with config["shared_lock_path"].open("w") as owner:
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            assert runner.tick(config, [])["status"] == "lock_busy"


def test_low_reserve_does_not_admit_work_after_retention_integrity_error(monkeypatch, tmp_path):
    def prune(*_a, **_kw):
        raise ValueError("modified protected evidence")

    config, _ = _mock_tick(monkeypatch, tmp_path, prune=prune)
    _under_reserve(monkeypatch, freed=True)
    monkeypatch.setattr(runner, "locked_tick", lambda *_: pytest.fail("integrity error must stop work"))
    result = runner.tick(config, [])
    assert result["status"] == "process_failure"
    assert result["storage_retention"]["status"] == "error"


def test_bad_mount_or_identity_still_blocks_before_any_lock_creation(monkeypatch, tmp_path):
    config, _ = _mock_tick(monkeypatch, tmp_path,
                          prune=lambda *_a, **_k: pytest.fail("invalid mount cleanup"))

    def invalid(*_a, **_kw):
        raise ValueError("Storage sentinel identity is absent or changed")

    monkeypatch.setattr(runner.OBSERVABILITY, "storage_check", invalid)
    with pytest.raises(ValueError, match="sentinel"):
        runner.tick(config, [])
    assert not config["shared_lock_path"].exists()


def test_load_config_only_defers_reserve_for_explicit_enabled_maintenance(monkeypatch, fixture):
    fixture.configure(storage_retention={"enabled": True})
    checks = _under_reserve(monkeypatch)
    with pytest.raises(ValueError, match="reserve"):
        runner.load_config(fixture.config_path)
    runner.load_config(fixture.config_path, allow_storage_maintenance=True)
    fixture.configure(storage_retention={"enabled": False})
    with pytest.raises(ValueError, match="reserve"):
        runner.load_config(fixture.config_path, allow_storage_maintenance=True)
    assert checks == [True, False, True]


def test_retention_gate_scan_error_preserves_successful_publication(monkeypatch, tmp_path):
    config, health = _mock_tick(monkeypatch, tmp_path,
                               prune=lambda *_a, **_k: pytest.fail("uncertain gate"))
    scans = 0

    def unresolved(_root):
        nonlocal scans
        scans += 1
        if scans == 1:
            return []  # Normal work admission succeeds.
        raise ValueError("malformed old publication receipt")

    monkeypatch.setattr(runner, "unresolved_publications", unresolved)
    result = runner.tick(config, [])
    assert result["status"] == "complete"
    assert result["publication_status"] == "published"
    assert result["storage_retention"]["status"] == "error"
    assert "malformed" in result["storage_retention"]["reason"]
    assert health == [{"status": "complete", "publication_status": "published"}]
