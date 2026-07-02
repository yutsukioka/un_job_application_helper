from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from vaultsync.format import _b64decode, _b64encode, _json_loads, _require_mapping

SUPPORTED_RECORD_SCHEMA_VERSION = 1


class RecordFormatError(ValueError):
    """Raised when a plaintext or encrypted record has an invalid format."""


class UnsupportedRecordVersion(RecordFormatError):
    """Raised when an encrypted record schema version is unsupported."""


def _require_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise RecordFormatError(f"{field_name} must be non-empty text")
    return value


def _require_bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise RecordFormatError(f"{field_name} must be a boolean")
    return value


def _require_int(value: Any, field_name: str) -> int:
    if not isinstance(value, int):
        raise RecordFormatError(f"{field_name} must be an integer")
    return value


@dataclass(frozen=True)
class PlaintextRecord:
    type: str
    payload_schema: int
    payload: Mapping[str, Any]
    client_created_at: str
    client_updated_at: str

    def __post_init__(self) -> None:
        if self.type != "saved_text":
            raise RecordFormatError("unsupported plaintext record type")
        if self.payload_schema != 1:
            raise RecordFormatError("unsupported plaintext payload schema")
        payload = _require_mapping(self.payload, "payload", RecordFormatError)
        if not isinstance(payload.get("text"), str):
            raise RecordFormatError("saved_text payload.text must be text")
        _require_text(self.client_created_at, "client_created_at")
        _require_text(self.client_updated_at, "client_updated_at")

    @classmethod
    def saved_text(
        cls,
        text: str,
        *,
        client_created_at: str,
        client_updated_at: str,
    ) -> PlaintextRecord:
        return cls(
            type="saved_text",
            payload_schema=1,
            payload={"text": text},
            client_created_at=client_created_at,
            client_updated_at=client_updated_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "payload_schema": self.payload_schema,
            "payload": dict(self.payload),
            "client_created_at": self.client_created_at,
            "client_updated_at": self.client_updated_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PlaintextRecord:
        obj = _require_mapping(data, "plaintext record", RecordFormatError)
        return cls(
            type=_require_text(obj.get("type"), "type"),
            payload_schema=_require_int(obj.get("payload_schema"), "payload_schema"),
            payload=_require_mapping(obj.get("payload"), "payload", RecordFormatError),
            client_created_at=_require_text(obj.get("client_created_at"), "client_created_at"),
            client_updated_at=_require_text(obj.get("client_updated_at"), "client_updated_at"),
        )

    @classmethod
    def from_json_bytes(cls, data: bytes) -> PlaintextRecord:
        try:
            obj = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RecordFormatError("plaintext record payload is invalid") from exc
        return cls.from_dict(obj)


@dataclass(frozen=True)
class EncryptedRecord:
    id: str
    schema_version: int
    revision: str
    parent_revision: str | None
    deleted: bool
    key_id: str
    nonce: bytes
    ciphertext: bytes

    def __post_init__(self) -> None:
        _require_text(self.id, "id")
        if self.schema_version != SUPPORTED_RECORD_SCHEMA_VERSION:
            raise UnsupportedRecordVersion("unsupported encrypted record schema version")
        _require_text(self.revision, "revision")
        if self.parent_revision is not None:
            _require_text(self.parent_revision, "parent_revision")
        _require_bool(self.deleted, "deleted")
        _require_text(self.key_id, "key_id")
        if not isinstance(self.nonce, bytes) or len(self.nonce) != 12:
            raise RecordFormatError("record nonce must be 96 bits")
        if not isinstance(self.ciphertext, bytes) or not self.ciphertext:
            raise RecordFormatError("record ciphertext must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "schema_version": self.schema_version,
            "revision": self.revision,
            "parent_revision": self.parent_revision,
            "deleted": self.deleted,
            "key_id": self.key_id,
            "nonce": _b64encode(self.nonce),
            "ciphertext": _b64encode(self.ciphertext),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EncryptedRecord:
        obj = _require_mapping(data, "encrypted record", RecordFormatError)
        parent_revision = obj.get("parent_revision")
        if parent_revision is not None and not isinstance(parent_revision, str):
            raise RecordFormatError("parent_revision must be text or null")
        return cls(
            id=_require_text(obj.get("id"), "id"),
            schema_version=_require_int(obj.get("schema_version"), "schema_version"),
            revision=_require_text(obj.get("revision"), "revision"),
            parent_revision=parent_revision,
            deleted=_require_bool(obj.get("deleted"), "deleted"),
            key_id=_require_text(obj.get("key_id"), "key_id"),
            nonce=_b64decode(obj.get("nonce"), "nonce"),
            ciphertext=_b64decode(obj.get("ciphertext"), "ciphertext"),
        )


def serialize_encrypted_record(record: EncryptedRecord) -> str:
    return json.dumps(
        record.to_dict(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def deserialize_encrypted_record(data: str | bytes | bytearray | Mapping[str, Any]) -> EncryptedRecord:
    obj = data if isinstance(data, Mapping) else _json_loads(data, RecordFormatError)
    return EncryptedRecord.from_dict(_require_mapping(obj, "encrypted record", RecordFormatError))
