"""Shared domain models for job aggregation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


@dataclass(slots=True)
class OrganizationSource:
    """A configured job source for one organization and ATS family."""

    id: str
    name: str
    ats_family: str
    base_url: str
    enabled: bool = True
    adapter: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "OrganizationSource":
        return cls(
            id=str(data["id"]),
            name=str(data.get("name") or data["id"]),
            ats_family=str(data["ats_family"]).lower(),
            base_url=str(data["base_url"]).rstrip("/"),
            enabled=bool(data.get("enabled", True)),
            adapter=data.get("adapter"),
            extra=dict(data.get("extra") or {}),
        )


@dataclass(slots=True)
class JobRecord:
    """Normalized job record emitted by adapters."""

    source_id: str
    org_id: str
    ats_family: str
    title: str
    apply_url: str
    external_id: str | None = None
    location: str | None = None
    department: str | None = None
    employment_type: str | None = None
    posted_at: datetime | None = None
    closes_at: datetime | None = None
    # ``closes_at`` is normalized to UTC for sorting and indexing, but the
    # vendor-supplied wall-clock time and IANA timezone are also kept so
    # downstream consumers can render a faithful local deadline.
    closes_at_local: str | None = None
    closes_tz: str | None = None
    source_url: str | None = None
    description: str | None = None
    status: str = "open"
    raw: dict[str, Any] = field(default_factory=dict)
    normalized_hash: str | None = None
    # Coarse cross-source dedup key. Computed by ``ensure_posting_fingerprint``
    # in the persistence layer and intentionally excluded from
    # ``hash_payload`` so that adding/changing the fingerprint algorithm
    # does not invalidate every existing ``normalized_hash``.
    posting_fingerprint: str | None = None
    first_seen_at: datetime = field(default_factory=utc_now)
    last_seen_at: datetime = field(default_factory=utc_now)

    def identity_key(self) -> str:
        org_key = _identity_part(self.org_id or self.source_id)
        if self.external_id:
            return f"{org_key}:{_source_vacancy_id_part(self.external_id)}"
        fallback = "|".join(
            [
                org_key,
                _identity_part(self.title),
                _identity_part(self.location),
                _identity_date(self.closes_at),
            ]
        )
        return f"{org_key}:fallback:{hashlib.sha256(fallback.encode('utf-8')).hexdigest()}"

    def hash_payload(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "location": self.location,
            "grade": _raw_first(
                self.raw,
                (
                    "grade",
                    "Grade",
                    "jobGrade",
                    "JobGrade",
                    "staffGrade",
                    "StaffGrade",
                    "Staff grade/level",
                ),
            ),
            "contract_type": _raw_first(
                self.raw,
                ("contract_type", "ContractType", "contractType", "employmentType", "EmploymentType"),
            )
            or self.employment_type,
            "closing_date": _identity_date(self.closes_at),
            "description_text": self.description,
        }


@dataclass(slots=True)
class ChangeEvent:
    """A material change detected during source synchronization."""

    source_id: str
    job_key: str
    change_type: str
    old_hash: str | None
    new_hash: str | None
    observed_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class SyncResult:
    """Summary of one source synchronization run."""

    source_id: str
    fetched: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    missing: int = 0
    closed: int = 0
    errors: list[str] = field(default_factory=list)
    diagnostics: SourceRunDiagnostics | None = None

    @property
    def changed(self) -> int:
        return self.inserted + self.updated + self.missing + self.closed


@dataclass(slots=True)
class SourceRunDiagnostics:
    """Structured health metadata for one source synchronization run."""

    source_id: str
    adapter_version: str | None = None
    fetch_method: str | None = None
    platform_host: str | None = None
    site_number: str | None = None
    expected_site_name: str | None = None
    observed_site_name: str | None = None
    endpoint_family: str | None = None
    http_status: int | None = None
    total_reported_by_source: int | None = None
    pages_fetched: int | None = None
    pagination_complete: bool | None = None
    list_error_count: int = 0
    detail_attempted: int = 0
    detail_succeeded: int = 0
    detail_failed: int = 0
    detail_skipped: int = 0
    empty_reason: str | None = None
    zero_fetched_evidence: dict[str, Any] = field(default_factory=dict)
    observed_agency_counts: dict[str, int] = field(default_factory=dict)
    observed_organization_counts: dict[str, int] = field(default_factory=dict)
    count_delta_pct: float | None = None
    health_status: str | None = None
    run_classification: str | None = None
    publishability_classification: str | None = None
    blocked: bool = False
    transient_error: bool = False
    list_breaker_state: str | None = None
    detail_breaker_state: str | None = None
    scope_validation_status: str | None = None
    missing_transition_allowed: bool = False
    observed_at: datetime = field(default_factory=utc_now)


def _identity_part(value: object | None) -> str:
    if value is None:
        return ""
    return " ".join(str(value).casefold().split())


def _source_vacancy_id_part(value: object | None) -> str:
    if value is None:
        return ""
    return " ".join(str(value).strip().split())


def _identity_date(value: datetime | date | None) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    return value.isoformat()


def _raw_first(raw: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = raw.get(key)
        if value not in (None, ""):
            return value
    return None
