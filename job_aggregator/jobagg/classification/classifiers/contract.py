"""Contract classification."""

from __future__ import annotations

import re

from jobagg.classification.models import ContractCategory, ContractResult, FeatureBundle, GradeResult
from jobagg.classification.rules import load_rule_file


def classify_contract(features: FeatureBundle, grade: GradeResult) -> ContractResult:
    if features.source_id == "unv_uvp":
        return ContractResult(
            category=ContractCategory.VOLUNTEERING_UNV,
            confidence=0.99,
            evidence={"source_id": features.source_id},
        )
    candidates: list[ContractResult] = []
    explicit = _source_contract(features.contract_raw)
    if explicit.category is not ContractCategory.UNKNOWN:
        candidates.append(explicit)
    text_fields = [
        ("title", features.title),
        ("employment_type", features.employment_type),
        ("description", features.description),
    ]
    for field_name, text in text_fields:
        result = _match_rules(text, field_name)
        if result.category is not ContractCategory.UNKNOWN:
            candidates.append(result)
    if candidates:
        result = _best_candidate(candidates)
        result.subtype = result.subtype or _grade_contract_subtype(grade)
        return result
    subtype = _grade_contract_subtype(grade)
    if subtype and grade.family in {"SSA"}:
        return ContractResult(
            category=ContractCategory.CONSULTANT,
            subtype=subtype,
            confidence=0.74,
            evidence={"grade_code": grade.code},
        )
    if subtype and grade.family in {"SC", "IICA", "LICA", "ICS"}:
        return ContractResult(
            category=ContractCategory.STAFF_OTHER,
            subtype=subtype,
            confidence=0.70,
            evidence={"grade_code": grade.code},
        )
    if grade.family in {"P", "D", "G", "NO", "FS", "UG"}:
        return ContractResult(
            category=ContractCategory.STAFF_OTHER,
            confidence=0.62,
            evidence={"grade_code": grade.code, "grade_family": grade.family},
        )
    return ContractResult(evidence={"checked": [name for name, _ in text_fields]})


def _source_contract(value: str | None) -> ContractResult:
    text = (value or "").strip().casefold()
    if not text:
        return ContractResult()
    rule_match = _match_rules(value, "source_contract")
    if rule_match.category is not ContractCategory.UNKNOWN:
        rule_match.subtype = value
        rule_match.confidence = 0.94
        return rule_match
    mappings = {
        "consultant": ContractCategory.CONSULTANT,
        "consultancy": ContractCategory.CONSULTANT,
        "intern": ContractCategory.INTERNSHIP_UNKNOWN,
        "internship": ContractCategory.INTERNSHIP_UNKNOWN,
        "temporary appointment": ContractCategory.TEMPORARY_APPOINTMENT_STAFF,
        "temporary": ContractCategory.TEMPORARY_APPOINTMENT_STAFF,
        "fixed term": ContractCategory.FIXED_TERM_APPOINTMENT_STAFF,
        "fixed-term": ContractCategory.FIXED_TERM_APPOINTMENT_STAFF,
        "general service": ContractCategory.STAFF_OTHER,
        "professional": ContractCategory.STAFF_OTHER,
    }
    for needle, category in mappings.items():
        if needle in text:
            return ContractResult(
                category=category,
                subtype=value,
                confidence=0.90,
                evidence={"source_contract": value, "matched": needle},
            )
    return ContractResult()


def _best_candidate(candidates: list[ContractResult]) -> ContractResult:
    return max(candidates, key=lambda result: (_specificity(result.category), result.confidence))


def _specificity(category: ContractCategory) -> int:
    if category in {ContractCategory.INTERNSHIP_PAID, ContractCategory.INTERNSHIP_UNPAID}:
        return 4
    if category in {
        ContractCategory.FIXED_TERM_APPOINTMENT_STAFF,
        ContractCategory.TEMPORARY_APPOINTMENT_STAFF,
        ContractCategory.CONSULTANT,
        ContractCategory.VOLUNTEERING_UNV,
    }:
        return 3
    if category is ContractCategory.INTERNSHIP_UNKNOWN:
        return 1
    return 0


def _match_rules(text: str | None, field_name: str) -> ContractResult:
    normalized = f" {(text or '').casefold()} "
    if not normalized.strip():
        return ContractResult()
    rules = load_rule_file("contract_rules.yaml").get("contract_rules", {})
    priority = [
        ContractCategory.INTERNSHIP_UNPAID,
        ContractCategory.INTERNSHIP_PAID,
        ContractCategory.FIXED_TERM_APPOINTMENT_STAFF,
        ContractCategory.TEMPORARY_APPOINTMENT_STAFF,
        ContractCategory.CONSULTANT,
        ContractCategory.INTERNSHIP_UNKNOWN,
    ]
    for category in priority:
        config = rules.get(category.value, {})
        for keyword in config.get("keywords", []) or []:
            if _keyword_matches(normalized, str(keyword).casefold()):
                return ContractResult(
                    category=category,
                    confidence=0.82 if field_name == "description" else 0.90,
                    evidence={"field": field_name, "matched": keyword},
                )
    return ContractResult()


def _keyword_matches(text: str, keyword: str) -> bool:
    if keyword.strip() in {"ta", "con"}:
        return re.search(rf"\b{re.escape(keyword.strip())}\b", text) is not None
    return keyword in text


def _grade_contract_subtype(grade: GradeResult) -> str | None:
    if grade.family in {"SSA", "SC", "IICA", "LICA", "ICS"}:
        return grade.code
    return None
