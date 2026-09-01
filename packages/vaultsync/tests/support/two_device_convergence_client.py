from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from vaultsync.sync_queue import (
    DurableEncryptedConvergentReplica,
    DurableEncryptedInbox,
    DurableEncryptedOutbox,
    EncryptedPatchOperation,
)


COLLECTION_ID = "collection_c20"


def _key(label: str) -> bytes:
    return hashlib.sha256(f"atlasvault-c20-synthetic-{label}".encode("ascii")).digest()


def _load(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write(path: str | Path, value: dict[str, Any]) -> None:
    Path(path).write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def _parts(directory: Path) -> tuple[
    DurableEncryptedConvergentReplica,
    DurableEncryptedOutbox,
    DurableEncryptedInbox,
]:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    replica = DurableEncryptedConvergentReplica(
        directory / "replica.state",
        encryption_key=_key("replica"),
        authentication_key=_key("snapshot-authentication"),
        collection_id=COLLECTION_ID,
    )
    outbox = DurableEncryptedOutbox(
        directory / "outbox.state",
        encryption_key=_key("outbox"),
    )
    inbox = DurableEncryptedInbox(
        directory / "inbox.state",
        encryption_key=_key("inbox"),
    )
    return replica, outbox, inbox


def _operations(vector_path: str | Path) -> dict[str, EncryptedPatchOperation]:
    root = _load(vector_path)
    return {
        name: EncryptedPatchOperation.from_dict(value)
        for name, value in root["operations"].items()
    }


def _decrypt_transport(operation: EncryptedPatchOperation) -> EncryptedPatchOperation:
    envelope = operation.envelope
    plaintext = AESGCM(_key("transport")).decrypt(
        base64.b64decode(envelope.nonce_b64),
        base64.b64decode(envelope.ciphertext_b64),
        base64.b64decode(envelope.aad_b64),
    )
    return EncryptedPatchOperation.from_dict(json.loads(plaintext))


def _result(
    replica: DurableEncryptedConvergentReplica,
    outbox: DurableEncryptedOutbox,
    inbox: DurableEncryptedInbox,
) -> dict[str, Any]:
    records = [item.to_dict() for item in replica.current_records()]
    canonical = json.dumps(records, sort_keys=True, separators=(",", ":")).encode(
        "ascii"
    )
    return {
        "pid": os.getpid(),
        "records": records,
        "state_sha256": hashlib.sha256(canonical).hexdigest(),
        "accepted_operation_count": replica.accepted_operation_count,
        "pending_replica_count": len(replica.pending_operations()),
        "pending_outbox_count": len(outbox.pending_operations()),
        "cursor": inbox.cursor,
    }


def _prepare(arguments: list[str]) -> None:
    if len(arguments) != 5:
        raise SystemExit(64)
    directory = Path(arguments[1])
    operations = _operations(arguments[2])
    names = arguments[3].split(",")
    replica, outbox, inbox = _parts(directory)
    for name in names:
        replica.queue_local(operations[name])
        outbox.enqueue(operations[name])
    result = _result(replica, outbox, inbox)
    result["operations"] = [item.to_dict() for item in outbox.pending_operations()]
    _write(arguments[4], result)


def _apply(arguments: list[str]) -> None:
    if len(arguments) != 4:
        raise SystemExit(64)
    directory = Path(arguments[1])
    page = _load(arguments[2])
    replica, outbox, inbox = _parts(directory)
    pending_replica = {item.operation_id for item in replica.pending_operations()}
    pending_outbox = {item.operation_id for item in outbox.pending_operations()}
    for operation_id in page["accepted_operation_ids"]:
        if operation_id in pending_replica:
            replica.confirm_remote_acceptance(operation_id)
        if operation_id in pending_outbox:
            outbox.confirm_remote_acceptance(operation_id)
    inbox.stage_page(
        expected_cursor=page["expected_cursor"],
        next_cursor=page["next_cursor"],
        operations=(
            EncryptedPatchOperation.from_dict(item)
            for item in page["transport_operations"]
        ),
    )
    while inbox.apply_next(
        lambda transport: replica.ingest_remote(_decrypt_transport(transport))
    ) is not None:
        pass
    _write(arguments[3], _result(replica, outbox, inbox))


def main(arguments: list[str]) -> None:
    if len(arguments) < 2:
        raise SystemExit(64)
    if arguments[1] == "prepare":
        _prepare(arguments[1:])
    elif arguments[1] == "apply":
        _apply(arguments[1:])
    else:
        raise SystemExit(64)


if __name__ == "__main__":
    main(sys.argv)
