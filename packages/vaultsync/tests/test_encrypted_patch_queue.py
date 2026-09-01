from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import time
from dataclasses import replace
from pathlib import Path

import pytest

from vaultsync.sync_queue import (
    DurableEncryptedConvergentReplica,
    DurableEncryptedInbox,
    DurableEncryptedOutbox,
    DurableEncryptedPatchCollection,
    EncryptedPatchOperation,
    PatchQueueError,
)


VECTOR_PATH = (
    Path(__file__).resolve().parents[3]
    / "contracts"
    / "sync"
    / "test_vectors"
    / "atlasvault_encrypted_patch_queue_vectors_v1.json"
)


def _root() -> dict[str, object]:
    value = json.loads(VECTOR_PATH.read_text(encoding="utf-8"))
    assert value["format"] == "atlasvault-encrypted-patch-queue-vectors"
    assert value["version"] == 1
    return value


def _operations() -> tuple[EncryptedPatchOperation, ...]:
    return tuple(
        EncryptedPatchOperation.from_dict(item)
        for item in _root()["operations"]  # type: ignore[index]
    )


def _queue_key() -> bytes:
    return hashlib.sha256(b"atlasvault-c17-synthetic-queue-key").digest()


def _authentication_key() -> bytes:
    return hashlib.sha256(b"atlasvault-c17-synthetic-authentication-key").digest()


def _crash_writer(outbox_path: str, inbox_path: str, ready_path: str) -> None:
    first, second = _operations()
    outbox = DurableEncryptedOutbox(outbox_path, encryption_key=_queue_key())
    outbox.enqueue(second)
    outbox.enqueue(first)
    inbox = DurableEncryptedInbox(inbox_path, encryption_key=_queue_key())
    inbox.stage_page(
        expected_cursor=None,
        next_cursor="cursor-after-two",
        operations=(first, second),
    )
    Path(ready_path).write_text("ready\n", encoding="ascii")
    while True:
        time.sleep(60)


def test_shared_patch_contract_is_strict_and_deterministically_ordered() -> None:
    root = _root()
    operations = _operations()

    assert [item.operation_id for item in sorted(operations)] == root["expected_transport_order"]
    assert operations[0].idempotency_key == operations[0].operation_id
    assert operations[0].envelope.parent_revision is None
    assert operations[1].envelope.parent_revision == operations[0].envelope.revision

    malformed = operations[0].to_dict()
    malformed["plaintext"] = "forbidden"
    with pytest.raises(PatchQueueError):
        EncryptedPatchOperation.from_dict(malformed)

    inconsistent = operations[1].to_dict()
    inconsistent["operation_type"] = "upsert"
    with pytest.raises(PatchQueueError):
        EncryptedPatchOperation.from_dict(inconsistent)


def test_outbox_is_encrypted_durable_ordered_and_acknowledgement_gated(
    tmp_path: Path,
) -> None:
    first, second = _operations()
    path = tmp_path / "outbox.queue"
    outbox = DurableEncryptedOutbox(path, encryption_key=_queue_key())

    outbox.enqueue(second)
    outbox.enqueue(first)
    outbox.enqueue(first)

    assert outbox.next_pending() == first
    assert outbox.pending_operations() == (first, second)
    assert outbox.next_pending() == first
    stored = path.read_bytes()
    for forbidden in (
        first.operation_id.encode(),
        first.envelope.object_id.encode(),
        first.envelope.revision.encode(),
        first.envelope.ciphertext_b64.encode(),
    ):
        assert forbidden not in stored
    assert set(json.loads(stored)) == {
        "format",
        "version",
        "nonce_b64",
        "ciphertext_b64",
    }

    restarted = DurableEncryptedOutbox(path, encryption_key=_queue_key())
    assert restarted.pending_operations() == (first, second)
    restarted.confirm_remote_acceptance(first.operation_id)
    assert restarted.pending_operations() == (second,)
    with pytest.raises(PatchQueueError):
        restarted.confirm_remote_acceptance(first.operation_id)
    with pytest.raises(PatchQueueError):
        DurableEncryptedOutbox(path, encryption_key=b"x" * 32).pending_operations()


def test_queue_creation_syncs_each_new_parent_and_cleans_abandoned_stages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sync_calls: list[int] = []
    monkeypatch.setattr(os, "fsync", lambda descriptor: sync_calls.append(descriptor))
    nested_path = tmp_path / "first" / "second" / "outbox.queue"

    DurableEncryptedOutbox(nested_path, encryption_key=_queue_key()).enqueue(
        _operations()[0]
    )

    assert nested_path.exists()
    assert len(sync_calls) == 4

    stale = nested_path.parent / (
        ".outbox.queue.00000000000000000000000000000000.tmp"
    )
    stale.write_bytes(b"abandoned")
    DurableEncryptedOutbox(nested_path, encryption_key=_queue_key())
    assert not stale.exists()

    unrelated = nested_path.parent / ".outbox.queue.not-a-uuid.tmp"
    unrelated.write_bytes(b"unrelated")
    DurableEncryptedOutbox(nested_path, encryption_key=_queue_key())
    assert unrelated.read_bytes() == b"unrelated"


def test_inbox_cursor_waits_for_durable_apply_and_duplicates_apply_once(
    tmp_path: Path,
) -> None:
    first, second = _operations()
    path = tmp_path / "inbox.queue"
    inbox = DurableEncryptedInbox(path, encryption_key=_queue_key())
    inbox.stage_page(
        expected_cursor=None,
        next_cursor="cursor-after-two",
        operations=(first, second),
    )

    assert inbox.cursor is None
    assert inbox.pending_operations() == (first, second)
    applied: list[str] = []
    assert inbox.apply_next(lambda item: applied.append(item.operation_id)) == first
    assert inbox.cursor is None
    assert DurableEncryptedInbox(path, encryption_key=_queue_key()).pending_operations() == (
        second,
    )
    assert inbox.apply_next(lambda item: applied.append(item.operation_id)) == second
    assert inbox.cursor == "cursor-after-two"
    assert applied == [first.operation_id, second.operation_id]

    inbox.stage_page(
        expected_cursor="cursor-after-two",
        next_cursor="cursor-after-replay",
        operations=(first, second),
    )
    assert inbox.pending_operations() == ()
    assert inbox.cursor == "cursor-after-replay"
    assert inbox.apply_next(lambda item: applied.append(item.operation_id)) is None
    assert applied == [first.operation_id, second.operation_id]

    inbox.stage_page(
        expected_cursor="cursor-after-replay",
        next_cursor=None,
        operations=(first, second),
    )
    assert inbox.cursor is None

    changed_duplicate = first.to_dict()
    changed_duplicate["lamport"] = 99
    with pytest.raises(PatchQueueError):
        inbox.stage_page(
            expected_cursor=None,
            next_cursor="cursor-invalid",
            operations=(EncryptedPatchOperation.from_dict(changed_duplicate),),
        )


def test_inbox_rejects_order_and_parent_regressions_before_persistence(
    tmp_path: Path,
) -> None:
    first, second = _operations()
    path = tmp_path / "inbox.queue"
    inbox = DurableEncryptedInbox(path, encryption_key=_queue_key())

    with pytest.raises(PatchQueueError):
        inbox.stage_page(
            expected_cursor=None,
            next_cursor="cursor-invalid",
            operations=(second, first),
        )
    assert not path.exists()

    invalid = second.to_dict()
    invalid["envelope"]["parent_revision"] = "wrong-parent"  # type: ignore[index]
    with pytest.raises(PatchQueueError):
        inbox.stage_page(
            expected_cursor=None,
            next_cursor="cursor-invalid",
            operations=(first, EncryptedPatchOperation.from_dict(invalid)),
        )
    assert not path.exists()


def test_public_operation_objects_are_validated_before_persistence(
    tmp_path: Path,
) -> None:
    invalid = replace(_operations()[0], operation_type="delete")
    paths = {name: tmp_path / name for name in ("outbox", "inbox", "collection", "replica")}

    with pytest.raises(PatchQueueError):
        DurableEncryptedOutbox(paths["outbox"], encryption_key=_queue_key()).enqueue(invalid)
    with pytest.raises(PatchQueueError):
        DurableEncryptedInbox(paths["inbox"], encryption_key=_queue_key()).stage_page(
            expected_cursor=None,
            next_cursor="cursor-invalid",
            operations=(invalid,),
        )
    with pytest.raises(PatchQueueError):
        DurableEncryptedPatchCollection(
            paths["collection"],
            encryption_key=_queue_key(),
            authentication_key=_authentication_key(),
            collection_id="collection_a",
        ).append(invalid)
    with pytest.raises(PatchQueueError):
        DurableEncryptedConvergentReplica(
            paths["replica"],
            encryption_key=_queue_key(),
            authentication_key=_authentication_key(),
            collection_id="collection_a",
        ).ingest_remote(invalid)

    assert all(not path.exists() for path in paths.values())


def test_outbox_and_inbox_survive_process_kill_and_restart(tmp_path: Path) -> None:
    outbox_path = tmp_path / "crash-outbox.queue"
    inbox_path = tmp_path / "crash-inbox.queue"
    ready_path = tmp_path / "ready"
    process = multiprocessing.get_context("spawn").Process(
        target=_crash_writer,
        args=(str(outbox_path), str(inbox_path), str(ready_path)),
    )
    process.start()
    deadline = time.monotonic() + 20
    while not ready_path.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert ready_path.exists()
    process.kill()
    process.join(timeout=10)
    assert process.exitcode is not None and process.exitcode != 0

    first, second = _operations()
    assert DurableEncryptedOutbox(
        outbox_path, encryption_key=_queue_key()
    ).pending_operations() == (first, second)
    restarted_inbox = DurableEncryptedInbox(inbox_path, encryption_key=_queue_key())
    assert restarted_inbox.cursor is None
    assert restarted_inbox.pending_operations() == (first, second)
