"""Search taxonomy enrichment for classified vacancies."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from jobagg.classification.classifiers.ccog import (
    collapse_ccog_to_medium,
    get_ccog_family,
    get_ccog_medium,
    normalize_ccog_code,
)
from jobagg.classification.models import (
    CCOGResult,
    ContractCategory,
    ContractResult,
    FeatureBundle,
    GradeResult,
    UNVCategory,
    UNVResult,
)


TAXONOMY_VERSION = "search-taxonomy-v1"


@dataclass(slots=True)
class TaxonomyEnrichment:
    occupational_family_code: str | None = None
    occupational_family_label: str | None = None
    occupational_medium_code: str | None = None
    occupational_medium_label: str | None = None
    occupational_small_code: str | None = None
    occupational_small_label: str | None = None
    occupational_confidence: float = 0.0
    occupational_classifier_version: str = TAXONOMY_VERSION
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
    capability_classifier_version: str = TAXONOMY_VERSION
    contract_group: str = "unknown"
    contract_group_confidence: float = 0.0
    contract_group_evidence: dict[str, Any] = field(default_factory=dict)
    seniority_group: str = "unknown"
    seniority_confidence: float = 0.0
    seniority_evidence: dict[str, Any] = field(default_factory=dict)
    quality_flags: list[str] = field(default_factory=list)


def enrich_search_taxonomy(
    features: FeatureBundle,
    grade: GradeResult,
    contract: ContractResult,
    ccog: CCOGResult,
    unv: UNVResult | None,
) -> TaxonomyEnrichment:
    contract_group, contract_group_confidence, contract_group_evidence = _contract_group(
        features,
        grade,
        contract,
    )
    seniority_group, seniority_confidence, seniority_evidence = _seniority_group(
        features,
        grade,
        contract_group,
    )
    result = TaxonomyEnrichment(
        **_occupational_fields(ccog),
        **_mandate_fields(features),
        **_capability_fields(features),
        contract_group=contract_group,
        contract_group_confidence=contract_group_confidence,
        contract_group_evidence=contract_group_evidence,
        seniority_group=seniority_group,
        seniority_confidence=seniority_confidence,
        seniority_evidence=seniority_evidence,
    )
    result.quality_flags = _quality_flags(result, features, grade, contract, ccog, unv)
    return result


def _occupational_fields(ccog: CCOGResult) -> dict[str, Any]:
    code = normalize_ccog_code(ccog.code or "")
    if not code:
        return {
            "occupational_confidence": 0.0,
            "occupational_evidence": {"reason": "no ccog_primary_code"},
        }
    family = get_ccog_family(code) or {}
    medium = get_ccog_medium(code) or {}
    medium_code = collapse_ccog_to_medium(code)
    parts = code.split(".")
    small_code = code if len(parts) >= 4 else None
    small_label = ccog.label if small_code else None
    return {
        "occupational_family_code": family.get("code") or ccog.family_code,
        "occupational_family_label": family.get("label") or ccog.family_label,
        "occupational_medium_code": medium_code,
        "occupational_medium_label": medium.get("label") if medium else ccog.label,
        "occupational_small_code": small_code,
        "occupational_small_label": small_label,
        "occupational_confidence": ccog.confidence,
        "occupational_evidence": {
            "ccog_primary_code": ccog.code,
            "ccog_method": ccog.method,
            "ccog_evidence": ccog.evidence,
        },
    }


def _mandate_fields(features: FeatureBundle) -> dict[str, Any]:
    source_native_family = features.job_family_label or features.job_family_code
    source_native_network = features.job_network_label or features.job_network_code
    if features.job_network_label and features.job_family_label:
        network_code = _network_code_for_label(features.job_network_label)
        family_code = _mandate_family_code(network_code, features.job_family_label)
        return {
            "mandate_network_code": network_code,
            "mandate_network_label": features.job_network_label,
            "mandate_family_code": family_code,
            "mandate_family_label": features.job_family_label,
            "primary_mandate_network": features.job_network_label,
            "primary_mandate_family": features.job_family_label,
            "mandate_source": "un_careers_native",
            "mandate_confidence": 1.0,
            "mandate_evidence": {
                "job_network_label": features.job_network_label,
                "job_family_label": features.job_family_label,
            },
            "source_native_category": features.evidence.get("source_native_category"),
            "source_native_job_family": source_native_family,
            "source_native_job_network": source_native_network,
        }

    text = _combined_text(features)
    for rule in _taxonomy_file("mandate_crosswalk.yaml").get("rules", []):
        for pattern in rule.get("patterns", []) or []:
            if _phrase_match(text, str(pattern)):
                network_code = str(rule["network_code"])
                family = str(rule["family"])
                network_label = _network_label(network_code)
                return {
                    "mandate_network_code": network_code,
                    "mandate_network_label": network_label,
                    "mandate_family_code": _mandate_family_code(network_code, family),
                    "mandate_family_label": family,
                    "primary_mandate_network": network_label,
                    "primary_mandate_family": family,
                    "mandate_source": "classifier_inferred",
                    "mandate_confidence": 0.72,
                    "mandate_evidence": {"matched": pattern, "fields": ["title", "department", "description"]},
                    "source_native_category": features.evidence.get("source_native_category"),
                    "source_native_job_family": source_native_family,
                    "source_native_job_network": source_native_network,
                }
    return {
        "mandate_source": "manual_review",
        "mandate_confidence": 0.0,
        "mandate_evidence": {"reason": "no native or mapped mandate signal"},
        "source_native_category": features.evidence.get("source_native_category"),
        "source_native_job_family": source_native_family,
        "source_native_job_network": source_native_network,
    }


def _capability_fields(features: FeatureBundle) -> dict[str, Any]:
    text = _combined_text(features)
    scores: dict[str, float] = {}
    evidence: dict[str, Any] = {}
    tags = _taxonomy_file("capability_tags.yaml").get("tags", {})
    for tag, config in tags.items():
        keywords = list((config or {}).get("keywords", []) or [])
        keywords.append(str(tag).replace("_", " "))
        matches = [keyword for keyword in keywords if _phrase_match(text, str(keyword))]
        if not matches:
            continue
        scores[str(tag)] = 0.95 if any(keyword in text for keyword in matches) else 0.80
        evidence[str(tag)] = {"matched": sorted(set(matches))[:5]}
    selected = sorted(scores, key=lambda item: (-scores[item], item))[:8]
    return {
        "capability_tags": selected,
        "capability_tag_scores": {tag: scores[tag] for tag in selected},
        "capability_tag_evidence": {tag: evidence[tag] for tag in selected},
    }


def _contract_group(
    features: FeatureBundle,
    grade: GradeResult,
    contract: ContractResult,
) -> tuple[str, float, dict[str, Any]]:
    category = contract.category.value
    family = grade.family
    title = (features.title or "").casefold()
    config = _taxonomy_file("contract_groups.yaml").get("groups", {})
    for group, rules in config.items():
        if category in set(rules.get("contract_categories", []) or []):
            return str(group), max(contract.confidence, 0.80), {"contract_category": category}
        if family and family in set(rules.get("grade_families", []) or []):
            return str(group), max(grade.confidence, 0.72), {"grade_family": family}
        for keyword in rules.get("title_keywords", []) or []:
            if _phrase_match(title, str(keyword)):
                return str(group), 0.66, {"title_keyword": keyword}
    return "unknown", 0.0, {"contract_category": category, "grade_family": family}


def _seniority_group(
    features: FeatureBundle,
    grade: GradeResult,
    contract_group: str,
) -> tuple[str, float, dict[str, Any]]:
    if contract_group == "volunteer":
        return "volunteer", 0.95, {"contract_group": contract_group}
    code = grade.code
    title = (features.title or "").casefold()
    config = _taxonomy_file("seniority_groups.yaml").get("groups", {})
    for group, rules in config.items():
        if code and code in set(rules.get("grade_codes", []) or []):
            return str(group), max(grade.confidence, 0.74), {"grade_code": code}
        if contract_group in set(rules.get("contract_groups", []) or []):
            return str(group), 0.70, {"contract_group": contract_group}
        for keyword in rules.get("title_keywords", []) or []:
            if _phrase_match(title, str(keyword)):
                return str(group), 0.58, {"title_keyword": keyword}
    if contract_group == "consultant_contractor":
        return "ungraded_nonstaff_or_pathway", 0.62, {"contract_group": contract_group}
    return "unknown", 0.0, {"grade_code": code, "contract_group": contract_group}


def _quality_flags(
    result: TaxonomyEnrichment,
    features: FeatureBundle,
    grade: GradeResult,
    contract: ContractResult,
    ccog: CCOGResult,
    unv: UNVResult | None,
) -> list[str]:
    flags: list[str] = []
    if result.occupational_confidence < 0.70:
        flags.append("low_occupational_confidence")
    if result.mandate_confidence < 0.60:
        flags.append("low_mandate_confidence")
    if not result.capability_tags:
        flags.append("low_capability_confidence")
    if result.contract_group == "unknown":
        flags.append("missing_contract_group")
    if result.seniority_group == "unknown":
        flags.append("missing_seniority")
    if not result.mandate_family_label:
        flags.append("missing_mandate_area")
    if unv and unv.category is UNVCategory.UNKNOWN and features.source_id == "unv_uvp":
        flags.append("possible_non_io_unv_host")
    if contract.category is ContractCategory.UNKNOWN and not grade.code and not ccog.code:
        flags.append("classification_sparse")
    return sorted(set(flags))


def _combined_text(features: FeatureBundle) -> str:
    parts = [
        features.title,
        features.department,
        features.employment_type,
        features.contract_raw,
        features.grade_raw,
        features.job_family_label,
        features.job_network_label,
        features.description,
    ]
    return " ".join(str(part) for part in parts if part).casefold()


def _phrase_match(text: str, phrase: str) -> bool:
    normalized = phrase.casefold().strip()
    if not normalized:
        return False
    if re.fullmatch(r"[a-z0-9]+", normalized):
        return re.search(rf"\b{re.escape(normalized)}\b", text) is not None
    return normalized in text


def _network_label(code: str | None) -> str | None:
    if not code:
        return None
    data = _taxonomy_file("un_job_networks.yaml").get("networks", {})
    network = data.get(code)
    return network.get("label") if network else None


def _network_code_for_label(label: str | None) -> str | None:
    if not label:
        return None
    normalized = label.casefold().strip()
    for code, network in _taxonomy_file("un_job_networks.yaml").get("networks", {}).items():
        if str(network.get("label") or "").casefold().strip() == normalized:
            return str(code)
    return None


def _mandate_family_code(network_code: str | None, family: str | None) -> str | None:
    if not network_code or not family:
        return None
    slug = re.sub(r"[^a-z0-9]+", "_", family.casefold()).strip("_")
    return f"{network_code}.{slug}" if slug else network_code


@lru_cache(maxsize=None)
def _taxonomy_file(name: str) -> dict[str, Any]:
    for base in _taxonomy_dirs():
        path = base / name
        if path.is_file():
            return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {}


def _taxonomy_dirs() -> list[Path]:
    package_root = Path(__file__).resolve().parents[2]
    return [
        Path.cwd() / "config" / "taxonomies",
        package_root / "config" / "taxonomies",
    ]
