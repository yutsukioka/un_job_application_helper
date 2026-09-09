"""New-store enrollment publication using the existing P5 replica and P6 author checks."""

import base64
import copy
import hashlib
import json

from .epoch_rotation import _canonical
from .epoch_vault import _EpochComponent, _reject
from .revocation import _exact
from .sync_queue import (
    DurableEncryptedConvergentReplica,
    EncryptedPatchOperation,
    OpaqueCiphertextEnvelope,
)


class _StagedReplica:
    def __init__(self, state):
        self.state = state

    def read(self, default):
        return copy.deepcopy(self.state["components"].get("runtime", default))

    def write(self, value):
        self.state["components"]["runtime"] = copy.deepcopy(value)


def _replica(owner, store):
    value = DurableEncryptedConvergentReplica(
        owner._file.path,
        encryption_key=owner._key,
        authentication_key=owner._key,
        collection_id=owner._context["vault_id"],
    )
    value._store = store
    return value


def stage_runtime_projection(owner, initial, opaque):
    origin = owner._history_origin
    if origin is None or len(opaque) > 1024 * 1024:
        _reject()
    if hashlib.sha256(opaque).hexdigest() != origin["anchor"]["checkpoint"]["collection_sha256"]:
        _reject()
    # Existing owner.open validates each envelope's AEAD and D106 historical author.
    state = copy.deepcopy(initial)
    owner._enrollment_staging = state
    try:
        replica = _replica(owner, _StagedReplica(state))
        records = json.loads(opaque)["records"]
        if len(records) > 256:
            _reject()
        for raw in records:
            envelope = OpaqueCiphertextEnvelope.from_dict(raw)
            body = json.loads(owner.open(envelope))
            _exact(
                body,
                {
                    "format",
                    "version",
                    "operation_id",
                    "author_device_id",
                    "author_sequence",
                    "lamport",
                    "object_id",
                    "revision",
                    "parent_revision",
                    "tombstone",
                    "payload",
                },
            )
            aad = json.loads(base64.b64decode(envelope.aad_b64))
            if (
                body["format"] != "atlasvault-runtime-record"
                or type(body["version"]) is not int
                or body["version"] != 1
                or body["author_device_id"] != aad["device_id"]
                or body["object_id"] != envelope.object_id
                or body["revision"] != envelope.revision
                or body["parent_revision"] != envelope.parent_revision
                or type(body["tombstone"]) is not bool
                or body["tombstone"] != envelope.tombstone
                or (body["payload"] is None) != envelope.tombstone
            ):
                _reject()
            operation = EncryptedPatchOperation.from_dict(
                dict(
                    format="atlasvault-encrypted-patch-operation",
                    version=1,
                    operation_type="delete" if envelope.tombstone else "upsert",
                    envelope=raw,
                    **{
                        k: body[k]
                        for k in ("operation_id", "author_device_id", "author_sequence", "lamport")
                    },
                )
            )
            replica.ingest_remote(operation)
        if _canonical([r.to_dict() for r in replica.current_records()]) != _canonical(records):
            _reject()
        return state
    finally:
        owner._enrollment_staging = None


def runtime_projection(owner):
    with owner._lock:
        state = owner._load()
        owner._active(state)
        if owner._history_origin is None or "runtime" not in state["components"]:
            _reject()
        replica = _replica(owner, _EpochComponent(owner, "runtime"))
        if replica.pending_operations():
            _reject()
        return replica.current_records()
