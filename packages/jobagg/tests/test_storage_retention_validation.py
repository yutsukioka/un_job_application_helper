"""Validation progress is bound evidence, never authority to delete a group."""

import fcntl
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from jobagg import storage_retention as retention
from jobagg import storage_cold_archive as cold
from test_storage_retention import make_populated_root, write_json


AS_OF = datetime(2026, 10, 14, tzinfo=UTC)


def execute(root, **kwargs):
    lock = root / "remediation" / "manual-fetch-owner.lock"
    with lock.open("a+") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX)
        return retention.prune_completed(
            root, execute=True, owner_fd=owner.fileno(), shared_lock=lock,
            **{"as_of": AS_OF, **kwargs},
        )


def cache_before_intent(root, target, monkeypatch):
    """Stop a real execution after it durably saves its first file proof."""
    original = retention._save_validation

    def save_then_defer(group, progress, budget=None):
        original(group, progress, budget)
        if group["path"] == target:
            raise TimeoutError("test stops after durable validation progress")

    with monkeypatch.context() as patch:
        patch.setattr(retention, "_save_validation", save_then_defer)
        result = execute(root, max_groups=2)
    assert result["deadline_deferred"] is True
    assert not (target / retention.RECEIPT).exists()
    cache = target / retention.VALIDATION
    assert json.loads(cache.read_text())["verified"]
    return cache


@pytest.mark.parametrize("changed", ["schema_version", "controls", "files", "proof"])
def test_validation_rejects_changed_schema_or_binding(tmp_path, monkeypatch, changed):
    root, runs, _ = make_populated_root(tmp_path)
    cache = cache_before_intent(root, runs[0], monkeypatch)
    saved = json.loads(cache.read_text())
    if changed == "schema_version":
        saved[changed] += 1
    elif changed == "controls":
        control = next(iter(saved[changed]))
        saved[changed][control]["sha256"] = "invalid"
    elif changed == "files":
        saved[changed][0]["size"] += 1
    else:
        saved["controls"][next(iter(saved["controls"]))]["sha256"] = "0" * 64
        saved["verified"][next(iter(saved["verified"]))]["identity"] = []
    write_json(cache, saved)
    snapshot = runs[0] / "publication_snapshot.sqlite3"
    before = snapshot.read_bytes()

    with pytest.raises(ValueError, match="validation (binding|proof) differs"):
        execute(root)

    assert snapshot.read_bytes() == before
    assert not (runs[0] / retention.RECEIPT).exists()


def test_validation_copied_from_another_group_is_rejected(tmp_path, monkeypatch):
    source_root, source_runs, _ = make_populated_root(tmp_path / "source")
    source_cache = cache_before_intent(source_root, source_runs[0], monkeypatch)
    root, runs, _ = make_populated_root(tmp_path / "destination")
    # The group name and artifact bytes match, but its physical group differs.
    cache = runs[0] / retention.VALIDATION
    cache.write_bytes(source_cache.read_bytes())
    snapshot = runs[0] / "publication_snapshot.sqlite3"
    before = snapshot.read_bytes()

    with pytest.raises(ValueError, match="validation binding differs"):
        execute(root)

    assert snapshot.read_bytes() == before
    assert not (runs[0] / retention.RECEIPT).exists()
    assert not (source_runs[0] / retention.RECEIPT).exists()


@pytest.mark.parametrize("protection", ["current", "newest_two", "selected_run", "selected_generation"])
def test_validation_cache_does_not_override_new_protection(
    tmp_path, monkeypatch, protection,
):
    root, runs, generations = make_populated_root(tmp_path)
    target = runs[0] if protection == "selected_run" else generations[0]
    cache = cache_before_intent(root, target, monkeypatch)
    cached_bytes = cache.read_bytes()
    proof = json.loads(cached_bytes)
    original_files = {item["path"]: (target / item["path"]).read_bytes()
                      for item in proof["files"]}
    as_of = AS_OF
    selection = {}
    if protection == "current":
        gate = root / "output" / ".jobagg-publication-state.json"
        value = json.loads(gate.read_text())
        value["generation_id"] = target.name
        write_json(gate, value)
    elif protection == "newest_two":
        result = target / "result.json"
        value = json.loads(result.read_text())
        value["completed_at"] = "2026-09-04T00:05:00+00:00"
        write_json(result, value)
    elif protection == "selected_run":
        selection["selected_runs"] = [target.name]
    else:
        selection["selected_generations"] = [target.name]

    preview = retention.prune_completed(root, as_of=as_of, max_groups=10, **selection)
    assert str(target) not in {item["path"] for item in preview["selected"]}
    result = execute(root, as_of=as_of, max_groups=10, **selection)

    assert str(target) not in {item["path"] for item in result["selected"]}
    assert cache.read_bytes() == cached_bytes
    assert not (target / retention.RECEIPT).exists()
    assert {relative: (target / relative).read_bytes() for relative in original_files} == original_files


def test_validation_symlink_preview_and_execute_do_not_write_outside(
    tmp_path, monkeypatch,
):
    root, runs, _ = make_populated_root(tmp_path)
    cache = cache_before_intent(root, runs[0], monkeypatch)
    outside = tmp_path / "outside-validation.json"
    outside.write_bytes(cache.read_bytes())

    def outside_state():
        contents = outside.read_bytes()
        info = outside.stat()
        return (contents, info.st_dev, info.st_ino, info.st_size,
                info.st_mtime_ns, info.st_ctime_ns)

    before = outside_state()
    cache.unlink()
    cache.symlink_to(outside)
    snapshot = runs[0] / "publication_snapshot.sqlite3"
    snapshot_bytes = snapshot.read_bytes()

    preview = retention.prune_completed(root, as_of=AS_OF)
    assert preview["selected"][0]["path"] == str(runs[0])
    assert outside_state() == before
    with pytest.raises(ValueError, match="regular"):
        execute(root)

    assert cache.is_symlink()
    assert outside_state() == before
    assert snapshot.read_bytes() == snapshot_bytes
    assert not (runs[0] / retention.RECEIPT).exists()


def test_validation_cache_does_not_authorize_missing_file_before_intent(
    tmp_path, monkeypatch,
):
    root, runs, _ = make_populated_root(tmp_path)
    cache = cache_before_intent(root, runs[0], monkeypatch)
    cached_bytes = cache.read_bytes()
    (runs[0] / "publication_snapshot.sqlite3").unlink()

    with pytest.raises(ValueError, match="source missing before intent"):
        execute(root)

    assert cache.read_bytes() == cached_bytes
    assert not (runs[0] / retention.RECEIPT).exists()


@pytest.mark.parametrize("corrupt_archive", [False, True])
def test_partial_validation_resumes_after_supported_cold_archive(
    tmp_path, monkeypatch, corrupt_archive,
):
    root, _, generations = make_populated_root(tmp_path)
    generation = generations[0]
    for candidate in generations:
        result_path = candidate / "result.json"
        result = json.loads(result_path.read_text())
        result["plan_path"] = str(candidate / "plan.json")
        write_json(result_path, result)
    cache = cache_before_intent(root, generation, monkeypatch)
    cached_bytes = cache.read_bytes()
    saved = json.loads(cache.read_text())
    assert str(generation / "plan.json") in saved["controls"]
    export_paths = [generation / item["path"] for item in saved["files"]]
    source_bytes = {path: path.read_bytes() for path in export_paths}
    archived = cold.archive_completed(
        root / "deterministic-live-publication", root / "output",
        root / "remediation" / "manual-fetch-owner.lock",
        dispatcher_state_dir=root / "deterministic-dispatcher-proposal", execute=True,
    )
    assert archived["archived"][0]["generation_id"] == generation.name
    assert not (generation / "plan.json").exists()
    assert cache.read_bytes() == cached_bytes
    assert {path: path.read_bytes() for path in export_paths} == source_bytes
    if corrupt_archive:
        manifest = json.loads((generation / cold.MANIFEST).read_text())
        Path(manifest["objects"]["plan.json"]["object_path"]).write_bytes(b"corrupt archive")
        with pytest.raises(ValueError, match="Compressed object differs"):
            execute(root)
        assert cache.read_bytes() == cached_bytes
        assert {path: path.read_bytes() for path in export_paths} == source_bytes
        assert not (generation / retention.RECEIPT).exists()
        return
    original_digest = retention._digest
    hashed = []

    def observe_hash(path, **kwargs):
        if path in export_paths:
            hashed.append(path)
        return original_digest(path, **kwargs)

    monkeypatch.setattr(retention, "_digest", observe_hash)
    result = execute(root)
    assert result["removed_groups"] == 1
    assert result["reused_verified_bytes"] == 0
    assert result["hashed_bytes"] == sum(map(len, source_bytes.values()))
    assert set(hashed) == set(export_paths)
    refreshed = json.loads(cache.read_text())
    assert str(generation / "plan.json") not in refreshed["controls"]
    assert str(generation / cold.MANIFEST) in refreshed["controls"]
    assert json.loads((generation / retention.RECEIPT).read_text())["phase"] == "complete"
    cold.verify_archived_generation(generation)


@pytest.mark.parametrize("change", ["saved_control_hash", "changed_control"])
def test_stale_control_cache_rechecks_full_source(tmp_path, monkeypatch, change):
    root, runs, _ = make_populated_root(tmp_path)
    cache = cache_before_intent(root, runs[0], monkeypatch)
    saved = json.loads(cache.read_text())
    if change == "saved_control_hash":
        saved["controls"][next(iter(saved["controls"]))]["sha256"] = "0" * 64
        write_json(cache, saved)
    else:
        outcome = runs[0] / "outcome.json"
        value = json.loads(outcome.read_text())
        value["finished_at"] = "2024-09-01T00:01:00+00:00"
        write_json(outcome, value)
    size = (runs[0] / "publication_snapshot.sqlite3").stat().st_size
    result = execute(root)
    assert result["reused_verified_bytes"] == 0
    assert result["hashed_bytes"] == size
    assert result["removed_groups"] == 1


def test_real_intent_rejects_changed_controls_even_with_valid_cache(tmp_path, monkeypatch):
    root, runs, _ = make_populated_root(tmp_path)
    receipt = runs[0] / retention.RECEIPT
    original_seal = retention._seal_json

    def stop_after_intent(path, value):
        original_seal(path, value)
        if path == receipt:
            raise TimeoutError("defer after durable intent before unlink")

    with monkeypatch.context() as patch:
        patch.setattr(retention, "_seal_json", stop_after_intent)
        assert execute(root)["deadline_deferred"]
    assert json.loads(receipt.read_text())["phase"] == "intent"
    cache = runs[0] / retention.VALIDATION
    cached_bytes = cache.read_bytes()
    outcome = runs[0] / "outcome.json"
    value = json.loads(outcome.read_text())
    value["finished_at"] = "2024-09-01T00:01:00+00:00"
    write_json(outcome, value)
    with pytest.raises(ValueError, match="control receipt changed"):
        execute(root)
    assert cache.read_bytes() == cached_bytes
    assert (runs[0] / "publication_snapshot.sqlite3").exists()
