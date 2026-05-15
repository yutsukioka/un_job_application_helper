"""Classification orchestration and persistence."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from jobagg.classification.classifiers.ccog import classify_ccog
from jobagg.classification.classifiers.contract import classify_contract
from jobagg.classification.classifiers.grade import classify_grade
from jobagg.classification.classifiers.location import classify_location
from jobagg.classification.classifiers.modality import classify_modality
from jobagg.classification.classifiers.national_scope import classify_national_scope
from jobagg.classification.classifiers.unv import classify_unv
from jobagg.classification.extractors import get_extractor
from jobagg.classification.locations import (
    best_vacancy_location,
    build_vacancy_locations,
    location_result_from_vacancy_location,
)
from jobagg.classification.models import (
    CLASSIFICATION_VERSION,
    ClassificationResult,
    ContractCategory,
    FeatureBundle,
    NationalInternational,
    UNVCategory,
    VacancyLocation,
    WorkModality,
)
from jobagg.db import JobDatabase


def classify_database(
    db: JobDatabase,
    *,
    source_id: str | None = None,
    status: str | None = None,
    version: str = CLASSIFICATION_VERSION,
) -> int:
    count = 0
    for row in db.iter_jobs(source_id=source_id, status=status):
        classify_and_store(row, db, version=version)
        count += 1
    return count


def classify_and_store(
    vacancy: dict[str, Any],
    db: JobDatabase,
    *,
    version: str = CLASSIFICATION_VERSION,
) -> ClassificationResult:
    features, result, locations = classify_job_with_locations(vacancy, version=version)
    db.upsert_vacancy_source_features(features)
    result = apply_overrides(result, db.classification_overrides(result.vacancy_id))
    db.upsert_vacancy_classification(result)
    db.replace_vacancy_locations(result.vacancy_id, locations)
    return result


def classify_job(
    vacancy: dict[str, Any],
    *,
    version: str = CLASSIFICATION_VERSION,
) -> tuple[FeatureBundle, ClassificationResult]:
    features, result, _ = classify_job_with_locations(vacancy, version=version)
    return features, result


def classify_job_with_locations(
    vacancy: dict[str, Any],
    *,
    version: str = CLASSIFICATION_VERSION,
) -> tuple[FeatureBundle, ClassificationResult, list[VacancyLocation]]:
    extractor = get_extractor(str(vacancy["source_id"]), str(vacancy["ats_family"]))
    features = extractor.extract(vacancy)
    grade = classify_grade(features)
    contract = classify_contract(features, grade)
    national_scope = classify_national_scope(features, grade, contract)
    location = classify_location(features)
    modality = classify_modality(features, location)
    locations = build_vacancy_locations(features, location, modality)
    best_location = best_vacancy_location(locations)
    if best_location is not None and best_location.confidence >= location.confidence:
        location = location_result_from_vacancy_location(best_location)
    unv = classify_unv(features)
    ccog = classify_ccog(features, grade, contract, unv)
    needs_review = (
        ccog.confidence < 0.70
        or contract.confidence < 0.50
        or (grade.confidence < 0.50 and features.source_id not in {"unv_uvp"})
    )
    result = ClassificationResult(
        vacancy_id=features.vacancy_id,
        ccog_primary_code=ccog.code,
        ccog_primary_label=ccog.label,
        ccog_family_code=ccog.family_code,
        ccog_family_label=ccog.family_label,
        ccog_part=ccog.part,
        ccog_confidence=ccog.confidence,
        ccog_method=ccog.method,
        contract_category=contract.category,
        contract_subtype=contract.subtype,
        contract_confidence=contract.confidence,
        national_international=national_scope.value,
        national_international_confidence=national_scope.confidence,
        grade_system=grade.system,
        grade_family=grade.family,
        grade_code=grade.code,
        grade_level=grade.level,
        staff_category=grade.staff_category,
        min_years_experience=grade.min_years_experience,
        grade_confidence=grade.confidence,
        country=location.country,
        country_iso2=location.iso2,
        country_iso3=location.iso3,
        city=location.city,
        region=location.region,
        subregion=location.subregion,
        location_confidence=location.confidence,
        work_modality=modality.value,
        work_modality_confidence=modality.confidence,
        unv_category=unv.category if unv else None,
        unv_raw_category=unv.raw_category if unv else None,
        unv_volunteer_type=unv.volunteer_type if unv else None,
        unv_assignment_duration=unv.assignment_duration if unv else None,
        unv_work_arrangement=unv.work_arrangement if unv else None,
        unv_hours_per_week=unv.hours_per_week if unv else None,
        unv_host_entity=unv.host_entity if unv else None,
        unv_sdg=unv.sdg if unv else None,
        unv_expertise_areas=unv.expertise_areas if unv else [],
        needs_review=needs_review,
        classification_version=version,
        evidence={
            "features": features.evidence,
            "grade": grade.evidence,
            "contract": contract.evidence,
            "national_scope": national_scope.evidence,
            "location": location.evidence,
            "modality": modality.evidence,
            "unv": unv.evidence if unv else None,
            "ccog": ccog.evidence,
            "locations": [location_to_row(item) for item in locations],
        },
    )
    return features, result, locations


def apply_overrides(
    result: ClassificationResult,
    overrides: dict[str, str],
) -> ClassificationResult:
    for field_name, value in overrides.items():
        if not hasattr(result, field_name):
            continue
        current = getattr(result, field_name)
        setattr(result, field_name, _coerce_override(value, current))
    if overrides:
        result.evidence.setdefault("manual_overrides", {}).update(overrides)
    return result


def classification_to_row(result: ClassificationResult) -> dict[str, Any]:
    row = asdict(result)
    for key in ("contract_category", "national_international", "work_modality", "unv_category"):
        if row.get(key) is not None:
            row[key] = row[key].value if hasattr(row[key], "value") else row[key]
    row["unv_expertise_areas"] = json.dumps(row["unv_expertise_areas"], ensure_ascii=True)
    row["evidence"] = json.dumps(row["evidence"], sort_keys=True, ensure_ascii=True)
    row["needs_review"] = int(bool(row["needs_review"]))
    row["classified_at"] = result.classified_at.isoformat()
    return row


def feature_to_row(features: FeatureBundle) -> dict[str, Any]:
    return {
        "vacancy_id": features.vacancy_id,
        "source_id": features.source_id,
        "ats_family": features.ats_family,
        "raw_title": features.title,
        "raw_description": features.description,
        "raw_location": features.location_text,
        "raw_department": features.department,
        "raw_employment_type": features.employment_type,
        "source_grade": features.grade_raw,
        "source_grade_field": features.grade_source_field,
        "source_contract_type": features.contract_raw,
        "source_contract_field": features.contract_source_field,
        "source_job_family_code": features.job_family_code,
        "source_job_family_label": features.job_family_label,
        "source_job_network_code": features.job_network_code,
        "source_job_network_label": features.job_network_label,
        "source_recruitment_type": features.recruitment_type,
        "source_staff_category": features.staff_category_raw,
        "source_seniority": features.seniority_raw,
        "source_country_code": features.country_code_raw,
        "source_city": features.city_raw,
        "source_region": features.region_raw,
        "source_work_modality": features.work_modality_raw,
        "source_unv_category_code": features.unv_category_code,
        "source_unv_category_label": features.unv_category_label,
        "source_unv_volunteer_type": features.unv_volunteer_type,
        "source_unv_work_location": features.unv_work_location,
        "source_unv_work_arrangement": features.unv_work_arrangement,
        "source_unv_assignment_duration": features.unv_assignment_duration,
        "source_unv_hours_week": features.unv_hours_week,
        "source_unv_host_entity": features.unv_host_entity,
        "source_unv_sdg": features.unv_sdg,
        "source_unv_expertise_areas": json.dumps(
            features.unv_expertise_areas,
            ensure_ascii=True,
        ),
        "evidence": json.dumps(features.evidence, sort_keys=True, ensure_ascii=True),
        "extracted_at": features.extracted_at.isoformat(),
        "extractor_version": features.extractor_version,
    }


def location_to_row(location: VacancyLocation) -> dict[str, Any]:
    return {
        "vacancy_id": location.vacancy_id,
        "city": location.city,
        "city_key": location.city_key,
        "country": location.country,
        "country_iso2": location.country_iso2,
        "country_iso3": location.country_iso3,
        "region": location.region,
        "subregion": location.subregion,
        "location_type": location.location_type,
        "is_primary": int(bool(location.is_primary)),
        "is_remote": int(bool(location.is_remote)),
        "confidence": location.confidence,
        "source_field": location.source_field,
        "evidence": json.dumps(location.evidence, sort_keys=True, ensure_ascii=True),
    }


def _coerce_override(value: str, current: object) -> object:
    if isinstance(current, bool):
        return value.casefold() in {"1", "true", "yes", "y"}
    if isinstance(current, float):
        return float(value)
    if isinstance(current, int):
        return int(value)
    if isinstance(current, ContractCategory):
        return ContractCategory(value)
    if isinstance(current, NationalInternational):
        return NationalInternational(value)
    if isinstance(current, WorkModality):
        return WorkModality(value)
    if isinstance(current, UNVCategory):
        return UNVCategory(value)
    if isinstance(current, list):
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else [str(parsed)]
    return value
