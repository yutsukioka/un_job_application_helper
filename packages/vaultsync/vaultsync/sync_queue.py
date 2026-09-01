from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import secrets
import uuid
from dataclasses import dataclass, replace
from functools import total_ordering
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


PATCH_FORMAT = "atlasvault-encrypted-patch-operation"
PATCH_VERSION = 1
OPAQUE_ENVELOPE_FORMAT = "atlasvault-opaque-ciphertext-envelope"
QUEUE_ENVELOPE_FORMAT = "atlasvault-encrypted-transfer-queue"
QUEUE_VERSION = 1
SNAPSHOT_FORMAT = "atlasvault-authenticated-collection-snapshot"
SNAPSHOT_PAYLOAD_FORMAT = "atlasvault-authenticated-collection-snapshot-payload"
SNAPSHOT_AUTHENTICATION_ALGORITHM = "HMAC-SHA256"
COLLECTION_STATE_FORMAT = "atlasvault-encrypted-patch-collection-state"
CONVERGENT_REPLICA_STATE_FORMAT = "atlasvault-encrypted-convergent-replica-state"
MAXIMUM_QUEUE_FILE_BYTES = 128 * 1024 * 1024
MAXIMUM_QUEUE_OPERATIONS = 65_536
MAXIMUM_ENVELOPE_FIELD_BYTES = 96 * 1024 * 1024
MAXIMUM_CURSOR_LENGTH = 2_048
_MAXIMUM_INTEGER = 2**63 - 1
_IDENTIFIER = re.compile(r"^[A-Za-z0-9._~-]{1,128}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


class PatchQueueError(ValueError):
    """Raised when encrypted patch or queue state fails closed."""


def _error() -> PatchQueueError:
    return PatchQueueError("encrypted patch queue state is invalid")


def _mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise _error()
    return value


def _exact(value: Mapping[str, Any], keys: set[str]) -> None:
    if set(value) != keys:
        raise _error()


def _text(value: Any, *, maximum: int = 128) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise _error()
    return value


def _identifier(value: Any) -> str:
    text = _text(value)
    if _IDENTIFIER.fullmatch(text) is None or text == "*":
        raise _error()
    return text


def _integer(value: Any) -> int:
    if type(value) is not int or value < 1 or value > _MAXIMUM_INTEGER:
        raise _error()
    return value


def _canonical_uuid(value: Any) -> str:
    text = _text(value, maximum=36)
    if _UUID.fullmatch(text) is None:
        raise _error()
    try:
        if str(uuid.UUID(text)) != text:
            raise _error()
    except (ValueError, AttributeError) as exc:
        raise _error() from exc
    return text


def _canonical_base64(
    value: Any,
    *,
    exact_bytes: int | None = None,
    minimum_bytes: int = 1,
) -> str:
    text = _text(value, maximum=MAXIMUM_ENVELOPE_FIELD_BYTES * 2)
    try:
        decoded = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise _error() from exc
    if base64.b64encode(decoded).decode("ascii") != text:
        raise _error()
    if len(decoded) < minimum_bytes or len(decoded) > MAXIMUM_ENVELOPE_FIELD_BYTES:
        raise _error()
    if exact_bytes is not None and len(decoded) != exact_bytes:
        raise _error()
    return text


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise _error() from exc


@dataclass(frozen=True)
class OpaqueCiphertextEnvelope:
    format: str
    version: int
    object_id: str
    revision: str
    parent_revision: str | None
    key_epoch: int
    nonce_b64: str
    ciphertext_b64: str
    aad_b64: str
    signature_b64: str
    tombstone: bool
    content_sha256: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> OpaqueCiphertextEnvelope:
        obj = _mapping(value)
        _exact(
            obj,
            {
                "format",
                "version",
                "object_id",
                "revision",
                "parent_revision",
                "key_epoch",
                "nonce_b64",
                "ciphertext_b64",
                "aad_b64",
                "signature_b64",
                "tombstone",
                "content_sha256",
            },
        )
        if obj["format"] != OPAQUE_ENVELOPE_FORMAT:
            raise _error()
        version = _integer(obj["version"])
        parent = obj["parent_revision"]
        if parent is not None:
            parent = _identifier(parent)
        if type(obj["tombstone"]) is not bool:
            raise _error()
        ciphertext = _canonical_base64(obj["ciphertext_b64"], minimum_bytes=16)
        digest = _text(obj["content_sha256"], maximum=64)
        if _SHA256.fullmatch(digest) is None or not secrets.compare_digest(
            hashlib.sha256(base64.b64decode(ciphertext)).hexdigest(), digest
        ):
            raise _error()
        return cls(
            format=OPAQUE_ENVELOPE_FORMAT,
            version=version,
            object_id=_identifier(obj["object_id"]),
            revision=_identifier(obj["revision"]),
            parent_revision=parent,
            key_epoch=_integer(obj["key_epoch"]),
            nonce_b64=_canonical_base64(obj["nonce_b64"], exact_bytes=12),
            ciphertext_b64=ciphertext,
            aad_b64=_canonical_base64(obj["aad_b64"]),
            signature_b64=_canonical_base64(obj["signature_b64"]),
            tombstone=obj["tombstone"],
            content_sha256=digest,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "version": self.version,
            "object_id": self.object_id,
            "revision": self.revision,
            "parent_revision": self.parent_revision,
            "key_epoch": self.key_epoch,
            "nonce_b64": self.nonce_b64,
            "ciphertext_b64": self.ciphertext_b64,
            "aad_b64": self.aad_b64,
            "signature_b64": self.signature_b64,
            "tombstone": self.tombstone,
            "content_sha256": self.content_sha256,
        }


@total_ordering
@dataclass(frozen=True)
class EncryptedPatchOperation:
    operation_id: str
    operation_type: str
    author_device_id: str
    author_sequence: int
    lamport: int
    envelope: OpaqueCiphertextEnvelope
    format: str = PATCH_FORMAT
    version: int = PATCH_VERSION

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> EncryptedPatchOperation:
        obj = _mapping(value)
        _exact(
            obj,
            {
                "format",
                "version",
                "operation_id",
                "operation_type",
                "author_device_id",
                "author_sequence",
                "lamport",
                "envelope",
            },
        )
        if obj["format"] != PATCH_FORMAT or obj["version"] != PATCH_VERSION:
            raise _error()
        envelope = OpaqueCiphertextEnvelope.from_dict(_mapping(obj["envelope"]))
        operation_type = obj["operation_type"]
        expected_type = "delete" if envelope.tombstone else "upsert"
        if operation_type != expected_type:
            raise _error()
        return cls(
            operation_id=_canonical_uuid(obj["operation_id"]),
            operation_type=expected_type,
            author_device_id=_identifier(obj["author_device_id"]),
            author_sequence=_integer(obj["author_sequence"]),
            lamport=_integer(obj["lamport"]),
            envelope=envelope,
        )

    @property
    def idempotency_key(self) -> str:
        return self.operation_id

    @property
    def order_key(self) -> tuple[int, str, int, str]:
        return (
            self.lamport,
            self.author_device_id,
            self.author_sequence,
            self.operation_id,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "version": self.version,
            "operation_id": self.operation_id,
            "operation_type": self.operation_type,
            "author_device_id": self.author_device_id,
            "author_sequence": self.author_sequence,
            "lamport": self.lamport,
            "envelope": self.envelope.to_dict(),
        }

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, EncryptedPatchOperation):
            return NotImplemented
        return self.order_key < other.order_key


def _fingerprint(operation: EncryptedPatchOperation) -> str:
    return hashlib.sha256(_canonical_json(operation.to_dict())).hexdigest()


def _validated_operation(operation: EncryptedPatchOperation) -> EncryptedPatchOperation:
    if not isinstance(operation, EncryptedPatchOperation):
        raise _error()
    return EncryptedPatchOperation.from_dict(operation.to_dict())


def _sequence_owners_json(
    owners: Mapping[tuple[str, int], str],
) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for (device, sequence), operation_id in sorted(owners.items()):
        result.setdefault(device, {})[str(sequence)] = operation_id
    return result


class _EncryptedQueueFile:
    def __init__(self, path: str | Path, encryption_key: bytes, *, kind: str) -> None:
        if not isinstance(encryption_key, bytes) or len(encryption_key) != 32:
            raise _error()
        self.path = Path(path)
        self._key = encryption_key
        self._aad = f"{QUEUE_ENVELOPE_FORMAT}:v1:{kind}".encode("ascii")

    def read(self, default: Mapping[str, Any]) -> dict[str, Any]:
        if not self.path.exists():
            return dict(default)
        try:
            size = self.path.stat().st_size
            if size <= 0 or size > MAXIMUM_QUEUE_FILE_BYTES:
                raise _error()
            outer = _mapping(json.loads(self.path.read_bytes()))
            _exact(outer, {"format", "version", "nonce_b64", "ciphertext_b64"})
            if (
                outer["format"] != QUEUE_ENVELOPE_FORMAT
                or outer["version"] != QUEUE_VERSION
            ):
                raise _error()
            nonce = base64.b64decode(
                _canonical_base64(outer["nonce_b64"], exact_bytes=12)
            )
            ciphertext = base64.b64decode(
                _canonical_base64(outer["ciphertext_b64"], minimum_bytes=16)
            )
            state_bytes = AESGCM(self._key).decrypt(nonce, ciphertext, self._aad)
            return dict(_mapping(json.loads(state_bytes)))
        except PatchQueueError:
            raise
        except (InvalidTag, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _error() from exc

    def write(
        self,
        state: Mapping[str, Any],
        *,
        before_replace: Callable[[], None] | None = None,
    ) -> None:
        state_bytes = _canonical_json(state)
        if not state_bytes or len(state_bytes) > MAXIMUM_QUEUE_FILE_BYTES:
            raise _error()
        nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(self._key).encrypt(nonce, state_bytes, self._aad)
        encoded = _canonical_json(
            {
                "format": QUEUE_ENVELOPE_FORMAT,
                "version": QUEUE_VERSION,
                "nonce_b64": base64.b64encode(nonce).decode("ascii"),
                "ciphertext_b64": base64.b64encode(ciphertext).decode("ascii"),
            }
        )
        if len(encoded) > MAXIMUM_QUEUE_FILE_BYTES:
            raise _error()
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        descriptor: int | None = None
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = None
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            if before_replace is not None:
                before_replace()
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
            directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            directory = os.open(self.path.parent, directory_flags)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except (OSError, ValueError) as exc:
            raise _error() from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


@dataclass(frozen=True)
class AuthenticatedCollectionSnapshot:
    collection_id: str
    collection_revision: int
    last_order: tuple[int, str, int, str]
    records: tuple[OpaqueCiphertextEnvelope, ...]
    applied_fingerprints: Mapping[str, str]
    author_sequences: Mapping[str, int]
    author_sequence_owners: Mapping[tuple[str, int], str]
    authentication_tag_b64: str
    canonical_payload_sha256: str

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
        *,
        authentication_key: bytes,
    ) -> AuthenticatedCollectionSnapshot:
        if not isinstance(authentication_key, bytes) or len(authentication_key) != 32:
            raise _error()
        outer = _mapping(value)
        _exact(outer, {"format", "version", "payload", "authentication"})
        if outer["format"] != SNAPSHOT_FORMAT or outer["version"] != 1:
            raise _error()
        payload = _mapping(outer["payload"])
        authentication = _mapping(outer["authentication"])
        _exact(authentication, {"algorithm", "tag_b64"})
        if authentication["algorithm"] != SNAPSHOT_AUTHENTICATION_ALGORITHM:
            raise _error()
        tag_text = _canonical_base64(authentication["tag_b64"], exact_bytes=32)
        payload_bytes = _canonical_json(payload)
        expected = hmac.new(authentication_key, payload_bytes, hashlib.sha256).digest()
        if not secrets.compare_digest(base64.b64decode(tag_text), expected):
            raise _error()
        _exact(
            payload,
            {
                "format",
                "version",
                "collection_id",
                "collection_revision",
                "last_order",
                "records",
                "applied_fingerprints",
                "author_sequences",
                "author_sequence_owners",
                "record_count",
                "live_record_count",
                "tombstone_count",
            },
        )
        if payload["format"] != SNAPSHOT_PAYLOAD_FORMAT or payload["version"] != 1:
            raise _error()
        revision = _integer(payload["collection_revision"])
        raw_order = payload["last_order"]
        if not isinstance(raw_order, list) or len(raw_order) != 4:
            raise _error()
        order = (
            _integer(raw_order[0]),
            _identifier(raw_order[1]),
            _integer(raw_order[2]),
            _canonical_uuid(raw_order[3]),
        )
        raw_records = payload["records"]
        if not isinstance(raw_records, list) or len(raw_records) > MAXIMUM_QUEUE_OPERATIONS:
            raise _error()
        records = tuple(
            OpaqueCiphertextEnvelope.from_dict(_mapping(item)) for item in raw_records
        )
        if tuple(sorted(records, key=lambda item: item.object_id)) != records or len(
            {item.object_id for item in records}
        ) != len(records):
            raise _error()
        raw_fingerprints = _mapping(payload["applied_fingerprints"])
        if len(raw_fingerprints) != revision or revision > MAXIMUM_QUEUE_OPERATIONS:
            raise _error()
        fingerprints: dict[str, str] = {}
        for operation_id, digest_value in raw_fingerprints.items():
            operation_id = _canonical_uuid(operation_id)
            digest = _text(digest_value, maximum=64)
            if _SHA256.fullmatch(digest) is None:
                raise _error()
            fingerprints[operation_id] = digest
        raw_sequences = _mapping(payload["author_sequences"])
        if not raw_sequences or len(raw_sequences) > MAXIMUM_QUEUE_OPERATIONS:
            raise _error()
        sequences = {
            _identifier(device): _integer(sequence)
            for device, sequence in raw_sequences.items()
        }
        if sum(sequences.values()) != revision or order[3] not in fingerprints:
            raise _error()
        raw_owners = _mapping(payload["author_sequence_owners"])
        if set(raw_owners) != set(sequences):
            raise _error()
        sequence_owners: dict[tuple[str, int], str] = {}
        for device, maximum_sequence in sequences.items():
            device_owners = _mapping(raw_owners[device])
            if len(device_owners) != maximum_sequence:
                raise _error()
            for sequence_text, operation_id in device_owners.items():
                if (
                    not isinstance(sequence_text, str)
                    or not sequence_text.isascii()
                    or not sequence_text.isdigit()
                ):
                    raise _error()
                sequence = int(sequence_text)
                if sequence < 1 or str(sequence) != sequence_text:
                    raise _error()
                sequence_owners[(device, sequence)] = _canonical_uuid(operation_id)
            if {sequence for owner, sequence in sequence_owners if owner == device} != set(
                range(1, maximum_sequence + 1)
            ):
                raise _error()
        owner_ids = set(sequence_owners.values())
        if len(owner_ids) != revision or owner_ids != set(fingerprints):
            raise _error()
        record_count = payload["record_count"]
        live_count = payload["live_record_count"]
        tombstone_count = payload["tombstone_count"]
        if (
            type(record_count) is not int
            or type(live_count) is not int
            or type(tombstone_count) is not int
            or record_count != len(records)
            or live_count != sum(not item.tombstone for item in records)
            or tombstone_count != sum(item.tombstone for item in records)
            or live_count + tombstone_count != record_count
        ):
            raise _error()
        return cls(
            collection_id=_identifier(payload["collection_id"]),
            collection_revision=revision,
            last_order=order,
            records=records,
            applied_fingerprints=fingerprints,
            author_sequences=sequences,
            author_sequence_owners=sequence_owners,
            authentication_tag_b64=tag_text,
            canonical_payload_sha256=hashlib.sha256(payload_bytes).hexdigest(),
        )

    @classmethod
    def from_json_bytes(
        cls,
        value: bytes,
        *,
        authentication_key: bytes,
    ) -> AuthenticatedCollectionSnapshot:
        try:
            parsed = json.loads(value)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _error() from exc
        return cls.from_dict(_mapping(parsed), authentication_key=authentication_key)

    @classmethod
    def _create(
        cls,
        *,
        collection_id: str,
        records: Mapping[str, OpaqueCiphertextEnvelope],
        fingerprints: Mapping[str, str],
        author_sequences: Mapping[str, int],
        author_sequence_owners: Mapping[tuple[str, int], str],
        last_order: tuple[int, str, int, str],
        authentication_key: bytes,
    ) -> AuthenticatedCollectionSnapshot:
        ordered_records = [records[key].to_dict() for key in sorted(records)]
        payload: dict[str, Any] = {
            "format": SNAPSHOT_PAYLOAD_FORMAT,
            "version": 1,
            "collection_id": collection_id,
            "collection_revision": len(fingerprints),
            "last_order": list(last_order),
            "records": ordered_records,
            "applied_fingerprints": dict(sorted(fingerprints.items())),
            "author_sequences": dict(sorted(author_sequences.items())),
            "author_sequence_owners": _sequence_owners_json(author_sequence_owners),
            "record_count": len(ordered_records),
            "live_record_count": sum(not item.tombstone for item in records.values()),
            "tombstone_count": sum(item.tombstone for item in records.values()),
        }
        tag = hmac.new(authentication_key, _canonical_json(payload), hashlib.sha256).digest()
        return cls.from_dict(
            {
                "format": SNAPSHOT_FORMAT,
                "version": 1,
                "payload": payload,
                "authentication": {
                    "algorithm": SNAPSHOT_AUTHENTICATION_ALGORITHM,
                    "tag_b64": base64.b64encode(tag).decode("ascii"),
                },
            },
            authentication_key=authentication_key,
        )

    def to_dict(self) -> dict[str, Any]:
        records = [item.to_dict() for item in self.records]
        payload: dict[str, Any] = {
            "format": SNAPSHOT_PAYLOAD_FORMAT,
            "version": 1,
            "collection_id": self.collection_id,
            "collection_revision": self.collection_revision,
            "last_order": list(self.last_order),
            "records": records,
            "applied_fingerprints": dict(self.applied_fingerprints),
            "author_sequences": dict(self.author_sequences),
            "author_sequence_owners": _sequence_owners_json(
                self.author_sequence_owners
            ),
            "record_count": len(records),
            "live_record_count": sum(not item.tombstone for item in self.records),
            "tombstone_count": sum(item.tombstone for item in self.records),
        }
        return {
            "format": SNAPSHOT_FORMAT,
            "version": 1,
            "payload": payload,
            "authentication": {
                "algorithm": SNAPSHOT_AUTHENTICATION_ALGORITHM,
                "tag_b64": self.authentication_tag_b64,
            },
        }


@dataclass
class _CollectionReplay:
    records: dict[str, OpaqueCiphertextEnvelope]
    fingerprints: dict[str, str]
    author_sequences: dict[str, int]
    author_sequence_owners: dict[tuple[str, int], str]
    object_revisions: dict[str, str]
    last_order: tuple[int, str, int, str] | None

    @classmethod
    def from_snapshot(
        cls, snapshot: AuthenticatedCollectionSnapshot | None
    ) -> _CollectionReplay:
        if snapshot is None:
            return cls({}, {}, {}, {}, {}, None)
        records = {item.object_id: item for item in snapshot.records}
        return cls(
            records=records,
            fingerprints=dict(snapshot.applied_fingerprints),
            author_sequences=dict(snapshot.author_sequences),
            author_sequence_owners=dict(snapshot.author_sequence_owners),
            object_revisions={key: item.revision for key, item in records.items()},
            last_order=snapshot.last_order,
        )

    def apply(self, operation: EncryptedPatchOperation) -> bool:
        digest = _fingerprint(operation)
        known = self.fingerprints.get(operation.operation_id)
        if known is not None:
            if not secrets.compare_digest(known, digest):
                raise _error()
            return False
        if len(self.fingerprints) >= MAXIMUM_QUEUE_OPERATIONS:
            raise _error()
        sequence_key = (operation.author_device_id, operation.author_sequence)
        known_owner = self.author_sequence_owners.get(sequence_key)
        if known_owner is not None and known_owner != operation.operation_id:
            raise _error()
        self.last_order = _advance_metadata(
            operation,
            author_sequences=self.author_sequences,
            object_revisions=self.object_revisions,
            last_order=self.last_order,
        )
        self.records[operation.envelope.object_id] = operation.envelope
        self.fingerprints[operation.operation_id] = digest
        self.author_sequence_owners[sequence_key] = operation.operation_id
        return True


def _collection_default(collection_id: str) -> dict[str, Any]:
    return {
        "format": COLLECTION_STATE_FORMAT,
        "version": 1,
        "collection_id": collection_id,
        "snapshot": None,
        "tail_operations": [],
    }


def _load_collection(
    store: _EncryptedQueueFile,
    *,
    collection_id: str,
    authentication_key: bytes,
) -> tuple[
    AuthenticatedCollectionSnapshot | None,
    tuple[EncryptedPatchOperation, ...],
    _CollectionReplay,
]:
    state = _mapping(store.read(_collection_default(collection_id)))
    _exact(
        state,
        {"format", "version", "collection_id", "snapshot", "tail_operations"},
    )
    if (
        state["format"] != COLLECTION_STATE_FORMAT
        or state["version"] != 1
        or _identifier(state["collection_id"]) != collection_id
    ):
        raise _error()
    snapshot_value = state["snapshot"]
    snapshot = None
    if snapshot_value is not None:
        snapshot = AuthenticatedCollectionSnapshot.from_dict(
            _mapping(snapshot_value), authentication_key=authentication_key
        )
        if snapshot.collection_id != collection_id:
            raise _error()
    raw_tail = state["tail_operations"]
    if not isinstance(raw_tail, list) or len(raw_tail) > MAXIMUM_QUEUE_OPERATIONS:
        raise _error()
    tail = tuple(
        EncryptedPatchOperation.from_dict(_mapping(item)) for item in raw_tail
    )
    if tail != tuple(sorted(tail)) or len({item.operation_id for item in tail}) != len(tail):
        raise _error()
    replay = _CollectionReplay.from_snapshot(snapshot)
    for operation in tail:
        if not replay.apply(operation):
            raise _error()
    return snapshot, tail, replay


class DurableEncryptedPatchCollection:
    def __init__(
        self,
        path: str | Path,
        *,
        encryption_key: bytes,
        authentication_key: bytes,
        collection_id: str,
    ) -> None:
        if not isinstance(authentication_key, bytes) or len(authentication_key) != 32:
            raise _error()
        self._collection_id = _identifier(collection_id)
        self._authentication_key = authentication_key
        self._store = _EncryptedQueueFile(path, encryption_key, kind="collection")

    def _load(
        self,
    ) -> tuple[
        AuthenticatedCollectionSnapshot | None,
        tuple[EncryptedPatchOperation, ...],
        _CollectionReplay,
    ]:
        return _load_collection(
            self._store,
            collection_id=self._collection_id,
            authentication_key=self._authentication_key,
        )

    def append(self, operation: EncryptedPatchOperation) -> None:
        operation = _validated_operation(operation)
        snapshot, tail, replay = self._load()
        if not replay.apply(operation):
            return
        updated_tail = (*tail, operation)
        self._store.write(
            {
                "format": COLLECTION_STATE_FORMAT,
                "version": 1,
                "collection_id": self._collection_id,
                "snapshot": snapshot.to_dict() if snapshot is not None else None,
                "tail_operations": [item.to_dict() for item in updated_tail],
            }
        )

    def current_records(self) -> tuple[OpaqueCiphertextEnvelope, ...]:
        replay = self._load()[2]
        return tuple(replay.records[key] for key in sorted(replay.records))

    def tail_operations(self) -> tuple[EncryptedPatchOperation, ...]:
        return self._load()[1]

    @property
    def snapshot(self) -> AuthenticatedCollectionSnapshot | None:
        return self._load()[0]

    @property
    def committed_operation_count(self) -> int:
        return len(self._load()[2].fingerprints)

    def compact(
        self,
        *,
        before_replace: Callable[[], None] | None = None,
    ) -> AuthenticatedCollectionSnapshot:
        _, _, replay = self._load()
        if replay.last_order is None or not replay.fingerprints:
            raise _error()
        snapshot = AuthenticatedCollectionSnapshot._create(
            collection_id=self._collection_id,
            records=replay.records,
            fingerprints=replay.fingerprints,
            author_sequences=replay.author_sequences,
            author_sequence_owners=replay.author_sequence_owners,
            last_order=replay.last_order,
            authentication_key=self._authentication_key,
        )
        self._store.write(
            {
                "format": COLLECTION_STATE_FORMAT,
                "version": 1,
                "collection_id": self._collection_id,
                "snapshot": snapshot.to_dict(),
                "tail_operations": [],
            },
            before_replace=before_replace,
        )
        return snapshot


@dataclass(frozen=True)
class _ConvergentReplicaState:
    operations: tuple[EncryptedPatchOperation, ...]
    snapshots: tuple[AuthenticatedCollectionSnapshot, ...]
    pending_operation_ids: tuple[str, ...]
    receipts: Mapping[str, str]


def _convergent_default(collection_id: str) -> dict[str, Any]:
    return {
        "format": CONVERGENT_REPLICA_STATE_FORMAT,
        "version": 1,
        "collection_id": collection_id,
        "operations": [],
        "snapshots": [],
        "pending_operation_ids": [],
    }


def _validate_convergent_history(
    operations: tuple[EncryptedPatchOperation, ...],
    snapshots: tuple[AuthenticatedCollectionSnapshot, ...],
) -> dict[str, str]:
    receipts: dict[str, str] = {}
    snapshot_sequences: dict[str, int] = {}
    sequence_owners: dict[tuple[str, int], str] = {}
    operation_sequences: dict[str, tuple[str, int]] = {}
    revision_values: dict[tuple[str, str], bytes] = {}
    revision_parents: dict[tuple[str, str], str | None] = {}

    def add_receipt(operation_id: str, digest: str) -> None:
        known = receipts.get(operation_id)
        if known is not None and not secrets.compare_digest(known, digest):
            raise _error()
        receipts[operation_id] = digest

    def add_envelope(envelope: OpaqueCiphertextEnvelope) -> None:
        if envelope.parent_revision == envelope.revision:
            raise _error()
        key = (envelope.object_id, envelope.revision)
        encoded = _canonical_json(envelope.to_dict())
        known = revision_values.get(key)
        if known is not None and not secrets.compare_digest(known, encoded):
            raise _error()
        revision_values[key] = encoded
        revision_parents[key] = envelope.parent_revision

    def add_sequence_owner(
        sequence_key: tuple[str, int], operation_id: str
    ) -> None:
        known_owner = sequence_owners.get(sequence_key)
        known_sequence = operation_sequences.get(operation_id)
        if (
            known_owner is not None
            and known_owner != operation_id
            or known_sequence is not None
            and known_sequence != sequence_key
        ):
            raise _error()
        sequence_owners[sequence_key] = operation_id
        operation_sequences[operation_id] = sequence_key

    for snapshot in snapshots:
        for operation_id, digest in snapshot.applied_fingerprints.items():
            add_receipt(operation_id, digest)
        for device_id, sequence in snapshot.author_sequences.items():
            snapshot_sequences[device_id] = max(
                snapshot_sequences.get(device_id, 0), sequence
            )
        for sequence_key, operation_id in snapshot.author_sequence_owners.items():
            add_sequence_owner(sequence_key, operation_id)
        for envelope in snapshot.records:
            add_envelope(envelope)

    for operation in operations:
        digest = _fingerprint(operation)
        known_receipt = receipts.get(operation.operation_id)
        add_receipt(operation.operation_id, digest)
        sequence_key = (operation.author_device_id, operation.author_sequence)
        add_sequence_owner(sequence_key, operation.operation_id)
        if (
            known_receipt is None
            and operation.author_sequence
            <= snapshot_sequences.get(operation.author_device_id, 0)
        ):
            raise _error()
        add_envelope(operation.envelope)

    if len(receipts) > MAXIMUM_QUEUE_OPERATIONS:
        raise _error()

    for start in revision_parents:
        seen: set[tuple[str, str]] = set()
        current: tuple[str, str] | None = start
        while current is not None and current in revision_parents:
            if current in seen:
                raise _error()
            seen.add(current)
            parent = revision_parents[current]
            current = (current[0], parent) if parent is not None else None
    return receipts


def _load_convergent_replica(
    store: _EncryptedQueueFile,
    *,
    collection_id: str,
    authentication_key: bytes,
) -> _ConvergentReplicaState:
    state = _mapping(store.read(_convergent_default(collection_id)))
    _exact(
        state,
        {
            "format",
            "version",
            "collection_id",
            "operations",
            "snapshots",
            "pending_operation_ids",
        },
    )
    if (
        state["format"] != CONVERGENT_REPLICA_STATE_FORMAT
        or state["version"] != 1
        or _identifier(state["collection_id"]) != collection_id
    ):
        raise _error()
    raw_operations = state["operations"]
    raw_snapshots = state["snapshots"]
    raw_pending = state["pending_operation_ids"]
    if (
        not isinstance(raw_operations, list)
        or not isinstance(raw_snapshots, list)
        or not isinstance(raw_pending, list)
        or len(raw_operations) > MAXIMUM_QUEUE_OPERATIONS
        or len(raw_snapshots) > MAXIMUM_QUEUE_OPERATIONS
        or len(raw_pending) > MAXIMUM_QUEUE_OPERATIONS
    ):
        raise _error()
    operations = tuple(
        EncryptedPatchOperation.from_dict(_mapping(item)) for item in raw_operations
    )
    if operations != tuple(sorted(operations, key=lambda item: item.operation_id)) or len(
        {item.operation_id for item in operations}
    ) != len(operations):
        raise _error()
    snapshots = tuple(
        AuthenticatedCollectionSnapshot.from_dict(
            _mapping(item), authentication_key=authentication_key
        )
        for item in raw_snapshots
    )
    if any(item.collection_id != collection_id for item in snapshots) or snapshots != tuple(
        sorted(snapshots, key=lambda item: item.canonical_payload_sha256)
    ) or len({item.canonical_payload_sha256 for item in snapshots}) != len(snapshots):
        raise _error()
    pending = tuple(_canonical_uuid(item) for item in raw_pending)
    if pending != tuple(sorted(pending)) or len(set(pending)) != len(pending):
        raise _error()
    operation_ids = {item.operation_id for item in operations}
    if not set(pending).issubset(operation_ids):
        raise _error()
    receipts = _validate_convergent_history(operations, snapshots)
    return _ConvergentReplicaState(operations, snapshots, pending, receipts)


def _convergent_dict(
    state: _ConvergentReplicaState,
    *,
    collection_id: str,
) -> dict[str, Any]:
    return {
        "format": CONVERGENT_REPLICA_STATE_FORMAT,
        "version": 1,
        "collection_id": collection_id,
        "operations": [item.to_dict() for item in state.operations],
        "snapshots": [item.to_dict() for item in state.snapshots],
        "pending_operation_ids": list(state.pending_operation_ids),
    }


def _resolve_convergent_records(
    state: _ConvergentReplicaState,
) -> tuple[OpaqueCiphertextEnvelope, ...]:
    candidates: dict[
        str,
        list[
            tuple[
                OpaqueCiphertextEnvelope,
                tuple[int, int, str, int, str],
            ]
        ],
    ] = {}
    for snapshot in state.snapshots:
        for envelope in snapshot.records:
            candidates.setdefault(envelope.object_id, []).append(
                (
                    envelope,
                    (0, 0, "", 0, snapshot.canonical_payload_sha256),
                )
            )
    for operation in state.operations:
        candidates.setdefault(operation.envelope.object_id, []).append(
            (operation.envelope, (1, *operation.order_key))
        )
    resolved: list[OpaqueCiphertextEnvelope] = []
    for object_id in sorted(candidates):
        object_candidates = candidates[object_id]
        if any(item[0].tombstone for item in object_candidates):
            object_candidates = [item for item in object_candidates if item[0].tombstone]
        resolved.append(max(object_candidates, key=lambda item: item[1])[0])
    return tuple(resolved)


class DurableEncryptedConvergentReplica:
    """Durable ciphertext replica with order-independent conflict reduction."""

    def __init__(
        self,
        path: str | Path,
        *,
        encryption_key: bytes,
        authentication_key: bytes,
        collection_id: str,
    ) -> None:
        if not isinstance(authentication_key, bytes) or len(authentication_key) != 32:
            raise _error()
        self._collection_id = _identifier(collection_id)
        self._authentication_key = authentication_key
        self._store = _EncryptedQueueFile(path, encryption_key, kind="convergent-replica")

    def _load(self) -> _ConvergentReplicaState:
        return _load_convergent_replica(
            self._store,
            collection_id=self._collection_id,
            authentication_key=self._authentication_key,
        )

    def _write(
        self,
        operations: Iterable[EncryptedPatchOperation],
        snapshots: Iterable[AuthenticatedCollectionSnapshot],
        pending_operation_ids: Iterable[str],
    ) -> None:
        ordered_operations = tuple(sorted(operations, key=lambda item: item.operation_id))
        ordered_snapshots = tuple(
            sorted(snapshots, key=lambda item: item.canonical_payload_sha256)
        )
        ordered_pending = tuple(sorted(pending_operation_ids))
        state = _ConvergentReplicaState(
            ordered_operations,
            ordered_snapshots,
            ordered_pending,
            _validate_convergent_history(ordered_operations, ordered_snapshots),
        )
        if not set(ordered_pending).issubset(
            {item.operation_id for item in ordered_operations}
        ):
            raise _error()
        self._store.write(_convergent_dict(state, collection_id=self._collection_id))

    def ingest_remote(self, operation: EncryptedPatchOperation) -> bool:
        operation = _validated_operation(operation)
        state = self._load()
        digest = _fingerprint(operation)
        known = state.receipts.get(operation.operation_id)
        if known is not None:
            if not secrets.compare_digest(known, digest):
                raise _error()
            return False
        self._write((*state.operations, operation), state.snapshots, state.pending_operation_ids)
        return True

    def queue_local(self, operation: EncryptedPatchOperation) -> bool:
        operation = _validated_operation(operation)
        state = self._load()
        digest = _fingerprint(operation)
        known = state.receipts.get(operation.operation_id)
        if known is not None:
            if not secrets.compare_digest(known, digest):
                raise _error()
            return False
        self._write(
            (*state.operations, operation),
            state.snapshots,
            (*state.pending_operation_ids, operation.operation_id),
        )
        return True

    def merge_snapshot(self, snapshot: AuthenticatedCollectionSnapshot) -> bool:
        if not isinstance(snapshot, AuthenticatedCollectionSnapshot):
            raise _error()
        verified = AuthenticatedCollectionSnapshot.from_dict(
            snapshot.to_dict(), authentication_key=self._authentication_key
        )
        if verified.collection_id != self._collection_id:
            raise _error()
        state = self._load()
        if any(
            item.canonical_payload_sha256 == verified.canonical_payload_sha256
            for item in state.snapshots
        ):
            return False
        self._write(
            state.operations,
            (*state.snapshots, verified),
            state.pending_operation_ids,
        )
        return True

    def current_records(self) -> tuple[OpaqueCiphertextEnvelope, ...]:
        return _resolve_convergent_records(self._load())

    @property
    def accepted_operation_count(self) -> int:
        return len(self._load().receipts)

    def pending_operations(self) -> tuple[EncryptedPatchOperation, ...]:
        state = self._load()
        pending = set(state.pending_operation_ids)
        return tuple(sorted(item for item in state.operations if item.operation_id in pending))

    def confirm_remote_acceptance(self, operation_id: str) -> None:
        operation_id = _canonical_uuid(operation_id)
        state = self._load()
        if operation_id not in state.pending_operation_ids:
            raise _error()
        self._write(
            state.operations,
            state.snapshots,
            (item for item in state.pending_operation_ids if item != operation_id),
        )

    def synchronize_to(self, remote: DurableEncryptedConvergentReplica) -> int:
        if (
            not isinstance(remote, DurableEncryptedConvergentReplica)
            or remote._collection_id != self._collection_id
        ):
            raise _error()
        accepted = 0
        for operation in self.pending_operations():
            remote.ingest_remote(operation)
            self.confirm_remote_acceptance(operation.operation_id)
            accepted += 1
        return accepted


def _outbox_default() -> dict[str, Any]:
    return {
        "format": "atlasvault-encrypted-outbox-state",
        "version": 1,
        "operations": [],
    }


def _load_outbox(store: _EncryptedQueueFile) -> list[EncryptedPatchOperation]:
    state = _mapping(store.read(_outbox_default()))
    _exact(state, {"format", "version", "operations"})
    if state["format"] != "atlasvault-encrypted-outbox-state" or state["version"] != 1:
        raise _error()
    raw = state["operations"]
    if not isinstance(raw, list) or len(raw) > MAXIMUM_QUEUE_OPERATIONS:
        raise _error()
    operations = [EncryptedPatchOperation.from_dict(_mapping(item)) for item in raw]
    if operations != sorted(operations) or len({item.operation_id for item in operations}) != len(
        operations
    ):
        raise _error()
    return operations


class DurableEncryptedOutbox:
    def __init__(self, path: str | Path, *, encryption_key: bytes) -> None:
        self._store = _EncryptedQueueFile(path, encryption_key, kind="outbox")

    def pending_operations(self) -> tuple[EncryptedPatchOperation, ...]:
        return tuple(_load_outbox(self._store))

    def next_pending(self) -> EncryptedPatchOperation | None:
        pending = self.pending_operations()
        return pending[0] if pending else None

    def enqueue(self, operation: EncryptedPatchOperation) -> None:
        operation = _validated_operation(operation)
        operations = _load_outbox(self._store)
        for current in operations:
            if current.operation_id == operation.operation_id:
                if not secrets.compare_digest(_fingerprint(current), _fingerprint(operation)):
                    raise _error()
                return
        if len(operations) >= MAXIMUM_QUEUE_OPERATIONS:
            raise _error()
        operations.append(operation)
        operations.sort()
        self._store.write(
            {
                "format": "atlasvault-encrypted-outbox-state",
                "version": 1,
                "operations": [item.to_dict() for item in operations],
            }
        )

    def confirm_remote_acceptance(self, operation_id: str) -> None:
        operation_id = _canonical_uuid(operation_id)
        operations = _load_outbox(self._store)
        retained = [item for item in operations if item.operation_id != operation_id]
        if len(retained) == len(operations):
            raise _error()
        self._store.write(
            {
                "format": "atlasvault-encrypted-outbox-state",
                "version": 1,
                "operations": [item.to_dict() for item in retained],
            }
        )


@dataclass(frozen=True)
class _InboxState:
    cursor: str | None
    pending_page: bool
    pending_next_cursor: str | None
    pending: tuple[EncryptedPatchOperation, ...]
    applied_fingerprints: Mapping[str, str]
    author_sequences: Mapping[str, int]
    object_revisions: Mapping[str, str]
    last_order: tuple[int, str, int, str] | None


def _inbox_default() -> dict[str, Any]:
    return {
        "format": "atlasvault-encrypted-inbox-state",
        "version": 1,
        "cursor": None,
        "pending_page": False,
        "pending_next_cursor": None,
        "pending_operations": [],
        "applied_fingerprints": {},
        "author_sequences": {},
        "object_revisions": {},
        "last_order": None,
    }


def _cursor(value: Any) -> str | None:
    if value is None:
        return None
    return _text(value, maximum=MAXIMUM_CURSOR_LENGTH)


def _load_inbox(store: _EncryptedQueueFile) -> _InboxState:
    state = _mapping(store.read(_inbox_default()))
    _exact(
        state,
        {
            "format",
            "version",
            "cursor",
            "pending_page",
            "pending_next_cursor",
            "pending_operations",
            "applied_fingerprints",
            "author_sequences",
            "object_revisions",
            "last_order",
        },
    )
    if state["format"] != "atlasvault-encrypted-inbox-state" or state["version"] != 1:
        raise _error()
    raw_pending = state["pending_operations"]
    if not isinstance(raw_pending, list) or len(raw_pending) > MAXIMUM_QUEUE_OPERATIONS:
        raise _error()
    pending = tuple(
        EncryptedPatchOperation.from_dict(_mapping(item)) for item in raw_pending
    )
    if pending != tuple(sorted(pending)) or len({item.operation_id for item in pending}) != len(
        pending
    ):
        raise _error()
    fingerprints = _mapping(state["applied_fingerprints"])
    if len(fingerprints) > MAXIMUM_QUEUE_OPERATIONS:
        raise _error()
    validated_fingerprints: dict[str, str] = {}
    for operation_id, digest in fingerprints.items():
        operation_id = _canonical_uuid(operation_id)
        digest = _text(digest, maximum=64)
        if _SHA256.fullmatch(digest) is None:
            raise _error()
        validated_fingerprints[operation_id] = digest
    author_sequences = _mapping(state["author_sequences"])
    object_revisions = _mapping(state["object_revisions"])
    if (
        len(author_sequences) > MAXIMUM_QUEUE_OPERATIONS
        or len(object_revisions) > MAXIMUM_QUEUE_OPERATIONS
    ):
        raise _error()
    validated_sequences = {
        _identifier(key): _integer(value) for key, value in author_sequences.items()
    }
    validated_revisions = {
        _identifier(key): _identifier(value) for key, value in object_revisions.items()
    }
    raw_order = state["last_order"]
    last_order: tuple[int, str, int, str] | None = None
    if raw_order is not None:
        if not isinstance(raw_order, list) or len(raw_order) != 4:
            raise _error()
        last_order = (
            _integer(raw_order[0]),
            _identifier(raw_order[1]),
            _integer(raw_order[2]),
            _canonical_uuid(raw_order[3]),
        )
    if type(state["pending_page"]) is not bool:
        raise _error()
    pending_page = state["pending_page"]
    pending_next_cursor = _cursor(state["pending_next_cursor"])
    if bool(pending) != pending_page:
        raise _error()
    return _InboxState(
        cursor=_cursor(state["cursor"]),
        pending_page=pending_page,
        pending_next_cursor=pending_next_cursor,
        pending=pending,
        applied_fingerprints=validated_fingerprints,
        author_sequences=validated_sequences,
        object_revisions=validated_revisions,
        last_order=last_order,
    )


def _inbox_dict(state: _InboxState) -> dict[str, Any]:
    return {
        "format": "atlasvault-encrypted-inbox-state",
        "version": 1,
        "cursor": state.cursor,
        "pending_page": state.pending_page,
        "pending_next_cursor": state.pending_next_cursor,
        "pending_operations": [item.to_dict() for item in state.pending],
        "applied_fingerprints": dict(state.applied_fingerprints),
        "author_sequences": dict(state.author_sequences),
        "object_revisions": dict(state.object_revisions),
        "last_order": list(state.last_order) if state.last_order is not None else None,
    }


def _advance_metadata(
    operation: EncryptedPatchOperation,
    *,
    author_sequences: dict[str, int],
    object_revisions: dict[str, str],
    last_order: tuple[int, str, int, str] | None,
) -> tuple[int, str, int, str]:
    if last_order is not None and operation.order_key <= last_order:
        raise _error()
    expected_sequence = author_sequences.get(operation.author_device_id, 0) + 1
    if operation.author_sequence != expected_sequence:
        raise _error()
    expected_parent = object_revisions.get(operation.envelope.object_id)
    if operation.envelope.parent_revision != expected_parent:
        raise _error()
    author_sequences[operation.author_device_id] = operation.author_sequence
    object_revisions[operation.envelope.object_id] = operation.envelope.revision
    return operation.order_key


class DurableEncryptedInbox:
    def __init__(self, path: str | Path, *, encryption_key: bytes) -> None:
        self._store = _EncryptedQueueFile(path, encryption_key, kind="inbox")

    @property
    def cursor(self) -> str | None:
        return _load_inbox(self._store).cursor

    def pending_operations(self) -> tuple[EncryptedPatchOperation, ...]:
        return _load_inbox(self._store).pending

    def stage_page(
        self,
        *,
        expected_cursor: str | None,
        next_cursor: str | None,
        operations: Iterable[EncryptedPatchOperation],
    ) -> None:
        expected_cursor = _cursor(expected_cursor)
        next_cursor = _cursor(next_cursor)
        state = _load_inbox(self._store)
        if state.pending or state.cursor != expected_cursor:
            raise _error()
        incoming = tuple(operations)
        if len(incoming) > MAXIMUM_QUEUE_OPERATIONS:
            raise _error()
        incoming = tuple(_validated_operation(item) for item in incoming)
        if incoming != tuple(sorted(incoming)) or len(
            {item.operation_id for item in incoming}
        ) != len(incoming):
            raise _error()

        sequences = dict(state.author_sequences)
        revisions = dict(state.object_revisions)
        last_order = state.last_order
        new_operations: list[EncryptedPatchOperation] = []
        for operation in incoming:
            digest = _fingerprint(operation)
            known = state.applied_fingerprints.get(operation.operation_id)
            if known is not None:
                if not secrets.compare_digest(known, digest):
                    raise _error()
                continue
            last_order = _advance_metadata(
                operation,
                author_sequences=sequences,
                object_revisions=revisions,
                last_order=last_order,
            )
            new_operations.append(operation)

        if len(state.applied_fingerprints) + len(new_operations) > MAXIMUM_QUEUE_OPERATIONS:
            raise _error()
        updated = replace(
            state,
            cursor=next_cursor if not new_operations else state.cursor,
            pending_page=bool(new_operations),
            pending_next_cursor=next_cursor,
            pending=tuple(new_operations),
        )
        self._store.write(_inbox_dict(updated))

    def apply_next(
        self,
        apply: Callable[[EncryptedPatchOperation], None],
    ) -> EncryptedPatchOperation | None:
        if not callable(apply):
            raise _error()
        state = _load_inbox(self._store)
        if not state.pending:
            return None
        operation = state.pending[0]
        apply(operation)
        sequences = dict(state.author_sequences)
        revisions = dict(state.object_revisions)
        last_order = _advance_metadata(
            operation,
            author_sequences=sequences,
            object_revisions=revisions,
            last_order=state.last_order,
        )
        fingerprints = dict(state.applied_fingerprints)
        fingerprints[operation.operation_id] = _fingerprint(operation)
        remaining = state.pending[1:]
        updated = replace(
            state,
            cursor=state.pending_next_cursor if not remaining else state.cursor,
            pending_page=bool(remaining),
            pending_next_cursor=state.pending_next_cursor if remaining else None,
            pending=remaining,
            applied_fingerprints=fingerprints,
            author_sequences=sequences,
            object_revisions=revisions,
            last_order=last_order,
        )
        self._store.write(_inbox_dict(updated))
        return operation
