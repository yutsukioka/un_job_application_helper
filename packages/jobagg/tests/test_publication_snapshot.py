"""Publication file bindings, recovery exception, and real SQLite backup tests."""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time

import pytest

from jobagg import publication_snapshot as module
from jobagg.publication_snapshot import create_publication_snapshot, validate_publication_snapshot


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def inputs(tmp_path):
    root = tmp_path.resolve()
    database, acceptance = root / "worker.sqlite3", root / "acceptance.json"
    database.write_bytes(b"original database bytes")
    acceptance.write_text('{"status":"accepted"}')
    request = {
        "worker_database": str(database),
        "worker_acceptance_path": str(acceptance),
        "worker_acceptance_sha256": digest(acceptance),
        "worker_database_files": [{"path": str(database), "sha256": digest(database)}],
    }
    return root, database, acceptance, request


def test_exact_legacy_binding_without_owner(inputs):
    _, database, _, request = inputs
    result = validate_publication_snapshot(request, database)
    assert result["effective_database"] == str(database)
    assert result["validation_receipt"]["mode"] == "legacy_exact_files"
    assert result["validation_receipt"]["legacy_exception"] is None


def test_new_empty_wal_requires_owner_and_records_exception(inputs):
    _, database, _, request = inputs
    wal = Path(str(database) + "-wal")
    wal.touch()
    before = json.dumps(request, sort_keys=True)
    with pytest.raises(ValueError, match="verified shared owner"):
        validate_publication_snapshot(request, database)
    result = validate_publication_snapshot(request, database, owner_held=True)
    assert json.dumps(request, sort_keys=True) == before
    receipt = result["validation_receipt"]
    assert receipt["mode"] == "legacy_empty_wal_exception"
    assert receipt["legacy_exception"]["original_bindings_preserved"]
    assert receipt["legacy_exception"]["wal"]["size_bytes"] == 0
    assert receipt["legacy_exception"]["wal"]["sha256"] == hashlib.sha256(b"").hexdigest()


@pytest.mark.parametrize("mutation", ["unbound_nonempty", "database", "acceptance", "bound_wal_changed", "bound_wal_removed"])
def test_original_bindings_never_waived(inputs, mutation):
    _, database, acceptance, request = inputs
    wal = Path(str(database) + "-wal")
    if mutation.startswith("bound_wal"):
        wal.touch()
        request["worker_database_files"].append({"path": str(wal), "sha256": digest(wal)})
    if mutation == "unbound_nonempty":
        wal.write_bytes(b"uncommitted frames")
    elif mutation == "database":
        database.write_bytes(b"changed")
    elif mutation == "acceptance":
        acceptance.write_text("changed")
    elif mutation == "bound_wal_changed":
        wal.write_bytes(b"changed")
    else:
        wal.unlink()
    with pytest.raises(ValueError):
        validate_publication_snapshot(request, database, owner_held=True)


@pytest.mark.parametrize("kind", ["symlink", "dangling", "fifo", "directory"])
def test_new_wal_must_be_regular(inputs, kind):
    root, database, _, request = inputs
    wal = Path(str(database) + "-wal")
    if kind in {"symlink", "dangling"}:
        other = root / "other"
        if kind == "symlink":
            other.touch()
        wal.symlink_to(other)
    elif kind == "fifo":
        os.mkfifo(wal)
    else:
        wal.mkdir()
    with pytest.raises(ValueError, match="regular"):
        validate_publication_snapshot(request, database, owner_held=True)


def test_duplicate_and_unrelated_bindings_rejected(inputs):
    root, database, _, request = inputs
    request["worker_database_files"] *= 2
    with pytest.raises(ValueError, match="duplicate"):
        validate_publication_snapshot(request, database, owner_held=True)
    other = root / "other"
    other.touch()
    request["worker_database_files"] = [{"path": str(other), "sha256": digest(other)}]
    with pytest.raises(ValueError, match="unexpected"):
        validate_publication_snapshot(request, database, owner_held=True)


def test_wal_growing_during_final_validation_rejected(inputs, monkeypatch):
    _, database, _, request = inputs
    wal = Path(str(database) + "-wal")
    wal.touch()
    original = module._unchanged
    calls = 0

    def changing(path, stamp):
        nonlocal calls
        if path == wal:
            calls += 1
            if calls == 2:
                wal.write_bytes(b"new frames")
        return original(path, stamp)

    monkeypatch.setattr(module, "_unchanged", changing)
    with pytest.raises(ValueError, match="changed during validation"):
        validate_publication_snapshot(request, database, owner_held=True)


def test_previously_absent_wal_appears_during_final_validation(inputs, monkeypatch):
    _, database, acceptance, request = inputs
    original = module._unchanged
    calls = 0

    def changing(path, stamp):
        nonlocal calls
        if path == acceptance:
            calls += 1
            if calls == 2:
                Path(str(database) + "-wal").touch()
        return original(path, stamp)

    monkeypatch.setattr(module, "_unchanged", changing)
    with pytest.raises(ValueError, match="appeared"):
        validate_publication_snapshot(request, database, owner_held=True)


def test_parent_directory_alias_preserves_old_bound_lexical_paths(inputs):
    root, database, _, request = inputs
    alias = root / "alias"
    alias.symlink_to(root, target_is_directory=True)
    request["worker_database"] = str(alias / database.name)
    request["worker_database_files"][0]["path"] = str(alias / database.name)
    assert validate_publication_snapshot(request, alias / database.name)["effective_database"] == str(alias / database.name)


def make_sqlite_snapshot(inputs):
    root, database, _, request = inputs
    database.unlink()
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE observations(id INTEGER PRIMARY KEY, public_text TEXT)")
    connection.executemany("INSERT INTO observations VALUES(?,?)", ((i, "public job text " + str(i)) for i in range(1000)))
    connection.commit()
    lock = root / "owner.lock"
    target = root / "sealed.sqlite3"
    with lock.open("a+") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        snapshot = create_publication_snapshot(database, target, request["worker_acceptance_sha256"], owner.fileno(), lock)
    connection.close()
    request["publication_snapshot"] = snapshot
    request["worker_database_files"] = [{"path": str(target), "sha256": snapshot["sha256"]}]
    return target, snapshot


def test_sealed_backup_contains_wal_rows_and_ignores_later_worker_mutation(inputs):
    _, database, _, request = inputs
    target, snapshot = make_sqlite_snapshot(inputs)
    assert target.stat().st_mode & 0o222 == 0
    assert target.stat().st_nlink == 1
    with sqlite3.connect(target.as_uri() + "?mode=ro", uri=True) as conn:
        assert conn.execute("SELECT count(*) FROM observations").fetchone()[0] == 1000
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert conn.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    assert digest(target) == snapshot["sha256"]
    database.write_bytes(b"later unrelated worker state")
    Path(str(database) + "-wal").write_bytes(b"later worker WAL frames")
    result = validate_publication_snapshot(request, database)
    assert result["effective_database"] == str(target)
    assert result["validation_receipt"]["mode"] == "sealed_sqlite_backup"


@pytest.mark.parametrize("mutation", ["acceptance_binding", "digest", "size", "sidecar", "writable", "source_binding"])
def test_invalid_sealed_backup_rejected(inputs, mutation):
    _, database, _, request = inputs
    target, snapshot = make_sqlite_snapshot(inputs)
    if mutation == "acceptance_binding":
        snapshot["worker_acceptance_sha256"] = "0" * 64
    elif mutation == "digest":
        os.chmod(target, 0o644)
        target.write_bytes(b"changed sealed bytes")
        os.chmod(target, 0o444)
    elif mutation == "size":
        snapshot["size_bytes"] += 1
    elif mutation == "sidecar":
        Path(str(target) + "-wal").touch()
    elif mutation == "writable":
        os.chmod(target, 0o644)
    else:
        snapshot["source_database"] = str(database) + ".different"
    with pytest.raises(ValueError):
        validate_publication_snapshot(request, database, owner_held=True)


def test_snapshot_requires_held_owner_and_never_overwrites(inputs):
    root, database, _, request = inputs
    lock, target = root / "owner.lock", root / "sealed.sqlite3"
    with lock.open("a+") as owner:
        with pytest.raises(ValueError, match="held shared owner"):
            create_publication_snapshot(database, target, request["worker_acceptance_sha256"], owner.fileno(), lock)
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        target.write_bytes(b"prior generation")
        with pytest.raises(ValueError, match="target exists"):
            create_publication_snapshot(database, target, request["worker_acceptance_sha256"], owner.fileno(), lock)
        assert target.read_bytes() == b"prior generation"


def test_deadline_prevents_validation_and_backup(inputs):
    root, database, _, request = inputs
    with pytest.raises(TimeoutError):
        validate_publication_snapshot(request, database, deadline_at=time.monotonic() - 1)
    lock = root / "owner.lock"
    with lock.open("a+") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(TimeoutError):
            create_publication_snapshot(database, root / "never.sqlite3", request["worker_acceptance_sha256"], owner.fileno(), lock, deadline_at=time.monotonic() - 1)
    assert not (root / "never.sqlite3").exists()


def test_cli_uses_sealed_database_and_echoes_validation(inputs, monkeypatch, capsys):
    from jobagg import publish_worker

    root, database, _, request = inputs
    target, _ = make_sqlite_snapshot(inputs)
    registry = root / "sources.yaml"
    registry.write_text("sources:\n- id: example\n  name: Example\n  enabled: true\n  ats_family: custom_html\n  base_url: https://example.org/jobs\n")
    lock = root / "owner.lock"
    request.update(schema_version=1, run_id="test", shared_lock_path=str(lock), registry_sha256=digest(registry), expected_source_ids=["example"])
    path = root / "request.json"
    path.write_text(json.dumps(request))
    seen = []

    def publish(effective, *_args, **_kwargs):
        seen.append(str(effective))
        return {"status": "no_changes", "observation_set_sha256": "0" * 64}

    monkeypatch.setattr(publish_worker, "publish_incremental", publish)
    assert publish_worker.main(["--worker-database", str(database), "--registry", str(registry), "--output-dir", str(root / "output"), "--state-dir", str(root / "state"), "--shared-lock", str(lock), "--request", str(path), "--execute"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert seen == [str(target)]
    assert result["publication_snapshot"] == request["publication_snapshot"]
    assert result["publication_snapshot_validation"]["mode"] == "sealed_sqlite_backup"
    assert result["worker_database"] == str(database)


def test_snapshot_rejects_unlocked_fd_when_other_fd_owns_same_lock(inputs):
    root, database, _, request = inputs
    lock = root / "owner.lock"
    with lock.open("a+") as owner, lock.open("a+") as unlocked:
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            create_publication_snapshot(database, root / "not-created.sqlite3", request["worker_acceptance_sha256"], unlocked.fileno(), lock)
    assert not (root / "not-created.sqlite3").exists()


def test_backup_interruption_closes_connections_and_removes_temporary(inputs, monkeypatch):
    root, database, _, request = inputs
    database.unlink()
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE sample(value TEXT)")
        conn.execute("INSERT INTO sample VALUES('public detail')")
    conn.close()
    calls = 0

    def interrupt(_deadline):
        nonlocal calls
        calls += 1
        if calls >= 3:
            raise TimeoutError("test deadline")

    monkeypatch.setattr(module, "_check", interrupt)
    lock = root / "owner.lock"
    with lock.open("a+") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(TimeoutError):
            create_publication_snapshot(database, root / "interrupted.sqlite3", request["worker_acceptance_sha256"], owner.fileno(), lock)
    assert not (root / "interrupted.sqlite3").exists()
    assert not list(root.glob(".publication-backup-*"))
    with sqlite3.connect(database, timeout=0.1) as check:
        check.execute("BEGIN EXCLUSIVE")
        assert check.execute("SELECT value FROM sample").fetchone()[0] == "public detail"


def test_actual_publisher_reads_fixed_backup_after_worker_advances(tmp_path):
    from jobagg.pipelines.live_publication import publish_incremental
    from test_live_publication import add_detail, setup

    fixture = setup.__wrapped__(tmp_path)
    incoming, _ = add_detail(fixture)
    worker = fixture["worker"].path
    lock = tmp_path / "owner.lock"
    target = tmp_path / "sealed.sqlite3"
    with lock.open("a+") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        snapshot = create_publication_snapshot(worker, target, "0" * 64, owner.fileno(), lock)
        with fixture["worker"].connect() as conn:
            conn.execute("DELETE FROM remediation_observations")
        result = publish_incremental(target, fixture["registry"], fixture["output"], fixture["state"], execute=True)
    assert result["status"] == "published"
    with sqlite3.connect(fixture["output"] / "all_jobs.sqlite3") as live:
        actual = live.execute("SELECT description FROM jobs WHERE external_id='001'").fetchone()[0]
    assert actual == incoming.description
    assert digest(target) == snapshot["sha256"]
    assert not Path(str(target) + "-wal").exists()


def recovery_fixture(inputs):
    root, database, _, request = inputs
    output, state = root / "output", root / "publication"
    output.mkdir()
    registry = root / "sources.yaml"
    registry.write_text("sources:\n- id: example\n  name: Example\n  enabled: true\n  ats_family: custom_html\n  base_url: https://example.org/jobs\n")
    request.update(schema_version=1, run_id="original", registry_sha256=digest(registry), expected_source_ids=["example"], generated_at="2026-09-15T00:00:00+00:00", deadline_at="2026-09-15T00:03:00+00:00", shared_lock_path=str(root / "owner.lock"))
    generation = "a" * 32
    directory = state / "generations" / generation
    directory.mkdir(parents=True)
    plan = {
        "generation_id": generation, "generation_root": str(directory),
        "output_dir": str(output), "worker_database": str(database),
        "registry_sha256": request["registry_sha256"], "observation_set_sha256": "1" * 64,
        "created_at": "2026-09-15T00:00:01+00:00", "changes": [{"worker_row": {"source_id": "example"}}], "listing_frames": [],
    }
    path = directory / "plan.json"
    path.write_text(json.dumps(plan))
    gate = {
        "generation_id": generation, "plan_path": str(path), "plan_sha256": digest(path),
        "observation_set_sha256": plan["observation_set_sha256"],
        "state": "exporting", "started_at": "2026-09-15T00:00:02+00:00",
        "database_transactions_complete": True,
    }
    gate_path = output / ".jobagg-publication-state.json"
    gate_path.write_text(json.dumps(gate))
    return output, state, registry, directory, gate_path, gate


def test_recovery_binding_requires_owner_and_original_unfinished_window(inputs):
    _, database, _, request = inputs
    output, state, _, _, _, _ = recovery_fixture(inputs)
    with pytest.raises(ValueError, match="verified shared owner"):
        module.capture_recovery_generation(request, database, output, state)
    original = dict(request)
    request["generated_at"] = "2026-09-15T00:02:00+00:00"
    with pytest.raises(ValueError, match="outside original"):
        module.capture_recovery_generation(request, database, output, state, owner_held=True)
    binding = module.capture_recovery_generation(original, database, output, state, owner_held=True)
    assert binding["generation_id"] == "a" * 32


def test_first_recovery_cannot_infer_binding_from_complete_gate(inputs):
    _, database, _, request = inputs
    output, state, _, directory, gate_path, gate = recovery_fixture(inputs)
    gate.update(state="complete", status="published")
    gate_path.write_text(json.dumps(gate))
    (directory / "result.json").write_text(json.dumps(gate))
    with pytest.raises(ValueError, match="lacks prior generation binding"):
        module.capture_recovery_generation(request, database, output, state, owner_held=True)


@pytest.mark.parametrize("mutation", ["missing_gate", "new_generation", "changed_plan", "wrong_request", "wrong_result"])
def test_pinned_recovery_rejects_missing_changed_or_borrowed_generation(inputs, mutation):
    _, database, _, request = inputs
    output, state, _, directory, gate_path, gate = recovery_fixture(inputs)
    binding = module.capture_recovery_generation(request, database, output, state, owner_held=True)
    request.update(recover_publication=True, recovery_generation_binding=binding)
    if mutation == "missing_gate":
        gate_path.unlink()
    elif mutation == "new_generation":
        gate["generation_id"] = "b" * 32
        gate_path.write_text(json.dumps(gate))
    elif mutation == "changed_plan":
        plan_path = directory / "plan.json"
        plan = json.loads(plan_path.read_text())
        plan["extra"] = "changed prepared generation"
        plan_path.write_text(json.dumps(plan))
        gate["plan_sha256"] = digest(plan_path)
        gate_path.write_text(json.dumps(gate))
    elif mutation == "wrong_request":
        request["run_id"] = "different original publication"
    else:
        gate.update(state="complete", status="published")
        gate_path.write_text(json.dumps(gate))
        (directory / "result.json").write_text(json.dumps({**gate, "generation_id": "b" * 32}))
    with pytest.raises(ValueError):
        module.validate_recovery_generation(request, database, output, state)


def test_cli_reconciles_pinned_complete_generation_without_planning(inputs, monkeypatch, capsys):
    from jobagg import publish_worker

    root, database, _, request = inputs
    output, state, registry, directory, gate_path, gate = recovery_fixture(inputs)
    binding = module.capture_recovery_generation(request, database, output, state, owner_held=True)
    gate.update(state="complete", status="published")
    gate_path.write_text(json.dumps(gate))
    (directory / "result.json").write_text(json.dumps(gate))
    # Changed retry timestamps must not change the original immutable request identity.
    request.update(generated_at="2026-09-15T01:00:00+00:00", recover_publication=True, recovery_generation_binding=binding)
    assert module.capture_recovery_generation(request, database, output, state, prior_binding=binding, owner_held=True) == binding
    path = root / "request.json"
    path.write_text(json.dumps(request))

    def no_new_plan(*_args, **_kwargs):
        pytest.fail("A completed recovery generation must never start a new plan")

    monkeypatch.setattr(publish_worker, "publish_incremental", no_new_plan)
    assert publish_worker.main(["--worker-database", str(database), "--registry", str(registry), "--output-dir", str(output), "--state-dir", str(state), "--shared-lock", request["shared_lock_path"], "--request", str(path), "--execute"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["publication"]["generation_id"] == binding["generation_id"]
    assert result["publication"]["reconciled_completed_generation"]
    assert result["status"] == "published"


def test_actual_missing_gate_recovery_cannot_noop_resolve_partial_exports(tmp_path):
    from datetime import UTC, datetime, timedelta
    from jobagg.pipelines.live_publication import publish_incremental
    from jobagg import publish_worker
    from test_live_publication import add_detail, setup

    fixture = setup.__wrapped__(tmp_path)
    add_detail(fixture)
    worker = fixture["worker"].path
    receipt = tmp_path / "acceptance.json"
    receipt.write_text('{"status":"accepted"}')
    lock = tmp_path / "owner.lock"
    now = datetime.now(UTC)
    request = {
        "schema_version": 1, "run_id": "interrupted", "registry_sha256": digest(fixture["registry"]),
        "expected_source_ids": ["test_custom_html"], "generated_at": now.isoformat(),
        "deadline_at": (now + timedelta(minutes=5)).isoformat(), "shared_lock_path": str(lock),
        "worker_database": str(worker), "worker_database_files": [{"path": str(worker), "sha256": digest(worker)}],
        "worker_acceptance_path": str(receipt), "worker_acceptance_sha256": digest(receipt),
    }

    def interrupt(stage, _key):
        if stage == "after_export_replace":
            raise RuntimeError("simulated publisher crash")

    with lock.open("a+") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match="simulated"):
            publish_incremental(worker, fixture["registry"], fixture["output"], fixture["state"], execute=True, fault=interrupt)
        binding = module.capture_recovery_generation(request, worker, fixture["output"], fixture["state"], owner_held=True)
    generation = Path(binding["plan_path"]).parent
    assert not (generation / "result.json").exists()
    (fixture["output"] / ".jobagg-publication-state.json").unlink()
    request.update(recover_publication=True, recovery_generation_binding=binding)
    path = tmp_path / "recovery.json"
    path.write_text(json.dumps(request))
    with pytest.raises(ValueError):
        publish_worker.main(["--worker-database", str(worker), "--registry", str(fixture["registry"]), "--output-dir", str(fixture["output"]), "--state-dir", str(fixture["state"]), "--shared-lock", str(lock), "--request", str(path), "--execute"])
    assert not (generation / "result.json").exists()
    assert not (fixture["output"] / ".jobagg-publication-state.json").exists()


def test_expired_generation_requires_matching_ownership_or_review(inputs):
    _, database, _, request = inputs
    output, state, _, directory, gate_path, gate = recovery_fixture(inputs)
    gate['started_at'] = '2026-09-15T00:04:00+00:00'
    gate_path.write_text(json.dumps(gate))
    with pytest.raises(ValueError, match='outside original'):
        module.capture_recovery_generation(request, database, output, state, owner_held=True)
    auth = {'schema_version': 1, 'kind': 'reviewed_expired_publication',
            'request_identity_sha256': module._recovery_identity(request),
            'generation_id': gate['generation_id'], 'plan_sha256': gate['plan_sha256'],
            'original_generated_at': request['generated_at'], 'original_deadline_at': request['deadline_at'],
            'gate_started_at': gate['started_at']}
    for key in ('generation_id', 'plan_sha256', 'request_identity_sha256', 'original_deadline_at'):
        with pytest.raises(ValueError, match='authorization differs'):
            module.capture_recovery_generation(request, database, output, state, owner_held=True,
                                               expired_authorization={**auth, key: 'wrong'})
    binding = module.capture_recovery_generation(request, database, output, state, owner_held=True, expired_authorization=auth)
    assert binding['expiry_recovery_authorization'] == auth
    plan_path = directory / 'plan.json'
    plan = json.loads(plan_path.read_text())
    plan['request_identity_sha256'] = module._recovery_identity(request)
    plan_path.write_text(json.dumps(plan))
    gate.update(plan_sha256=digest(plan_path), request_identity_sha256=plan['request_identity_sha256'])
    gate_path.write_text(json.dumps(gate))
    assert module.capture_recovery_generation(request, database, output, state, owner_held=True)['request_ownership_verified']
    gate['request_identity_sha256'] = 'wrong'
    gate_path.write_text(json.dumps(gate))
    with pytest.raises(ValueError, match='ownership differs'):
        module.capture_recovery_generation(request, database, output, state, owner_held=True)
