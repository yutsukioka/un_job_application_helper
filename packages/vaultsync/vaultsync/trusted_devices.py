from __future__ import annotations

import hmac
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Literal, Mapping, Sequence

from vaultsync.device_identity import (
    DeviceIdentityError,
    SignedDeviceDescriptor,
    verify_signed_device_descriptor,
)
from vaultsync.format import VaultFormatError, _require_vault_id


TRUSTED_DEVICE_REGISTRY_FORMAT = "atlasvault-trusted-device-registry"
PAIRING_REPLAY_STORE_FORMAT = "atlasvault-pairing-replay"
TRUSTED_DEVICE_STATE_VERSION = 1
MAXIMUM_TRUSTED_PEERS = 64
MAXIMUM_REPLAY_ENTRIES = 2048
_DEVICE_ID = re.compile(r"^avd1-[0-9a-f]{64}$")


class TrustedDeviceStateError(ValueError):
    """Raised when protected trusted-device state is invalid."""


def _invalid_state() -> TrustedDeviceStateError:
    return TrustedDeviceStateError("trusted-device state is invalid")


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _invalid_state()
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str]) -> None:
    if set(value) != expected:
        raise _invalid_state()


def _integer(value: Any) -> int:
    if type(value) is not int:
        raise _invalid_state()
    return value


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise _invalid_state()
    return value


def _device_id(value: Any) -> str:
    if not isinstance(value, str) or _DEVICE_ID.fullmatch(value) is None:
        raise _invalid_state()
    return value


def _uuid(value: Any) -> str:
    if not isinstance(value, str):
        raise _invalid_state()
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise _invalid_state() from exc
    if str(parsed) != value:
        raise _invalid_state()
    return value


def _optional_uuid(value: Any) -> str | None:
    return None if value is None else _uuid(value)


def _timestamp(value: Any) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 20
        or value[4] != "-"
        or value[7] != "-"
        or value[10] != "T"
        or value[13] != ":"
        or value[16] != ":"
        or value[19] != "Z"
    ):
        raise _invalid_state()
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise _invalid_state() from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise _invalid_state()
    return value


def _time(value: str) -> datetime:
    return datetime.strptime(_timestamp(value), "%Y-%m-%dT%H:%M:%SZ")


def _sha256(value: Any) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise _invalid_state()
    return value


def _vault_id(value: Any) -> str:
    try:
        return _require_vault_id(value)
    except VaultFormatError as exc:
        raise _invalid_state() from exc


def _canonical_object(data: bytes) -> Mapping[str, Any]:
    try:
        if not isinstance(data, bytes) or not data:
            raise _invalid_state()
        result = _mapping(json.loads(data.decode("utf-8")))
        if not hmac.compare_digest(_canonical_bytes(result), data):
            raise _invalid_state()
        return result
    except TrustedDeviceStateError:
        raise
    except Exception as exc:
        raise _invalid_state() from exc


@dataclass(frozen=True)
class TrustedDevicePeer:
    peer_device_id: str
    peer_descriptor: SignedDeviceDescriptor
    pairing_transcript_sha256: str
    linked_at: str
    role: Literal["inviter", "invitee"]
    vault_id: str
    key_epoch: int
    delivery_id: str
    acknowledgement_sha256: str

    def __post_init__(self) -> None:
        try:
            _device_id(self.peer_device_id)
            if not isinstance(self.peer_descriptor, SignedDeviceDescriptor):
                raise _invalid_state()
            descriptor = verify_signed_device_descriptor(self.peer_descriptor)
            if not hmac.compare_digest(descriptor.device_id, self.peer_device_id):
                raise _invalid_state()
            _sha256(self.pairing_transcript_sha256)
            _timestamp(self.linked_at)
            if self.role not in {"inviter", "invitee"}:
                raise _invalid_state()
            _vault_id(self.vault_id)
            epoch = _integer(self.key_epoch)
            if epoch <= 0 or epoch > (1 << 63) - 1:
                raise _invalid_state()
            _uuid(self.delivery_id)
            _sha256(self.acknowledgement_sha256)
        except TrustedDeviceStateError:
            raise
        except (DeviceIdentityError, Exception) as exc:
            raise _invalid_state() from exc

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TrustedDevicePeer:
        try:
            obj = _mapping(data)
            _exact_keys(
                obj,
                {
                    "peer_device_id",
                    "peer_descriptor",
                    "pairing_transcript_sha256",
                    "linked_at",
                    "role",
                    "vault_id",
                    "key_epoch",
                    "delivery_id",
                    "acknowledgement_sha256",
                },
            )
            return cls(
                peer_device_id=_device_id(obj.get("peer_device_id")),
                peer_descriptor=SignedDeviceDescriptor.from_dict(
                    _mapping(obj.get("peer_descriptor"))
                ),
                pairing_transcript_sha256=_sha256(obj.get("pairing_transcript_sha256")),
                linked_at=_timestamp(obj.get("linked_at")),
                role=_text(obj.get("role")),
                vault_id=_vault_id(obj.get("vault_id")),
                key_epoch=_integer(obj.get("key_epoch")),
                delivery_id=_uuid(obj.get("delivery_id")),
                acknowledgement_sha256=_sha256(obj.get("acknowledgement_sha256")),
            )
        except TrustedDeviceStateError:
            raise
        except Exception as exc:
            raise _invalid_state() from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "peer_device_id": self.peer_device_id,
            "peer_descriptor": self.peer_descriptor.to_dict(),
            "pairing_transcript_sha256": self.pairing_transcript_sha256,
            "linked_at": self.linked_at,
            "role": self.role,
            "vault_id": self.vault_id,
            "key_epoch": self.key_epoch,
            "delivery_id": self.delivery_id,
            "acknowledgement_sha256": self.acknowledgement_sha256,
        }


@dataclass(frozen=True)
class TrustedDeviceRegistry:
    local_device_id: str
    revision: str
    parent_revision: str | None
    created_at: str
    updated_at: str
    devices: tuple[TrustedDevicePeer, ...] = ()
    format: str = TRUSTED_DEVICE_REGISTRY_FORMAT
    version: int = TRUSTED_DEVICE_STATE_VERSION

    def __post_init__(self) -> None:
        try:
            if (
                self.format != TRUSTED_DEVICE_REGISTRY_FORMAT
                or _integer(self.version) != TRUSTED_DEVICE_STATE_VERSION
            ):
                raise _invalid_state()
            _device_id(self.local_device_id)
            _uuid(self.revision)
            _optional_uuid(self.parent_revision)
            created = _time(self.created_at)
            updated = _time(self.updated_at)
            if updated < created:
                raise _invalid_state()
            devices = tuple(self.devices)
            if len(devices) > MAXIMUM_TRUSTED_PEERS:
                raise _invalid_state()
            if any(not isinstance(peer, TrustedDevicePeer) for peer in devices):
                raise _invalid_state()
            if any(peer.peer_device_id == self.local_device_id for peer in devices):
                raise _invalid_state()
            if len({peer.peer_device_id for peer in devices}) != len(devices):
                raise _invalid_state()
            if devices != tuple(sorted(devices, key=lambda peer: peer.peer_device_id)):
                raise _invalid_state()
            object.__setattr__(self, "devices", devices)
        except TrustedDeviceStateError:
            raise
        except Exception as exc:
            raise _invalid_state() from exc

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TrustedDeviceRegistry:
        try:
            obj = _mapping(data)
            _exact_keys(
                obj,
                {
                    "format",
                    "version",
                    "local_device_id",
                    "revision",
                    "parent_revision",
                    "created_at",
                    "updated_at",
                    "devices",
                },
            )
            values = obj.get("devices")
            if not isinstance(values, list):
                raise _invalid_state()
            return cls(
                format=obj.get("format"),
                version=_integer(obj.get("version")),
                local_device_id=_device_id(obj.get("local_device_id")),
                revision=_uuid(obj.get("revision")),
                parent_revision=_optional_uuid(obj.get("parent_revision")),
                created_at=_timestamp(obj.get("created_at")),
                updated_at=_timestamp(obj.get("updated_at")),
                devices=tuple(TrustedDevicePeer.from_dict(_mapping(value)) for value in values),
            )
        except TrustedDeviceStateError:
            raise
        except Exception as exc:
            raise _invalid_state() from exc

    @classmethod
    def from_canonical_bytes(cls, data: bytes) -> TrustedDeviceRegistry:
        return cls.from_dict(_canonical_object(data))

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "version": self.version,
            "local_device_id": self.local_device_id,
            "revision": self.revision,
            "parent_revision": self.parent_revision,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "devices": [peer.to_dict() for peer in self.devices],
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_dict())


class TrustedDeviceCommitOutcome(Enum):
    committed = "committed"
    already_trusted = "already_trusted"


def commit_trusted_device(
    registry: TrustedDeviceRegistry,
    peer: TrustedDevicePeer,
    *,
    revision: str,
    updated_at: str,
) -> tuple[TrustedDeviceRegistry, TrustedDeviceCommitOutcome]:
    try:
        if not isinstance(registry, TrustedDeviceRegistry) or not isinstance(
            peer, TrustedDevicePeer
        ):
            raise _invalid_state()
        existing = next(
            (value for value in registry.devices if value.peer_device_id == peer.peer_device_id),
            None,
        )
        if existing is not None:
            if existing == peer:
                return registry, TrustedDeviceCommitOutcome.already_trusted
            raise _invalid_state()
        if len(registry.devices) >= MAXIMUM_TRUSTED_PEERS:
            raise _invalid_state()
        next_revision = _uuid(revision)
        if next_revision in {registry.revision, registry.parent_revision}:
            raise _invalid_state()
        updated = _timestamp(updated_at)
        if _time(updated) < _time(registry.updated_at):
            raise _invalid_state()
        devices = tuple(sorted((*registry.devices, peer), key=lambda value: value.peer_device_id))
        return (
            TrustedDeviceRegistry(
                local_device_id=registry.local_device_id,
                revision=next_revision,
                parent_revision=registry.revision,
                created_at=registry.created_at,
                updated_at=updated,
                devices=devices,
            ),
            TrustedDeviceCommitOutcome.committed,
        )
    except TrustedDeviceStateError:
        raise
    except Exception as exc:
        raise _invalid_state() from exc


@dataclass(frozen=True)
class PairingReplayEntry:
    kind: Literal["offer", "acknowledgement"]
    object_id: str
    transcript_sha256: str
    consumed_at: str
    expires_at: str

    def __post_init__(self) -> None:
        if self.kind not in {"offer", "acknowledgement"}:
            raise _invalid_state()
        _uuid(self.object_id)
        _sha256(self.transcript_sha256)
        consumed = _time(self.consumed_at)
        expires = _time(self.expires_at)
        if expires <= consumed:
            raise _invalid_state()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PairingReplayEntry:
        try:
            obj = _mapping(data)
            _exact_keys(
                obj,
                {"kind", "object_id", "transcript_sha256", "consumed_at", "expires_at"},
            )
            return cls(
                kind=_text(obj.get("kind")),
                object_id=_uuid(obj.get("object_id")),
                transcript_sha256=_sha256(obj.get("transcript_sha256")),
                consumed_at=_timestamp(obj.get("consumed_at")),
                expires_at=_timestamp(obj.get("expires_at")),
            )
        except TrustedDeviceStateError:
            raise
        except Exception as exc:
            raise _invalid_state() from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "object_id": self.object_id,
            "transcript_sha256": self.transcript_sha256,
            "consumed_at": self.consumed_at,
            "expires_at": self.expires_at,
        }


def _replay_order(entry: PairingReplayEntry) -> tuple[str, str, str]:
    return (entry.expires_at, entry.kind, entry.object_id)


@dataclass(frozen=True)
class PairingReplayStore:
    local_device_id: str
    revision: str
    parent_revision: str | None
    created_at: str
    updated_at: str
    entries: tuple[PairingReplayEntry, ...] = ()
    format: str = PAIRING_REPLAY_STORE_FORMAT
    version: int = TRUSTED_DEVICE_STATE_VERSION

    def __post_init__(self) -> None:
        try:
            if (
                self.format != PAIRING_REPLAY_STORE_FORMAT
                or _integer(self.version) != TRUSTED_DEVICE_STATE_VERSION
            ):
                raise _invalid_state()
            _device_id(self.local_device_id)
            _uuid(self.revision)
            _optional_uuid(self.parent_revision)
            if _time(self.updated_at) < _time(self.created_at):
                raise _invalid_state()
            entries = tuple(self.entries)
            if len(entries) > MAXIMUM_REPLAY_ENTRIES:
                raise _invalid_state()
            if any(not isinstance(entry, PairingReplayEntry) for entry in entries):
                raise _invalid_state()
            keys = {(entry.kind, entry.object_id) for entry in entries}
            if len(keys) != len(entries) or entries != tuple(sorted(entries, key=_replay_order)):
                raise _invalid_state()
            object.__setattr__(self, "entries", entries)
        except TrustedDeviceStateError:
            raise
        except Exception as exc:
            raise _invalid_state() from exc

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PairingReplayStore:
        try:
            obj = _mapping(data)
            _exact_keys(
                obj,
                {
                    "format",
                    "version",
                    "local_device_id",
                    "revision",
                    "parent_revision",
                    "created_at",
                    "updated_at",
                    "entries",
                },
            )
            values = obj.get("entries")
            if not isinstance(values, list):
                raise _invalid_state()
            return cls(
                format=obj.get("format"),
                version=_integer(obj.get("version")),
                local_device_id=_device_id(obj.get("local_device_id")),
                revision=_uuid(obj.get("revision")),
                parent_revision=_optional_uuid(obj.get("parent_revision")),
                created_at=_timestamp(obj.get("created_at")),
                updated_at=_timestamp(obj.get("updated_at")),
                entries=tuple(PairingReplayEntry.from_dict(_mapping(value)) for value in values),
            )
        except TrustedDeviceStateError:
            raise
        except Exception as exc:
            raise _invalid_state() from exc

    @classmethod
    def from_canonical_bytes(cls, data: bytes) -> PairingReplayStore:
        return cls.from_dict(_canonical_object(data))

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "version": self.version,
            "local_device_id": self.local_device_id,
            "revision": self.revision,
            "parent_revision": self.parent_revision,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "entries": [entry.to_dict() for entry in self.entries],
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_dict())


class ReplayConsumeOutcome(Enum):
    consumed = "consumed"
    already_consumed = "already_consumed"


def consume_pairing_replay(
    store: PairingReplayStore,
    entry: PairingReplayEntry,
    *,
    revision: str,
    updated_at: str,
    current_time: str,
) -> tuple[PairingReplayStore, ReplayConsumeOutcome]:
    try:
        if not isinstance(store, PairingReplayStore) or not isinstance(entry, PairingReplayEntry):
            raise _invalid_state()
        existing = next(
            (
                value
                for value in store.entries
                if value.kind == entry.kind and value.object_id == entry.object_id
            ),
            None,
        )
        if existing is not None:
            if hmac.compare_digest(existing.transcript_sha256, entry.transcript_sha256):
                return store, ReplayConsumeOutcome.already_consumed
            raise _invalid_state()
        now = _time(current_time)
        if _time(entry.expires_at) <= now:
            raise _invalid_state()
        retained: Sequence[PairingReplayEntry] = tuple(
            value for value in store.entries if _time(value.expires_at) > now
        )
        values = sorted((*retained, entry), key=_replay_order)
        if len(values) > MAXIMUM_REPLAY_ENTRIES:
            values = values[-MAXIMUM_REPLAY_ENTRIES:]
        next_revision = _uuid(revision)
        if next_revision in {store.revision, store.parent_revision}:
            raise _invalid_state()
        updated = _timestamp(updated_at)
        if _time(updated) < _time(store.updated_at):
            raise _invalid_state()
        return (
            PairingReplayStore(
                local_device_id=store.local_device_id,
                revision=next_revision,
                parent_revision=store.revision,
                created_at=store.created_at,
                updated_at=updated,
                entries=tuple(values),
            ),
            ReplayConsumeOutcome.consumed,
        )
    except TrustedDeviceStateError:
        raise
    except Exception as exc:
        raise _invalid_state() from exc
