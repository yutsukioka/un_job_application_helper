"""Filter query dataclasses."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass(slots=True)
class VacancyFilters:
    text: str | None = None
    organization: str | None = None
    source_id: str | None = None
    ats_family: str | None = None
    ccog_part: str | None = None
    ccog_family: str | None = None
    ccog_code: str | None = None
    contract_category: str | None = None
    contract_subtype: str | None = None
    grade_system: str | None = None
    grade_family: str | None = None
    grade_code: str | None = None
    staff_category: str | None = None
    max_min_years_experience: int | None = None
    national_international: str | None = None
    country: str | None = None
    country_iso3: str | None = None
    city: str | None = None
    region: str | None = None
    subregion: str | None = None
    work_modality: str | None = None
    unv_category: str | None = None
    unv_volunteer_type: str | None = None
    unv_assignment_duration: str | None = None
    unv_work_arrangement: str | None = None
    unv_hours_per_week: str | None = None
    unv_host_entity: str | None = None
    unv_sdg: str | None = None
    unv_expertise_area: str | None = None
    posted_date_from: str | None = None
    posted_date_to: str | None = None
    closing_date_from: str | None = None
    closing_date_to: str | None = None
    only_active: bool = True
    needs_review: bool | None = None
    limit: int | None = None


@dataclass(slots=True)
class VacancySearchRequest:
    text: str | None = None
    status: list[str] = field(default_factory=lambda: ["open"])
    organizations: list[str] = field(default_factory=list)
    source_ids: list[str] = field(default_factory=list)
    ats_families: list[str] = field(default_factory=list)
    cities: list[str] = field(default_factory=list)
    countries_iso3: list[str] = field(default_factory=list)
    regions: list[str] = field(default_factory=list)
    location_types: list[str] = field(
        default_factory=lambda: ["primary", "duty_station", "outposted"]
    )
    national_international: list[str] = field(default_factory=list)
    contract_categories: list[str] = field(default_factory=list)
    grade_systems: list[str] = field(default_factory=list)
    grade_families: list[str] = field(default_factory=list)
    grade_codes: list[str] = field(default_factory=list)
    ccog_codes: list[str] = field(default_factory=list)
    ccog_families: list[str] = field(default_factory=list)
    work_modalities: list[str] = field(default_factory=list)
    unv_categories: list[str] = field(default_factory=list)
    unv_volunteer_types: list[str] = field(default_factory=list)
    closing_date_from: date | str | None = None
    closing_date_to: date | str | None = None
    posted_date_from: date | str | None = None
    posted_date_to: date | str | None = None
    min_location_confidence: float = 0.70
    min_grade_confidence: float = 0.70
    include_low_confidence: bool = False
    limit: int = 50
    offset: int = 0
    sort: str = "closing_date_asc"


@dataclass(slots=True)
class VacancySearchResponse:
    total: int
    limit: int
    offset: int
    results: list[dict[str, Any]]
    facets: dict[str, dict[str, int]] = field(default_factory=dict)
