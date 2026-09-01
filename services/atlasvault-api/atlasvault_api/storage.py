"""Opaque in-memory storage with compare-and-set and retry semantics."""

from __future__ import annotations

import hashlib
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

OPAQUE_ID_MAX_LENGTH = 128
REVISION_MAX_LENGTH = 128
IDEMPOTENCY_KEY_MAX_LENGTH = 128
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 100
CURSOR_PREFIX = "avcur1-"
_STRICT_MODEL = ConfigDict(extra="forbid", frozen=True)


class EncryptedVaultMetadataEnvelopeModel(BaseModel):
    model_config = _STRICT_MODEL

    format: Literal["atlasvault-encrypted-metadata-envelope"]
    version: int = Field(ge=1)
    vault_id: str = Field(min_length=1, max_length=OPAQUE_ID_MAX_LENGTH)
    revision: str = Field(
        min_length=1,
        max_length=REVISION_MAX_LENGTH,
        json_schema_extra={"not": {"const": "*"}},
    )
    key_epoch: int = Field(ge=1)
    nonce_b64: str = Field(min_length=1)
    ciphertext_b64: str = Field(min_length=1)
    aad_b64: str = Field(min_length=1)
    signature_b64: str = Field(min_length=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("revision")
    @classmethod
    def reject_creation_wildcard(cls, value: str) -> str:
        if value == "*":
            raise ValueError("creation wildcard cannot be stored as a revision")
        return value


class OpaqueCiphertextEnvelopeModel(BaseModel):
    model_config = _STRICT_MODEL

    format: Literal["atlasvault-opaque-ciphertext-envelope"]
    version: int = Field(ge=1)
    object_id: str = Field(min_length=1, max_length=OPAQUE_ID_MAX_LENGTH)
    revision: str = Field(
        min_length=1,
        max_length=REVISION_MAX_LENGTH,
        json_schema_extra={"not": {"const": "*"}},
    )
    parent_revision: str | None = Field(
        default=None,
        max_length=REVISION_MAX_LENGTH,
        json_schema_extra={"not": {"const": "*"}},
    )
    key_epoch: int = Field(ge=1)
    nonce_b64: str = Field(min_length=1)
    ciphertext_b64: str = Field(min_length=1)
    aad_b64: str = Field(min_length=1)
    signature_b64: str = Field(min_length=1)
    tombstone: bool
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("revision", "parent_revision")
    @classmethod
    def reject_creation_wildcard(cls, value: str | None) -> str | None:
        if value == "*":
            raise ValueError("creation wildcard cannot be stored as a revision")
        return value


class OpaqueCiphertextPageModel(BaseModel):
    model_config = _STRICT_MODEL

    objects: tuple[OpaqueCiphertextEnvelopeModel, ...]
    next_cursor: str | None


class OpaqueStorageConflict(ValueError):
    """Raised when CAS or idempotency invariants reject a write."""


class OpaqueStorageNotFound(ValueError):
    """Raised when an opaque resource does not exist."""


class InvalidOpaqueStorageRequest(ValueError):
    """Raised when opaque path or cursor metadata is inconsistent."""


StorageEnvelope = EncryptedVaultMetadataEnvelopeModel | OpaqueCiphertextEnvelopeModel


@dataclass(frozen=True)
class _WriteAttempt:
    expected_revision: str
    envelope: StorageEnvelope


@dataclass(frozen=True)
class _Receipt:
    attempt: _WriteAttempt
    response: StorageEnvelope


@dataclass
class _VaultState:
    metadata: EncryptedVaultMetadataEnvelopeModel | None = None
    metadata_revision_fingerprints: dict[str, bytes] = field(default_factory=dict)
    objects: dict[str, OpaqueCiphertextEnvelopeModel] = field(default_factory=dict)
    object_revision_fingerprints: dict[
        str,
        dict[str, bytes],
    ] = field(default_factory=dict)
    patches: list[OpaqueCiphertextEnvelopeModel] = field(default_factory=list)
    patches_by_revision: dict[str, OpaqueCiphertextEnvelopeModel] = field(
        default_factory=dict
    )
    snapshot: OpaqueCiphertextEnvelopeModel | None = None
    snapshot_revision_fingerprints: dict[str, bytes] = field(default_factory=dict)
    receipts: dict[tuple[str, str], _Receipt] = field(default_factory=dict)


@dataclass
class _Cursor:
    account_id: str
    vault_id: str
    start: int
    end: int
    page_size: int
    next_cursor: str | None = None


class InMemoryOpaqueStore:
    """Thread-safe test store that never decodes or indexes ciphertext fields."""

    def __init__(
        self,
        *,
        entropy: Callable[[int], bytes] = secrets.token_bytes,
    ) -> None:
        self._entropy = entropy
        self._lock = threading.RLock()
        self._vaults: dict[tuple[str, str], _VaultState] = {}
        self._cursors: dict[str, _Cursor] = {}

    def put_metadata(
        self,
        account_id: str,
        vault_id: str,
        envelope: EncryptedVaultMetadataEnvelopeModel,
        *,
        expected_revision: str,
        idempotency_key: str,
    ) -> EncryptedVaultMetadataEnvelopeModel:
        if envelope.vault_id != vault_id:
            raise InvalidOpaqueStorageRequest
        with self._lock:
            state = self._state(account_id, vault_id)
            replay = self._replay(
                state,
                "metadata",
                idempotency_key,
                expected_revision,
                envelope,
            )
            if replay is not None:
                return _require_metadata(replay)
            duplicate = _same_revision(state.metadata, envelope)
            if duplicate is not None:
                self._record(
                    state,
                    "metadata",
                    idempotency_key,
                    expected_revision,
                    envelope,
                    duplicate,
                )
                return _require_metadata(duplicate)
            _reject_historical_revision(
                state.metadata_revision_fingerprints,
                envelope,
            )
            _require_cas(state.metadata, expected_revision)
            state.metadata = envelope
            state.metadata_revision_fingerprints[envelope.revision] = (
                _envelope_fingerprint(envelope)
            )
            self._record(
                state,
                "metadata",
                idempotency_key,
                expected_revision,
                envelope,
                envelope,
            )
            return envelope

    def get_metadata(
        self,
        account_id: str,
        vault_id: str,
    ) -> EncryptedVaultMetadataEnvelopeModel:
        with self._lock:
            state = self._vaults.get((account_id, vault_id))
            if state is None or state.metadata is None:
                raise OpaqueStorageNotFound
            return state.metadata

    def put_object(
        self,
        account_id: str,
        vault_id: str,
        object_id: str,
        envelope: OpaqueCiphertextEnvelopeModel,
        *,
        expected_revision: str,
        idempotency_key: str,
    ) -> OpaqueCiphertextEnvelopeModel:
        if envelope.object_id != object_id:
            raise InvalidOpaqueStorageRequest
        with self._lock:
            state = self._state(account_id, vault_id)
            scope = f"object:{object_id}"
            replay = self._replay(
                state,
                scope,
                idempotency_key,
                expected_revision,
                envelope,
            )
            if replay is not None:
                return _require_opaque(replay)
            current = state.objects.get(object_id)
            revisions = state.object_revision_fingerprints.setdefault(object_id, {})
            duplicate = _same_revision(current, envelope)
            if duplicate is not None:
                self._record(
                    state,
                    scope,
                    idempotency_key,
                    expected_revision,
                    envelope,
                    duplicate,
                )
                return _require_opaque(duplicate)
            _reject_historical_revision(revisions, envelope)
            _require_parent_cas(current, envelope, expected_revision)
            state.objects[object_id] = envelope
            revisions[envelope.revision] = _envelope_fingerprint(envelope)
            self._record(
                state,
                scope,
                idempotency_key,
                expected_revision,
                envelope,
                envelope,
            )
            return envelope

    def get_object(
        self,
        account_id: str,
        vault_id: str,
        object_id: str,
    ) -> OpaqueCiphertextEnvelopeModel:
        with self._lock:
            state = self._vaults.get((account_id, vault_id))
            if state is None or object_id not in state.objects:
                raise OpaqueStorageNotFound
            return state.objects[object_id]

    def append_patch(
        self,
        account_id: str,
        vault_id: str,
        envelope: OpaqueCiphertextEnvelopeModel,
        *,
        expected_revision: str,
        idempotency_key: str,
    ) -> OpaqueCiphertextEnvelopeModel:
        with self._lock:
            state = self._state(account_id, vault_id)
            replay = self._replay(
                state,
                "patches",
                idempotency_key,
                expected_revision,
                envelope,
            )
            if replay is not None:
                return _require_opaque(replay)
            duplicate = _same_revision(
                state.patches_by_revision.get(envelope.revision),
                envelope,
            )
            if duplicate is not None:
                self._record(
                    state,
                    "patches",
                    idempotency_key,
                    expected_revision,
                    envelope,
                    duplicate,
                )
                return _require_opaque(duplicate)
            current = state.patches[-1] if state.patches else None
            _require_parent_cas(current, envelope, expected_revision)
            state.patches.append(envelope)
            state.patches_by_revision[envelope.revision] = envelope
            self._record(
                state,
                "patches",
                idempotency_key,
                expected_revision,
                envelope,
                envelope,
            )
            return envelope

    def list_patches(
        self,
        account_id: str,
        vault_id: str,
        *,
        cursor: str | None,
        page_size: int | None,
    ) -> OpaqueCiphertextPageModel:
        with self._lock:
            state = self._vaults.get((account_id, vault_id))
            patches = state.patches if state is not None else []
            if cursor is None:
                resolved_page_size = page_size or DEFAULT_PAGE_SIZE
                return self._page(
                    account_id,
                    vault_id,
                    patches,
                    start=0,
                    end=len(patches),
                    page_size=resolved_page_size,
                )
            record = self._cursors.get(cursor)
            if (
                record is None
                or record.account_id != account_id
                or record.vault_id != vault_id
                or (page_size is not None and page_size != record.page_size)
            ):
                raise InvalidOpaqueStorageRequest
            return self._page(
                account_id,
                vault_id,
                patches,
                start=record.start,
                end=record.end,
                page_size=record.page_size,
                record=record,
            )

    def put_snapshot(
        self,
        account_id: str,
        vault_id: str,
        envelope: OpaqueCiphertextEnvelopeModel,
        *,
        expected_revision: str,
        idempotency_key: str,
    ) -> OpaqueCiphertextEnvelopeModel:
        with self._lock:
            state = self._state(account_id, vault_id)
            replay = self._replay(
                state,
                "snapshot",
                idempotency_key,
                expected_revision,
                envelope,
            )
            if replay is not None:
                return _require_opaque(replay)
            duplicate = _same_revision(state.snapshot, envelope)
            if duplicate is not None:
                self._record(
                    state,
                    "snapshot",
                    idempotency_key,
                    expected_revision,
                    envelope,
                    duplicate,
                )
                return _require_opaque(duplicate)
            _reject_historical_revision(
                state.snapshot_revision_fingerprints,
                envelope,
            )
            _require_parent_cas(state.snapshot, envelope, expected_revision)
            state.snapshot = envelope
            state.snapshot_revision_fingerprints[envelope.revision] = (
                _envelope_fingerprint(envelope)
            )
            self._record(
                state,
                "snapshot",
                idempotency_key,
                expected_revision,
                envelope,
                envelope,
            )
            return envelope

    def get_snapshot(
        self,
        account_id: str,
        vault_id: str,
    ) -> OpaqueCiphertextEnvelopeModel:
        with self._lock:
            state = self._vaults.get((account_id, vault_id))
            if state is None or state.snapshot is None:
                raise OpaqueStorageNotFound
            return state.snapshot

    def _state(self, account_id: str, vault_id: str) -> _VaultState:
        return self._vaults.setdefault((account_id, vault_id), _VaultState())

    def _replay(
        self,
        state: _VaultState,
        scope: str,
        idempotency_key: str,
        expected_revision: str,
        envelope: StorageEnvelope,
    ) -> StorageEnvelope | None:
        _require_write_tokens(expected_revision, idempotency_key)
        receipt = state.receipts.get((scope, idempotency_key))
        if receipt is None:
            return None
        if receipt.attempt != _WriteAttempt(expected_revision, envelope):
            raise OpaqueStorageConflict
        return receipt.response

    def _record(
        self,
        state: _VaultState,
        scope: str,
        idempotency_key: str,
        expected_revision: str,
        envelope: StorageEnvelope,
        response: StorageEnvelope,
    ) -> None:
        state.receipts[(scope, idempotency_key)] = _Receipt(
            attempt=_WriteAttempt(expected_revision, envelope),
            response=response,
        )

    def _page(
        self,
        account_id: str,
        vault_id: str,
        patches: list[OpaqueCiphertextEnvelopeModel],
        *,
        start: int,
        end: int,
        page_size: int,
        record: _Cursor | None = None,
    ) -> OpaqueCiphertextPageModel:
        if not 1 <= page_size <= MAX_PAGE_SIZE or not 0 <= start <= end <= len(patches):
            raise InvalidOpaqueStorageRequest
        stop = min(start + page_size, end)
        next_cursor: str | None = None
        if stop < end:
            if record is not None and record.next_cursor is not None:
                next_cursor = record.next_cursor
            else:
                next_cursor = self._new_cursor(
                    account_id,
                    vault_id,
                    start=stop,
                    end=end,
                    page_size=page_size,
                )
                if record is not None:
                    record.next_cursor = next_cursor
        return OpaqueCiphertextPageModel(
            objects=tuple(patches[start:stop]),
            next_cursor=next_cursor,
        )

    def _new_cursor(
        self,
        account_id: str,
        vault_id: str,
        *,
        start: int,
        end: int,
        page_size: int,
    ) -> str:
        for _ in range(8):
            token = f"{CURSOR_PREFIX}{_entropy_bytes(self._entropy, 16).hex()}"
            if token not in self._cursors:
                self._cursors[token] = _Cursor(
                    account_id=account_id,
                    vault_id=vault_id,
                    start=start,
                    end=end,
                    page_size=page_size,
                )
                return token
        raise RuntimeError("cursor entropy source repeated")


def _same_revision(
    current: StorageEnvelope | None,
    incoming: StorageEnvelope,
) -> StorageEnvelope | None:
    if current is None or current.revision != incoming.revision:
        return None
    if current != incoming:
        raise OpaqueStorageConflict
    return current


def _reject_historical_revision(
    history: dict[str, bytes],
    incoming: StorageEnvelope,
) -> None:
    if incoming.revision in history:
        raise OpaqueStorageConflict


def _envelope_fingerprint(envelope: StorageEnvelope) -> bytes:
    return hashlib.sha256(envelope.model_dump_json().encode("utf-8")).digest()


def _require_cas(
    current: StorageEnvelope | None,
    expected_revision: str,
) -> None:
    if current is None:
        if expected_revision != "*":
            raise OpaqueStorageConflict
        return
    if expected_revision != current.revision:
        raise OpaqueStorageConflict


def _require_parent_cas(
    current: OpaqueCiphertextEnvelopeModel | None,
    incoming: OpaqueCiphertextEnvelopeModel,
    expected_revision: str,
) -> None:
    _require_cas(current, expected_revision)
    expected_parent = None if current is None else current.revision
    if incoming.parent_revision != expected_parent:
        raise OpaqueStorageConflict


def _require_write_tokens(expected_revision: str, idempotency_key: str) -> None:
    if (
        not expected_revision
        or len(expected_revision) > REVISION_MAX_LENGTH
        or not expected_revision.isascii()
        or not idempotency_key
        or len(idempotency_key) > IDEMPOTENCY_KEY_MAX_LENGTH
        or not idempotency_key.isascii()
    ):
        raise InvalidOpaqueStorageRequest


def _require_metadata(
    value: StorageEnvelope,
) -> EncryptedVaultMetadataEnvelopeModel:
    if not isinstance(value, EncryptedVaultMetadataEnvelopeModel):
        raise TypeError("storage receipt type mismatch")
    return value


def _require_opaque(value: StorageEnvelope) -> OpaqueCiphertextEnvelopeModel:
    if not isinstance(value, OpaqueCiphertextEnvelopeModel):
        raise TypeError("storage receipt type mismatch")
    return value


def _entropy_bytes(entropy: Callable[[int], bytes], length: int) -> bytes:
    result = entropy(length)
    if not isinstance(result, bytes) or len(result) != length:
        raise RuntimeError("entropy source returned an invalid result")
    return result
