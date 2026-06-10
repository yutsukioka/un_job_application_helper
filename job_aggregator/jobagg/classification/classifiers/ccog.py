"""CCOG family classification."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from jobagg.classification.models import CCOGResult, ContractResult, FeatureBundle, GradeResult, UNVResult
from jobagg.classification.rules import load_rule_file


@dataclass(frozen=True, slots=True)
class CCOGEntry:
    code: str
    title: str
    family_code: str
    family_label: str
    level_signal: str


def classify_ccog(
    features: FeatureBundle,
    grade: GradeResult,
    contract: ContractResult,
    unv: UNVResult | None,
) -> CCOGResult:
    part = infer_ccog_part(grade, features, unv)
    candidates: list[CCOGResult] = []
    source_candidate = _source_mapping_candidate(features, part)
    if source_candidate:
        candidates.append(source_candidate)
    candidates.extend(_alias_candidates(features, part))
    if not candidates:
        return CCOGResult(
            part=part,
            method="unknown",
            evidence={"part_inference": part, "contract_category": contract.category.value},
        )
    candidates.sort(key=lambda item: item.confidence, reverse=True)
    best = _hydrate(candidates[0])
    best.part = best.part or part
    return best


def infer_ccog_part(
    grade: GradeResult,
    features: FeatureBundle,
    unv: UNVResult | None,
) -> str | None:
    if grade.family in {"P", "D", "NO", "FS", "IICA", "ICS"}:
        return "1"
    if grade.family in {"G", "GS", "SC", "LICA"}:
        return "2"
    if unv and unv.category.value in {"un_volunteer_specialist", "un_volunteer_expert"}:
        return "1"
    if unv and unv.category.value == "un_community_volunteer":
        return "2"
    title = (features.title or "").casefold()
    if any(signal in title for signal in ("officer", "specialist", "adviser", "advisor", "manager", "lead")):
        return "1"
    if any(signal in title for signal in ("assistant", "associate", "clerk", "driver", "chauffeur")):
        return "2"
    return None


def ccog_tree() -> list[dict[str, str | None]]:
    return [
        {
            "code": entry.code,
            "label": entry.title,
            "family_code": entry.family_code,
            "family_label": entry.family_label,
            "part": "2" if entry.code.startswith("2.") else "1",
        }
        for entry in _ccog_entries().values()
    ]


def normalize_ccog_code(code: str) -> str:
    """Normalize known CCOG OCR/code-format artifacts without changing semantics."""

    compact = re.sub(r"\s+", "", str(code or "").strip())
    if compact.startswith(("I.", "L.")):
        compact = "1." + compact[2:]
    compact = compact.rstrip(".")
    if re.fullmatch(r"[12]\.[A-Z0-9]\.\d{2}[a-z]", compact, flags=re.I):
        compact = compact[:-1] + "." + compact[-1]
    return compact


def collapse_ccog_to_medium(code: str) -> str | None:
    """Return the medium-level CCOG code for a medium or small code."""

    normalized = normalize_ccog_code(code)
    parts = normalized.split(".")
    if len(parts) < 3:
        return None
    return ".".join(parts[:3])


def get_ccog_family(code: str) -> dict[str, str | None] | None:
    """Return the CCOG family metadata for ``code``."""

    normalized = normalize_ccog_code(code)
    parts = normalized.split(".")
    if len(parts) < 2:
        return None
    family_code = ".".join(parts[:2])
    for entry in _ccog_entries().values():
        if entry.family_code == family_code:
            return {"code": family_code, "label": entry.family_label}
    entry = _ccog_entries().get(family_code)
    if entry:
        return {"code": family_code, "label": entry.title}
    return {"code": family_code, "label": None}


def get_ccog_medium(code: str) -> dict[str, str | None] | None:
    """Return medium-level CCOG metadata for a medium or small code."""

    medium_code = collapse_ccog_to_medium(code)
    if medium_code is None:
        return None
    entry = _ccog_entries().get(medium_code)
    return {"code": medium_code, "label": entry.title if entry else None}


def _source_mapping_candidate(features: FeatureBundle, part: str | None) -> CCOGResult | None:
    mappings = load_rule_file("ccog_source_mappings.yaml")
    if features.job_family_code:
        code = features.job_family_code.upper()
        mapping = mappings.get("inspira_job_family", {}).get(code)
        if mapping:
            key = "part_2_default" if part == "2" else "part_1_default"
            selected = mapping.get(key) or mapping.get("part_1_default") or mapping.get("part_2_default")
            if selected:
                return CCOGResult(
                    code=selected.get("code"),
                    label=selected.get("label"),
                    part=part,
                    confidence=0.86,
                    method="source_job_family",
                    evidence={"job_family_code": features.job_family_code},
                )
    for expertise in features.unv_expertise_areas:
        mapping = mappings.get("unv_expertise_area", {}).get(expertise)
        if not mapping:
            continue
        code = mapping.get("code")
        label = mapping.get("label")
        if part == "2":
            code = mapping.get("part_2_code") or code
            label = mapping.get("part_2_label") or label
        elif part == "1":
            code = mapping.get("part_1_code") or code
            label = mapping.get("part_1_label") or label
        if code:
            return CCOGResult(
                code=code,
                label=label,
                part=part,
                confidence=0.82,
                method="unv_expertise_area",
                evidence={"expertise_area": expertise},
            )
    return None


def _alias_candidates(features: FeatureBundle, part: str | None) -> list[CCOGResult]:
    rules = load_rule_file("ccog_aliases.yaml").get("rules", [])
    title = features.title or ""
    description = features.description or ""
    candidates = []
    for rule in rules:
        required_part = str(rule.get("required_part") or "")
        if part and required_part and required_part != part:
            continue
        title_score, title_matches = _pattern_score(title, rule.get("title_patterns", []))
        keyword_score, keyword_matches = _keyword_score(description, rule.get("keywords", []))
        part_score = 1.0 if not required_part or not part or required_part == part else 0.0
        score = 0.55 * title_score + 0.30 * keyword_score + 0.15 * part_score
        if score <= 0:
            continue
        candidates.append(
            CCOGResult(
                code=rule.get("code"),
                label=rule.get("label"),
                part=required_part or part,
                confidence=min(0.99, score),
                method="title_keyword_rules",
                evidence={
                    "title_matches": title_matches,
                    "keyword_matches": keyword_matches,
                    "required_part": required_part,
                },
            )
        )
    return candidates


def _pattern_score(text: str, patterns: list[str]) -> tuple[float, list[str]]:
    matches = []
    for pattern in patterns or []:
        if re.search(pattern, text, flags=re.IGNORECASE):
            matches.append(pattern)
    return (1.0 if matches else 0.0), matches


def _keyword_score(text: str, keywords: list[str]) -> tuple[float, list[str]]:
    if not text or not keywords:
        return 0.0, []
    normalized = text.casefold()
    matches = [keyword for keyword in keywords if _keyword_matches(normalized, str(keyword))]
    return min(1.0, len(matches) / 2), matches


def _keyword_matches(text: str, keyword: str) -> bool:
    normalized = keyword.casefold().strip()
    if not normalized:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])", text) is not None


def _hydrate(result: CCOGResult) -> CCOGResult:
    entries = _ccog_entries()
    entry = entries.get(result.code or "")
    if not entry:
        result.family_code = _parent_family_code(result.code)
        result.family_label = result.label
        return result
    result.label = result.label or entry.title
    result.family_code = entry.family_code or _parent_family_code(entry.code)
    result.family_label = entry.family_label
    return result


@lru_cache(maxsize=1)
def _ccog_entries() -> dict[str, CCOGEntry]:
    data = load_rule_file("ccog_reference.yaml")
    entries: dict[str, CCOGEntry] = _markdown_ccog_entries()
    for item in data.get("entries", []):
        code = normalize_ccog_code(str(item.get("code") or ""))
        if not code:
            continue
        entries[code] = CCOGEntry(
            code=code,
            title=str(item.get("label") or ""),
            family_code=str(item.get("family_code") or _parent_family_code(code) or ""),
            family_label=str(item.get("family_label") or item.get("label") or ""),
            level_signal=str(item.get("level_signal") or ""),
        )
    return entries


def _markdown_ccog_entries() -> dict[str, CCOGEntry]:
    path = _full_markdown_path()
    if path is None:
        return {}
    entries: dict[str, CCOGEntry] = {}
    current_family_code = ""
    current_family_label = ""
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        family_match = re.match(r"^## Family:\s+([0-9A-Z]\.[A-Z0-9])\s+—\s+(.+?)\s*$", line)
        if family_match:
            current_family_code = normalize_ccog_code(family_match.group(1))
            current_family_label = family_match.group(2).strip()
            continue
        entry_match = re.match(r"^###\s+([0-9IL]\.[A-Z0-9](?:\.[0-9]{1,2})?(?:\.[a-z])?)\s+—\s+(.+?)\s*$", line)
        if not entry_match:
            continue
        code = normalize_ccog_code(entry_match.group(1))
        title = entry_match.group(2).strip()
        family_code = current_family_code or ".".join(code.split(".")[:2])
        family_label = current_family_label
        level_signal = ""
        for detail in lines[index + 1:index + 7]:
            if detail.startswith("**Family:**"):
                detail_match = re.match(r"^\*\*Family:\*\*\s+([0-9A-Z]\.[A-Z0-9])\s+—\s*(.*?)\s*$", detail)
                if detail_match:
                    family_code = normalize_ccog_code(detail_match.group(1))
                    family_label = detail_match.group(2).strip() or family_label
            elif detail.startswith("**Level signal:**"):
                level_signal = detail.split(":", 1)[1].strip()
        entries[code] = CCOGEntry(
            code=code,
            title=title,
            family_code=family_code,
            family_label=family_label,
            level_signal=level_signal,
        )
    return entries


def _full_markdown_path() -> Path | None:
    candidates = [
        Path(__file__).resolve().parents[4]
        / "skills"
        / "apex-ccog-resolver"
        / "resource"
        / "ccog_reference_full.md",
        Path.cwd() / "output" / "ccog_reference_full.md",
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def _parent_family_code(code: str | None) -> str | None:
    if not code:
        return None
    parts = code.split(".")
    if len(parts) >= 4:
        return ".".join(parts[:3])
    return code


def _normalize_code(code: str) -> str:
    return normalize_ccog_code(code)
