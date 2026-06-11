"""Dataclass models for deterministic vacancy classification."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


CLASSIFICATION_VERSION = "ccog-filter-v4"
EXTRACTOR_VERSION = "source-features-v1"


class ContractCategory(str, Enum):
    CONSULTANT = "consultant"
    TEMPORARY_APPOINTMENT_STAFF = "temporary_appointment_staff"
    FIXED_TERM_APPOINTMENT_STAFF = "fixed_term_appointment_staff"
    INTERNSHIP_PAID = "internship_paid"
    INTERNSHIP_UNPAID = "internship_unpaid"
    INTERNSHIP_UNKNOWN = "internship_unknown"
    VOLUNTEERING_UNV = "volunteering_unv"
    STAFF_OTHER = "staff_other"
    OTHER = "other"
    UNKNOWN = "unknown"


class NationalInternational(str, Enum):
    NATIONAL = "national"
    INTERNATIONAL = "international"
    LOCAL = "local"
    GLOBAL_REMOTE = "global_remote"
    UNV_NATIONAL = "unv_national"
    UNV_INTERNATIONAL = "unv_international"
    UNKNOWN = "unknown"


class WorkModality(str, Enum):
    ONSITE = "onsite"
    ONLINE_REMOTE = "online_remote"
    HOME_BASED = "home_based"
    HYBRID = "hybrid"
    MULTIPLE_LOCATIONS = "multiple_locations"
    UNKNOWN = "unknown"


class UNVCategory(str, Enum):
    UN_COMMUNITY_VOLUNTEER = "un_community_volunteer"
    UN_UNIVERSITY_VOLUNTEER = "un_university_volunteer"
    UN_YOUTH_VOLUNTEER = "un_youth_volunteer"
    UN_VOLUNTEER_SPECIALIST = "un_volunteer_specialist"
    UN_VOLUNTEER_EXPERT = "un_volunteer_expert"
    OTHER_UNV = "other_unv"
    UNKNOWN = "unknown"


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


@dataclass(slots=True)
class FeatureBundle:
    vacancy_id: str
    source_id: str
    ats_family: str
    title: str | None = None
    description: str | None = None
    location_text: str | None = None
    department: str | None = None
    employment_type: str | None = None
    grade_raw: str | None = None
    grade_source_field: str | None = None
    contract_raw: str | None = None
    contract_source_field: str | None = None
    job_family_code: str | None = None
    job_family_label: str | None = None
    job_network_code: str | None = None
    job_network_label: str | None = None
    recruitment_type: str | None = None
    staff_category_raw: str | None = None
    seniority_raw: str | None = None
    country_code_raw: str | None = None
    city_raw: str | None = None
    region_raw: str | None = None
    work_modality_raw: str | None = None
    unv_category_code: str | None = None
    unv_category_label: str | None = None
    unv_volunteer_type: str | None = None
    unv_work_location: str | None = None
    unv_work_arrangement: str | None = None
    unv_assignment_duration: str | None = None
    unv_hours_week: str | None = None
    unv_host_entity: str | None = None
    unv_sdg: str | None = None
    unv_expertise_areas: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    extracted_at: datetime = field(default_factory=utc_now)
    extractor_version: str = EXTRACTOR_VERSION


@dataclass(slots=True)
class GradeResult:
    system: str | None = None
    family: str | None = None
    code: str | None = None
    level: str | None = None
    staff_category: str | None = None
    min_years_experience: int | None = None
    confidence: float = 0.0
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ContractResult:
    category: ContractCategory = ContractCategory.UNKNOWN
    subtype: str | None = None
    confidence: float = 0.0
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ScopeResult:
    value: NationalInternational = NationalInternational.UNKNOWN
    confidence: float = 0.0
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LocationResult:
    country: str | None = None
    iso2: str | None = None
    iso3: str | None = None
    city: str | None = None
    region: str | None = None
    subregion: str | None = None
    confidence: float = 0.0
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class VacancyLocation:
    vacancy_id: str
    city: str | None = None
    city_key: str | None = None
    country: str | None = None
    country_iso2: str | None = None
    country_iso3: str | None = None
    region: str | None = None
    subregion: str | None = None
    location_type: str = "primary"
    is_primary: bool = False
    is_remote: bool = False
    confidence: float = 0.0
    source_field: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ModalityResult:
    value: WorkModality = WorkModality.UNKNOWN
    confidence: float = 0.0
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class UNVResult:
    category: UNVCategory = UNVCategory.UNKNOWN
    raw_category: str | None = None
    volunteer_type: str | None = None
    assignment_duration: str | None = None
    work_arrangement: str | None = None
    hours_per_week: str | None = None
    host_entity: str | None = None
    sdg: str | None = None
    expertise_areas: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CCOGResult:
    code: str | None = None
    label: str | None = None
    family_code: str | None = None
    family_label: str | None = None
    part: str | None = None
    confidence: float = 0.0
    method: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ClassificationResult:
    vacancy_id: str
    ccog_primary_code: str | None = None
    ccog_primary_label: str | None = None
    ccog_family_code: str | None = None
    ccog_family_label: str | None = None
    ccog_part: str | None = None
    ccog_confidence: float = 0.0
    ccog_method: str | None = None
    occupational_family_code: str | None = None
    occupational_family_label: str | None = None
    occupational_medium_code: str | None = None
    occupational_medium_label: str | None = None
    occupational_small_code: str | None = None
    occupational_small_label: str | None = None
    occupational_confidence: float = 0.0
    occupational_classifier_version: str | None = None
    occupational_evidence: dict[str, Any] = field(default_factory=dict)
    mandate_network_code: str | None = None
    mandate_network_label: str | None = None
    mandate_family_code: str | None = None
    mandate_family_label: str | None = None
    primary_mandate_network: str | None = None
    primary_mandate_family: str | None = None
    secondary_mandate_families: list[str] = field(default_factory=list)
    mandate_source: str | None = None
    mandate_confidence: float = 0.0
    mandate_evidence: dict[str, Any] = field(default_factory=dict)
    source_native_category: str | None = None
    source_native_job_family: str | None = None
    source_native_job_network: str | None = None
    capability_tags: list[str] = field(default_factory=list)
    capability_tag_scores: dict[str, float] = field(default_factory=dict)
    capability_tag_evidence: dict[str, Any] = field(default_factory=dict)
    capability_classifier_version: str | None = None
    contract_category: ContractCategory = ContractCategory.UNKNOWN
    contract_subtype: str | None = None
    contract_confidence: float = 0.0
    contract_group: str | None = None
    contract_group_confidence: float = 0.0
    contract_group_evidence: dict[str, Any] = field(default_factory=dict)
    seniority_group: str | None = None
    seniority_confidence: float = 0.0
    seniority_evidence: dict[str, Any] = field(default_factory=dict)
    national_international: NationalInternational = NationalInternational.UNKNOWN
    national_international_confidence: float = 0.0
    grade_system: str | None = None
    grade_family: str | None = None
    grade_code: str | None = None
    grade_level: str | None = None
    staff_category: str | None = None
    min_years_experience: int | None = None
    grade_confidence: float = 0.0
    grade_mapping_organization: str | None = None
    grade_mapping_raw_grade_code: str | None = None
    standard_grade_family: str | None = None
    standard_seniority_tier: str | None = None
    standard_scope: str | None = None
    standard_employment_category: str | None = None
    standard_un_equivalent: str | None = None
    standard_experience_range: str | None = None
    standard_role_scope: str | None = None
    standard_supervisory_expectations: str | None = None
    grade_mapping_confidence: str | None = None
    grade_mapping_evidence_type: str | None = None
    grade_mapping_notes: str | None = None
    country: str | None = None
    country_iso2: str | None = None
    country_iso3: str | None = None
    city: str | None = None
    region: str | None = None
    subregion: str | None = None
    location_confidence: float = 0.0
    work_modality: WorkModality = WorkModality.UNKNOWN
    work_modality_confidence: float = 0.0
    unv_category: UNVCategory | None = None
    unv_raw_category: str | None = None
    unv_volunteer_type: str | None = None
    unv_assignment_duration: str | None = None
    unv_work_arrangement: str | None = None
    unv_hours_per_week: str | None = None
    unv_host_entity: str | None = None
    unv_sdg: str | None = None
    unv_expertise_areas: list[str] = field(default_factory=list)
    quality_flags: list[str] = field(default_factory=list)
    needs_review: bool = False
    classification_version: str = CLASSIFICATION_VERSION
    evidence: dict[str, Any] = field(default_factory=dict)
    classified_at: datetime = field(default_factory=utc_now)
