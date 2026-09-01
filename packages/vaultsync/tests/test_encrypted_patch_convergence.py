from __future__ import annotations

import hashlib
import json
import multiprocessing
import time
from pathlib import Path

import pytest

from vaultsync.sync_queue import (
    DurableEncryptedConvergentReplica,
    DurableEncryptedPatchCollection,
    EncryptedPatchOperation,
    PatchQueueError,
)


VECTOR_ROOT = Path(__file__).resolve().parents[3] / "contracts" / "sync" / "test_vectors"


def _root() -> dict[str, object]:
    value = json.loads(
        (VECTOR_ROOT / "atlasvault_encrypted_patch_convergence_vectors_v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert "test-only" in value["warning"]
    return value


def _operations() -> dict[str, EncryptedPatchOperation]:
    return {
        name: EncryptedPatchOperation.from_dict(operation)
        for name, operation in _root()["operations"].items()
    }


def _queue_key() -> bytes:
    return hashlib.sha256(b"atlasvault-c19-synthetic-replica-queue").digest()


def _authentication_key() -> bytes:
    return hashlib.sha256(
        b"atlasvault-c19-synthetic-snapshot-authentication"
    ).digest()


def _replica(path: Path) -> DurableEncryptedConvergentReplica:
    return DurableEncryptedConvergentReplica(
        path,
        encryption_key=_queue_key(),
        authentication_key=_authentication_key(),
        collection_id="collection_a",
    )


def _queue_then_wait(path: str, ready: str) -> None:
    operations = _operations()
    replica = _replica(Path(path))
    replica.queue_local(operations["base"])
    replica.queue_local(operations["edit_a"])
    Path(ready).write_text("ready\n", encoding="ascii")
    while True:
        time.sleep(60)


def test_concurrent_conflicts_are_commutative_and_cross_language_fixed(
    tmp_path: Path,
) -> None:
    operations = _operations()
    root = _root()
    left = _replica(tmp_path / "left")
    right = _replica(tmp_path / "right")

    for operation in (operations["base"], operations["edit_a"], operations["edit_b"]):
        left.ingest_remote(operation)
    for operation in (operations["base"], operations["edit_b"], operations["edit_a"]):
        right.ingest_remote(operation)

    assert left.current_records() == right.current_records()
    assert left.current_records()[0].revision == root["expected_concurrent_winner_revision"]
    assert left.accepted_operation_count == right.accepted_operation_count == 3


def test_delete_wins_over_stale_patch_and_authenticated_snapshot(tmp_path: Path) -> None:
    operations = _operations()
    root = _root()
    snapshot_source = DurableEncryptedPatchCollection(
        tmp_path / "snapshot-source",
        encryption_key=_queue_key(),
        authentication_key=_authentication_key(),
        collection_id="collection_a",
    )
    snapshot_source.append(operations["base"])
    old_snapshot = snapshot_source.compact()

    left = _replica(tmp_path / "left")
    right = _replica(tmp_path / "right")
    for operation in (
        operations["base"],
        operations["edit_a"],
        operations["delete"],
        operations["stale_edit"],
    ):
        left.ingest_remote(operation)
    left.merge_snapshot(old_snapshot)

    right.merge_snapshot(old_snapshot)
    for operation in (
        operations["stale_edit"],
        operations["delete"],
        operations["edit_a"],
        operations["base"],
    ):
        right.ingest_remote(operation)

    assert left.current_records() == right.current_records()
    assert left.current_records()[0].tombstone is True
    assert left.current_records()[0].revision == root["expected_delete_winner_revision"]


def test_offline_queue_survives_kill_reconnects_and_retries_exactly_once(
    tmp_path: Path,
) -> None:
    operations = _operations()
    path = tmp_path / "offline"
    ready = tmp_path / "ready"
    process = multiprocessing.get_context("spawn").Process(
        target=_queue_then_wait,
        args=(str(path), str(ready)),
    )
    process.start()
    deadline = time.monotonic() + 20
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert ready.exists()
    process.kill()
    process.join(timeout=10)
    assert process.exitcode is not None and process.exitcode != 0

    restarted = _replica(path)
    assert [item.operation_id for item in restarted.pending_operations()] == [
        operations["base"].operation_id,
        operations["edit_a"].operation_id,
    ]
    remote = _replica(tmp_path / "remote")
    assert restarted.synchronize_to(remote) == 2
    assert restarted.pending_operations() == ()
    assert remote.accepted_operation_count == 2
    assert remote.ingest_remote(operations["edit_a"]) is False
    assert remote.accepted_operation_count == 2

    raw = path.read_bytes()
    assert operations["edit_a"].operation_id.encode() not in raw
    assert operations["edit_a"].envelope.revision.encode() not in raw


def test_divergent_offline_edits_converge_and_receipt_aliases_fail_closed(
    tmp_path: Path,
) -> None:
    operations = _operations()
    left = _replica(tmp_path / "left")
    right = _replica(tmp_path / "right")
    left.queue_local(operations["base"])
    right.ingest_remote(operations["base"])
    left.queue_local(operations["edit_a"])
    right.queue_local(operations["edit_b"])

    assert left.synchronize_to(right) == 2
    assert right.synchronize_to(left) == 1
    assert left.current_records() == right.current_records()
    assert left.current_records()[0].revision == "rev-edit-b"
    assert left.accepted_operation_count == right.accepted_operation_count == 3

    changed = operations["edit_a"].to_dict()
    changed["lamport"] = 99
    with pytest.raises(PatchQueueError):
        left.ingest_remote(EncryptedPatchOperation.from_dict(changed))
