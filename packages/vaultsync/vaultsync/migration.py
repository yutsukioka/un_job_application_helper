from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from vaultsync.crypto import encrypt_record_payload
from vaultsync.export import AtlasVaultExport, write_atlasvault_export
from vaultsync.format import VaultMetadata
from vaultsync.records import EncryptedRecord, PlaintextRecord
from vaultsync.store import LocalVaultStore, write_local_store

UNKNOWN_TIMESTAMP = "1970-01-01T00:00:00Z"


class MigrationSourceError(ValueError):
    """Raised when a caller-provided plaintext migration source is unreadable."""


class MigrationSafetyError(ValueError):
    """Raised when a migration helper would overwrite caller data."""


@dataclass(frozen=True)
class MigrationDryRunReport:
    saved_search_count: int = 0
    saved_job_count: int = 0
    skipped_saved_searches: int = 0
    skipped_saved_jobs: int = 0
    warnings: tuple[str, ...] = ()
    output_path: str | None = None

    @property
    def total_records(self) -> int:
        return self.saved_search_count + self.saved_job_count

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "saved_search_count": self.saved_search_count,
            "saved_job_count": self.saved_job_count,
            "total_records": self.total_records,
            "skipped_saved_searches": self.skipped_saved_searches,
            "skipped_saved_jobs": self.skipped_saved_jobs,
            "warnings": list(self.warnings),
        }
        if self.output_path is not None:
            payload["output_path"] = self.output_path
        return payload


@dataclass(frozen=True)
class MigrationResult:
    records: tuple[EncryptedRecord, ...]
    report: MigrationDryRunReport


@dataclass(frozen=True)
class _Candidate:
    record_type: str
    payload: Mapping[str, Any]
    created_at: str
    updated_at: str

    def to_plaintext_record(self) -> PlaintextRecord:
        return PlaintextRecord(
            type=self.record_type,
            payload_schema=1,
            payload=self.payload,
            client_created_at=self.created_at,
            client_updated_at=self.updated_at,
        )


def load_saved_searches_source(path: str | Path) -> object:
    return _load_json_source(path, "saved searches source")


def load_tracker_source(path: str | Path) -> object:
    return _load_json_source(path, "tracker source")


def dry_run_sources(
    *,
    saved_searches_source: object | None = None,
    tracker_source: object | None = None,
) -> MigrationDryRunReport:
    saved_searches, saved_warnings = _saved_search_candidates(saved_searches_source)
    saved_jobs, tracker_warnings = _saved_job_candidates(tracker_source)
    return MigrationDryRunReport(
        saved_search_count=len(saved_searches),
        saved_job_count=len(saved_jobs),
        skipped_saved_searches=sum(1 for warning in saved_warnings if warning.endswith(".skipped")),
        skipped_saved_jobs=sum(1 for warning in tracker_warnings if warning.endswith(".skipped")),
        warnings=tuple(saved_warnings + tracker_warnings),
    )


def build_encrypted_records_from_sources(
    vault_key: bytes,
    vault_metadata: VaultMetadata,
    *,
    saved_searches_source: object | None = None,
    tracker_source: object | None = None,
) -> list[EncryptedRecord]:
    saved_searches, _ = _saved_search_candidates(saved_searches_source)
    saved_jobs, _ = _saved_job_candidates(tracker_source)
    records: list[EncryptedRecord] = []
    for candidate in [*saved_searches, *saved_jobs]:
        records.append(
            encrypt_record_payload(
                vault_key,
                vault_metadata,
                candidate.to_plaintext_record(),
            )
        )
    return records


def migrate_sources_to_encrypted_records(
    vault_key: bytes,
    vault_metadata: VaultMetadata,
    *,
    saved_searches_source: object | None = None,
    tracker_source: object | None = None,
) -> MigrationResult:
    records = tuple(
        build_encrypted_records_from_sources(
            vault_key,
            vault_metadata,
            saved_searches_source=saved_searches_source,
            tracker_source=tracker_source,
        )
    )
    return MigrationResult(
        records=records,
        report=dry_run_sources(
            saved_searches_source=saved_searches_source,
            tracker_source=tracker_source,
        ),
    )


def write_staged_local_store(
    path: str | Path,
    vault_metadata: VaultMetadata,
    encrypted_records: Sequence[EncryptedRecord],
    *,
    overwrite: bool = False,
) -> LocalVaultStore:
    output_path = Path(path)
    _ensure_output_allowed(output_path, overwrite=overwrite)
    store = LocalVaultStore.new(vault_metadata, tuple(encrypted_records))
    write_local_store(store, output_path)
    return store


def write_staged_export(
    path: str | Path,
    vault_metadata: VaultMetadata,
    encrypted_records: Sequence[EncryptedRecord],
    *,
    overwrite: bool = False,
) -> AtlasVaultExport:
    output_path = Path(path)
    _ensure_output_allowed(output_path, overwrite=overwrite)
    export = AtlasVaultExport.new(vault_metadata, tuple(encrypted_records))
    write_atlasvault_export(export, output_path)
    return export


def _load_json_source(path: str | Path, source_name: str) -> object:
    source_path = Path(path)
    try:
        raw = source_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MigrationSourceError(f"{source_name} could not be read") from exc
    try:
        return json.loads(raw or "null")
    except json.JSONDecodeError as exc:
        raise MigrationSourceError(f"{source_name} must be valid JSON") from exc


def _ensure_output_allowed(path: Path, *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise MigrationSafetyError("output path already exists")


def _saved_search_candidates(source: object | None) -> tuple[list[_Candidate], list[str]]:
    if source is None:
        return [], []
    entries, warnings = _entries_from_source(
        source,
        container_keys=("saved_searches",),
        keyed_name_field="name",
        source_label="saved_search",
    )
    candidates: list[_Candidate] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            warnings.append("saved_search.skipped")
            continue
        name = _text_or_none(entry.get("name"))
        request = entry.get("request")
        if name is None or not isinstance(request, Mapping):
            warnings.append("saved_search.skipped")
            continue
        description = _text_or_none(entry.get("description"))
        summary = _text_or_none(entry.get("summary")) or description or ""
        payload: dict[str, Any] = {
            "name": name,
            "summary": summary,
            "request": dict(request),
        }
        if description is not None:
            payload["description"] = description
        candidates.append(
            _Candidate(
                record_type="saved_search",
                payload=payload,
                created_at=_timestamp(entry.get("created_at")),
                updated_at=_timestamp(entry.get("updated_at")),
            )
        )
    return candidates, warnings


def _saved_job_candidates(source: object | None) -> tuple[list[_Candidate], list[str]]:
    if source is None:
        return [], []
    entries, warnings = _entries_from_source(
        source,
        container_keys=("records", "applications"),
        keyed_name_field="id",
        source_label="saved_job",
    )
    candidates: list[_Candidate] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            warnings.append("saved_job.skipped")
            continue
        job_key = _text_or_none(entry.get("job_key") or entry.get("jobKey"))
        if job_key is None:
            warnings.append("saved_job.skipped")
            continue
        applied_at = _text_or_none(entry.get("applied_at") or entry.get("appliedAt"))
        updated_at = _timestamp(entry.get("updated_at") or entry.get("updatedAt"))
        payload: dict[str, Any] = {
            "job_key": job_key,
            "status": _text_or_none(entry.get("status")) or "saved",
            "notes": _text_or_none(entry.get("notes")) or "",
            "applied_at": applied_at,
            "updated_at": updated_at,
        }
        record_id = _text_or_none(entry.get("id"))
        if record_id is not None:
            payload["id"] = record_id
        candidates.append(
            _Candidate(
                record_type="saved_job",
                payload=payload,
                created_at=_timestamp(entry.get("created_at") or applied_at or updated_at),
                updated_at=updated_at,
            )
        )
    return candidates, warnings


def _entries_from_source(
    source: object,
    *,
    container_keys: tuple[str, ...],
    keyed_name_field: str,
    source_label: str,
) -> tuple[list[object], list[str]]:
    warnings: list[str] = []
    if isinstance(source, list):
        return list(source), warnings
    if not isinstance(source, Mapping):
        raise MigrationSourceError(f"{source_label} source must be a JSON object or array")
    for key in container_keys:
        if key not in source:
            continue
        nested = source[key]
        if isinstance(nested, list):
            return list(nested), warnings
        if isinstance(nested, Mapping):
            return _keyed_entries(nested, keyed_name_field), warnings
        warnings.append(f"{source_label}.container.skipped")
        return [], warnings
    return _keyed_entries(source, keyed_name_field), warnings


def _keyed_entries(source: Mapping[str, Any], keyed_name_field: str) -> list[object]:
    entries: list[object] = []
    for key, value in source.items():
        if not isinstance(value, Mapping):
            entries.append(value)
            continue
        entry = dict(value)
        entry.setdefault(keyed_name_field, key)
        entries.append(entry)
    return entries


def _timestamp(value: object) -> str:
    text = _text_or_none(value)
    return text or UNKNOWN_TIMESTAMP


def _text_or_none(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None
