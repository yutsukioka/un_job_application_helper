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
    {"enabled": 1}, {"max_seconds": 0}, {"max_seconds": 21},
    {"max_seconds": True}, {"max_groups": 0}, {"max_groups": 3},
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
    monkeypatch.setattr(runner.OBSERVABILITY, "storage_check", lambda _: {"ready": True})
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
