"""Retention deletes only verified, derived copies after a durable intent."""

import fcntl
import gzip
import hashlib
import json
from datetime import UTC, datetime

import pytest

from jobagg import storage_retention as retention


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n")


def make_root(tmp_path):
    root = tmp_path / "jobagg"
    for path in (
        root / "deterministic-dispatcher-proposal" / "runs",
        root / "deterministic-live-publication" / "generations",
        root / "output",
        root / "remediation",
        root / "deterministic-worker-generation001" / "captures",
        root / "deterministic-worker-generation001" / "blobs",
        root / "complete_fetch_20260910",
    ):
        path.mkdir(parents=True)
    (root / "remediation" / "manual-fetch-owner.lock").touch()
    for path in (
        root / "output" / "all_jobs.sqlite3",
        root / "output" / "all_jobs_current.json",
        root / "deterministic-worker-generation001" / "jobs.sqlite3",
        root / "deterministic-worker-generation001" / "captures" / "old.body.gz",
        root / "deterministic-worker-generation001" / "blobs" / "old-document",
        root / "complete_fetch_20260910" / "old-attachment.pdf",
    ):
        path.write_bytes(b"do not prune")
    return root


def make_run(root, index, *, result_status="noop", finished_at=None):
    run = root / "deterministic-dispatcher-proposal" / "runs" / f"run-{index}"
    run.mkdir()
    snapshot_path = run / "publication_snapshot.sqlite3"
    snapshot_path.write_bytes((f"projection {index}\n" * 10).encode())
    acceptance = run / "acceptance.json"
    acceptance.write_text('{"accepted":true}\n')
    snapshot = {
        "schema_version": 1, "kind": "sqlite_publication_projection",
        "path": str(snapshot_path), "sha256": digest(snapshot_path),
        "size_bytes": snapshot_path.stat().st_size,
        "worker_acceptance_sha256": digest(acceptance),
    }
    request_path = run / "publication_request.json"
    write_json(request_path, {
        "run_id": run.name, "publication_snapshot": snapshot,
        "worker_database_files": [{"path": str(snapshot_path), "sha256": digest(snapshot_path)}],
        "worker_acceptance_path": str(acceptance),
        "worker_acceptance_sha256": digest(acceptance),
    })
    result_path = run / "publication_result.json"
    write_json(result_path, {
        "run_id": run.name, "status": result_status, "publication_snapshot": snapshot,
        "publication_request_sha256": digest(request_path),
        "publication": {} if result_status == "noop" else {
            "generation_id": f"{index:032x}", "state": "complete",
            "status": "published", "database_transactions_complete": True,
        },
    })
    write_json(run / "outcome.json", {
        "run_id": run.name, "status": "incomplete", "publication_status": result_status,
        "publication_recovery_allowed": False,
        "publication_result_sha256": digest(result_path),
        "finished_at": finished_at or f"2024-09-{index:02d}T00:00:00+00:00",
    })
    return run


def make_generation(root, index):
    generation = root / "deterministic-live-publication" / "generations" / f"{index:032x}"
    generation.mkdir()
    output = root / "output"
    export = generation / "exports-v2" / "all_jobs_current.json"
    backup = generation / "before_exports" / "all_jobs_current.json"
    export.parent.mkdir()
    backup.parent.mkdir()
    export.write_bytes(f"new export {index}".encode())
    backup.write_bytes(f"old export {index}".encode())
    (generation / "beforeimages").mkdir()
    (generation / "beforeimages" / "row.json").write_text("historical beforeimage")
    target = output / "all_jobs_current.json"
    plan = generation / "plan.json"
    write_json(plan, {"generation_id": generation.name, "generation_root": str(generation)})
    write_json(generation / "result.json", {
        "generation_id": generation.name, "state": "complete", "status": "published",
        "database_transactions_complete": True, "plan_sha256": digest(plan),
        "completed_at": f"2026-09-{index:02d}T00:05:00+00:00", "exports": 1,
    })
    write_json(generation / "exports.json", {str(target): {"sha256": digest(export)}})
    write_json(generation / "export-checkpoints.json", {
        "schema_version": 1, "generation_id": generation.name,
        "entries": {str(target): {
            "phase": "replaced_verified", "prepared": str(export),
            "new": {"sha256": digest(export), "size": export.stat().st_size},
            "backup_path": str(backup), "backup_status": "verified",
            "old": {"sha256": digest(backup), "size": backup.stat().st_size},
        }},
    })
    return generation


def make_populated_root(tmp_path):
    root = make_root(tmp_path)
    runs = [make_run(root, index) for index in range(1, 4)]
    generations = [make_generation(root, index) for index in range(1, 4)]
    write_json(root / "output" / ".jobagg-publication-state.json", {
        "state": "complete", "status": "published", "database_transactions_complete": True,
        "generation_id": generations[-1].name,
    })
    return root, runs, generations


def test_preview_is_read_only_and_selects_both_classes(tmp_path):
    root, runs, generations = make_populated_root(tmp_path)
    result = retention.prune_completed(root, max_groups=2)
    assert result["mode"] == "preview"
    assert result["eligible_by_kind"]["run_snapshot"]["groups"] == 1
    assert result["eligible_by_kind"]["generation_exports"]["groups"] == 1
    assert {item["kind"] for item in result["selected"]} == {
        "run_snapshot", "generation_exports"
    }
    assert result["selected_estimated_bytes"] > 0
    assert (runs[0] / "publication_snapshot.sqlite3").exists()
    assert (generations[0] / "exports-v2" / "all_jobs_current.json").exists()
    assert not (runs[0] / retention.RECEIPT).exists()


@pytest.mark.parametrize("scanner", ["_run_group", "_generation_group"])
def test_deadline_stops_metadata_scan_before_any_retirement(tmp_path, monkeypatch, scanner):
    root, runs, generations = make_populated_root(tmp_path)
    clock = [1.0]
    monkeypatch.setattr(retention.time, "monotonic", lambda: clock[0])
    original = getattr(retention, scanner)
    visited = []

    def expire_after_first_group(path, *, deadline_at=None):
        visited.append(path)
        result = original(path, deadline_at=deadline_at)
        clock[0] = 11.0
        return result

    monkeypatch.setattr(retention, scanner, expire_after_first_group)
    lock = root / "remediation" / "manual-fetch-owner.lock"
    with lock.open("a+") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX)
        with pytest.raises(TimeoutError, match="deadline exhausted"):
            retention.prune_completed(
                root, max_groups=2, execute=True, owner_fd=owner.fileno(),
                shared_lock=lock, deadline_at=10.0,
            )
    assert len(visited) == 1
    assert not list(root.rglob(retention.RECEIPT))
    assert (runs[0] / "publication_snapshot.sqlite3").exists()
    assert (generations[0] / "exports-v2" / "all_jobs_current.json").exists()


def test_execute_requires_same_owner_and_preserves_fetched_data(tmp_path):
    root, runs, generations = make_populated_root(tmp_path)
    lock = root / "remediation" / "manual-fetch-owner.lock"
    with pytest.raises(ValueError, match="owner"):
        retention.prune_completed(root, max_groups=2, execute=True)
    with lock.open("a+") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX)
        result = retention.prune_completed(
            root, max_groups=2, execute=True,
            owner_fd=owner.fileno(), shared_lock=lock,
        )
    assert result["removed_groups"] == 2
    assert result["deleted_bytes"] == result["selected_estimated_bytes"]
    assert not (runs[0] / "publication_snapshot.sqlite3").exists()
    assert not (generations[0] / "exports-v2" / "all_jobs_current.json").exists()
    assert not (generations[0] / "before_exports" / "all_jobs_current.json").exists()
    assert json.loads((runs[0] / retention.RECEIPT).read_text())["phase"] == "complete"
    assert json.loads((generations[0] / retention.RECEIPT).read_text())["phase"] == "complete"
    for path in (
        root / "output" / "all_jobs.sqlite3",
        root / "output" / "all_jobs_current.json",
        root / "deterministic-worker-generation001" / "jobs.sqlite3",
        root / "deterministic-worker-generation001" / "captures" / "old.body.gz",
        root / "deterministic-worker-generation001" / "blobs" / "old-document",
        root / "complete_fetch_20260910" / "old-attachment.pdf",
        generations[0] / "plan.json",
        generations[0] / "beforeimages" / "row.json",
    ):
        assert path.read_bytes()
    assert (runs[1] / "publication_snapshot.sqlite3").exists()
    assert (generations[1] / "exports-v2" / "all_jobs_current.json").exists()
    assert retention.prune_completed(root, max_groups=2)["eligible_groups"] == 0


def test_corruption_and_unknown_export_are_rejected(tmp_path):
    root, runs, generations = make_populated_root(tmp_path)
    (runs[0] / "publication_snapshot.sqlite3").write_bytes(b"changed")
    lock = root / "remediation" / "manual-fetch-owner.lock"
    with lock.open("a+") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX)
        with pytest.raises(ValueError, match="source bytes differ"):
            retention.prune_completed(root, max_groups=1, execute=True,
                                      owner_fd=owner.fileno(), shared_lock=lock)
    assert not (runs[0] / retention.RECEIPT).exists()
    (runs[0] / "publication_snapshot.sqlite3").write_bytes(("projection 1\n" * 10).encode())
    (generations[0] / "exports-v2" / "unknown.txt").write_text("unknown")
    preview = retention.prune_completed(root, max_groups=2)
    assert preview["eligible_by_kind"]["generation_exports"]["groups"] == 0
    assert (generations[0] / "exports-v2" / "unknown.txt").read_text() == "unknown"


def test_symlink_is_rejected(tmp_path):
    root, runs, _ = make_populated_root(tmp_path)
    path = runs[0] / "publication_snapshot.sqlite3"
    path.unlink()
    path.symlink_to(root / "output" / "all_jobs.sqlite3")
    lock = root / "remediation" / "manual-fetch-owner.lock"
    with lock.open("a+") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX)
        with pytest.raises(ValueError, match="not regular"):
            retention.prune_completed(root, max_groups=1, execute=True,
                                      owner_fd=owner.fileno(), shared_lock=lock)
    assert (root / "output" / "all_jobs.sqlite3").read_bytes() == b"do not prune"


def test_unresolved_live_gate_blocks_retirement(tmp_path):
    root, runs, generations = make_populated_root(tmp_path)
    gate = root / "output" / ".jobagg-publication-state.json"
    write_json(gate, {
        "state": "exporting", "status": "publication_pending",
        "database_transactions_complete": True, "generation_id": generations[-1].name,
    })
    with pytest.raises(ValueError, match="complete published live gate"):
        retention.prune_completed(root, max_groups=2)
    lock = root / "remediation" / "manual-fetch-owner.lock"
    with lock.open("a+") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX)
        with pytest.raises(ValueError, match="complete published live gate"):
            retention.prune_completed(root, max_groups=2, execute=True,
                                      owner_fd=owner.fileno(), shared_lock=lock)
    assert (runs[0] / "publication_snapshot.sqlite3").exists()
    assert (generations[0] / "exports-v2" / "all_jobs_current.json").exists()


def test_durable_intent_replays_after_partial_unlink(tmp_path, monkeypatch):
    root, runs, generations = make_populated_root(tmp_path)
    # Retire the run first so the next selected group is the generation.
    lock = root / "remediation" / "manual-fetch-owner.lock"
    with lock.open("a+") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX)
        retention.prune_completed(root, max_groups=1, execute=True,
                                  owner_fd=owner.fileno(), shared_lock=lock)
        original = retention._sync_dir

        def interrupt_after_first_unlink(path):
            if not (generations[0] / "exports-v2" / "all_jobs_current.json").exists():
                raise TimeoutError("test deadline")
            return original(path)

        monkeypatch.setattr(retention, "_sync_dir", interrupt_after_first_unlink)
        first = retention.prune_completed(root, max_groups=1, execute=True,
                                          owner_fd=owner.fileno(), shared_lock=lock)
        assert first["deadline_deferred"]
        assert json.loads((generations[0] / retention.RECEIPT).read_text())["phase"] == "intent"
        monkeypatch.setattr(retention, "_sync_dir", original)
        second = retention.prune_completed(root, max_groups=1, execute=True,
                                           owner_fd=owner.fileno(), shared_lock=lock)
    assert second["removed_groups"] == 1
    assert json.loads((generations[0] / retention.RECEIPT).read_text())["phase"] == "complete"
    assert not (generations[0] / "before_exports" / "all_jobs_current.json").exists()
    assert not (runs[1] / retention.RECEIPT).exists()


def test_published_recovery_requires_matching_generation_result(tmp_path):
    root, runs, generations = make_populated_root(tmp_path)
    run = runs[0]
    original_request = json.loads((run / "publication_request.json").read_text())
    write_json(run / "outcome.json", {
        "run_id": run.name, "status": "process_failure",
        "publication_status": "unknown_requires_review_no_replay",
        "publication_recovery_allowed": True,
        "finished_at": "2026-09-01T00:00:00+00:00",
    })
    write_json(run / "state.json", {
        "run_id": run.name, "status": "incomplete", "publication_status": "published",
        "publication_recovery_allowed": False,
        "finished_at": "2024-09-01T00:10:00+00:00",
    })
    binding = {"generation_id": generations[0].name}
    write_json(run / "publication_generation_binding.json", binding)
    recovery_request_path = run / "publication_recovery" / "attempt" / "request.json"
    recovery_request = {
        **original_request, "recover_publication": True,
        "recovery_generation_binding": binding,
    }
    write_json(recovery_request_path, recovery_request)
    recovery_result_path = recovery_request_path.parent / "result.json"
    write_json(recovery_result_path, {
        **recovery_request, "status": "published",
        "publication_request_sha256": digest(recovery_request_path),
        "publication": json.loads((generations[0] / "result.json").read_text()),
    })
    write_json(run / "publication_resolution.json", {
        "run_id": run.name,
        "previous_outcome_sha256": digest(run / "outcome.json"),
        "request": {"path": str(recovery_request_path.relative_to(run)),
                    "sha256": digest(recovery_request_path)},
        "result": {"path": str(recovery_result_path.relative_to(run)),
                   "sha256": digest(recovery_result_path)},
    })
    assert retention.prune_completed(root, max_groups=2)["eligible_by_kind"]["run_snapshot"]["groups"] == 1
    (generations[0] / "result.json").unlink()
    assert retention.prune_completed(root, max_groups=2)["eligible_by_kind"]["run_snapshot"]["groups"] == 0


def test_generation_with_verified_cold_plan_remains_eligible(tmp_path):
    root, _, generations = make_populated_root(tmp_path)
    generation = generations[0]
    plan = generation / "plan.json"
    raw = plan.read_bytes()
    original_sha = digest(plan)
    cold_root = root / "deterministic-live-publication" / "cold_archive"
    obj = cold_root / "objects" / original_sha[:2] / (original_sha + ".json.gz")
    obj.parent.mkdir(parents=True)
    with obj.open("wb") as stream:
        with gzip.GzipFile(fileobj=stream, mode="wb", filename="", mtime=0) as compressed:
            compressed.write(raw)
    write_json(generation / "storage-cold-archive.json", {
        "schema_version": 1, "generation_id": generation.name,
        "archive_root": str(cold_root),
        "objects": {"plan.json": {
            "sha256": original_sha, "size_bytes": len(raw),
            "object_sha256": digest(obj), "object_size_bytes": obj.stat().st_size,
            "object_path": str(obj),
        }},
    })
    plan.unlink()
    preview = retention.prune_completed(root, max_groups=2)
    assert preview["eligible_by_kind"]["generation_exports"]["groups"] == 1
    lock = root / "remediation" / "manual-fetch-owner.lock"
    with lock.open("a+") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX)
        result = retention.prune_completed(root, max_groups=2, execute=True,
                                           owner_fd=owner.fileno(), shared_lock=lock)
    assert result["removed_groups"] == 2
    assert obj.exists()
    assert (generation / "storage-cold-archive.json").exists()


def test_daily_and_monthly_debug_checkpoints_are_bounded(tmp_path):
    root = make_root(tmp_path)
    dates = [
        "2025-08-01T01:00:00+00:00",  # older than the 12-month monthly window
        "2026-08-10T01:00:00+00:00",  # earlier August copy is eligible
        "2026-08-25T01:00:00+00:00",  # latest August copy is retained
        "2026-09-21T01:00:00+00:00",  # earlier daily copy is eligible
        "2026-09-21T02:00:00+00:00",  # latest September 21 copy is retained
        "2026-09-27T01:00:00+00:00",  # earlier daily copy is eligible
        "2026-09-27T02:00:00+00:00",  # newest two and daily retained
        "2026-09-28T01:00:00+00:00",  # newest two and daily retained
    ]
    runs = [make_run(root, index, finished_at=date) for index, date in enumerate(dates, 1)]
    result = retention.prune_completed(
        root, max_groups=10, as_of=datetime(2026, 9, 28, 12, tzinfo=UTC)
    )
    selected = {item["id"] for item in result["selected"]}
    assert selected == {runs[index].name for index in (0, 1, 3, 5)}
    assert result["protected_run_checkpoints"]["newest_two_or_more"] == 2
    assert result["protected_run_checkpoints"]["daily_30_days"] == 3
    assert result["protected_run_checkpoints"]["monthly_12_months"] == 2
    assert result["skipped_protected"]["run_snapshot"] == 4
    assert result["eligible_by_kind"]["run_snapshot"]["groups"] == 4
