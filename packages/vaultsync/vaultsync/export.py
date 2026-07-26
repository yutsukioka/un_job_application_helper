from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from vaultsync.format import VaultFormatError, VaultMetadata
from vaultsync.records import EncryptedRecord, RecordFormatError

EXPORT_FORMAT = "atlasvault-export"
SUPPORTED_EXPORT_VERSION = 1


class VaultExportError(ValueError):
    """Raised when an encrypted AtlasVault export envelope is invalid."""


class UnsupportedExportVersion(VaultExportError):
    """Raised when an encrypted AtlasVault export version is unsupported."""


def _utc_timestamp() -> str:
    return datetime.now(tz=UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _require_mapping(value: Any, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise VaultExportError(f"{context} must be an object")
    return value


def _require_int(value: Any, field_name: str) -> int:
    if type(value) is not int:
        raise VaultExportError(f"{field_name} must be an integer")
    return value


def _require_exact_keys(
    value: Mapping[str, Any],
    expected: set[str],
    context: str,
) -> None:
    if set(value) != expected:
        raise VaultExportError(
            f"{context} must contain exactly the supported fields"
        )


def _require_canonical_lowercase_uuid(value: Any, field_name: str) -> str:
    error = f"{field_name} must be a canonical lowercase UUID"
    if not isinstance(value, str):
        raise VaultExportError(error)
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise VaultExportError(error) from exc
    if str(parsed) != value:
        raise VaultExportError(error)
    return value


def _require_strict_utc_seconds(value: Any, field_name: str) -> str:
    error = f"{field_name} must be UTC ISO-8601 seconds"
    digit_positions = (0, 1, 2, 3, 5, 6, 8, 9, 11, 12, 14, 15, 17, 18)
    if (
        not isinstance(value, str)
        or len(value) != 20
        or value[4] != "-"
        or value[7] != "-"
        or value[10] != "T"
        or value[13] != ":"
        or value[16] != ":"
        or value[19] != "Z"
        or any(value[index] not in "0123456789" for index in digit_positions)
    ):
        raise VaultExportError(error)
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise VaultExportError(error) from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise VaultExportError(error)
    return value


def _load_json(data: str | bytes | bytearray) -> Any:
    if isinstance(data, (bytes, bytearray)):
        try:
            data = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise VaultExportError("serialized export must be UTF-8 JSON") from exc
    if not isinstance(data, str):
        raise VaultExportError("serialized export must be JSON text")
    try:
        return json.loads(data)
    except json.JSONDecodeError as exc:
        raise VaultExportError("serialized export must be valid JSON") from exc


@dataclass(frozen=True)
class AtlasVaultExport:
    vault_metadata: VaultMetadata
    records: tuple[EncryptedRecord, ...] = ()
    export_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = field(default_factory=_utc_timestamp)
    format: str = EXPORT_FORMAT
    version: int = SUPPORTED_EXPORT_VERSION

    def __post_init__(self) -> None:
        if self.format != EXPORT_FORMAT:
            raise VaultExportError("unsupported export format")
        version = _require_int(self.version, "version")
        if version != SUPPORTED_EXPORT_VERSION:
            raise UnsupportedExportVersion("unsupported export version")
        if not isinstance(self.vault_metadata, VaultMetadata):
            raise VaultExportError("vault_metadata must be VaultMetadata")
        if not isinstance(self.records, tuple):
            object.__setattr__(self, "records", tuple(self.records))
        for record in self.records:
            if not isinstance(record, EncryptedRecord):
                raise VaultExportError("records must contain EncryptedRecord objects")
        _require_canonical_lowercase_uuid(self.export_id, "export_id")
        _require_strict_utc_seconds(self.created_at, "created_at")

    @classmethod
    def new(
        cls,
        vault_metadata: VaultMetadata,
        records: Sequence[EncryptedRecord] = (),
        *,
        export_id: str | None = None,
        created_at: str | None = None,
    ) -> AtlasVaultExport:
        return cls(
            vault_metadata=vault_metadata,
            records=tuple(records),
            export_id=str(uuid.uuid4()) if export_id is None else export_id,
            created_at=_utc_timestamp() if created_at is None else created_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "version": self.version,
            "export_id": self.export_id,
            "created_at": self.created_at,
            "vault_metadata": self.vault_metadata.to_dict(),
            "records": [record.to_dict() for record in self.records],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AtlasVaultExport:
        obj = _require_mapping(data, "export")
        _require_exact_keys(
            obj,
            {
                "format",
                "version",
                "export_id",
                "created_at",
                "vault_metadata",
                "records",
            },
            "export",
        )
        if obj.get("format") != EXPORT_FORMAT:
            raise VaultExportError("unsupported export format")
        version = _require_int(obj.get("version"), "version")
        if version != SUPPORTED_EXPORT_VERSION:
            raise UnsupportedExportVersion("unsupported export version")
        raw_records = obj.get("records")
        if not isinstance(raw_records, list):
            raise VaultExportError("records must be a list")
        try:
            vault_metadata = VaultMetadata.from_dict(
                _require_mapping(obj.get("vault_metadata"), "vault_metadata")
            )
            records = tuple(EncryptedRecord.from_dict(record) for record in raw_records)
        except (VaultFormatError, RecordFormatError) as exc:
            raise VaultExportError("export contains invalid vault data") from exc
        return cls(
            format=EXPORT_FORMAT,
            version=version,
            export_id=_require_canonical_lowercase_uuid(
                obj.get("export_id"),
                "export_id",
            ),
            created_at=_require_strict_utc_seconds(
                obj.get("created_at"),
                "created_at",
            ),
            vault_metadata=vault_metadata,
            records=records,
        )

    def to_json(self) -> str:
        return serialize_vault_export(self)

    def to_json_bytes(self) -> bytes:
        return serialize_vault_export_bytes(self)


def serialize_vault_export(export: AtlasVaultExport) -> str:
    return json.dumps(
        export.to_dict(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def serialize_vault_export_bytes(export: AtlasVaultExport) -> bytes:
    return serialize_vault_export(export).encode("utf-8")


def deserialize_vault_export(data: str | bytes | bytearray | Mapping[str, Any]) -> AtlasVaultExport:
    obj = data if isinstance(data, Mapping) else _load_json(data)
    return AtlasVaultExport.from_dict(_require_mapping(obj, "export"))


def write_atlasvault_export(export: AtlasVaultExport, path: str | Path) -> None:
    Path(path).write_bytes(serialize_vault_export_bytes(export))


def read_atlasvault_export(path: str | Path) -> AtlasVaultExport:
    return deserialize_vault_export(Path(path).read_bytes())
