"""Stable hashing for normalized job records."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from jobagg.models import JobRecord
from jobagg.normalize import clean_text


def stable_json(data: dict[str, Any]) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def stable_hash(data: dict[str, Any]) -> str:
    return hashlib.sha256(stable_json(data).encode("utf-8")).hexdigest()


def content_hash(job: JobRecord) -> str:
    payload = {
        key: clean_text(value) if isinstance(value, str) else value
        for key, value in job.hash_payload().items()
    }
    return stable_hash(payload)


def ensure_job_hash(job: JobRecord) -> JobRecord:
    job.normalized_hash = content_hash(job)
    return job

