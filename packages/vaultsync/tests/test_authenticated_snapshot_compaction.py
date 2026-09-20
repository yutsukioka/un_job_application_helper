from __future__ import annotations

import copy
import hashlib
import json
import multiprocessing
import time
from pathlib import Path

import pytest

from vaultsync.sync_queue import (
    AuthenticatedCollectionSnapshot,
    DurableEncryptedPatchCollection,
    EncryptedPatchOperation,
    PatchQueueError,
)


VECTOR_ROOT = Path(__file__).resolve().parents[3] / "contracts" / "sync" / "test_vectors"


def _json(name: str) -> dict[str, object]:
    value = json.loads((VECTOR_ROOT / name).read_text(encoding="utf-8"))
    assert "test-only" in value["warning"]
    return value


def _operations() -> tuple[EncryptedPatchOperation, EncryptedPatchOperation]:
    root = _json("atlasvault_encrypted_patch_queue_vectors_v1.json")
    first, second = root["operations"]
    return (
        EncryptedPatchOperation.from_dict(first),
        EncryptedPatchOperation.from_dict(second),
    )


def _third_operation() -> EncryptedPatchOperation:
    first, _ = _operations()
    value = first.to_dict()
    value["operation_id"] = "00000000-0000-4000-8000-000000000003"
    value["author_sequence"] = 3
    value["lamport"] = 9
    envelope = value["envelope"]
    envelope["object_id"] = "object_b"
    envelope["revision"] = "rev-b-001"
    envelope["parent_revision"] = None
    return EncryptedPatchOperation.from_dict(value)


def _queue_key() -> bytes:
    return hashlib.sha256(b"atlasvault-c18-synthetic-collection-queue").digest()


def _authentication_key() -> bytes:
    return hashlib.sha256(
        b"atlasvault-c18-synthetic-snapshot-authentication"
    ).digest()


def _collection(path: Path) -> DurableEncryptedPatchCollection:
    return DurableEncryptedPatchCollection(
        path,
        encryption_key=_queue_key(),
        authentication_key=_authentication_key(),
        collection_id="collection_a",
    )


def _crash_compaction(path: str, ready: str) -> None:
    collection = _collection(Path(path))

    def before_replace() -> None:
        Path(ready).write_text("ready\n", encoding="ascii")
        while True:
            time.sleep(60)

    collection.compact(before_replace=before_replace)


def test_snapshot_vector_authenticates_and_rejects_tamper_or_truncation() -> None:
    root = _json("atlasvault_authenticated_snapshot_vectors_v1.json")
    raw = root["snapshot"]
    snapshot = AuthenticatedCollectionSnapshot.from_dict(
        raw,
        authentication_key=_authentication_key(),
    )

    assert snapshot.to_dict() == raw
    assert snapshot.collection_revision == 2
    assert snapshot.records[0].tombstone is True
    assert snapshot.authentication_tag_b64 == raw["authentication"]["tag_b64"]
    canonical_payload = json.dumps(
        raw["payload"], sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    assert hashlib.sha256(canonical_payload).hexdigest() == root[
        "expected_canonical_payload_sha256"
    ]

    tampered = copy.deepcopy(raw)
    tampered["payload"]["records"][0]["revision"] = "rev-tampered"
    with pytest.raises(PatchQueueError):
        AuthenticatedCollectionSnapshot.from_dict(
            tampered,
            authentication_key=_authentication_key(),
        )
    with pytest.raises(PatchQueueError):
        AuthenticatedCollectionSnapshot.from_json_bytes(
            json.dumps(raw, separators=(",", ":")).encode()[:-9],
            authentication_key=_authentication_key(),
        )


def test_compaction_preserves_full_replay_bytes_tombstones_and_receipts(
    tmp_path: Path,
) -> None:
    first, second = _operations()
    third = _third_operation()
    full = _collection(tmp_path / "full.collection")
    compacted = _collection(tmp_path / "compacted.collection")

    for operation in (first, second, third):
        full.append(operation)
    for operation in (first, second):
        compacted.append(operation)

    snapshot = compacted.compact()
    assert snapshot.collection_revision == 2
    assert snapshot.records == (second.envelope,)
    assert snapshot.records[0].tombstone is True
    assert compacted.tail_operations() == ()

    compacted.append(first)
    assert compacted.committed_operation_count == 2
    compacted.append(third)
    assert compacted.current_records() == full.current_records()
    assert [item.to_dict() for item in compacted.current_records()] == [
        item.to_dict() for item in full.current_records()
    ]
    assert compacted.committed_operation_count == full.committed_operation_count == 3

    changed = first.to_dict()
    changed["lamport"] = 99
    with pytest.raises(PatchQueueError):
        compacted.append(EncryptedPatchOperation.from_dict(changed))

    stored = (tmp_path / "compacted.collection").read_bytes()
    for forbidden in (
        first.operation_id.encode(),
        second.envelope.revision.encode(),
        third.envelope.object_id.encode(),
    ):
        assert forbidden not in stored


def test_kill_mid_compaction_restarts_at_valid_pre_or_post_state(tmp_path: Path) -> None:
    first, second = _operations()
    path = tmp_path / "crash.collection"
    ready = tmp_path / "ready"
    collection = _collection(path)
    collection.append(first)
    collection.append(second)
    expected = collection.current_records()

    process = multiprocessing.get_context("spawn").Process(
        target=_crash_compaction,
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

    restarted = _collection(path)
    assert restarted.current_records() == expected
    assert restarted.committed_operation_count == 2
    assert (restarted.snapshot is None and len(restarted.tail_operations()) == 2) or (
        restarted.snapshot is not None and restarted.tail_operations() == ()
    )
    final_snapshot = restarted.compact()
    assert final_snapshot.records == expected
    assert restarted.tail_operations() == ()
    assert _collection(path).current_records() == expected


def test_snapshot_and_journal_fail_closed_with_wrong_key(tmp_path: Path) -> None:
    first, _ = _operations()
    path = tmp_path / "keys.collection"
    collection = _collection(path)
    collection.append(first)
    raw_snapshot = collection.compact().to_dict()

    with pytest.raises(PatchQueueError):
        AuthenticatedCollectionSnapshot.from_dict(
            raw_snapshot,
            authentication_key=b"x" * 32,
        )
    with pytest.raises(PatchQueueError):
        DurableEncryptedPatchCollection(
            path,
            encryption_key=b"y" * 32,
            authentication_key=_authentication_key(),
            collection_id="collection_a",
        ).current_records()
