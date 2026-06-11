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
    if grade.family == "NPSA":
        return ScopeResult(value=NationalInternational.NATIONAL, confidence=0.92, evidence=grade.evidence)
    if grade.family == "UNRWA":
        return ScopeResult(value=NationalInternational.NATIONAL, confidence=0.86, evidence=grade.evidence)
    if grade.family in {"G", "GS", "SC", "LICA"}:
        return ScopeResult(value=NationalInternational.LOCAL, confidence=0.90, evidence=grade.evidence)
    if grade.family == "IPSA":
        return ScopeResult(value=NationalInternational.INTERNATIONAL, confidence=0.92, evidence=grade.evidence)
    if grade.family == "GL":
        return ScopeResult(value=NationalInternational.INTERNATIONAL, confidence=0.78, evidence=grade.evidence)
    if grade.family in {"P", "D", "FS", "IICA", "ICS"}:
        return ScopeResult(
            value=NationalInternational.INTERNATIONAL,
            confidence=0.90,
            evidence=grade.evidence,
        )
    contract_text = (features.contract_raw or "").casefold()
    if "international consultant" in contract_text:
        return ScopeResult(
            value=NationalInternational.INTERNATIONAL,
            confidence=0.90,
            evidence={"matched": "source contract international consultant signal"},
        )
    if "national consultant" in contract_text:
        return ScopeResult(
            value=NationalInternational.NATIONAL,
            confidence=0.90,
            evidence={"matched": "source contract national consultant signal"},
        )
    text = " ".join(
        value or ""
        for value in (
            features.title,
            features.employment_type,
            features.contract_raw,
            features.description,
        )
    ).casefold()
    if "international consultant" in text or "jpo" in text:
        return ScopeResult(
            value=NationalInternational.INTERNATIONAL,
            confidence=0.85,
            evidence={"matched": "international title/description signal"},
        )
    if "nationals only" in text or "national consultant" in text:
        return ScopeResult(
            value=NationalInternational.NATIONAL,
            confidence=0.85,
            evidence={"matched": "national title/description signal"},
        )
    if contract.category.value.startswith("internship") and "home" in text and "country" not in text:
        return ScopeResult(
            value=NationalInternational.GLOBAL_REMOTE,
            confidence=0.70,
            evidence={"matched": "home-based internship"},
        )
    return ScopeResult()
