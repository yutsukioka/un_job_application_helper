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
    if features.source_id == "fao_taleo" and str(features.grade_raw or "").upper() in {"NPP", "PSA"}:
        return GradeResult(
            evidence={
                "source_grade": features.grade_raw,
                "reason": "FAO NPP/PSA is a non-staff contract form, not a grade",
            }
        )
    if features.source_id == "fao_taleo":
        fao_national_grade = _match_fao_national_grade(features.grade_raw)
        if fao_national_grade.code:
            return fao_national_grade
    if features.source_id == "imf_workday":
        imf_grade = _match_imf_grade(features.grade_raw)
        if imf_grade.code:
            return imf_grade
    source_grade = _match_source_native_grade(features.grade_raw, features.source_id)
    if source_grade.code:
        return source_grade
    candidates = [
        ("source_grade", features.grade_raw, 0.96),
        ("employment_type", features.employment_type, 0.74),
        ("title", features.title, 0.88),
        ("description", features.description, 0.58),
    ]
    for field_name, text, confidence in candidates:
        result = _match_grade(
            text,
            field_name=field_name,
            confidence=confidence,
            source_id=features.source_id,
        )
        if result.code:
            return result
    experience_years = _max_required_years_experience(features.description)
    if experience_years is not None:
        return GradeResult(
            system="EXPERIENCE_PROXY",
            family="EXPERIENCE",
            code=f"EXP{experience_years}",
            level=str(experience_years),
            staff_category="functional_proxy",
            min_years_experience=experience_years,
            confidence=0.42,
            evidence={
                "field": "description",
                "matched": f"{experience_years} years experience",
                "rule_family": "EXPERIENCE_PROXY",
                "precision": "functional fallback; no formal grade exposed",
            },
        )
    return GradeResult(evidence={"checked_fields": [name for name, _, _ in candidates]})


def _match_grade(
    text: str | None,
    *,
    field_name: str,
    confidence: float,
    source_id: str | None = None,
) -> GradeResult:
    if not text:
        return GradeResult()
    rules = load_rule_file("grade_rules.yaml").get("grade_patterns", [])
    for rule in rules:
        if not _rule_allowed_for_source(rule, source_id):
            continue
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


def _rule_allowed_for_source(rule: dict[str, Any], source_id: str | None) -> bool:
    system = str(rule.get("system") or "").upper()
    if system == "ADB":
        return source_id == "adb_taleo"
    if system == "CERN":
        return source_id in {"cern_custom_html", "cern_smartrecruiters"}
    return True


def _match_fao_national_grade(value: str | None) -> GradeResult:
    if not value:
        return GradeResult()
    match = re.search(r"\bN[-\s]?(?P<level>[1-4])\b", str(value), flags=re.I)
    if not match:
        return GradeResult()
    level = _NO_NUMBER_TO_LETTER[match.group("level")]
    code = f"NO{level}"
    return GradeResult(
        system="FAO",
        family="NO",
        code=code,
        level=level,
        staff_category="national_professional_officer",
        min_years_experience=GRADE_EXPERIENCE.get(code),
        confidence=0.96,
        evidence={"field": "source_grade", "matched": match.group(0), "rule_family": "FAO_N"},
    )


def _match_imf_grade(value: str | None) -> GradeResult:
    if not value:
        return GradeResult()
    text = str(value)
    match = re.search(r"\bA(?P<level>\d{2})\b", text, flags=re.I)
    if match:
        code = f"A{match.group('level')}"
        return GradeResult(
            system="IMF",
            family="A",
            code=code,
            level=match.group("level"),
            staff_category="imf_staff",
            confidence=0.96,
            evidence={"field": "source_grade", "matched": match.group(0), "rule_family": "IMF_A"},
        )
    if re.fullmatch(r"\s*B\s*", text, flags=re.I):
        return GradeResult(
            system="IMF",
            family="B",
            code="B",
            staff_category="imf_staff",
            confidence=0.82,
            evidence={
                "field": "source_grade",
                "matched": "B",
                "rule_family": "IMF_B",
                "precision": "broad grade family; exact B01-B05 level not exposed",
            },
        )
    match = re.search(r"\bB(?P<level>0[1-5])\b", text, flags=re.I)
    if match:
        code = f"B{match.group('level')}"
        return GradeResult(
            system="IMF",
            family="B",
            code=code,
            level=match.group("level"),
            staff_category="imf_staff",
            confidence=0.96,
            evidence={"field": "source_grade", "matched": match.group(0), "rule_family": "IMF_B"},
        )
    return GradeResult()


def _match_source_native_grade(value: str | None, source_id: str | None) -> GradeResult:
    if not value:
        return GradeResult()
    text = str(value)
    if source_id in {"undp_oracle_hcm", "unfpa_oracle_hcm", "unwomen_oracle_hcm"}:
        match = re.search(r"\b(?P<family>IPSA|NPSA)[-\s]?(?P<level>\d{1,2})\b", text, flags=re.I)
        if match:
            family = match.group("family").upper()
            level = str(int(match.group("level")))
            return GradeResult(
                system="UNDP_NONSTAFF",
                family=family,
                code=f"{family}-{level}",
                level=level,
                staff_category="affiliate_personnel",
                confidence=0.96,
                evidence={"field": "source_grade", "matched": match.group(0), "rule_family": family},
            )
        if source_id == "undp_oracle_hcm":
            match = re.search(r"\bNB[-\s]?(?P<level>[1-4])\b", text, flags=re.I)
            if match:
                level = match.group("level")
                return GradeResult(
                    system="UNDP_NONSTAFF",
                    family="NB",
                    code=f"NB{level}",
                    level=level,
                    staff_category="affiliate_personnel",
                    confidence=0.94,
                    evidence={"field": "source_grade", "matched": match.group(0), "rule_family": "NB"},
                )
        if source_id == "unfpa_oracle_hcm":
            match = re.search(r"\bSB[-\s]?(?P<level>[1-5])\b", text, flags=re.I)
            if match:
                level = match.group("level")
                return GradeResult(
                    system="UNFPA_SERVICE_CONTRACT",
                    family="SB",
                    code=f"SB{level}",
                    level=level,
                    staff_category="service_contract_consultant",
                    confidence=0.94,
                    evidence={"field": "source_grade", "matched": match.group(0), "rule_family": "SB"},
                )
    if source_id == "unesco_successfactors":
        match = re.search(r"\bLevel\s*(?P<level>[1-4])\s*[-–—]\s*(?P<label>[A-Za-z]+)\b", text, flags=re.I)
        if match:
            level = match.group("level")
            label = match.group("label").capitalize()
            return GradeResult(
                system="UNESCO_CONSULTANT",
                family="UNESCO_CONSULTANT",
                code=f"Level {level} - {label}",
                level=level,
                staff_category="consultant",
                confidence=0.90,
                evidence={
                    "field": "source_grade",
                    "matched": match.group(0),
                    "rule_family": "UNESCO_CONSULTANT_LEVEL",
                },
            )
    if source_id == "globalfund_workday":
        match = re.search(r"\b(?:GL[-\s]?)?(?P<level>[A-G])\b", text, flags=re.I)
        if match:
            level = match.group("level").upper()
            return GradeResult(
                system="GLOBAL_FUND",
                family="GL",
                code=f"GL {level}",
                level=level,
                staff_category="globalfund_staff",
                confidence=0.90,
                evidence={"field": "source_grade", "matched": match.group(0), "rule_family": "GL"},
            )
    if source_id == "un_inspira":
        match = re.search(r"\bUNRWA\s+Grade\s+(?P<level>\d{1,2})\b", text, flags=re.I)
        if match:
            level = str(int(match.group("level")))
            return GradeResult(
                system="UNRWA",
                family="UNRWA",
                code=f"UNRWA Grade {level}",
                level=level,
                staff_category="unrwa_local_grade",
                confidence=0.88,
                evidence={"field": "source_grade", "matched": match.group(0), "rule_family": "UNRWA"},
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


_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
}


def _max_required_years_experience(text: str | None) -> int | None:
    if not text:
        return None
    normalized = " ".join(str(text).split())
    matches: list[int] = []
    number = r"\d{1,2}|" + "|".join(_NUMBER_WORDS)
    year_word = r"years?|años|ans"
    experience_word = r"experience|experiencia|exp[eé]rience"
    patterns = (
        rf"(?P<value>{number})\s*[-–—]\s*(?P<end>\d{{1,2}})\s+{year_word}\b[^.；;]{{0,90}}\b{experience_word}\b",
        rf"\b{experience_word}\b[^.；;]{{0,90}}(?P<value>{number})\s*[-–—]\s*(?P<end>\d{{1,2}})\s+{year_word}\b",
        rf"(?<!maximum of )(?<!maximum )(?P<value>{number})\s+{year_word}'?\s+(?:of\s+)?(?:relevant\s+|professional\s+|work\s+)?{experience_word}\b",
        rf"\b{experience_word}\b[^.；;]{{0,90}}(?<!maximum of )(?<!maximum )(?P<value>{number})\s+{year_word}\b",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, normalized, flags=re.I):
            context = normalized[max(0, match.start() - 40): match.end() + 40].casefold()
            if "maximum" in context and "minimum" not in context and "at least" not in context:
                continue
            groups = match.groupdict()
            value = _experience_number(groups.get("end") or groups.get("value"))
            if value is not None:
                matches.append(value)
    return max(matches) if matches else None


def _experience_number(value: str | None) -> int | None:
    if not value:
        return None
    if value.isdigit():
        return int(value)
    return _NUMBER_WORDS.get(value.casefold())
