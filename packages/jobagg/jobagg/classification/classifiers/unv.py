"""UNV-specific classification."""

from __future__ import annotations

from jobagg.classification.models import FeatureBundle, UNVCategory, UNVResult


def classify_unv(features: FeatureBundle) -> UNVResult | None:
    if features.source_id != "unv_uvp":
        return None
    raw_code = (features.unv_category_code or "").upper()
    raw_label = (features.unv_category_label or "").casefold()
    title = (features.title or "").casefold()
    if raw_code == "COMMUNITY" or "community volunteer" in title:
        category = UNVCategory.UN_COMMUNITY_VOLUNTEER
    elif raw_code in {"UNIVERSITY", "UNIVERSITY_VOLUNTEER"} or "university volunteer" in title:
        category = UNVCategory.UN_UNIVERSITY_VOLUNTEER
    elif raw_code in {"YOUTH", "YOUTH_VOLUNTEER"} or "youth volunteer" in title:
        category = UNVCategory.UN_YOUTH_VOLUNTEER
    elif raw_code == "SPECIALIST" or raw_label == "specialist":
        category = UNVCategory.UN_VOLUNTEER_SPECIALIST
    elif raw_code == "EXPERT" or raw_label == "expert":
        category = UNVCategory.UN_VOLUNTEER_EXPERT
    elif raw_code == "ASSOCIATE" or raw_label == "associate":
        category = UNVCategory.OTHER_UNV
    else:
        category = UNVCategory.UNKNOWN
    return UNVResult(
        category=category,
        raw_category=features.unv_category_label or features.unv_category_code,
        volunteer_type=_normalize_unv_volunteer_type(features.unv_volunteer_type),
        assignment_duration=_normalize_duration(features.unv_assignment_duration),
        work_arrangement=_normalize_arrangement(features.unv_work_arrangement),
        hours_per_week=_normalize_hours(features.unv_hours_week),
        host_entity=features.unv_host_entity,
        sdg=features.unv_sdg,
        expertise_areas=features.unv_expertise_areas,
        evidence={
            "category_code": features.unv_category_code,
            "category_label": features.unv_category_label,
        },
    )


def _normalize_unv_volunteer_type(value: str | None) -> str | None:
    text = (value or "").casefold()
    if "international" in text:
        return "unv_international"
    if "national" in text:
        return "unv_national"
    return "unknown" if value else None


def _normalize_arrangement(value: str | None) -> str | None:
    text = (value or "").casefold()
    if "part" in text:
        return "part_time"
    if "full" in text:
        return "full_time"
    return value


def _normalize_duration(value: str | None) -> str | None:
    text = (value or "").casefold()
    if any(signal in text for signal in ("12", "one year", "long")):
        return "long_term"
    if any(signal in text for signal in ("short", "3 month", "6 month")):
        return "short_term"
    return value


def _normalize_hours(value: str | None) -> str | None:
    if not value:
        return None
    return " ".join(str(value).split())
