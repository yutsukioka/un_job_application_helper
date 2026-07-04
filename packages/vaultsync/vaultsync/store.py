from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from vaultsync.format import VaultFormatError, VaultMetadata
from vaultsync.records import EncryptedRecord, RecordFormatError

LOCAL_STORE_FORMAT = "atlasvault-local-store"
SUPPORTED_LOCAL_STORE_VERSION = 1


class VaultStoreError(ValueError):
    """Raised when a local encrypted vault store envelope is invalid."""


class UnsupportedStoreVersion(VaultStoreError):
    """Raised when a local encrypted vault store version is unsupported."""


def _utc_timestamp() -> str:
    return datetime.now(tz=UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _require_mapping(value: Any, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise VaultStoreError(f"{context} must be an object")
    return value


def _require_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise VaultStoreError(f"{field_name} must be non-empty text")
    return value


def _require_int(value: Any, field_name: str) -> int:
    if not isinstance(value, int):
        raise VaultStoreError(f"{field_name} must be an integer")
    return value


def _load_json(data: str | bytes | bytearray) -> Any:
    if isinstance(data, (bytes, bytearray)):
        try:
            data = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise VaultStoreError("serialized local store must be UTF-8 JSON") from exc
    if not isinstance(data, str):
        raise VaultStoreError("serialized local store must be JSON text")
    try:
        return json.loads(data)
    except json.JSONDecodeError as exc:
        raise VaultStoreError("serialized local store must be valid JSON") from exc


@dataclass(frozen=True)
class LocalVaultStore:
    vault_metadata: VaultMetadata
    records: tuple[EncryptedRecord, ...] = ()
    store_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = field(default_factory=_utc_timestamp)
    updated_at: str = field(default_factory=_utc_timestamp)
    format: str = LOCAL_STORE_FORMAT
    version: int = SUPPORTED_LOCAL_STORE_VERSION

    def __post_init__(self) -> None:
        if self.format != LOCAL_STORE_FORMAT:
            raise VaultStoreError("unsupported local store format")
        if self.version != SUPPORTED_LOCAL_STORE_VERSION:
            raise UnsupportedStoreVersion("unsupported local store version")
        if not isinstance(self.vault_metadata, VaultMetadata):
            raise VaultStoreError("vault_metadata must be VaultMetadata")
        if not isinstance(self.records, tuple):
            object.__setattr__(self, "records", tuple(self.records))
        for record in self.records:
            if not isinstance(record, EncryptedRecord):
                raise VaultStoreError("records must contain EncryptedRecord objects")
        _require_text(self.store_id, "store_id")
        _require_text(self.created_at, "created_at")
        _require_text(self.updated_at, "updated_at")

    @classmethod
    def new(
        cls,
        vault_metadata: VaultMetadata,
        records: Sequence[EncryptedRecord] = (),
        *,
        store_id: str | None = None,
        created_at: str | None = None,
        updated_at: str | None = None,
    ) -> LocalVaultStore:
        now = _utc_timestamp()
        return cls(
            vault_metadata=vault_metadata,
            records=tuple(records),
            store_id=store_id or str(uuid.uuid4()),
            created_at=created_at or now,
            updated_at=updated_at or now,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "version": self.version,
            "store_id": self.store_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "vault_metadata": self.vault_metadata.to_dict(),
            "records": [record.to_dict() for record in self.records],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> LocalVaultStore:
        obj = _require_mapping(data, "local store")
        if obj.get("format") != LOCAL_STORE_FORMAT:
            raise VaultStoreError("unsupported local store format")
        version = _require_int(obj.get("version"), "version")
        if version != SUPPORTED_LOCAL_STORE_VERSION:
            raise UnsupportedStoreVersion("unsupported local store version")
        raw_records = obj.get("records")
        if not isinstance(raw_records, list):
            raise VaultStoreError("records must be a list")
        try:
            vault_metadata = VaultMetadata.from_dict(
                _require_mapping(obj.get("vault_metadata"), "vault_metadata")
            )
            records = tuple(EncryptedRecord.from_dict(record) for record in raw_records)
        except (VaultFormatError, RecordFormatError) as exc:
            raise VaultStoreError("local store contains invalid vault data") from exc
        return cls(
            format=LOCAL_STORE_FORMAT,
            version=version,
            store_id=_require_text(obj.get("store_id"), "store_id"),
            created_at=_require_text(obj.get("created_at"), "created_at"),
            updated_at=_require_text(obj.get("updated_at"), "updated_at"),
            vault_metadata=vault_metadata,
            records=records,
        )

    def to_json(self) -> str:
        return serialize_local_store(self)

    def to_json_bytes(self) -> bytes:
        return serialize_local_store_bytes(self)


def serialize_local_store(store: LocalVaultStore) -> str:
    return json.dumps(
        store.to_dict(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def serialize_local_store_bytes(store: LocalVaultStore) -> bytes:
    return serialize_local_store(store).encode("utf-8")


def deserialize_local_store(data: str | bytes | bytearray | Mapping[str, Any]) -> LocalVaultStore:
    obj = data if isinstance(data, Mapping) else _load_json(data)
    return LocalVaultStore.from_dict(_require_mapping(obj, "local store"))


def write_local_store(store: LocalVaultStore, path: str | Path) -> None:
    Path(path).write_bytes(serialize_local_store_bytes(store))


def read_local_store(path: str | Path) -> LocalVaultStore:
    return deserialize_local_store(Path(path).read_bytes())
