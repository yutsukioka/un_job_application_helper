"""Cold publication evidence remains exact and recoverable without touching live data."""

from __future__ import annotations

import fcntl
import hashlib
import json
from pathlib import Path

import pytest

from jobagg import storage_cold_archive as cold


IDS = [f"{number:032x}" for number in range(1, 5)]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _save(path: Path, value: dict) -> bytes:
    data = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


@pytest.fixture
def case(tmp_path):
    state = tmp_path / "publication"
    output = tmp_path / "output"
    output.mkdir()
    lock = tmp_path / "manual-fetch-owner.lock"
    lock.touch()
    dispatcher = tmp_path / "dispatcher"
    (dispatcher / "runs").mkdir(parents=True)
    originals = {}
    common_beforeimage = b'{"old":"exact retained body and attachment metadata"}\n'
    for number, identifier in enumerate(IDS, 1):
        root = state / "generations" / identifier
        before = root / "beforeimages" / "old.json"
        before.parent.mkdir(parents=True)
        before.write_bytes(common_beforeimage)
        plan = {
            "generation_id": identifier,
            "generation_root": str(root),
            "changes": [{"destinations": [{
                "beforeimage_path": str(before),
                "beforeimage_sha256": _sha(common_beforeimage),
            }]}],
            "listing_frames": [],
        }
        plan_bytes = _save(root / "plan.json", plan)
        _save(root / "result.json", {
            "state": "complete", "status": "published", "database_transactions_complete": True,
            "generation_id": identifier, "plan_path": str(root / "plan.json"),
            "plan_sha256": _sha(plan_bytes), "completed_at": f"2026-09-{number:02d}T00:00:00+00:00",
        })
        (root / "exports.json").write_bytes(b'{"historical_receipt":true}\n')
        (root / "export-checkpoints.json").write_bytes(b'{"entries":{}}\n')
        originals[identifier] = {"plan.json": plan_bytes, "beforeimages/old.json": common_beforeimage}
    _save(output / ".jobagg-publication-state.json", {
        "state": "complete", "status": "published", "database_transactions_complete": True,
        "generation_id": IDS[-1]
    })
    return state, output, lock, dispatcher, originals


def test_preview_archive_deduplicates_and_restores_exact_bytes(case):
    state, output, lock, dispatcher, originals = case
    before = {path: path.read_bytes() for path in state.rglob("*") if path.is_file()}
    preview = cold.archive_completed(state, output, lock, dispatcher_state_dir=dispatcher)
    assert preview["eligible_generations"] == IDS[:1]
    assert preview["remaining_eligible_generations"] == 1
    assert preview["protected_generation_ids"] == IDS[2:]
    assert before == {path: path.read_bytes() for path in state.rglob("*") if path.is_file()}

    result = cold.archive_completed(state, output, lock, dispatcher_state_dir=dispatcher, execute=True, max_generations=2)
    assert [item["generation_id"] for item in result["archived"]] == IDS[:2]
    for identifier in IDS[:2]:
        generation = state / "generations" / identifier
        assert not (generation / "plan.json").exists()
        assert not (generation / "beforeimages" / "old.json").exists()
        assert (generation / "result.json").exists()
        assert (generation / "exports.json").read_bytes() == b'{"historical_receipt":true}\n'
        assert (generation / "export-checkpoints.json").read_bytes() == b'{"entries":{}}\n'
        manifest = cold.verify_archived_generation(generation)
        assert set(manifest["objects"]) == set(originals[identifier])
        assert cold.plan_available(generation, _sha(originals[identifier]["plan.json"]))
    objects = list((state / "cold_archive" / "objects").rglob("*.json.gz"))
    assert len(objects) == 3  # Two unique plans, one shared beforeimage.
    for identifier in IDS[2:]:
        generation = state / "generations" / identifier
        assert (generation / "plan.json").read_bytes() == originals[identifier]["plan.json"]
        assert not (generation / cold.MANIFEST).exists()

    restore_preview = cold.restore_generation(state, output, lock, IDS[0])
    assert restore_preview == {"generation_id": IDS[0], "files": 2, "restored": False}
    assert not (state / "generations" / IDS[0] / "plan.json").exists()
    restored = cold.restore_generation(state, output, lock, IDS[0], execute=True)
    assert restored["restored"]
    generation = state / "generations" / IDS[0]
    for relative, data in originals[IDS[0]].items():
        assert (generation / relative).read_bytes() == data
    assert not (generation / cold.MANIFEST).exists()


def test_unfinished_generation_and_open_gate_prevent_archival(case):
    state, output, lock, dispatcher, originals = case
    unfinished = state / "generations" / ("f" * 32)
    unfinished.mkdir()
    (unfinished / "plan.json").write_bytes(b"unfinished plan")
    preview = cold.archive_completed(state, output, lock, dispatcher_state_dir=dispatcher)
    assert next(row for row in preview["generations"] if row["generation_id"] == "f" * 32)["reason"] == "unresolved_or_unverified"
    cold.archive_completed(state, output, lock, dispatcher_state_dir=dispatcher, execute=True)
    assert (unfinished / "plan.json").read_bytes() == b"unfinished plan"

    _save(output / ".jobagg-publication-state.json", {
        "state": "publishing", "status": "incomplete", "generation_id": IDS[-1]
    })
    with pytest.raises(ValueError, match="unresolved"):
        cold.archive_completed(state, output, lock, dispatcher_state_dir=dispatcher, execute=True)
    assert (state / "generations" / IDS[-1] / "plan.json").read_bytes() == originals[IDS[-1]]["plan.json"]


def test_rejects_corrupt_archive_without_restoring_any_source(case):
    state, output, lock, dispatcher, _ = case
    cold.archive_completed(state, output, lock, dispatcher_state_dir=dispatcher, execute=True)
    generation = state / "generations" / IDS[0]
    manifest = cold.verify_archived_generation(generation)
    object_path = Path(manifest["objects"]["plan.json"]["object_path"])
    object_path.write_bytes(b"corrupt compressed bytes")
    with pytest.raises(ValueError, match="Compressed object differs"):
        cold.restore_generation(state, output, lock, IDS[0], execute=True)
    assert not (generation / "plan.json").exists()
    assert (generation / cold.MANIFEST).exists()


def test_archive_resumes_after_manifest_seal_and_partial_removal(case, monkeypatch):
    state, output, lock, dispatcher, originals = case
    original_remove = cold._remove_verified_sources
    interrupted = False

    def fail_once(generation, manifest, *, deadline_at=None):
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            (generation / "plan.json").unlink()
            raise RuntimeError("simulated crash after first unlink")
        return original_remove(generation, manifest, deadline_at=deadline_at)

    monkeypatch.setattr(cold, "_remove_verified_sources", fail_once)
    with pytest.raises(RuntimeError, match="simulated crash"):
        cold.archive_completed(state, output, lock, dispatcher_state_dir=dispatcher, execute=True)
    first = state / "generations" / IDS[0]
    assert (first / cold.MANIFEST).exists()
    assert not (first / "plan.json").exists()
    assert cold.preview_archive(state, output, dispatcher_state_dir=dispatcher)["eligible_generations"] == IDS[:1]
    cold.archive_completed(state, output, lock, dispatcher_state_dir=dispatcher, execute=True)
    assert not (first / "beforeimages" / "old.json").exists()
    assert cold.plan_available(first, _sha(originals[IDS[0]]["plan.json"]))


def test_shared_owner_lock_and_symlinks_block_execution(case):
    state, output, lock, dispatcher, _ = case
    with lock.open("r+") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="already held"):
            cold.archive_completed(state, output, lock, dispatcher_state_dir=dispatcher, execute=True)
    first = state / "generations" / IDS[0]
    beforeimage = first / "beforeimages" / "old.json"
    beforeimage.unlink()
    beforeimage.symlink_to(first / "plan.json")
    with pytest.raises(ValueError, match="missing or linked"):
        cold.archive_completed(state, output, lock, dispatcher_state_dir=dispatcher, execute=True)
    assert (first / "plan.json").exists()
    assert not (first / cold.MANIFEST).exists()


def test_archived_object_parent_cannot_be_replaced_with_symlink(case):
    state, output, lock, dispatcher, _ = case
    cold.archive_completed(state, output, lock, dispatcher_state_dir=dispatcher, execute=True)
    first = state / "generations" / IDS[0]
    objects = state / "cold_archive" / "objects"
    alternate = objects.with_name("objects-moved")
    objects.rename(alternate)
    objects.symlink_to(alternate, target_is_directory=True)
    with pytest.raises(ValueError, match="directory is linked"):
        cold.verify_archived_generation(first)


def test_dispatcher_recovery_binding_keeps_old_generation_hot(case):
    state, output, lock, dispatcher, originals = case
    run = dispatcher / "runs" / "recovery-run"
    run.mkdir()
    _save(run / "publication_generation_binding.json", {"generation_id": IDS[0]})
    preview = cold.preview_archive(state, output, dispatcher_state_dir=dispatcher)
    assert preview["dispatcher_recovery_generation_ids"] == [IDS[0]]
    assert preview["eligible_generations"] == [IDS[1]]
    cold.archive_completed(state, output, lock, dispatcher_state_dir=dispatcher, execute=True)
    assert (state / "generations" / IDS[0] / "plan.json").read_bytes() == originals[IDS[0]]["plan.json"]
    assert not (state / "generations" / IDS[1] / "plan.json").exists()


def test_dispatcher_no_write_proof_keeps_referenced_plan_hot(case):
    state, output, lock, dispatcher, originals = case
    run = dispatcher / "runs" / "resolved-unstarted-run"
    _save(run / "publication_no_write_resolution.json", {
        "proof": {"prior_plan": {
            "path": str(state / "generations" / IDS[0] / "plan.json"),
            "sha256": _sha(originals[IDS[0]]["plan.json"]),
        }},
    })
    preview = cold.preview_archive(state, output, dispatcher_state_dir=dispatcher)
    assert preview["dispatcher_recovery_generation_ids"] == [IDS[0]]
    assert preview["eligible_generations"] == [IDS[1]]
    cold.archive_completed(state, output, lock, dispatcher_state_dir=dispatcher, execute=True)
    plan = state / "generations" / IDS[0] / "plan.json"
    assert plan.read_bytes() == originals[IDS[0]]["plan.json"]
    assert not (state / "generations" / IDS[1] / "plan.json").exists()


@pytest.mark.parametrize("prior_path", ["generations/{id}/plan.json", "/other/generations/{id}/plan.json"])
def test_dispatcher_no_write_proof_rejects_unbound_plan_path(case, prior_path):
    state, output, _, dispatcher, originals = case
    _save(dispatcher / "runs" / "unstarted" / "publication_no_write_resolution.json", {
        "proof": {"prior_plan": {
            "path": prior_path.format(id=IDS[0]),
            "sha256": _sha(originals[IDS[0]]["plan.json"]),
        }},
    })
    with pytest.raises(ValueError, match="prior plan is outside publication state or invalid"):
        cold.preview_archive(state, output, dispatcher_state_dir=dispatcher)
    assert (state / "generations" / IDS[0] / "plan.json").exists()


def test_export_retention_intent_keeps_plan_hot_until_replay(case):
    state, output, lock, dispatcher, originals = case
    receipt = state / "generations" / IDS[0] / cold.EXPORT_RETENTION_RECEIPT
    _save(receipt, {"phase": "intent"})
    preview = cold.preview_archive(state, output, dispatcher_state_dir=dispatcher)
    assert preview["eligible_generations"] == [IDS[1]]
    cold.archive_completed(state, output, lock, dispatcher_state_dir=dispatcher, execute=True)
    assert (state / "generations" / IDS[0] / "plan.json").read_bytes() == originals[IDS[0]]["plan.json"]
    _save(receipt, {"phase": "complete"})
    assert cold.preview_archive(state, output, dispatcher_state_dir=dispatcher)["eligible_generations"] == [IDS[0]]


def test_preview_skips_content_hashing_and_execution_requires_dispatcher_scan(case, monkeypatch):
    state, output, lock, dispatcher, _ = case
    monkeypatch.setattr(cold, "_digest", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("preview hashed content")))
    assert cold.preview_archive(state, output, dispatcher_state_dir=dispatcher)["eligible_generations"] == [IDS[0]]
    assert cold.preview_archive(state, output)["eligible_generations"] == []
    with pytest.raises(ValueError, match="Dispatcher state directory is required"):
        cold.archive_completed(state, output, lock, execute=True)
