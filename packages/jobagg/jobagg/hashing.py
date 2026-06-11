"""Stable hashing for normalized job records."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from jobagg.models import JobRecord
from jobagg.normalize import clean_text, text_for_hash


def stable_json(data: dict[str, Any]) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def stable_hash(data: dict[str, Any]) -> str:
    return hashlib.sha256(stable_json(data).encode("utf-8")).hexdigest()


# Free-text fields that need the more aggressive ``text_for_hash`` cleanup
# so that vendor-side cosmetic re-renders (HTML comments, inline scripts,
# whitespace, NBSP, zero-width chars) do not produce spurious diffs.
_HASH_FREE_TEXT_FIELDS = frozenset({"description_text"})


def content_hash(job: JobRecord) -> str:
    payload = {}
    for key, value in job.hash_payload().items():
        if isinstance(value, str):
            if key in _HASH_FREE_TEXT_FIELDS:
                payload[key] = text_for_hash(value)
            else:
                payload[key] = clean_text(value)
        else:
            payload[key] = value
    return stable_hash(payload)


def ensure_job_hash(job: JobRecord) -> JobRecord:
    job.normalized_hash = content_hash(job)
    return job


# Cross-source posting fingerprint
# --------------------------------
# A vacancy republished on multiple boards (e.g. UN Careers + Inspira mirror,
# or an agency's own ATS + an aggregator) will have different ``job_key``s
# but otherwise describe the same role. The fingerprint is intentionally
# coarser than ``content_hash`` so that minor wording differences across
# vendors still collide.

_FP_TITLE_NOISE_RE = re.compile(r"[^a-z0-9]+")
_FP_LOCATION_NOISE_RE = re.compile(r"[^a-z0-9]+")


def _fp_norm_title(value: str | None) -> str:
    if not value:
        return ""
    cleaned = text_for_hash(value) or ""
    return _FP_TITLE_NOISE_RE.sub(" ", cleaned.lower()).strip()


def _fp_norm_location(value: str | None) -> str:
    if not value:
        return ""
    cleaned = text_for_hash(value) or ""
    # Use only the first comma-delimited segment (typically the city) so
    # "Geneva, Switzerland" matches "Geneva".
    head = cleaned.split(",", 1)[0]
    return _FP_LOCATION_NOISE_RE.sub(" ", head.lower()).strip()


def _fp_description_prefix(value: str | None, *, length: int = 240) -> str:
    if not value:
        return ""
    cleaned = text_for_hash(value) or ""
    return cleaned[:length].lower()


def posting_fingerprint(job: JobRecord) -> str | None:
    """Compute a stable cross-source fingerprint for a job posting.

    Returns ``None`` if the inputs are too thin to produce a reliable
    signal (e.g. no title at all).
    """

    title = _fp_norm_title(job.title)
    if not title:
        return None
    # ``org_id`` is intentionally excluded: the whole point of the
    # cross-source fingerprint is to surface the same posting that has
    # been published under different aggregator/source identifiers (which
    # often surface as different ``org_id``s). Disambiguation between
    # genuinely different roles relies on the description prefix.
    payload = {
        "title": title,
        "location": _fp_norm_location(job.location),
        "description_prefix": _fp_description_prefix(job.description),
    }
    return stable_hash(payload)

