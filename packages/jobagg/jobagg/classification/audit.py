"""Classification coverage and quality audit helpers."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from jobagg.db import JobDatabase


LOW_CONFIDENCE_THRESHOLD = 0.70


@dataclass(slots=True)
class ClassificationAuditBucket:
    source_id: str
    total_jobs: int = 0
    classified_jobs: int = 0
    needs_review_count: int = 0
    unknown_grade_count: int = 0
    unknown_contract_count: int = 0
    unknown_location_count: int = 0
    unknown_ccog_count: int = 0
    jobs_without_vacancy_locations: int = 0
    multiple_unknown_location_count: int = 0
    low_confidence_location_count: int = 0
    low_confidence_grade_count: int = 0
    low_confidence_ccog_count: int = 0
    missing_description_count: int = 0
    missing_closing_date_count: int = 0
    detail_fetch_required_count: int = 0
    quality_score: int = 0
    sample_needs_review: list[dict[str, str | None]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "total_jobs": self.total_jobs,
            "classified_jobs": self.classified_jobs,
            "needs_review_count": self.needs_review_count,
            "unknown_grade_count": self.unknown_grade_count,
            "unknown_contract_count": self.unknown_contract_count,
            "unknown_location_count": self.unknown_location_count,
            "unknown_ccog_count": self.unknown_ccog_count,
            "jobs_without_vacancy_locations": self.jobs_without_vacancy_locations,
            "multiple_unknown_location_count": self.multiple_unknown_location_count,
            "low_confidence_location_count": self.low_confidence_location_count,
            "low_confidence_grade_count": self.low_confidence_grade_count,
            "low_confidence_ccog_count": self.low_confidence_ccog_count,
            "missing_description_count": self.missing_description_count,
            "missing_closing_date_count": self.missing_closing_date_count,
            "detail_fetch_required_count": self.detail_fetch_required_count,
            "quality_score": self.quality_score,
            "sample_needs_review": self.sample_needs_review,
        }


def audit_classification(
    db: JobDatabase,
    *,
    source_ids: list[str] | None = None,
    status: str | None = "open",
    low_confidence_threshold: float = LOW_CONFIDENCE_THRESHOLD,
) -> dict[str, Any]:
    """Summarize classification coverage and weak spots by source."""

    selected_sources = set(source_ids or [])
    buckets: dict[str, ClassificationAuditBucket] = defaultdict(
        lambda: ClassificationAuditBucket(source_id="")
    )
    overall = ClassificationAuditBucket(source_id="__all__")

    rows = [
        row
        for row in db.iter_jobs_with_classification(status=status)
        if not selected_sources or str(row["source_id"]) in selected_sources
    ]
    locations_by_vacancy = db.vacancy_locations_by_vacancy(str(row["job_key"]) for row in rows)

    for row in rows:
        source_id = str(row["source_id"])
        bucket = buckets[source_id]
        if not bucket.source_id:
            bucket.source_id = source_id
        locations = locations_by_vacancy.get(str(row["job_key"]), [])
        _add_row(bucket, row, locations, low_confidence_threshold)
        _add_row(overall, row, locations, low_confidence_threshold)

    for bucket in [overall, *buckets.values()]:
        bucket.quality_score = _quality_score(bucket)

    source_buckets = sorted(buckets.values(), key=lambda item: item.source_id)
    return {
        "status": status,
        "low_confidence_threshold": low_confidence_threshold,
        "overall": overall.to_dict(),
        "sources": [bucket.to_dict() for bucket in source_buckets],
    }


def audit_to_markdown(audit: dict[str, Any]) -> str:
    lines = [
        "# Classification Audit",
        "",
        f"Status filter: `{audit.get('status') or 'all'}`",
        f"Low-confidence threshold: `{audit.get('low_confidence_threshold')}`",
        "",
        "## Overall",
        "",
    ]
    lines.extend(_metric_lines(audit["overall"]))
    lines.extend(["", "## By Source", ""])
    columns = [
        ("source_id", "Source"),
        ("quality_score", "Score"),
        ("total_jobs", "Jobs"),
        ("classified_jobs", "Classified"),
        ("needs_review_count", "Review"),
        ("unknown_grade_count", "Unknown Grade"),
        ("unknown_contract_count", "Unknown Contract"),
        ("unknown_location_count", "Unknown Location"),
        ("unknown_ccog_count", "Unknown CCOG"),
        ("low_confidence_location_count", "Low Loc"),
        ("low_confidence_grade_count", "Low Grade"),
        ("low_confidence_ccog_count", "Low CCOG"),
        ("detail_fetch_required_count", "Detail Needed"),
    ]
    lines.append("| " + " | ".join(label for _, label in columns) + " |")
    lines.append("| " + " | ".join("---" for _ in columns) + " |")
    for source in audit["sources"]:
        lines.append("| " + " | ".join(str(source.get(key, "")) for key, _ in columns) + " |")
    lines.append("")
    return "\n".join(lines)


def _add_row(
    bucket: ClassificationAuditBucket,
    row: dict[str, Any],
    locations: list[dict[str, Any]],
    low_confidence_threshold: float,
) -> None:
    bucket.total_jobs += 1
    if row.get("classification_version"):
        bucket.classified_jobs += 1
    if row.get("needs_review"):
        bucket.needs_review_count += 1
        if len(bucket.sample_needs_review) < 10:
            bucket.sample_needs_review.append(
                {
                    "job_key": row.get("job_key"),
                    "title": row.get("title"),
                    "source_id": row.get("source_id"),
                }
            )
    if _is_unknown(row.get("grade_code")):
        bucket.unknown_grade_count += 1
    if _is_unknown(row.get("contract_category")):
        bucket.unknown_contract_count += 1
    if _is_unknown(row.get("ccog_primary_code")):
        bucket.unknown_ccog_count += 1
    if _unknown_location(row, locations):
        bucket.unknown_location_count += 1
    if not locations:
        bucket.jobs_without_vacancy_locations += 1
    if any(location.get("location_type") == "multiple_unknown" for location in locations):
        bucket.multiple_unknown_location_count += 1
    if _has_low_confidence_location(row, locations, low_confidence_threshold):
        bucket.low_confidence_location_count += 1
    if _has_low_confidence(row.get("grade_confidence"), low_confidence_threshold):
        bucket.low_confidence_grade_count += 1
    if _has_low_confidence(row.get("ccog_confidence"), low_confidence_threshold):
        bucket.low_confidence_ccog_count += 1
    if _is_unknown(row.get("description")):
        bucket.missing_description_count += 1
    if _is_unknown(row.get("closes_at")):
        bucket.missing_closing_date_count += 1
    if _detail_fetch_required(row, locations):
        bucket.detail_fetch_required_count += 1


def _unknown_location(row: dict[str, Any], locations: list[dict[str, Any]]) -> bool:
    if row.get("city") or row.get("country_iso3"):
        return False
    return not any(location.get("city") or location.get("country_iso3") for location in locations)


def _has_low_confidence_location(
    row: dict[str, Any],
    locations: list[dict[str, Any]],
    threshold: float,
) -> bool:
    confidences = [
        float(location["confidence"])
        for location in locations
        if location.get("confidence") not in (None, "")
    ]
    if not confidences and row.get("location_confidence") not in (None, ""):
        confidences = [float(row["location_confidence"])]
    return any(0 < confidence < threshold for confidence in confidences)


def _has_low_confidence(value: object, threshold: float) -> bool:
    if value in (None, ""):
        return False
    confidence = float(value)
    return 0 < confidence < threshold


def _detail_fetch_required(row: dict[str, Any], locations: list[dict[str, Any]]) -> bool:
    return (
        not row.get("description")
        or not row.get("closes_at")
        or any(location.get("location_type") == "multiple_unknown" for location in locations)
    )


def _quality_score(bucket: ClassificationAuditBucket) -> int:
    if bucket.total_jobs == 0:
        return 0
    coverages = [
        _coverage(bucket, bucket.unknown_grade_count),
        _coverage(bucket, bucket.unknown_contract_count),
        _coverage(bucket, bucket.unknown_location_count),
        _coverage(bucket, bucket.unknown_ccog_count),
        _coverage(bucket, bucket.missing_description_count),
        _coverage(bucket, bucket.missing_closing_date_count),
        _coverage(bucket, bucket.needs_review_count),
        _coverage(bucket, bucket.detail_fetch_required_count),
    ]
    return round(sum(coverages) / len(coverages))


def _coverage(bucket: ClassificationAuditBucket, bad_count: int) -> float:
    return max(0.0, 100.0 * (1 - bad_count / bucket.total_jobs))


def _metric_lines(bucket: dict[str, Any]) -> list[str]:
    keys = [
        "quality_score",
        "total_jobs",
        "classified_jobs",
        "needs_review_count",
        "unknown_grade_count",
        "unknown_contract_count",
        "unknown_location_count",
        "unknown_ccog_count",
        "jobs_without_vacancy_locations",
        "multiple_unknown_location_count",
        "low_confidence_location_count",
        "low_confidence_grade_count",
        "low_confidence_ccog_count",
        "missing_description_count",
        "missing_closing_date_count",
        "detail_fetch_required_count",
    ]
    return [f"- `{key}`: {bucket.get(key, 0)}" for key in keys]


def _is_unknown(value: object) -> bool:
    return value in (None, "", "unknown")
