"""Export normalized jobs."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from jobagg.db import JobDatabase


EXPORT_FIELDS = [
    "source_id",
    "ats_family",
    "external_id",
    "title",
    "location",
    "department",
    "employment_type",
    "posted_at",
    "closes_at",
    "status",
    "apply_url",
    "source_url",
    "last_seen_at",
    "ccog_primary_code",
    "ccog_primary_label",
    "ccog_family_code",
    "ccog_family_label",
    "ccog_part",
    "ccog_confidence",
    "ccog_method",
    "contract_category",
    "contract_subtype",
    "contract_confidence",
    "national_international",
    "national_international_confidence",
    "grade_system",
    "grade_family",
    "grade_code",
    "grade_level",
    "staff_category",
    "min_years_experience",
    "grade_confidence",
    "country",
    "country_iso2",
    "country_iso3",
    "city",
    "region",
    "subregion",
    "location_confidence",
    "work_modality",
    "work_modality_confidence",
    "unv_category",
    "unv_raw_category",
    "unv_volunteer_type",
    "unv_assignment_duration",
    "unv_work_arrangement",
    "unv_hours_per_week",
    "unv_host_entity",
    "unv_sdg",
    "needs_review",
    "classification_version",
    "classified_at",
]


def export_jobs(
    db: JobDatabase,
    *,
    output_path: str | Path,
    output_format: str = "json",
    source_id: str | None = None,
    status: str | None = None,
) -> None:
    rows = list(db.iter_jobs_with_classification(source_id=source_id, status=status))
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if output_format == "json":
        path.write_text(json.dumps(rows, indent=2, ensure_ascii=True), encoding="utf-8")
        return
    if output_format == "csv":
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=EXPORT_FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        return
    raise ValueError(f"Unsupported export format: {output_format}")
