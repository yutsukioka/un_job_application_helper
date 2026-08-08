"""Pydantic models for the local job API contract."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator


StrictFiniteNumber = StrictInt | Annotated[float, Field(strict=True, allow_inf_nan=False)]


class SearchRequest(BaseModel):
    text: str | None = None
    status: list[str] = Field(default_factory=lambda: ["open"])
    organizations: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    ats_families: list[str] = Field(default_factory=list)
    cities: list[str] = Field(default_factory=list)
    countries_iso3: list[str] = Field(default_factory=list)
    regions: list[str] = Field(default_factory=list)
    location_types: list[str] = Field(default_factory=lambda: ["primary", "duty_station", "outposted"])
    national_international: list[str] = Field(default_factory=list)
    contract_categories: list[str] = Field(default_factory=list)
    grade_systems: list[str] = Field(default_factory=list)
    grade_families: list[str] = Field(default_factory=list)
    grade_codes: list[str] = Field(default_factory=list)
    ccog_codes: list[str] = Field(default_factory=list)
    ccog_families: list[str] = Field(default_factory=list)
    occupational_family_codes: list[str] = Field(default_factory=list)
    occupational_medium_codes: list[str] = Field(default_factory=list)
    mandate_network_codes: list[str] = Field(default_factory=list)
    mandate_family_codes: list[str] = Field(default_factory=list)
    capability_tags: list[str] = Field(default_factory=list)
    contract_groups: list[str] = Field(default_factory=list)
    seniority_groups: list[str] = Field(default_factory=list)
    work_modalities: list[str] = Field(default_factory=list)
    volunteer_kinds: list[str] = Field(default_factory=list)
    unv_categories: list[str] = Field(default_factory=list)
    unv_volunteer_types: list[str] = Field(default_factory=list)
    closing_date_from: str | None = None
    closing_date_to: str | None = None
    posted_date_from: str | None = None
    posted_date_to: str | None = None
    min_location_confidence: float = 0.70
    min_grade_confidence: float = 0.70
    include_low_confidence: bool = False
    exclude_expired_open: bool = True
    include_facets: bool = True
    include_explain: bool = False
    score_against: str | None = None
    min_score: float | None = None
    limit: int = 50
    offset: int = 0
    sort: str = "closing_date_asc"


class SearchResponse(BaseModel):
    total: int
    limit: int
    offset: int
    results: list[dict[str, Any]]
    facets: dict[str, dict[str, int]] = Field(default_factory=dict)
    facet_labels: dict[str, dict[str, str]] = Field(default_factory=dict)
    unclassified_count: int = 0


class SavedSearchModel(BaseModel):
    name: str
    request: SearchRequest
    summary: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None


ApplicationStatus = Literal[
    "saved",
    "interested",
    "drafting",
    "applied",
    "interview",
    "offer",
    "rejected",
    "withdrawn",
]


class ApplicationRecord(BaseModel):
    id: str
    job_key: str
    status: ApplicationStatus = "saved"
    notes: str = ""
    applied_at: datetime | None = None
    updated_at: datetime | None = None


class SavedSearchStoredRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    text: str | None
    status: list[str]
    organizations: list[str]
    source_ids: list[str]
    ats_families: list[str]
    cities: list[str]
    countries_iso3: list[str]
    regions: list[str]
    location_types: list[str]
    national_international: list[str]
    contract_categories: list[str]
    grade_systems: list[str]
    grade_families: list[str]
    grade_codes: list[str]
    ccog_codes: list[str]
    ccog_families: list[str]
    occupational_family_codes: list[str]
    occupational_medium_codes: list[str]
    mandate_network_codes: list[str]
    mandate_family_codes: list[str]
    capability_tags: list[str]
    contract_groups: list[str]
    seniority_groups: list[str]
    work_modalities: list[str]
    volunteer_kinds: list[str]
    unv_categories: list[str]
    unv_volunteer_types: list[str]
    closing_date_from: str | None
    closing_date_to: str | None
    posted_date_from: str | None
    posted_date_to: str | None
    min_location_confidence: StrictFiniteNumber
    min_grade_confidence: StrictFiniteNumber
    include_low_confidence: bool
    exclude_expired_open: bool
    limit: int
    offset: int
    sort: str


class SavedSearchStoredSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str
    description: str | None
    request: SavedSearchStoredRequest
    created_at: str = Field(min_length=1)
    updated_at: str = Field(min_length=1)


class SavedSearchConditionalDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    expected: SavedSearchStoredSnapshot


class StrictApplicationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str
    job_key: str
    status: ApplicationStatus
    notes: str
    applied_at: str | None
    updated_at: str | None

    @field_validator("id")
    @classmethod
    def validate_nonempty_id(cls, value: str) -> str:
        if not value:
            raise ValueError("identifier must not be empty")
        return value


class TrackerConditionalDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    expected: StrictApplicationRecord


class ConditionalDeleteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["deleted", "absent"]


class AssistantRunRequest(BaseModel):
    job_key: str
    agent_mode: Literal["single", "ensemble_v2", "auto_budget"] = "single"
    requested_documents: list[str] = Field(default_factory=list)
    llm_provider_config_id: str | None = None
    cost_limit_usd: float | None = None


class AssistantRunResult(BaseModel):
    id: str
    status: Literal["queued", "running", "complete", "failed", "not_implemented"]
    agent_mode: str
    artifacts: list[str] = Field(default_factory=list)
    message: str = ""


class LLMProviderConfig(BaseModel):
    id: str
    provider: str
    model: str
    key_source: Literal["user_keychain", "app_metered", "local"] = "user_keychain"
    base_url: str | None = None
