"""JSON-backed application tracker for the local-first MVP."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from jobagg.atomic_json_store import AtomicJsonStore, AtomicJsonStoreError

from job_api.models import ApplicationRecord


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _load(path: Path) -> list[ApplicationRecord]:
    return _decode_records(_store(path).read())


def list_records(path: Path) -> list[ApplicationRecord]:
    return _load(path)


def upsert_record(path: Path, record: ApplicationRecord) -> ApplicationRecord:
    def mutation(raw: list[Any]) -> tuple[ApplicationRecord, bool]:
        records = _decode_records(raw)
        if not record.id:
            record.id = str(uuid4())
        record.updated_at = _now()
        for index, existing in enumerate(records):
            if existing.id == record.id:
                records[index] = record
                raw[:] = _encode_records(records)
                return record, True
        records.append(record)
        raw[:] = _encode_records(records)
        return record, True

    return _store(path).mutate(mutation)


def create_record(path: Path, job_key: str) -> ApplicationRecord:
    def mutation(raw: list[Any]) -> tuple[ApplicationRecord, bool]:
        records = _decode_records(raw)
        now = _now()
        for record in records:
            if record.job_key == job_key:
                record.updated_at = now
                raw[:] = _encode_records(records)
                return record, True
        record = ApplicationRecord(
            id=str(uuid4()),
            job_key=job_key,
            status="saved",
            updated_at=now,
        )
        records.append(record)
        raw[:] = _encode_records(records)
        return record, True

    return _store(path).mutate(mutation)


def delete_record(path: Path, record_id: str) -> bool:
    def mutation(raw: list[Any]) -> tuple[bool, bool]:
        records = _decode_records(raw)
        kept = [record for record in records if record.id != record_id]
        if len(kept) == len(records):
            return False, False
        raw[:] = _encode_records(kept)
        return True, True

    return _store(path).mutate(mutation)


def compare_and_delete_record(
    path: Path,
    *,
    record_id: str,
    expected: ApplicationRecord,
) -> Literal["deleted", "absent", "mismatch"]:
    def mutation(
        raw: list[Any],
    ) -> tuple[Literal["deleted", "absent", "mismatch"], bool]:
        records = _decode_records(raw)
        current = next((record for record in records if record.id == record_id), None)
        if current is None:
            return "absent", False
        if expected.id != record_id or current != expected:
            return "mismatch", False
        raw[:] = _encode_records(
            [record for record in records if record.id != record_id]
        )
        return "deleted", True

    return _store(path).mutate(mutation)


def _store(path: Path) -> AtomicJsonStore[list[Any]]:
    return AtomicJsonStore(
        path,
        default_factory=list,
        validator=_validate_store,
        encoder=lambda raw: json.dumps(raw, indent=2, sort_keys=True).encode("utf-8"),
    )


def _validate_store(raw: Any) -> None:
    if not isinstance(raw, list):
        raise AtomicJsonStoreError("Tracker JSON store must contain an array.")


def _decode_records(raw: list[Any]) -> list[ApplicationRecord]:
    try:
        return [ApplicationRecord(**item) for item in raw]
    except (TypeError, ValueError):
        raise AtomicJsonStoreError("Tracker JSON store is invalid.") from None


def _encode_records(records: list[ApplicationRecord]) -> list[dict[str, Any]]:
    return [record.model_dump(mode="json") for record in records]
