"""National/international scope classification."""

from __future__ import annotations

from jobagg.classification.models import (
    ContractResult,
    FeatureBundle,
    GradeResult,
    NationalInternational,
    ScopeResult,
)


def classify_national_scope(
    features: FeatureBundle,
    grade: GradeResult,
    contract: ContractResult,
) -> ScopeResult:
    if features.source_id == "unv_uvp":
        volunteer_type = (features.unv_volunteer_type or "").casefold()
        if "international" in volunteer_type:
            return ScopeResult(
                value=NationalInternational.UNV_INTERNATIONAL,
                confidence=0.98,
                evidence={"unv_volunteer_type": features.unv_volunteer_type},
            )
        if "national" in volunteer_type:
            return ScopeResult(
                value=NationalInternational.UNV_NATIONAL,
                confidence=0.98,
                evidence={"unv_volunteer_type": features.unv_volunteer_type},
            )
    if grade.family == "NO":
        return ScopeResult(value=NationalInternational.NATIONAL, confidence=0.95, evidence=grade.evidence)
    if grade.family in {"G", "GS", "SC", "LICA"}:
        return ScopeResult(value=NationalInternational.LOCAL, confidence=0.90, evidence=grade.evidence)
    if grade.family in {"P", "D", "FS", "IICA", "ICS"}:
        return ScopeResult(
            value=NationalInternational.INTERNATIONAL,
            confidence=0.90,
            evidence=grade.evidence,
        )
    text = f"{features.title or ''} {features.description or ''}".casefold()
    if "nationals only" in text or "national consultant" in text:
        return ScopeResult(
            value=NationalInternational.NATIONAL,
            confidence=0.85,
            evidence={"matched": "national title/description signal"},
        )
    if "international consultant" in text or "jpo" in text:
        return ScopeResult(
            value=NationalInternational.INTERNATIONAL,
            confidence=0.85,
            evidence={"matched": "international title/description signal"},
        )
    if contract.category.value.startswith("internship") and "home" in text and "country" not in text:
        return ScopeResult(
            value=NationalInternational.GLOBAL_REMOTE,
            confidence=0.70,
            evidence={"matched": "home-based internship"},
        )
    return ScopeResult()
