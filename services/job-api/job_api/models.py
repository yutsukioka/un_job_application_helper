"""Pydantic models for the local job API contract."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


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
    unv_categories: list[str] = Field(default_factory=list)
    unv_volunteer_types: list[str] = Field(default_factory=list)
    closing_date_from: str | None = None
    closing_date_to: str | None = None
    posted_date_from: str | None = None
    posted_date_to: str | None = None
    min_location_confidence: float = 0.70
    min_grade_confidence: float = 0.70
    include_low_confidence: bool = False
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
    unclassified_count: int = 0


class SavedSearchModel(BaseModel):
    name: str
    request: SearchRequest
    summary: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ApplicationRecord(BaseModel):
    id: str
    job_key: str
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
    notes: str = ""
    applied_at: datetime | None = None
    updated_at: datetime | None = None


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
