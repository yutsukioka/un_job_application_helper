"""Classification orchestration and persistence."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from jobagg.classification.classifiers.ccog import ccog_tree, classify_ccog
from jobagg.classification.classifiers.contract import classify_contract
from jobagg.classification.classifiers.grade import classify_grade
from jobagg.classification.classifiers.location import classify_location
from jobagg.classification.classifiers.modality import classify_modality
from jobagg.classification.classifiers.national_scope import classify_national_scope
from jobagg.classification.classifiers.unv import classify_unv
from jobagg.classification.extractors import get_extractor
from jobagg.classification.grade_mapping import standardize_grade
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
from jobagg.filters.normalization import country_for_city, country_info, display_city, normalize_city


LOCATION_OVERRIDE_FIELDS = {
    "country",
    "country_iso2",
    "country_iso3",
    "city",
    "region",
    "subregion",
    "location_confidence",
}


def classify_database(
    db: JobDatabase,
    *,
    source_id: str | None = None,
    status: str | None = None,
    version: str = CLASSIFICATION_VERSION,
    force: bool = False,
) -> int:
    # Pre-compute existing (vacancy_id -> source_hash) for the current
    # classification version so we can skip rows whose underlying job
    # content has not changed. ``force=True`` (e.g. ``--reclassify-all``)
    # bypasses this and re-classifies every row.
    existing_state: dict[str, str | None] = (
        {} if force else db.classification_state(version)
    )
    count = 0
    skipped = 0
    for row in db.iter_jobs(source_id=source_id, status=status):
        current_hash = row.get("normalized_hash")
        previous_hash = existing_state.get(row["job_key"])
        if (
            not force
            and current_hash is not None
            and previous_hash is not None
            and previous_hash == current_hash
        ):
            skipped += 1
            continue
        classify_and_store(row, db, version=version)
        count += 1
    if skipped:
        import logging

        logging.getLogger(__name__).info(
            "classify_database: classified=%s skipped_unchanged=%s version=%s",
            count,
            skipped,
            version,
        )
    return count


def classify_and_store(
    vacancy: dict[str, Any],
    db: JobDatabase,
    *,
    version: str = CLASSIFICATION_VERSION,
) -> ClassificationResult:
    features, result, locations = classify_job_with_locations(vacancy, version=version)
    db.upsert_vacancy_source_features(features)
    overrides = db.classification_overrides(result.vacancy_id)
    result = apply_overrides(result, overrides)
    locations = apply_location_overrides(locations, result, overrides)
    db.upsert_vacancy_classification(result, source_hash=vacancy.get("normalized_hash"))
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
    grade_standardization = standardize_grade(features, grade, contract)
    national_scope = classify_national_scope(features, grade, contract)
    location = classify_location(features)
    modality = classify_modality(features, location)
    locations = build_vacancy_locations(features, location, modality)
    best_location = best_vacancy_location(locations)
    if best_location is not None and best_location.confidence >= location.confidence:
        location = location_result_from_vacancy_location(best_location)
        modality = classify_modality(features, location)
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
        grade_mapping_organization=grade_standardization.mapping_organization
        if grade_standardization
        else None,
        grade_mapping_raw_grade_code=grade_standardization.mapping_raw_grade_code
        if grade_standardization
        else None,
        standard_grade_family=grade_standardization.normalized_grade_family
        if grade_standardization
        else None,
        standard_seniority_tier=grade_standardization.normalized_seniority_tier
        if grade_standardization
        else None,
        standard_scope=grade_standardization.international_national_local
        if grade_standardization
        else None,
        standard_employment_category=grade_standardization.staff_consultant_contractor_other
        if grade_standardization
        else None,
        standard_un_equivalent=grade_standardization.approximate_un_equivalent
        if grade_standardization
        else None,
        standard_experience_range=grade_standardization.approximate_experience_range
        if grade_standardization
        else None,
        standard_role_scope=grade_standardization.typical_role_scope
        if grade_standardization
        else None,
        standard_supervisory_expectations=grade_standardization.supervisory_expectations
        if grade_standardization
        else None,
        grade_mapping_confidence=grade_standardization.confidence_level
        if grade_standardization
        else None,
        grade_mapping_evidence_type=grade_standardization.evidence_type
        if grade_standardization
        else None,
        grade_mapping_notes=grade_standardization.notes_caveats if grade_standardization else None,
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
            "grade_standardization": grade_standardization.evidence
            if grade_standardization
            else None,
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
        _apply_ccog_dependencies(result, overrides)
        result.evidence.setdefault("manual_overrides", {}).update(overrides)
    return result


def apply_location_overrides(
    locations: list[VacancyLocation],
    result: ClassificationResult,
    overrides: dict[str, str],
) -> list[VacancyLocation]:
    if not (LOCATION_OVERRIDE_FIELDS & set(overrides)):
        return locations

    city_key = normalize_city(result.city)
    has_country_override = bool({"country", "country_iso2", "country_iso3"} & set(overrides))
    if "city" in overrides and not has_country_override:
        country = country_for_city(city_key)
    else:
        country_lookup = (
            result.country_iso3
            if "country_iso3" in overrides
            else result.country_iso2
            if "country_iso2" in overrides
            else result.country
            if "country" in overrides
            else result.country_iso3 or result.country_iso2 or result.country
        )
        country = country_info(country_lookup)
    if country is None and city_key:
        country = country_for_city(city_key)

    if country is not None:
        result.country = country.name
        result.country_iso2 = result.country_iso2 if "country_iso2" in overrides else country.iso2
        result.country_iso3 = result.country_iso3 if "country_iso3" in overrides else country.iso3
        if "region" not in overrides:
            result.region = country.region
        if "subregion" not in overrides:
            result.subregion = country.subregion

    if result.city:
        result.city = display_city(result.city) or result.city

    if not (result.city or result.country_iso3 or result.country):
        return locations

    if "location_confidence" not in overrides:
        result.location_confidence = 1.0

    result.evidence.setdefault("manual_overrides", {}).update(
        {field: overrides[field] for field in sorted(LOCATION_OVERRIDE_FIELDS & set(overrides))}
    )
    return [
        VacancyLocation(
            vacancy_id=result.vacancy_id,
            city=result.city,
            city_key=city_key,
            country=result.country or (country.name if country else None),
            country_iso2=result.country_iso2 or (country.iso2 if country else None),
            country_iso3=result.country_iso3 or (country.iso3 if country else None),
            region=result.region or (country.region if country else None),
            subregion=result.subregion or (country.subregion if country else None),
            location_type="primary",
            is_primary=True,
            is_remote=False,
            confidence=result.location_confidence,
            source_field="manual_override",
            evidence={
                "manual_override": {
                    field: overrides[field]
                    for field in sorted(LOCATION_OVERRIDE_FIELDS & set(overrides))
                }
            },
        )
    ]


def _apply_ccog_dependencies(
    result: ClassificationResult,
    overrides: dict[str, str],
) -> None:
    tree = {entry["code"]: entry for entry in ccog_tree()}
    if "ccog_primary_code" in overrides and result.ccog_primary_code:
        entry = tree.get(result.ccog_primary_code)
        if entry:
            _set_if_not_overridden(result, overrides, "ccog_primary_label", entry.get("label"))
            _set_if_not_overridden(result, overrides, "ccog_family_code", entry.get("family_code"))
            _set_if_not_overridden(result, overrides, "ccog_family_label", entry.get("family_label"))
            _set_if_not_overridden(result, overrides, "ccog_part", entry.get("part"))
        _set_if_not_overridden(result, overrides, "ccog_confidence", 1.0)
        _set_if_not_overridden(result, overrides, "ccog_method", "manual_override")

    if "ccog_family_code" in overrides and result.ccog_family_code:
        entry = tree.get(result.ccog_family_code)
        if entry:
            _set_if_not_overridden(result, overrides, "ccog_family_label", entry.get("label"))
            _set_if_not_overridden(result, overrides, "ccog_part", entry.get("part"))


def _set_if_not_overridden(
    result: ClassificationResult,
    overrides: dict[str, str],
    field_name: str,
    value: object,
) -> None:
    if field_name in overrides or value in (None, ""):
        return
    setattr(result, field_name, value)


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
