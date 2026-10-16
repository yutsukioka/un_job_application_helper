"""Synthetic terminal retirement: owner lock, exact authority, restart, reachability."""
import fcntl
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_storage_retention import digest, make_generation, make_root, write_json

from jobagg import publication_artifacts
from jobagg import terminal_history as history


class Budget:
    def __init__(self):
        self.consumed = 0
        self.events = []

    def category_for(self, path):
        return "publication"

    def reserve(self, category, maximum, *, path):
        self.events.append(("reserved", str(path), maximum))
        return self

    def consume(self, count):
        self.consumed += count
        self.events.append(("consumed", count))

    def settle(self):
        self.events.append(("settled",))


@pytest.fixture
def world(tmp_path):
    root = make_root(tmp_path)
    generations = [make_generation(root, n) for n in (1, 2, 3)]
    write_json(root / "output" / ".jobagg-publication-state.json", {
        "state": "complete", "status": "published", "database_transactions_complete": True,
        "generation_id": generations[-1].name,
    })
    return root, generations


def backup(root, subject):
    files = {}
    paths = [path for path in subject.rglob("*") if path.is_file()]
    for sidecar in subject.rglob("*.publication.json"):
        paths.extend(publication_artifacts.referenced_objects(Path(str(sidecar)[:-len(".publication.json")])))
    for path in paths:
        if path.is_file():
            files[str(path)] = {"source_sha256": digest(path), "destination_sha256": digest(path),
                                "size_bytes": path.stat().st_size,
                                "destination_relative_path": "backup/" + str(path.relative_to(root))}
    path = root / "proof" / (subject.name + ".json")
    write_json(path, {"schema_version": 1, "status": "verified",
                      "verification_kind": "source_destination_full_sha256", "backup_id": "test-backup",
                      "backup_root": "/offline-unmounted-never-opened", "manifest_sha256": "a" * 64,
                      "files": files})
    return {"backup_attestation": {"path": str(path.relative_to(root)), "sha256": digest(path)}}


def execute(root, subject, evidence, **kwargs):
    lock = root / "remediation" / "manual-fetch-owner.lock"
    with lock.open("a+") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX)
        return history.retire_subject(root, subject, policy="legacy_backup_retirement", evidence=evidence,
                                      execute=True, owner_fd=owner.fileno(), shared_lock=lock,
                                      budget=Budget(), **kwargs)


def test_legacy_requires_exact_verified_backup_before_any_intent(world):
    root, generations = world
    subject = generations[0]
    evidence = backup(root, subject)
    path = root / evidence["backup_attestation"]["path"]
    data = json.loads(path.read_text())
    data["files"][str(subject / "plan.json")]["destination_sha256"] = "0" * 64
    write_json(path, data)
    evidence["backup_attestation"]["sha256"] = digest(path)
    with pytest.raises(ValueError, match="backup does not cover"):
        execute(root, subject, evidence)
    assert (subject / "plan.json").exists()
    assert not (subject / history.INTENT).exists()


def test_retirement_preserves_current_and_usable_previous(world):
    root, generations = world
    closure = history.operational_closure(root)
    assert closure["current"] == generations[2].name
    assert closure["previous"] == generations[1].name
    for subject in generations[1:]:
        with pytest.raises(ValueError, match="protected operational closure"):
            execute(root, subject, backup(root, subject))
    subject = generations[0]
    assert execute(root, subject, backup(root, subject))["status"] == "retired"
    assert history.classify_history(subject)["availability"] == "backup_only"
    assert not (subject / "plan.json").exists()
    assert (subject / "result.json").exists()
    assert all((g / "plan.json").exists() for g in generations[1:])


def test_partial_retirement_is_not_terminal_and_resumes_exact_intent(world, monkeypatch):
    root, generations = world
    subject = generations[0]
    evidence = backup(root, subject)
    original = Path.unlink
    def crash(path, *args, **kwargs):
        if path == subject / "beforeimages" / "row.json":
            raise OSError("synthetic interruption")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", crash)
    with pytest.raises(OSError, match="synthetic"):
        execute(root, subject, evidence)
    assert history.classify_history(subject)["disposition"] == "retirement_pending"
    assert not (subject / history.RECEIPT).exists()
    monkeypatch.setattr(Path, "unlink", original)
    assert execute(root, subject, evidence)["status"] == "retired"
    assert history.classify_history(subject)["disposition"] == "retired_terminal"


def test_unknown_reappearing_payload_is_not_valid_retired_history(world):
    root, generations = world
    subject = generations[0]
    execute(root, subject, backup(root, subject))
    (subject / "beforeimages" / "new.json").write_text("sole unknown content")
    with pytest.raises(ValueError, match="unclassified payload"):
        history.classify_history(subject)


def test_unresolved_a_does_not_block_independent_b(world):
    root, generations = world
    run = root / "deterministic-dispatcher-proposal" / "runs" / "unresolved"
    write_json(run / "state.json", {"run_id": run.name, "status": "incomplete"})
    write_json(run / "publication_result.json", {"publication": {"generation_id": generations[2].name}})
    closure = history.operational_closure(root)
    assert run.name in closure["runs"]
    assert execute(root, generations[0], backup(root, generations[0]))["status"] == "retired"
    assert (run / "publication_result.json").exists()


def test_future_expiration_uses_committed_semantics_not_backup(world, monkeypatch):
    root, generations = world
    subject = generations[0]
    plan = json.loads((subject / "plan.json").read_text())
    plan["created_at"] = "2026-10-15T00:00:00+00:00"
    write_json(subject / "plan.json", plan)
    result = json.loads((subject / "result.json").read_text())
    result["plan_sha256"] = digest(subject / "plan.json")
    write_json(subject / "result.json", result)
    reference = {"schema": "jobagg-semantic-history-1", "database": str(root / "online.sqlite3"),
                 "coverage_id": "a" * 64}
    write_json(subject / "semantic-coverage.json", reference)
    artifacts = [{"path": str(p), "sha256": digest(p), "size_bytes": p.stat().st_size}
                 for p in (subject / "plan.json", subject / "beforeimages" / "row.json")]
    coverage = {"artifacts": artifacts, "version_ids": ["v1"]}
    calls = []
    def validate(ref, **kwargs):
        calls.append(kwargs)
        assert ref == reference
        return coverage
    monkeypatch.setitem(history._MODULES, "semantic_history", SimpleNamespace(validate_semantic_coverage=validate))
    lock = root / "remediation" / "manual-fetch-owner.lock"
    with lock.open("a+") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX)
        result = history.maintain_history(root, policy={"enabled": True, "policy_epoch": "epoch1",
                  "activated_at": "2026-10-14T00:00:00+00:00", "allowed_roots": [str(root)]},
                  execute=True, owner_fd=owner.fileno(), shared_lock=lock, budget=Budget())
    assert result["selected"][0]["status"] == "retired"
    assert history.classify_history(subject)["availability"] == "expired_unavailable"
    assert calls and all(call["policy_epoch"] == "epoch1" for call in calls)
    coverage["version_ids"] = ["changed"]
    with pytest.raises(ValueError, match="semantic authority changed"):
        history.classify_history(subject)


def test_execute_requires_quota_before_writing(world):
    root, generations = world
    subject = generations[0]
    lock = root / "remediation" / "manual-fetch-owner.lock"
    with lock.open("a+") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX)
        with pytest.raises(ValueError, match="admitted storage budget"):
            history.retire_subject(root, subject, policy="legacy_backup_retirement",
                                   evidence=backup(root, subject), execute=True,
                                   owner_fd=owner.fileno(), shared_lock=lock)
    assert not (subject / history.INTENT).exists()


def chunk_export(generation, directory, content):
    logical = generation / directory / "all_jobs_current.json"
    if logical.exists():
        logical.unlink()
    with publication_artifacts.RecordWriter(logical) as writer:
        writer.write(content)
    return logical


@pytest.mark.parametrize("nested", [False, True])
def test_shared_objects_remain_until_last_sidecar_expires(world, nested):
    root, generations = world
    subject = generations[0]
    logical = subject / "exports-v2" / "all_jobs_current.json"
    content = logical.read_text()
    logical.unlink()
    writer = publication_artifacts.RecordWriter(logical)
    writer.write(content)
    writer.seal()
    # Another unresolved generation owns the same immutable object.
    other = root / "deterministic-live-publication" / "generations" / ("f" * 32)
    other_directory = other / "before_exports_verified" / ("b" * 64) if nested else other / "exports-v2"
    other_directory.mkdir(parents=True)
    second = publication_artifacts.RecordWriter(other_directory / logical.name)
    second.write(content)
    second.seal()
    objects = publication_artifacts.referenced_objects(logical)
    execute(root, subject, backup(root, subject))
    lock = root / "remediation" / "manual-fetch-owner.lock"
    with lock.open("a+") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX)
        result = history.collect_retired_objects(root, subject, execute=True,
                    owner_fd=owner.fileno(), shared_lock=lock, budget=Budget())
    assert result["deleted_bytes"] == 0
    assert all(path.exists() for path in objects)
    assert publication_artifacts.fingerprint(other_directory / logical.name)["sha256"] == hashlib.sha256(content.encode()).hexdigest()


def make_chunked(subject):
    logical = subject / "exports-v2" / "all_jobs_current.json"
    content = logical.read_text()
    logical.unlink()
    writer = publication_artifacts.RecordWriter(logical)
    writer.write(content)
    writer.seal()
    return publication_artifacts.referenced_objects(logical)


def collect(root, subject):
    lock = root / "remediation" / "manual-fetch-owner.lock"
    with lock.open("a+") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX)
        return history.collect_retired_objects(root, subject, execute=True,
                    owner_fd=owner.fileno(), shared_lock=lock, budget=Budget())


def test_legacy_backup_must_cover_shared_objects_before_sidecar_retirement(world):
    root, generations = world
    subject = generations[0]
    objects = make_chunked(subject)
    evidence = backup(root, subject)
    attestation_path = root / evidence["backup_attestation"]["path"]
    attestation = json.loads(attestation_path.read_text())
    del attestation["files"][str(objects[0])]
    write_json(attestation_path, attestation)
    evidence["backup_attestation"]["sha256"] = digest(attestation_path)
    with pytest.raises(ValueError, match="backup does not cover"):
        execute(root, subject, evidence)
    assert objects[0].exists()
    assert not (subject / history.INTENT).exists()


def test_unreferenced_chunk_gc_is_resumable_and_rejects_other_objects(world, monkeypatch):
    root, generations = world
    subject = generations[0]
    objects = make_chunked(subject)
    execute(root, subject, backup(root, subject))
    original = Path.unlink
    def crash(path, *args, **kwargs):
        if path in objects:
            raise OSError("synthetic GC interruption")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", crash)
    with pytest.raises(OSError, match="synthetic GC"):
        collect(root, subject)
    intent_path = subject / "terminal-object-gc.intent.json"
    original_intent = json.loads(intent_path.read_text())
    assert not (subject / "terminal-object-gc.json").exists()
    unrelated = objects[0].parent / hashlib.sha256(b"unknown unrelated bytes").hexdigest()
    unrelated.write_bytes(b"unknown unrelated bytes")
    bad = dict(original_intent)
    proof = history._retention()._digest(unrelated, include_identity=True)
    bad["files"] = [{"path": str(unrelated), **proof}]
    write_json(intent_path, bad)
    monkeypatch.setattr(Path, "unlink", original)
    with pytest.raises(ValueError, match="outside terminal receipt"):
        collect(root, subject)
    assert unrelated.exists() and objects[0].exists()
    write_json(intent_path, original_intent)
    assert collect(root, subject)["deleted_bytes"] == sum(p["size"] for p in original_intent["files"])
    assert not any(path.exists() for path in objects)
    assert unrelated.exists()
    assert collect(root, subject)["status"] == "already_collected"


def test_deadline_never_becomes_a_completed_retirement(world):
    root, generations = world
    subject = generations[0]
    with pytest.raises(TimeoutError):
        execute(root, subject, backup(root, subject), deadline_at=0)
    assert (subject / "plan.json").exists()
    assert not (subject / history.INTENT).exists()


def test_previous_checkpoint_is_usable_and_precedes_current(world):
    root, generations = world
    newer = make_generation(root, 4)
    closure = history.operational_closure(root)
    assert closure["previous"] == generations[1].name
    assert closure["previous"] != newer.name
    # A historical copy retirement cannot promote an empty directory to usable.
    from jobagg import storage_retention
    group = storage_retention._generation_group(generations[1])
    for item in group["files"]:
        (generations[1] / item["path"]).unlink()
    write_json(generations[1] / storage_retention.RECEIPT, {
        "schema_version": 1, "kind": "generation_exports", "group_id": generations[1].name,
        "phase": "complete", "files": group["files"], "controls": {},
    })
    assert history.operational_closure(root)["previous"] == generations[0].name


def test_gc_overlap_uses_exact_prior_completed_collection(world):
    root, generations = world
    fourth = make_generation(root, 4)
    fifth = make_generation(root, 5)
    write_json(root / "output" / ".jobagg-publication-state.json", {
        "state": "complete", "status": "published", "database_transactions_complete": True,
        "generation_id": fifth.name,
    })
    first, second = generations[:2]
    for subject in (first, second):
        logical = subject / "exports-v2" / "all_jobs_current.json"
        logical.write_text("identical shared completed export")
        journal_path = subject / "export-checkpoints.json"
        journal = json.loads(journal_path.read_text())
        target = str(root / "output" / logical.name)
        journal["entries"][target]["new"] = {"sha256": digest(logical), "size": logical.stat().st_size}
        write_json(journal_path, journal)
        write_json(subject / "exports.json", {target: {"sha256": digest(logical)}})
        make_chunked(subject)
    objects = publication_artifacts.referenced_objects(first / "exports-v2" / "all_jobs_current.json")
    evidence = [backup(root, subject) for subject in (first, second)]
    for subject, proof in zip((first, second), evidence):
        execute(root, subject, proof)
    assert collect(root, first)["deleted_bytes"] > 0
    assert collect(root, second)["deleted_bytes"] == 0
    assert not objects[0].exists()
    assert (fourth / "plan.json").exists()
    intent = json.loads((second / "terminal-object-gc.intent.json").read_text())
    assert intent["previous_collections"][0]["object"]["path"] == str(objects[0])


def test_missing_object_without_prior_gc_receipt_fails_closed(world):
    root, generations = world
    subject = generations[0]
    objects = make_chunked(subject)
    execute(root, subject, backup(root, subject))
    objects[0].unlink()
    with pytest.raises(ValueError, match="missing without verified collection"):
        collect(root, subject)
    assert not (subject / "terminal-object-gc.intent.json").exists()


def test_pending_retirement_a_allows_independent_b_and_copy_maintenance(world, monkeypatch):
    root, generations = world
    make_generation(root, 4)
    current = make_generation(root, 5)
    write_json(root / "output" / ".jobagg-publication-state.json", {
        "state": "complete", "status": "published", "database_transactions_complete": True,
        "generation_id": current.name,
    })
    first, second = generations[:2]
    evidence = backup(root, first)
    original = Path.unlink
    def crash(path, *args, **kwargs):
        if path == first / "beforeimages" / "row.json":
            raise OSError("synthetic pending A")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", crash)
    with pytest.raises(OSError, match="pending A"):
        execute(root, first, evidence)
    monkeypatch.setattr(Path, "unlink", original)
    assert execute(root, second, backup(root, second))["status"] == "retired"
    from jobagg import storage_cold_archive, storage_retention
    closure = history.operational_closure(root)
    preview = storage_retention.prune_completed(root, protection=closure)
    assert preview["eligible_by_kind"]["generation_exports"]["groups"] == 1
    cold = storage_cold_archive.preview_archive(
        root / "deterministic-live-publication", root / "output",
        dispatcher_state_dir=root / "deterministic-dispatcher-proposal")
    assert any(item["generation_id"] == first.name and item["reason"] == "retirement_pending"
               for item in cold["generations"])
    assert history.classify_history(first)["disposition"] == "retirement_pending"


def test_gc_restart_defers_objects_referenced_by_a_new_generation(world, monkeypatch):
    root, generations = world
    subject = generations[0]
    logical = subject / "exports-v2" / "all_jobs_current.json"
    content = logical.read_text()
    objects = make_chunked(subject)
    execute(root, subject, backup(root, subject))
    original = Path.unlink
    def crash(path, *args, **kwargs):
        if path in objects:
            raise OSError("synthetic pause before GC")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", crash)
    with pytest.raises(OSError, match="pause before GC"):
        collect(root, subject)
    monkeypatch.setattr(Path, "unlink", original)
    current_attempt = root / "deterministic-live-publication" / "generations" / ("e" * 32)
    (current_attempt / "exports-v2").mkdir(parents=True)
    writer = publication_artifacts.RecordWriter(current_attempt / "exports-v2" / logical.name)
    writer.write(content)
    writer.seal()
    outcome = collect(root, subject)
    assert outcome["status"] == "deferred" and outcome["reason"] == "object_acquired_live_reference"
    assert all(path.exists() for path in objects)
    assert not (subject / "terminal-object-gc.json").exists()


def test_old_copy_intent_cannot_override_new_evidence_protection(world, monkeypatch):
    from jobagg import storage_retention
    root, generations = world
    subject = generations[0]
    sync = storage_retention._sync_dir
    def interrupt(directory):
        if directory == subject / "exports-v2":
            raise OSError("synthetic copy retirement crash")
        return sync(directory)
    monkeypatch.setattr(storage_retention, "_sync_dir", interrupt)
    lock = root / "remediation" / "manual-fetch-owner.lock"
    with lock.open("a+") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX)
        with pytest.raises(OSError, match="copy retirement crash"):
            storage_retention.prune_completed(root, execute=True, owner_fd=owner.fileno(), shared_lock=lock)
        monkeypatch.setattr(storage_retention, "_sync_dir", sync)
        result = storage_retention.prune_completed(root, execute=True, owner_fd=owner.fileno(),
                    shared_lock=lock, selected_generations=[subject.name])
    assert result["deleted_bytes"] == 0
    assert (subject / "before_exports" / "all_jobs_current.json").exists()
