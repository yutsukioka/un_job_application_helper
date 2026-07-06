"""Pydantic models for the local job API contract."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, conint, conlist, constr, field_validator
from jobagg.filters.saved_searches import validate_saved_search_name


ShortText = constr(strip_whitespace=True, max_length=256)
MediumText = constr(strip_whitespace=True, max_length=1024)
LongText = constr(max_length=4000)
QueryText = constr(max_length=2000)
PathText = constr(strip_whitespace=True, max_length=4096)
DateText = constr(strip_whitespace=True, max_length=32)
SortText = constr(strip_whitespace=True, max_length=64)
FilterList = conlist(ShortText, max_length=100)
ResultList = conlist(dict[str, Any], max_length=200)
ArtifactList = conlist(PathText, max_length=100)
DocumentList = conlist(ShortText, max_length=50)
LimitInt = conint(gt=0, le=200)
OffsetInt = conint(ge=0, le=100_000)


class SearchRequest(BaseModel):
    text: QueryText | None = None
    status: FilterList = Field(default_factory=lambda: ["open"])
    organizations: FilterList = Field(default_factory=list)
    source_ids: FilterList = Field(default_factory=list)
    ats_families: FilterList = Field(default_factory=list)
    cities: FilterList = Field(default_factory=list)
    countries_iso3: FilterList = Field(default_factory=list)
    regions: FilterList = Field(default_factory=list)
    location_types: FilterList = Field(default_factory=lambda: ["primary", "duty_station", "outposted"])
    national_international: FilterList = Field(default_factory=list)
    contract_categories: FilterList = Field(default_factory=list)
    grade_systems: FilterList = Field(default_factory=list)
    grade_families: FilterList = Field(default_factory=list)
    grade_codes: FilterList = Field(default_factory=list)
    ccog_codes: FilterList = Field(default_factory=list)
    ccog_families: FilterList = Field(default_factory=list)
    occupational_family_codes: FilterList = Field(default_factory=list)
    occupational_medium_codes: FilterList = Field(default_factory=list)
    mandate_network_codes: FilterList = Field(default_factory=list)
    mandate_family_codes: FilterList = Field(default_factory=list)
    capability_tags: FilterList = Field(default_factory=list)
    contract_groups: FilterList = Field(default_factory=list)
    seniority_groups: FilterList = Field(default_factory=list)
    work_modalities: FilterList = Field(default_factory=list)
    volunteer_kinds: FilterList = Field(default_factory=list)
    unv_categories: FilterList = Field(default_factory=list)
    unv_volunteer_types: FilterList = Field(default_factory=list)
    closing_date_from: DateText | None = None
    closing_date_to: DateText | None = None
    posted_date_from: DateText | None = None
    posted_date_to: DateText | None = None
    min_location_confidence: float = 0.70
    min_grade_confidence: float = 0.70
    include_low_confidence: bool = False
    exclude_expired_open: bool = True
    include_facets: bool = True
    include_explain: bool = False
    score_against: PathText | None = None
    min_score: float | None = None
    limit: LimitInt = 50
    offset: OffsetInt = 0
    sort: SortText = "closing_date_asc"


class SearchResponse(BaseModel):
    total: int
    limit: LimitInt
    offset: OffsetInt
    results: ResultList
    facets: dict[str, dict[str, int]] = Field(default_factory=dict)
    facet_labels: dict[str, dict[str, str]] = Field(default_factory=dict)
    unclassified_count: int = 0


class SavedSearchModel(BaseModel):
    name: ShortText
    request: SearchRequest
    summary: LongText = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return validate_saved_search_name(value)


class ApplicationRecord(BaseModel):
    id: ShortText
    job_key: MediumText
    status: Literal[
        "saved",
        "interested",
        "drafting",
        "applied",
        "interview",
        "offer",
        "rejected",
        "withdrawn",
    ] = "saved"
    notes: LongText = ""
    applied_at: datetime | None = None
    updated_at: datetime | None = None


class AssistantRunRequest(BaseModel):
    job_key: MediumText
    agent_mode: Literal["single", "ensemble_v2", "auto_budget"] = "single"
    requested_documents: DocumentList = Field(default_factory=list)
    llm_provider_config_id: ShortText | None = None
    cost_limit_usd: float | None = None


class AssistantRunResult(BaseModel):
    id: ShortText
    status: Literal["queued", "running", "complete", "failed", "not_implemented"]
    agent_mode: ShortText
    artifacts: ArtifactList = Field(default_factory=list)
    message: LongText = ""


class LLMProviderConfig(BaseModel):
    id: ShortText
    provider: ShortText
    model: ShortText
    key_source: Literal["user_keychain", "app_metered", "local"] = "user_keychain"
    base_url: PathText | None = None
