"""Grade classification."""

from __future__ import annotations

import re
from typing import Any

from jobagg.classification.models import FeatureBundle, GradeResult
from jobagg.classification.rules import load_rule_file


GRADE_EXPERIENCE = {
    "P1": 0,
    "P2": 2,
    "P3": 5,
    "P4": 7,
    "P5": 10,
    "D1": 15,
    "D2": 16,
    "G1": 0,
    "G2": 2,
    "G3": 3,
    "G4": 4,
    "G5": 5,
    "G6": 6,
    "G7": 7,
    "NOA": 0,
    "NOB": 2,
    "NOC": 5,
    "NOD": 7,
    "FS4": 6,
    "FS5": 8,
    "FS6": 10,
    "FS7": 12,
}

_NO_NUMBER_TO_LETTER = {"1": "A", "2": "B", "3": "C", "4": "D"}


def classify_grade(features: FeatureBundle) -> GradeResult:
    candidates = [
        ("source_grade", features.grade_raw, 0.96),
        ("employment_type", features.employment_type, 0.74),
        ("title", features.title, 0.88),
        ("description", features.description, 0.58),
    ]
    for field_name, text, confidence in candidates:
        result = _match_grade(text, field_name=field_name, confidence=confidence)
        if result.code:
            return result
    return GradeResult(evidence={"checked_fields": [name for name, _, _ in candidates]})


def _match_grade(text: str | None, *, field_name: str, confidence: float) -> GradeResult:
    if not text:
        return GradeResult()
    rules = load_rule_file("grade_rules.yaml").get("grade_patterns", [])
    for rule in rules:
        match = re.search(str(rule["regex"]), text, flags=re.IGNORECASE)
        if not match:
            continue
        return _result_from_match(
            rule,
            match.group(0),
            field_name=field_name,
            confidence=confidence,
        )
    return GradeResult()


def _result_from_match(
    rule: dict[str, Any],
    matched: str,
    *,
    field_name: str,
    confidence: float,
) -> GradeResult:
    raw_family = str(rule["family"]).upper()
    normalized = _compact(matched)
    family = _normalized_family(raw_family)
    level = _level_from_text(raw_family, normalized)
    code = _normalized_code(family, level)
    return GradeResult(
        system=str(rule["system"]),
        family=family,
        code=code,
        level=level,
        staff_category=str(rule.get("staff_category") or ""),
        min_years_experience=GRADE_EXPERIENCE.get(code or ""),
        confidence=confidence,
        evidence={"field": field_name, "matched": matched, "rule_family": raw_family},
    )


def _compact(value: str) -> str:
    return re.sub(r"[\s_-]+", "", value).upper()


def _normalized_family(raw_family: str) -> str:
    if raw_family == "GS":
        return "G"
    return raw_family


def _level_from_text(family: str, compacted: str) -> str | None:
    if family == "NO":
        match = re.search(r"NO([ABCD1234])", compacted)
        if not match:
            return None
        level = match.group(1)
        return _NO_NUMBER_TO_LETTER.get(level, level)
    if family == "INTERN":
        return "1"
    if family == "CONSULTANT":
        return None
    match = re.search(r"(\d{1,2}|[ABCD])$", compacted)
    if match:
        return match.group(1)
    return None


def _normalized_code(family: str, level: str | None) -> str | None:
    if family == "CONSULTANT":
        return "CON"
    if family == "INTERN":
        return "I1"
    if family == "CERN" and level:
        return f"Grade {level}"
    if not level:
        return family
    if family == "NO":
        return f"NO{level}"
    return f"{family}{level}"
