"""JSON-backed application tracker for the local-first MVP."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from job_api.models import ApplicationRecord


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _load(path: Path) -> list[ApplicationRecord]:
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [ApplicationRecord(**item) for item in raw]


def _save(path: Path, records: list[ApplicationRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [record.model_dump(mode="json") for record in records]
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def list_records(path: Path) -> list[ApplicationRecord]:
    return _load(path)


def upsert_record(path: Path, record: ApplicationRecord) -> ApplicationRecord:
    records = _load(path)
    now = _now()
    if not record.id:
        record.id = str(uuid4())
    record.updated_at = now
    for index, existing in enumerate(records):
        if existing.id == record.id:
            records[index] = record
            _save(path, records)
            return record
    records.append(record)
    _save(path, records)
    return record


def create_record(path: Path, job_key: str) -> ApplicationRecord:
    now = _now()
    records = _load(path)
    for record in records:
        if record.job_key == job_key:
            record.updated_at = now
            _save(path, records)
            return record
    record = ApplicationRecord(id=str(uuid4()), job_key=job_key, status="saved", updated_at=now)
    records.append(record)
    _save(path, records)
    return record


def delete_record(path: Path, record_id: str) -> bool:
    records = _load(path)
    kept = [record for record in records if record.id != record_id]
    if len(kept) == len(records):
        return False
    _save(path, kept)
    return True
