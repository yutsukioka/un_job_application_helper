"""Opaque in-memory storage with compare-and-set and retry semantics."""

from __future__ import annotations

import hashlib
import heapq
import math
import re
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from atlasvault_api.controls import AbuseControlPolicy

OPAQUE_ID_MAX_LENGTH = 128
OPAQUE_ID_PATTERN = (
    r"^(?:[A-Za-z0-9_~-][A-Za-z0-9._~-]*|"
    r"\.[A-Za-z0-9_~-][A-Za-z0-9._~-]*|"
    r"\.\.[A-Za-z0-9._~-]+)$"
)
REVISION_MAX_LENGTH = 128
IDEMPOTENCY_KEY_MAX_LENGTH = 128
HEADER_SAFE_ASCII_PATTERN = r"^[!-~]+$"
REVISION_PATTERN = r"^[A-Za-z0-9._~-]+$"
IF_MATCH_HEADER_PATTERN = r'^(?:\*|"[A-Za-z0-9._~-]+")$'
MAX_KEY_EPOCH = (1 << 63) - 1
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 100
CURSOR_PREFIX = "avcur1-"
CURSOR_LIFETIME_SECONDS = 300
IDEMPOTENCY_RECEIPT_LIFETIME_SECONDS = 600
MAX_RECEIPT_PRUNE_PER_WRITE = 64
MAX_CURSOR_PRUNE_PER_LIST = 64
_STRICT_MODEL = ConfigDict(extra="forbid", frozen=True, strict=True)
Base64EnvelopeValue = Annotated[
    str,
    Field(
        min_length=1,
        json_schema_extra={"contentEncoding": "base64"},
    ),
]


class EncryptedVaultMetadataEnvelopeModel(BaseModel):
    model_config = _STRICT_MODEL

    format: Literal["atlasvault-encrypted-metadata-envelope"]
    version: int = Field(ge=1)
    vault_id: str = Field(
        min_length=1,
        max_length=OPAQUE_ID_MAX_LENGTH,
        pattern=OPAQUE_ID_PATTERN,
    )
    revision: str = Field(
        min_length=1,
        max_length=REVISION_MAX_LENGTH,
        pattern=REVISION_PATTERN,
        json_schema_extra={"not": {"const": "*"}},
    )
    key_epoch: int = Field(
        ge=1,
        le=MAX_KEY_EPOCH,
        json_schema_extra={"maximum": MAX_KEY_EPOCH},
    )
    nonce_b64: Base64EnvelopeValue
    ciphertext_b64: Base64EnvelopeValue
    aad_b64: Base64EnvelopeValue
    signature_b64: Base64EnvelopeValue
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def __get_pydantic_json_schema__(
        cls,
        core_schema: Any,
        handler: Any,
    ) -> dict[str, Any]:
        schema = handler(core_schema)
        schema["properties"]["key_epoch"]["maximum"] = MAX_KEY_EPOCH
        return schema

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
    object_id: str = Field(
        min_length=1,
        max_length=OPAQUE_ID_MAX_LENGTH,
        pattern=OPAQUE_ID_PATTERN,
    )
    revision: str = Field(
        min_length=1,
        max_length=REVISION_MAX_LENGTH,
        pattern=REVISION_PATTERN,
        json_schema_extra={"not": {"const": "*"}},
    )
    parent_revision: str | None = Field(
        min_length=1,
        max_length=REVISION_MAX_LENGTH,
        pattern=REVISION_PATTERN,
        json_schema_extra={"not": {"const": "*"}},
    )
    key_epoch: int = Field(
        ge=1,
        le=MAX_KEY_EPOCH,
        json_schema_extra={"maximum": MAX_KEY_EPOCH},
    )
    nonce_b64: Base64EnvelopeValue
    ciphertext_b64: Base64EnvelopeValue
    aad_b64: Base64EnvelopeValue
    signature_b64: Base64EnvelopeValue
    tombstone: bool
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def __get_pydantic_json_schema__(
        cls,
        core_schema: Any,
        handler: Any,
    ) -> dict[str, Any]:
        schema = handler(core_schema)
        schema["properties"]["key_epoch"]["maximum"] = MAX_KEY_EPOCH
        return schema

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


class OpaqueStorageCapacityExceeded(ValueError):
    """Raised before an opaque write would exceed retained-state limits."""


StorageEnvelope = EncryptedVaultMetadataEnvelopeModel | OpaqueCiphertextEnvelopeModel


@dataclass(frozen=True)
class _WriteAttempt:
    expected_revision: str
    envelope_fingerprint: bytes


@dataclass(frozen=True)
class _PreparedWrite:
    attempt: _WriteAttempt
    envelope_bytes: int


@dataclass(frozen=True)
class _Receipt:
    attempt: _WriteAttempt
    response: StorageEnvelope
    expires_at: float


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
    expires_at: float
    next_cursor: str | None = None


@dataclass(frozen=True)
class _CapacityDelta:
    vaults: int
    objects: int
    patches: int
    revisions: int
    envelope_bytes: int


class InMemoryOpaqueStore:
    """Thread-safe test store that never decodes or indexes ciphertext fields."""

    def __init__(
        self,
        *,
        entropy: Callable[[int], bytes] = secrets.token_bytes,
        monotonic: Callable[[], float] = time.monotonic,
        limits: AbuseControlPolicy | None = None,
    ) -> None:
        self._entropy = entropy
        self._monotonic = monotonic
        self._limits = limits or AbuseControlPolicy()
        self._lock = threading.RLock()
        self._vaults: dict[tuple[str, str], _VaultState] = {}
        self._vaults_per_account: dict[str, int] = {}
        self._objects_per_account: dict[str, int] = {}
        self._patches_per_account: dict[str, int] = {}
        self._revisions_per_account: dict[str, int] = {}
        self._retained_bytes = 0
        self._retained_bytes_per_account: dict[str, int] = {}
        self._cursors: dict[str, _Cursor] = {}
        self._cursor_expiries: list[tuple[float, int, str]] = []
        self._next_cursor_expiry_sequence = 0
        self._receipt_expiries: list[
            tuple[float, int, _VaultState, tuple[str, str]]
        ] = []
        self._next_receipt_expiry_sequence = 0
        self._last_observed_clock: float | None = None

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
        prepared = _prepare_write(envelope, expected_revision, idempotency_key)
        with self._lock:
            now = self._clock_now()
            self._prune_expired_receipts(now)
            state = self._state(account_id, vault_id)
            replay = self._replay(
                state,
                "metadata",
                idempotency_key,
                prepared.attempt,
                now,
            )
            if replay is not None:
                return _require_metadata(replay)
            duplicate = _same_revision(state.metadata, envelope)
            if duplicate is not None:
                self._record(
                    state,
                    "metadata",
                    idempotency_key,
                    prepared.attempt,
                    duplicate,
                    now,
                )
                return _require_metadata(duplicate)
            _reject_historical_revision(
                state.metadata_revision_fingerprints,
                envelope,
            )
            _require_cas(state.metadata, expected_revision)
            capacity = self._capacity_delta(
                account_id,
                vault_id,
                prepared.envelope_bytes,
            )
            self._require_capacity(account_id, capacity)
            state.metadata = envelope
            state.metadata_revision_fingerprints[envelope.revision] = (
                prepared.attempt.envelope_fingerprint
            )
            self._record(
                state,
                "metadata",
                idempotency_key,
                prepared.attempt,
                envelope,
                now,
            )
            self._retain_state(account_id, vault_id, state, capacity)
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
        prepared = _prepare_write(envelope, expected_revision, idempotency_key)
        with self._lock:
            now = self._clock_now()
            self._prune_expired_receipts(now)
            state = self._state(account_id, vault_id)
            scope = f"object:{object_id}"
            replay = self._replay(
                state,
                scope,
                idempotency_key,
                prepared.attempt,
                now,
            )
            if replay is not None:
                return _require_opaque(replay)
            current = state.objects.get(object_id)
            revisions = state.object_revision_fingerprints.get(object_id, {})
            duplicate = _same_revision(current, envelope)
            if duplicate is not None:
                self._record(
                    state,
                    scope,
                    idempotency_key,
                    prepared.attempt,
                    duplicate,
                    now,
                )
                return _require_opaque(duplicate)
            _reject_historical_revision(revisions, envelope)
            _require_parent_cas(current, envelope, expected_revision)
            capacity = self._capacity_delta(
                account_id,
                vault_id,
                prepared.envelope_bytes,
                objects=int(current is None),
            )
            self._require_capacity(account_id, capacity)
            state.objects[object_id] = envelope
            revisions[envelope.revision] = prepared.attempt.envelope_fingerprint
            state.object_revision_fingerprints[object_id] = revisions
            self._record(
                state,
                scope,
                idempotency_key,
                prepared.attempt,
                envelope,
                now,
            )
            self._retain_state(account_id, vault_id, state, capacity)
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
        prepared = _prepare_write(envelope, expected_revision, idempotency_key)
        with self._lock:
            now = self._clock_now()
            self._prune_expired_receipts(now)
            state = self._state(account_id, vault_id)
            replay = self._replay(
                state,
                "patches",
                idempotency_key,
                prepared.attempt,
                now,
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
                    prepared.attempt,
                    duplicate,
                    now,
                )
                return _require_opaque(duplicate)
            current = state.patches[-1] if state.patches else None
            _require_parent_cas(current, envelope, expected_revision)
            capacity = self._capacity_delta(
                account_id,
                vault_id,
                prepared.envelope_bytes,
                patches=1,
            )
            self._require_capacity(account_id, capacity)
            state.patches.append(envelope)
            state.patches_by_revision[envelope.revision] = envelope
            self._record(
                state,
                "patches",
                idempotency_key,
                prepared.attempt,
                envelope,
                now,
            )
            self._retain_state(account_id, vault_id, state, capacity)
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
            now = self._clock_now()
            self._prune_expired_cursors(now)
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
                    now=now,
                )
            record = self._cursors.get(cursor)
            if (
                record is None
                or record.expires_at <= now
                or record.account_id != account_id
                or record.vault_id != vault_id
                or (page_size is not None and page_size != record.page_size)
            ):
                if record is not None and record.expires_at <= now:
                    self._cursors.pop(cursor, None)
                raise InvalidOpaqueStorageRequest
            return self._page(
                account_id,
                vault_id,
                patches,
                start=record.start,
                end=record.end,
                page_size=record.page_size,
                record=record,
                now=now,
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
        prepared = _prepare_write(envelope, expected_revision, idempotency_key)
        with self._lock:
            now = self._clock_now()
            self._prune_expired_receipts(now)
            state = self._state(account_id, vault_id)
            replay = self._replay(
                state,
                "snapshot",
                idempotency_key,
                prepared.attempt,
                now,
            )
            if replay is not None:
                return _require_opaque(replay)
            duplicate = _same_revision(state.snapshot, envelope)
            if duplicate is not None:
                self._record(
                    state,
                    "snapshot",
                    idempotency_key,
                    prepared.attempt,
                    duplicate,
                    now,
                )
                return _require_opaque(duplicate)
            _reject_historical_revision(
                state.snapshot_revision_fingerprints,
                envelope,
            )
            _require_parent_cas(state.snapshot, envelope, expected_revision)
            capacity = self._capacity_delta(
                account_id,
                vault_id,
                prepared.envelope_bytes,
            )
            self._require_capacity(account_id, capacity)
            state.snapshot = envelope
            state.snapshot_revision_fingerprints[envelope.revision] = (
                prepared.attempt.envelope_fingerprint
            )
            self._record(
                state,
                "snapshot",
                idempotency_key,
                prepared.attempt,
                envelope,
                now,
            )
            self._retain_state(account_id, vault_id, state, capacity)
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
        return self._vaults.get((account_id, vault_id), _VaultState())

    def _retain_state(
        self,
        account_id: str,
        vault_id: str,
        state: _VaultState,
        capacity: _CapacityDelta,
    ) -> None:
        self._vaults[(account_id, vault_id)] = state
        if capacity.vaults:
            self._vaults_per_account[account_id] = (
                self._vaults_per_account.get(account_id, 0) + capacity.vaults
            )
        if capacity.objects:
            self._objects_per_account[account_id] = (
                self._objects_per_account.get(account_id, 0) + capacity.objects
            )
        if capacity.patches:
            self._patches_per_account[account_id] = (
                self._patches_per_account.get(account_id, 0) + capacity.patches
            )
        self._revisions_per_account[account_id] = (
            self._revisions_per_account.get(account_id, 0) + capacity.revisions
        )
        self._retained_bytes += capacity.envelope_bytes
        self._retained_bytes_per_account[account_id] = (
            self._retained_bytes_per_account.get(account_id, 0)
            + capacity.envelope_bytes
        )

    def _capacity_delta(
        self,
        account_id: str,
        vault_id: str,
        envelope_bytes: int,
        *,
        objects: int = 0,
        patches: int = 0,
    ) -> _CapacityDelta:
        return _CapacityDelta(
            vaults=int((account_id, vault_id) not in self._vaults),
            objects=objects,
            patches=patches,
            revisions=1,
            envelope_bytes=envelope_bytes,
        )

    def _require_capacity(
        self,
        account_id: str,
        capacity: _CapacityDelta,
    ) -> None:
        limits = self._limits
        if (
            len(self._vaults) + capacity.vaults > limits.max_retained_vaults
            or self._vaults_per_account.get(account_id, 0) + capacity.vaults
            > limits.max_retained_vaults_per_account
            or self._objects_per_account.get(account_id, 0) + capacity.objects
            > limits.max_retained_objects_per_account
            or self._patches_per_account.get(account_id, 0) + capacity.patches
            > limits.max_retained_patches_per_account
            or self._revisions_per_account.get(account_id, 0) + capacity.revisions
            > limits.max_retained_revisions_per_account
            or self._retained_bytes + capacity.envelope_bytes
            > limits.max_retained_bytes - limits.reserved_retained_bytes
            or self._retained_bytes_per_account.get(account_id, 0)
            + capacity.envelope_bytes
            > limits.max_retained_bytes_per_account
        ):
            raise OpaqueStorageCapacityExceeded

    def _replay(
        self,
        state: _VaultState,
        scope: str,
        idempotency_key: str,
        attempt: _WriteAttempt,
        now: float,
    ) -> StorageEnvelope | None:
        receipt = state.receipts.get((scope, idempotency_key))
        if receipt is None:
            return None
        if receipt.expires_at <= now:
            state.receipts.pop((scope, idempotency_key), None)
            return None
        if receipt.attempt != attempt:
            raise OpaqueStorageConflict
        return receipt.response

    def _record(
        self,
        state: _VaultState,
        scope: str,
        idempotency_key: str,
        attempt: _WriteAttempt,
        response: StorageEnvelope,
        now: float,
    ) -> None:
        receipt_key = (scope, idempotency_key)
        expires_at = now + IDEMPOTENCY_RECEIPT_LIFETIME_SECONDS
        state.receipts[receipt_key] = _Receipt(
            attempt=attempt,
            response=response,
            expires_at=expires_at,
        )
        self._next_receipt_expiry_sequence += 1
        heapq.heappush(
            self._receipt_expiries,
            (
                expires_at,
                self._next_receipt_expiry_sequence,
                state,
                receipt_key,
            ),
        )

    def _prune_expired_receipts(self, now: float) -> None:
        remaining = MAX_RECEIPT_PRUNE_PER_WRITE
        while (
            remaining and self._receipt_expiries and self._receipt_expiries[0][0] <= now
        ):
            _, _, state, receipt_key = heapq.heappop(self._receipt_expiries)
            remaining -= 1
            receipt = state.receipts.get(receipt_key)
            if receipt is not None and receipt.expires_at <= now:
                state.receipts.pop(receipt_key, None)

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
        now: float,
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
                    now=now,
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
        now: float | None = None,
    ) -> str:
        if now is None:
            now = self._clock_now()
        for _ in range(8):
            token = f"{CURSOR_PREFIX}{_entropy_bytes(self._entropy, 16).hex()}"
            if token not in self._cursors:
                expires_at = now + CURSOR_LIFETIME_SECONDS
                self._cursors[token] = _Cursor(
                    account_id=account_id,
                    vault_id=vault_id,
                    start=start,
                    end=end,
                    page_size=page_size,
                    expires_at=expires_at,
                )
                self._next_cursor_expiry_sequence += 1
                heapq.heappush(
                    self._cursor_expiries,
                    (expires_at, self._next_cursor_expiry_sequence, token),
                )
                return token
        raise RuntimeError("cursor entropy source repeated")

    def _clock_now(self) -> float:
        now = self._monotonic()
        if (
            not math.isfinite(now)
            or self._last_observed_clock is not None
            and now < self._last_observed_clock
        ):
            raise InvalidOpaqueStorageRequest
        self._last_observed_clock = now
        return now

    def _prune_expired_cursors(self, now: float) -> None:
        remaining = MAX_CURSOR_PRUNE_PER_LIST
        while (
            remaining and self._cursor_expiries and self._cursor_expiries[0][0] <= now
        ):
            _, _, token = heapq.heappop(self._cursor_expiries)
            remaining -= 1
            cursor = self._cursors.get(token)
            if cursor is not None and cursor.expires_at <= now:
                self._cursors.pop(token, None)


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


def _prepare_write(
    envelope: StorageEnvelope,
    expected_revision: str,
    idempotency_key: str,
) -> _PreparedWrite:
    _require_write_tokens(expected_revision, idempotency_key)
    encoded = envelope.model_dump_json().encode("utf-8")
    return _PreparedWrite(
        attempt=_WriteAttempt(
            expected_revision=expected_revision,
            envelope_fingerprint=hashlib.sha256(encoded).digest(),
        ),
        envelope_bytes=len(encoded),
    )


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
        or expected_revision != "*"
        and re.fullmatch(REVISION_PATTERN, expected_revision) is None
        or not idempotency_key
        or len(idempotency_key) > IDEMPOTENCY_KEY_MAX_LENGTH
        or not _is_header_safe_ascii(idempotency_key)
    ):
        raise InvalidOpaqueStorageRequest


def _is_header_safe_ascii(value: str) -> bool:
    return all("!" <= character <= "~" for character in value)


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
