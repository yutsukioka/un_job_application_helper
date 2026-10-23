import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest
from test_runner_reliability import phase_config, RUNNER

pytest_plugins = ["test_runner_reliability"]

spec = importlib.util.spec_from_file_location("sep15_runner", RUNNER)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
obs = runner.OBSERVABILITY


def outcomes(fixture):
    return sorted((fixture.root / "state/attempts").glob("*/outcome.json"))


def test_preflight_failure_persists_outcome_and_health(fixture):
    config = json.loads(fixture.config_path.read_text())
    config["source_manifest_path"] = "missing.json"
    fixture.config_path.write_text(json.dumps(config))
    code, result = fixture.invoke()
    assert code == 3 and result["status"] == "process_failure"
    assert len(outcomes(fixture)) == 1
    assert json.loads(outcomes(fixture)[0].read_text())["result"] == result
    health = obs.health_view(fixture.root / "state")
    assert health["invocation_alive"] is True
    assert health["publication_currently_confirmed"] is False
    assert not (fixture.root / "state/runs").exists()


def test_health_ages_liveness_without_manufacturing_publication(fixture):
    fixture.invoke()
    path = fixture.root / "state/health.json"
    h = json.loads(path.read_text())
    h["last_invocation_at"] = "2000-01-01T00:00:00+00:00"
    path.write_text(json.dumps(h))
    result = obs.health_view(path.parent)
    assert result["invocation_alive"] is False
    assert result["publication_currently_confirmed"] is False


def test_three_same_recovery_failures_hold_then_evidence_change_rearms(fixture):
    phase_config(fixture, publication="commit_then_fail")
    code, first = fixture.invoke()
    assert code == 3
    wal = fixture.root / "worker.sqlite-wal"
    wal.write_bytes(b"nonempty-unbound-wal")
    for _ in range(3):
        code, result = fixture.invoke()
        assert code == 3 and "Unbound WAL is nonempty" in result["reasons"][0]
    code, held = fixture.invoke()
    assert code == 75 and held["status"] == "unchanged_failure_hold"
    assert len(outcomes(fixture)) == 5
    assert len((fixture.root / "worker_starts.txt").read_text().splitlines()) == 1
    original = (Path(first["run_dir"]) / "publication_request.json").read_bytes()
    wal.write_bytes(b"")
    code, recovered = fixture.invoke()
    assert code == 0 and recovered["publication_status"] == "published"
    assert (
        Path(first["run_dir"]) / "publication_request.json"
    ).read_bytes() == original
    proof = next(
        (Path(first["run_dir"]) / "publication_recovery").glob(
            "*/snapshot_validation.json"
        )
    )
    assert json.loads(proof.read_text())["mode"] == "legacy_empty_wal_exception"
    assert len((fixture.root / "publication_commits.txt").read_text().splitlines()) == 1
    assert (
        obs.health_view(fixture.root / "state")["publication_currently_confirmed"]
        is True
    )


def test_failed_snapshot_binding_remains_held_after_code_or_config_change(fixture):
    # A condition change permits revalidation; it never bypasses it.
    phase_config(fixture, publication="commit_then_fail")
    fixture.invoke()
    (fixture.root / "worker.sqlite").write_bytes(b"changed database")
    for _ in range(3):
        assert fixture.invoke()[0] == 3
    assert fixture.invoke()[0] == 75
    c = json.loads(fixture.config_path.read_text())
    c["timeout_seconds"] += 1
    fixture.config_path.write_text(json.dumps(c))
    code, result = fixture.invoke()
    assert code == 3 and "configuration differs" in result["reasons"][0]
    assert len((fixture.root / "publication_commits.txt").read_text().splitlines()) == 1


def test_sealed_backup_recovers_even_if_mutable_worker_later_changes(fixture):
    phase_config(
        fixture,
        publication="commit_then_fail",
        sealed_publication_snapshots=True,
        publication_timeout_seconds=3,
    )
    code, first = fixture.invoke()
    assert code == 3
    run = Path(first["run_dir"])
    request = json.loads((run / "publication_request.json").read_text())
    snapshot = Path(request["publication_snapshot"]["path"])
    before = snapshot.read_bytes()
    assert snapshot.stat().st_mode & 0o222 == 0
    with sqlite3.connect(fixture.root / "worker.sqlite") as db:
        db.execute("UPDATE fixture_jobs SET body='Newer mutable body'")
    code, recovered = fixture.invoke()
    assert code == 0 and recovered["publication_status"] == "published"
    assert snapshot.read_bytes() == before
    assert request["worker_database"] == str(fixture.root / "worker.sqlite")
    assert len((fixture.root / "worker_starts.txt").read_text().splitlines()) == 1
    assert len((fixture.root / "publication_commits.txt").read_text().splitlines()) == 1


def test_legacy_zero_wal_symlink_never_accepted(fixture):
    phase_config(fixture, publication="commit_then_fail")
    fixture.invoke()
    target = fixture.root / "empty"
    target.write_bytes(b"")
    (fixture.root / "worker.sqlite-wal").symlink_to(target)
    code, result = fixture.invoke()
    assert code == 3 and "regular unaliased" in result["reasons"][0]


def test_missing_drive_records_internal_failure_without_creating_volume(fixture):
    missing = fixture.root / "unmounted"
    fixture.configure(
        state_dir=str(missing / "state"),
        shared_lock_path=str(missing / "owner"),
        attempt_state_dir=str(fixture.root / "internal"),
        storage_guard={
            "mount_root": str(missing),
            "sentinel_path": str(missing / "sentinel"),
            "sentinel_sha256": "a" * 64,
        },
    )
    code, result = fixture.invoke()
    assert code == 3 and "not mounted" in result["reasons"][0]
    assert not missing.exists()
    assert len(list((fixture.root / "internal/attempts").glob("*/outcome.json"))) == 1
    before = set(fixture.root.rglob("*"))
    proc = subprocess.run(
        [sys.executable, str(RUNNER), "--config", str(fixture.config_path), "--health"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.returncode == 0 and json.loads(proc.stdout)["invocation_alive"]
    assert set(fixture.root.rglob("*")) == before


def test_storage_identity_reserve_and_escape_checks(tmp_path, monkeypatch):
    mount = tmp_path / "volume"
    mount.mkdir()
    sentinel = mount / "identity.json"
    sentinel.write_text('{"id":"expected"}')
    guard = {
        "mount_root": str(mount),
        "sentinel_path": str(sentinel),
        "sentinel_sha256": hashlib.sha256(sentinel.read_bytes()).hexdigest(),
    }
    config = {
        "storage_guard": guard,
        "attempt_state_dir": tmp_path / "internal",
        "state_dir": mount / "state",
        "shared_lock_path": mount / "lock",
        "publication_worker_database": mount / "worker.sqlite",
    }
    monkeypatch.setattr(obs.os.path, "ismount", lambda path: Path(path) == mount)
    assert obs.storage_check(config)["ready"]
    config["shared_lock_path"] = tmp_path / "wrong"
    with pytest.raises(ValueError, match="escapes"):
        obs.storage_check(config)
    config["shared_lock_path"] = mount / "lock"
    guard["min_free_bytes"] = 10**30
    with pytest.raises(ValueError, match="static reserve"):
        obs.storage_check(config)
    guard["min_free_bytes"] = 0
    sentinel.write_text("wrong volume identity")
    with pytest.raises(ValueError, match="identity"):
        obs.storage_check(config)


@pytest.mark.parametrize('key', ['warning_free_bytes', 'publication_headroom_bytes', 'warning_runway_seconds'])
@pytest.mark.parametrize('value', [1.5, True, -1])
def test_invalid_storage_warning_config_fails_before_volume_access(fixture, monkeypatch, key, value):
    fixture.configure(storage_guard={
        'mount_root': str(fixture.root / 'volume'),
        'sentinel_path': str(fixture.root / 'volume/sentinel'),
        'sentinel_sha256': 'a' * 64,
        key: value,
    })
    monkeypatch.setattr(obs, 'storage_check', lambda config: pytest.fail('invalid config reached volume access'))
    with pytest.raises(ValueError, match='Invalid storage ' + key):
        runner.load_config(fixture.config_path)


def test_lexical_config_paths_survive_parent_alias(fixture):
    real = fixture.root / "real"
    real.mkdir()
    alias = fixture.root / "alias"
    alias.symlink_to(real, target_is_directory=True)
    fixture.configure(
        state_dir=str(alias / "state"), shared_lock_path=str(alias / "lock")
    )
    c, _ = runner.load_config(fixture.config_path)
    assert str(c["state_dir"]) == str(alias / "state")
    assert str(c["shared_lock_path"]) == str(alias / "lock")
    code, result = fixture.invoke()
    assert code == 0 and result["run_dir"].startswith(str(alias))


def test_historical_resolution_survives_approved_parent_alias_migration(fixture):
    import shutil

    phase_config(fixture, publication="commit_then_fail")
    assert fixture.invoke()[0] == 3
    assert fixture.invoke()[0] == 0
    original = fixture.root / "state"
    migrated = fixture.root / "external-state"
    shutil.move(original, migrated)
    original.symlink_to(migrated, target_is_directory=True)
    assert runner.unresolved_publications(migrated / "runs") == []


def test_code_change_alters_hold_condition_without_touching_receipts(fixture):
    code = fixture.root / "worker_fixture.py"
    code.write_text("# before")
    fixture.configure(
        worker_argv=[sys.executable, str(code), "{request_path}", "{report_path}"]
    )
    config, _ = runner.load_config(fixture.config_path)
    before = obs.condition_fingerprint(config, RUNNER)
    code.write_text("# reviewed change")
    assert obs.condition_fingerprint(config, RUNNER) != before
    assert not (fixture.root / "state").exists()


def test_delayed_health_write_cannot_roll_back_newer_failure(tmp_path):
    newer = {
        "attempt_id": "new",
        "started_at": "2026-09-15T02:00:00+00:00",
        "finished_at": "2026-09-15T02:00:01+00:00",
        "status": "process_failure",
        "result": {"unresolved_publication": True},
    }
    older = {
        "attempt_id": "old",
        "started_at": "2026-09-15T01:00:00+00:00",
        "finished_at": "2026-09-15T01:00:01+00:00",
        "status": "complete",
        "result": {"publication_status": "published"},
    }
    obs.update_health(tmp_path, newer)
    obs.update_health(tmp_path, older)
    health = json.loads((tmp_path / "health.json").read_text())
    assert health["last_attempt_id"] == "new"
    assert health["unresolved_publication"] is True
    assert health["publication_currently_confirmed"] is False


def test_unexpected_snapshot_claim_rejected(fixture):
    phase_config(fixture, publication="published")
    code, result = fixture.invoke()
    assert code == 0
    d = Path(result["run_dir"])
    request = json.loads((d / "publication_request.json").read_text())
    receipt = json.loads((d / "publication_result.json").read_text())
    receipt["publication_snapshot"] = None
    with pytest.raises(runner.InvalidReport, match="presence differs"):
        runner.validate_publication(receipt, request)


def test_failed_preflight_clears_readiness_but_preserves_last_publication(fixture):
    phase_config(fixture, publication="published")
    assert fixture.invoke()[0] == 0
    before = obs.health_view(fixture.root / "state")
    assert before["publication_currently_confirmed"] is True
    config = json.loads(fixture.config_path.read_text())
    config["source_manifest_path"] = "missing-after-success.json"
    fixture.config_path.write_text(json.dumps(config))
    assert fixture.invoke()[0] == 3
    after = obs.health_view(fixture.root / "state")
    assert after["publication_currently_confirmed"] is False
    assert after["operational_ready"] is False
    assert after["invocation_alive"] is True
    assert (
        after["last_publication_complete_at"] == before["last_publication_complete_at"]
    )


def test_busy_other_descriptor_does_not_prove_supplied_owner(tmp_path):
    import fcntl

    path = tmp_path / "owner.lock"
    with path.open("a+") as actual, path.open("a+") as unowned:
        fcntl.flock(actual, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(runner.InvalidReport, match="not the exclusive holder"):
            runner.owner_evidence({"shared_lock_path": path}, unowned.fileno())
        assert runner.owner_evidence({"shared_lock_path": path}, actual.fileno())[
            "exclusive_owner_held"
        ]


@pytest.mark.parametrize("phase", ["worker_spawn", "snapshot", "publisher_spawn"])
def test_storage_disconnect_stops_next_phase(fixture, monkeypatch, phase):
    phase_config(
        fixture,
        publication="published",
        sealed_publication_snapshots=True,
        publication_timeout_seconds=3,
    )
    config, expected = runner.load_config(fixture.config_path)
    calls = 0

    def phase_guard(_config):
        nonlocal calls
        calls += 1
        runs = fixture.root / "state/runs"
        ready = phase == "worker_spawn" and calls == 4
        ready |= phase == "snapshot" and any(runs.glob("*/worker_result.json"))
        ready |= phase == "publisher_spawn" and any(
            runs.glob("*/publication_intent.json")
        )
        if ready:
            raise ValueError("Simulated storage disconnection at " + phase)
        return {"ready": True}

    monkeypatch.setattr(obs, "storage_check", phase_guard)
    result = runner.tick(config, expected)
    assert result["status"] == "process_failure"
    assert "Simulated storage disconnection" in result["reasons"][0]
    run = Path(result["run_dir"])
    assert not (fixture.root / "publication_commits.txt").exists()
    if phase == "worker_spawn":
        assert not (fixture.root / "worker_starts.txt").exists()
    elif phase == "snapshot":
        assert (run / "worker_result.json").exists()
        assert not (run / "publication_snapshot.sqlite3").exists()
        assert not (run / "publication_intent.json").exists()
    else:
        assert (run / "publication_snapshot.sqlite3").exists()
        assert (run / "publication_intent.json").exists()


def test_storage_disconnect_before_recovery_spawn_keeps_intent(fixture, monkeypatch):
    phase_config(fixture, publication="commit_then_fail")
    code, first = fixture.invoke()
    assert code == 3
    run = Path(first["run_dir"])
    original = (run / "publication_request.json").read_bytes()
    config, expected = runner.load_config(fixture.config_path)

    def phase_guard(_config):
        if any((run / "publication_recovery").glob("*/state.json")):
            raise ValueError("Simulated recovery storage disconnection")
        return {"ready": True}

    monkeypatch.setattr(obs, "storage_check", phase_guard)
    result = runner.tick(config, expected)
    assert result["status"] == "process_failure"
    assert "storage disconnection" in result["reasons"][0]
    assert (run / "publication_request.json").read_bytes() == original
    assert len((fixture.root / "worker_starts.txt").read_text().splitlines()) == 1
    assert len((fixture.root / "publication_commits.txt").read_text().splitlines()) == 1
    assert not (run / "publication_resolution.json").exists()


def test_existing_guarded_parent_required(tmp_path, monkeypatch):
    mount = tmp_path / "volume"
    mount.mkdir()
    sentinel = mount / "identity"
    sentinel.write_text("identity")
    config = {
        "storage_guard": {
            "mount_root": str(mount),
            "sentinel_path": str(sentinel),
            "sentinel_sha256": runner.digest(sentinel),
        },
        "attempt_state_dir": tmp_path / "internal",
        "state_dir": mount / "absent-parent/state",
    }
    monkeypatch.setattr(obs.os.path, "ismount", lambda _: True)
    with pytest.raises(ValueError, match="runtime parent is missing"):
        obs.storage_check(config)
    assert not (mount / "absent-parent").exists()


def recovery_fixture(fixture):
    phase_config(fixture, publication="published")
    config, _ = runner.load_config(fixture.config_path)
    output, state = fixture.root / "output", fixture.root / "publisher"
    output.mkdir()
    state.mkdir()
    config["publication_argv"] = [
        sys.executable,
        "-m",
        "jobagg.publish_worker",
        "--output-dir",
        str(output),
        "--state-dir",
        str(state),
    ]
    run = fixture.root / "run"
    run.mkdir()
    source = fixture.root / "worker.sqlite"
    original = {
        "schema_version": 1,
        "run_id": "original-run",
        "worker_database": str(source),
        "worker_database_files": [{"path": str(source), "sha256": "a" * 64}],
        "registry_sha256": "b" * 64,
        "source_manifest_sha256": "c" * 64,
        "expected_source_ids": ["source-a"],
        "worker_acceptance_path": str(run / "acceptance.json"),
        "worker_acceptance_sha256": "d" * 64,
        "generated_at": "2026-09-15T01:00:00+00:00",
        "deadline_at": "2026-09-15T01:05:00+00:00",
    }
    generation = "e" * 32
    root = state / "generations" / generation
    root.mkdir(parents=True)
    plan = {
        "generation_id": generation,
        "generation_root": str(root),
        "output_dir": str(output),
        "worker_database": str(source),
        "registry_sha256": original["registry_sha256"],
        "observation_set_sha256": "f" * 64,
        "created_at": "2026-09-15T01:00:10+00:00",
        "changes": [{"worker_row": {"source_id": "source-a"}}],
        "listing_frames": [],
    }
    runner.atomic_json(root / "plan.json", plan)
    gate = {
        "generation_id": generation,
        "plan_path": str(root / "plan.json"),
        "plan_sha256": runner.digest(root / "plan.json"),
        "state": "exporting",
        "observation_set_sha256": plan["observation_set_sha256"],
        "started_at": "2026-09-15T01:00:15+00:00",
    }
    gate_path = output / ".jobagg-publication-state.json"
    runner.atomic_json(gate_path, gate)
    return config, original, {"effective_database": str(source)}, run, gate_path, root


def test_generation_binding_persisted_then_reconciles_same_complete_gate(fixture):
    import time

    config, original, snapshot, run, gate_path, root = recovery_fixture(fixture)
    args = config, original, snapshot, run, time.monotonic() + 10
    fields = runner.recovery_generation_fields(*args)
    binding_path = run / "publication_generation_binding.json"
    sealed = binding_path.read_bytes()
    gate = runner.read_json(gate_path)
    gate.update(
        state="complete", status="published", database_transactions_complete=True
    )
    runner.atomic_json(gate_path, gate)
    runner.atomic_json(root / "result.json", gate)
    assert runner.recovery_generation_fields(*args) == fields
    assert binding_path.read_bytes() == sealed
    gate["observation_set_sha256"] = "0" * 64
    runner.atomic_json(gate_path, gate)
    with pytest.raises(ValueError, match="binding differs|changed"):
        runner.recovery_generation_fields(*args)
    assert binding_path.read_bytes() == sealed


@pytest.mark.parametrize(
    "problem", ["complete", "absent", "outside_time", "foreign_source"]
)
def test_initial_generation_binding_holds_unsupported_prior_gate(fixture, problem):
    import time

    config, original, snapshot, run, gate_path, root = recovery_fixture(fixture)
    gate = runner.read_json(gate_path)
    if problem == "complete":
        gate["state"] = "complete"
        runner.atomic_json(gate_path, gate)
    elif problem == "absent":
        gate_path.unlink()
    elif problem == "outside_time":
        original["generated_at"] = "2026-09-16T00:00:00+00:00"
    else:
        original["expected_source_ids"] = ["different-source"]
    with pytest.raises((ValueError, FileNotFoundError)):
        runner.recovery_generation_fields(
            config, original, snapshot, run, time.monotonic() + 10
        )
    assert not (run / "publication_generation_binding.json").exists()


def test_recovery_gate_change_rearms_condition_without_mutating_old_proof(fixture):
    config, _, _, _, gate_path, _ = recovery_fixture(fixture)
    before = obs.condition_fingerprint(config, RUNNER)
    gate = runner.read_json(gate_path)
    gate["state"] = "complete"
    runner.atomic_json(gate_path, gate)
    assert obs.condition_fingerprint(config, RUNNER) != before
