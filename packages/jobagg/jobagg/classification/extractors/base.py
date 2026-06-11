"""Source feature extraction helpers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from jobagg.classification.models import FeatureBundle


class SourceFeatureExtractor(ABC):
    source_id: str | None = None

    @abstractmethod
    def extract(self, vacancy: dict[str, Any]) -> FeatureBundle:
        """Extract source-specific classification evidence."""


def base_features(vacancy: dict[str, Any], *, evidence: dict[str, Any] | None = None) -> FeatureBundle:
    raw = raw_dict(vacancy)
    return FeatureBundle(
        vacancy_id=str(vacancy["job_key"]),
        source_id=str(vacancy["source_id"]),
        ats_family=str(vacancy["ats_family"]),
        title=vacancy.get("title"),
        description=vacancy.get("description"),
        location_text=vacancy.get("location"),
        department=vacancy.get("department"),
        employment_type=vacancy.get("employment_type"),
        contract_raw=vacancy.get("employment_type"),
        contract_source_field="jobs.employment_type" if vacancy.get("employment_type") else None,
        evidence=evidence or {"raw_keys": sorted(raw.keys())},
    )


def raw_dict(vacancy: dict[str, Any]) -> dict[str, Any]:
    raw = vacancy.get("raw")
    return raw if isinstance(raw, dict) else {}


def get_flex(raw: dict[str, Any], prompt: str) -> str | None:
    for item in raw.get("requisitionFlexFields", []) or []:
        if isinstance(item, dict) and item.get("Prompt") == prompt:
            value = item.get("Value")
            return str(value) if value not in (None, "") else None
    return None


def label(obj: Any) -> str | None:
    if obj is None:
        return None
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        for key in ("longDescription", "label", "shortDescription", "Name", "name", "description"):
            value = obj.get(key)
            if value not in (None, ""):
                return str(value)
    return None


def code(obj: Any) -> str | None:
    if not isinstance(obj, dict):
        return None
    value = obj.get("value")
    if isinstance(value, dict) and value.get("code") not in (None, ""):
        return str(value["code"])
    for key in ("code", "Code"):
        if obj.get(key) not in (None, ""):
            return str(obj[key])
    return None


def first_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value[0]
    if isinstance(value, dict):
        return value
    return {}


def text_join(values: list[Any]) -> str | None:
    parts = [str(value).strip() for value in values if value not in (None, "") and str(value).strip()]
    return "; ".join(parts) if parts else None
