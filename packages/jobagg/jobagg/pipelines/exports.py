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
    "stale_current",
    "canonical_job_key",
    "duplicate_of_job_key",
    "consolidation_status",
    "source_latest_observed_at",
    "source_freshness_status",
    "source_health_status",
    "source_run_classification",
    "source_publishability_classification",
    "detail_quality_status",
    "deadline_state",
    "source_listed_current",
    "trusted_current",
    "application_ready",
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
    "occupational_family_code",
    "occupational_family_label",
    "occupational_medium_code",
    "occupational_medium_label",
    "occupational_small_code",
    "occupational_small_label",
    "occupational_confidence",
    "occupational_classifier_version",
    "mandate_network_code",
    "mandate_network_label",
    "mandate_family_code",
    "mandate_family_label",
    "primary_mandate_network",
    "primary_mandate_family",
    "secondary_mandate_families",
    "mandate_source",
    "mandate_confidence",
    "source_native_category",
    "source_native_job_family",
    "source_native_job_network",
    "capability_tags",
    "capability_classifier_version",
    "contract_category",
    "contract_subtype",
    "contract_confidence",
    "contract_group",
    "contract_group_confidence",
    "seniority_group",
    "seniority_confidence",
    "national_international",
    "national_international_confidence",
    "grade_system",
    "grade_family",
    "grade_code",
    "grade_level",
    "staff_category",
    "min_years_experience",
    "grade_confidence",
    "grade_mapping_organization",
    "grade_mapping_raw_grade_code",
    "standard_grade_family",
    "standard_seniority_tier",
    "standard_scope",
    "standard_employment_category",
    "standard_un_equivalent",
    "standard_experience_range",
    "standard_role_scope",
    "standard_supervisory_expectations",
    "grade_mapping_confidence",
    "grade_mapping_evidence_type",
    "grade_mapping_notes",
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
    "quality_flags",
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
    trusted_current_only: bool = False,
    application_ready_only: bool = False,
    history_only: bool = False,
) -> None:
    rows = list(
        db.iter_jobs_with_classification(
            source_id=source_id,
            status=status,
            trusted_current_only=trusted_current_only,
            application_ready_only=application_ready_only,
            history_only=history_only,
        )
    )
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
