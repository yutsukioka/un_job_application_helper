"""Organization-specific grade standardization from bundled mapping tables."""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from jobagg.classification.models import ContractCategory
from jobagg.classification.rules import rules_path


GRADE_MAPPING_VERSION = "grade_mapping_table_v1"
GRADE_MAPPING_JSON = "grade_mapping_table.json"
GRADE_MAPPING_CSV = "grade_mapping_table.csv"
ADB_GRADE_JSON = "adb_grade.json"

CSV_TO_DB_COLUMNS = {
    "Organization": "organization",
    "Raw Grade Code": "raw_grade_code",
    "Normalized Grade Family": "normalized_grade_family",
    "Normalized Seniority Tier": "normalized_seniority_tier",
    "International / National / Local": "international_national_local",
    "Staff / Consultant / Contractor / Other": "staff_consultant_contractor_other",
    "Approximate UN Equivalent": "approximate_un_equivalent",
    "Approximate Experience Range": "approximate_experience_range",
    "Typical Role Scope": "typical_role_scope",
    "Supervisory Expectations": "supervisory_expectations",
    "Notes / Caveats": "notes_caveats",
    "Confidence Level": "confidence_level",
    "Evidence Type": "evidence_type",
}

SOURCE_ORGANIZATION_OVERRIDES = {
    "adb_taleo": "Asian Development Bank",
    "au_successfactors": "African Union",
    "cern_custom_html": "CERN",
    "cern_smartrecruiters": "CERN",
    "ctbto_successfactors_legacy": "Comprehensive Nuclear-Test-Ban Treaty Organization",
    "fao_taleo": "Food and Agriculture Organization",
    "globalfund_workday": "The Global Fund",
    "iaea_taleo": "International Atomic Energy Agency",
    "icao_oracle_hcm": "International Civil Aviation Organization",
    "icc_successfactors_legacy": "International Criminal Court",
    "icddrb_custom_html": "icddr,b",
    "icrc_successfactors": "International Committee of the Red Cross",
    "ideglobal_workable": "iDE Global",
    "ifad_peoplesoft": "International Fund for Agricultural Development",
    "ilo_successfactors": "International Labour Organization",
    "imf_workday": "International Monetary Fund",
    "imo_api": "International Maritime Organization",
    "iom_oracle_hcm": "International Organization for Migration",
    "ipu_static_html": "Inter-Parliamentary Union",
    "isa_inspira_split": "International Seabed Authority",
    "itc_inspira_split": "International Trade Centre",
    "itcilo_custom_html": "International Training Centre of the ILO",
    "itlos_static_html": "International Tribunal for the Law of the Sea",
    "itu_successfactors": "International Telecommunication Union",
    "nato_taleo": "North Atlantic Treaty Organization",
    "opcw_talentsoft_candidatespace": "Organisation for the Prohibition of Chemical Weapons",
    "osce_custom_html": "OSCE",
    "paho_workday": "Pan American Health Organization",
    "tbi_workday": "Tony Blair Institute",
    "un_inspira": "United Nations Careers / UN Secretariat",
    "unaids_sharepoint": "UNAIDS",
    "undp_oracle_hcm": "UNDP",
    "unesco_successfactors": "UNESCO",
    "unfpa_oracle_hcm": "UNFPA",
    "unhcr_workday": "UNHCR",
    "unicef_pageup": "UNICEF",
    "unido_successfactors": "UNIDO",
    "unops_avature": "UNOPS",
    "unssc_drupal_custom": "United Nations System Staff College",
    "untourism_custom": "UN Tourism",
    "unu_recruitee": "United Nations University",
    "unv_uvp": "United Nations Volunteers",
    "unwomen_oracle_hcm": "UN Women",
    "wef_workday": "World Economic Forum",
    "wfp_workday": "World Food Programme",
    "who_taleo": "World Health Organization",
    "wipo_taleo": "World Intellectual Property Organization",
    "wmo_oracle_hcm": "World Meteorological Organization",
    "worldbank_csod": "World Bank Group",
    "wto_workday": "World Trade Organization",
}

COMMON_SYSTEM_ORGANIZATION = "United Nations Careers / UN Secretariat"
ADB_MAPPING_ORGANIZATION = "Asian Development Bank"
INTERNSHIP_CONTRACT_CATEGORIES = {
    ContractCategory.INTERNSHIP_PAID.value,
    ContractCategory.INTERNSHIP_UNPAID.value,
    ContractCategory.INTERNSHIP_UNKNOWN.value,
}
GENERIC_NONSTAFF_GRADE_FAMILIES = {"CONSULTANT", "INTERN"}
LEVEL_BEARING_NONSTAFF_GRADE_FAMILIES = {"IICA", "LICA", "ICS", "SC", "SSA"}


@dataclass(frozen=True, slots=True)
class GradeMappingEntry:
    mapping_version: str
    organization: str
    raw_grade_code: str
    normalized_raw_grade_code: str
    normalized_grade_family: str | None
    normalized_seniority_tier: str | None
    international_national_local: str | None
    staff_consultant_contractor_other: str | None
    approximate_un_equivalent: str | None
    approximate_experience_range: str | None
    typical_role_scope: str | None
    supervisory_expectations: str | None
    notes_caveats: str | None
    confidence_level: str | None
    evidence_type: str | None

    def to_row(self) -> dict[str, Any]:
        return {
            "mapping_version": self.mapping_version,
            "organization": self.organization,
            "raw_grade_code": self.raw_grade_code,
            "normalized_raw_grade_code": self.normalized_raw_grade_code,
            "normalized_grade_family": self.normalized_grade_family,
            "normalized_seniority_tier": self.normalized_seniority_tier,
            "international_national_local": self.international_national_local,
            "staff_consultant_contractor_other": self.staff_consultant_contractor_other,
            "approximate_un_equivalent": self.approximate_un_equivalent,
            "approximate_experience_range": self.approximate_experience_range,
            "typical_role_scope": self.typical_role_scope,
            "supervisory_expectations": self.supervisory_expectations,
            "notes_caveats": self.notes_caveats,
            "confidence_level": self.confidence_level,
            "evidence_type": self.evidence_type,
        }


@dataclass(frozen=True, slots=True)
class GradeStandardization:
    mapping_organization: str
    mapping_raw_grade_code: str
    normalized_grade_family: str | None = None
    normalized_seniority_tier: str | None = None
    international_national_local: str | None = None
    staff_consultant_contractor_other: str | None = None
    approximate_un_equivalent: str | None = None
    approximate_experience_range: str | None = None
    typical_role_scope: str | None = None
    supervisory_expectations: str | None = None
    confidence_level: str | None = None
    evidence_type: str | None = None
    notes_caveats: str | None = None
    evidence: dict[str, Any] | None = None


def source_mapping_organization(source_id: str) -> str | None:
    return SOURCE_ORGANIZATION_OVERRIDES.get(source_id)


def grade_mapping_rows() -> list[dict[str, Any]]:
    return [entry.to_row() for entry in load_grade_mappings()]


@lru_cache(maxsize=1)
def load_grade_mappings() -> tuple[GradeMappingEntry, ...]:
    entries = _load_bundled_grade_mappings()
    adb_entries = _load_adb_grade_mappings_from_rules()
    if adb_entries:
        entries = [
            entry
            for entry in entries
            if entry.organization != ADB_MAPPING_ORGANIZATION
        ]
        entries.extend(adb_entries)
    return tuple(entries)


def _load_bundled_grade_mappings() -> list[GradeMappingEntry]:
    json_path = rules_path(GRADE_MAPPING_JSON)
    if json_path.is_file():
        entries = _load_grade_mappings_from_json_path(json_path)
        if entries:
            return entries
    csv_path = rules_path(GRADE_MAPPING_CSV)
    if csv_path.is_file():
        return _load_grade_mappings_from_csv_path(csv_path)
    return []


def _load_grade_mappings_from_json_path(path: Path) -> list[GradeMappingEntry]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return _load_grade_mappings_from_records(
        row for row in payload if isinstance(row, dict)
    )


def _load_grade_mappings_from_csv_path(path: Path) -> list[GradeMappingEntry]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return _load_grade_mappings_from_records(csv.DictReader(handle))


def _load_grade_mappings_from_path(path: Path) -> list[GradeMappingEntry]:
    return _load_grade_mappings_from_csv_path(path)


def _load_grade_mappings_from_records(
    rows: Any,
) -> list[GradeMappingEntry]:
    entries: list[GradeMappingEntry] = []
    for row in rows:
        mapped = {
            db_column: _clean(row.get(csv_column))
            for csv_column, db_column in CSV_TO_DB_COLUMNS.items()
        }
        organization = mapped["organization"]
        raw_grade_code = mapped["raw_grade_code"]
        if not organization or not raw_grade_code:
            continue
        entries.append(
            GradeMappingEntry(
                mapping_version=GRADE_MAPPING_VERSION,
                organization=organization,
                raw_grade_code=raw_grade_code,
                normalized_raw_grade_code=normalize_grade_key(raw_grade_code),
                normalized_grade_family=mapped["normalized_grade_family"],
                normalized_seniority_tier=mapped["normalized_seniority_tier"],
                international_national_local=mapped["international_national_local"],
                staff_consultant_contractor_other=mapped[
                    "staff_consultant_contractor_other"
                ],
                approximate_un_equivalent=mapped["approximate_un_equivalent"],
                approximate_experience_range=mapped["approximate_experience_range"],
                typical_role_scope=mapped["typical_role_scope"],
                supervisory_expectations=mapped["supervisory_expectations"],
                notes_caveats=mapped["notes_caveats"],
                confidence_level=mapped["confidence_level"],
                evidence_type=mapped["evidence_type"],
            )
        )
    return entries


def _load_adb_grade_mappings_from_rules() -> list[GradeMappingEntry]:
    path = rules_path(ADB_GRADE_JSON)
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []

    entries: list[GradeMappingEntry] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        raw_grade_code = _clean(item.get("grade_code"))
        family = _clean(item.get("grade_family"))
        if not raw_grade_code or not family:
            continue
        un_mapping = item.get("un_mapping") if isinstance(item.get("un_mapping"), dict) else {}
        label_range = un_mapping.get("label_range") if isinstance(un_mapping, dict) else None
        secondary = item.get("secondary_mapping") if isinstance(item.get("secondary_mapping"), dict) else None
        entries.append(
            GradeMappingEntry(
                mapping_version=GRADE_MAPPING_VERSION,
                organization=ADB_MAPPING_ORGANIZATION,
                raw_grade_code=raw_grade_code,
                normalized_raw_grade_code=normalize_grade_key(raw_grade_code),
                normalized_grade_family=_adb_normalized_family(family),
                normalized_seniority_tier=_adb_standard_tier(item.get("tier")),
                international_national_local=_adb_scope(family),
                staff_consultant_contractor_other="Staff",
                approximate_un_equivalent=_un_equivalent(label_range),
                approximate_experience_range=None,
                typical_role_scope=_adb_role_scope(family),
                supervisory_expectations="Varies by vacancy",
                notes_caveats=_adb_notes(item.get("tier"), secondary),
                confidence_level=_clean(item.get("confidence")),
                evidence_type="rules/adb_grade.json",
            )
        )
    return entries


def _adb_normalized_family(family: str) -> str:
    labels = {
        "AS": "ADB Administrative Staff",
        "NS": "ADB National Staff",
        "TL": "ADB Technical Local Staff",
        "IS": "ADB International Staff",
        "TI": "ADB Technical International Staff",
    }
    return labels.get(family.upper(), f"ADB {family.upper()} Staff")


def _adb_scope(family: str) -> str:
    if family.upper() in {"IS", "TI"}:
        return "International"
    if family.upper() in {"NS", "TL"}:
        return "National / Local"
    return "Local / headquarters administrative"


def _adb_role_scope(family: str) -> str:
    labels = {
        "AS": "Administrative/support staff",
        "NS": "National professional or specialist staff",
        "TL": "Technical local professional or specialist staff",
        "IS": "International professional staff",
        "TI": "Technical international professional staff",
    }
    return labels.get(family.upper(), "ADB staff role")


def _adb_standard_tier(value: object | None) -> str | None:
    tier = _clean(value)
    if not tier:
        return None
    upper_tier = tier.split("-")[-1].strip().upper()
    return {
        "T1": "T1_ENTRY_SUPPORT",
        "T2": "T2_JUNIOR_PROFESSIONAL",
        "T3": "T3_MID_PROFESSIONAL",
        "T4": "T4_SENIOR_PROFESSIONAL",
        "T5": "T5_PRINCIPAL_MANAGER",
        "T6": "T6_DIRECTOR",
        "T7": "T7_EXECUTIVE",
    }.get(upper_tier, tier)


def _un_equivalent(label_range: object | None) -> str | None:
    if not isinstance(label_range, list):
        return None
    labels = [_clean(label) for label in label_range]
    labels = [label for label in labels if label]
    if not labels:
        return None
    return "~" + "/".join(labels)


def _adb_notes(tier: object | None, secondary: dict[str, Any] | None) -> str:
    notes = [
        "ADB mapping loaded from jobagg/classification/rules/adb_grade.json.",
        "ADB is outside the UN Common System; UN equivalence is functional only.",
    ]
    raw_tier = _clean(tier)
    if raw_tier:
        notes.append(f"Source tier: {raw_tier}.")
    if secondary:
        secondary_labels = _un_equivalent(secondary.get("label_range"))
        if secondary_labels:
            notes.append(f"Secondary mapping: {secondary_labels}.")
    return " ".join(notes)


def standardize_grade(
    features: Any,
    grade: Any,
    contract: Any | None = None,
) -> GradeStandardization | None:
    if _should_skip_nonstaff_standardization(features, grade, contract):
        return None

    entries = _candidate_entries(str(features.source_id))
    if not entries:
        return None

    signals = _grade_signals(features, grade)
    direct = _match_direct(entries, signals)
    if direct is not None:
        entry, field_name, matched = direct
        return _standardization_from_entry(
            entry,
            evidence={"method": "direct_grade_signal", "field": field_name, "matched": matched},
        )

    unv_signal = _unv_signal(features)
    if unv_signal:
        for entry in entries:
            if normalize_grade_key(entry.raw_grade_code) == normalize_grade_key(unv_signal):
                return _standardization_from_entry(
                    entry,
                    evidence={"method": "unv_category_signal", "matched": unv_signal},
                )

    role_match = _match_role_level(entries, features)
    if role_match is not None:
        entry, field_name, matched = role_match
        return _standardization_from_entry(
            entry,
            evidence={"method": "role_title_signal", "field": field_name, "matched": matched},
        )
    return None


def _should_skip_nonstaff_standardization(
    features: Any,
    grade: Any,
    contract: Any | None,
) -> bool:
    category = _contract_category_value(contract)
    grade_family = str(getattr(grade, "family", "") or "").upper()

    if category in INTERNSHIP_CONTRACT_CATEGORIES:
        return True
    if grade_family in GENERIC_NONSTAFF_GRADE_FAMILIES:
        return True
    if category == ContractCategory.CONSULTANT.value:
        return not _has_level_bearing_nonstaff_grade(features, grade)
    return False


def _contract_category_value(contract: Any | None) -> str | None:
    category = getattr(contract, "category", None)
    if isinstance(category, ContractCategory):
        return category.value
    if category is None:
        return None
    return str(category)


def _has_level_bearing_nonstaff_grade(features: Any, grade: Any) -> bool:
    family = str(getattr(grade, "family", "") or "").upper()
    level = getattr(grade, "level", None)
    if family in LEVEL_BEARING_NONSTAFF_GRADE_FAMILIES and level:
        return True
    pattern = re.compile(r"\b(?:IICA|LICA|ICS|SC|SSA)[-\s]?\d{1,2}\b", re.IGNORECASE)
    for _, signal in _grade_signals(features, grade):
        if pattern.search(signal):
            return True
    return False


def _candidate_entries(source_id: str) -> list[GradeMappingEntry]:
    by_org = _entries_by_organization()
    organization = source_mapping_organization(source_id)
    entries: list[GradeMappingEntry] = []
    if organization:
        entries.extend(by_org.get(organization, []))
    if organization != COMMON_SYSTEM_ORGANIZATION:
        entries.extend(by_org.get(COMMON_SYSTEM_ORGANIZATION, []))
    return entries


@lru_cache(maxsize=1)
def _entries_by_organization() -> dict[str, list[GradeMappingEntry]]:
    grouped: dict[str, list[GradeMappingEntry]] = {}
    for entry in load_grade_mappings():
        grouped.setdefault(entry.organization, []).append(entry)
    return grouped


def _grade_signals(features: Any, grade: Any) -> list[tuple[str, str]]:
    signals = []
    for field_name, value in (
        ("grade_code", getattr(grade, "code", None)),
        ("grade_matched", getattr(grade, "evidence", {}).get("matched")),
        ("source_grade", getattr(features, "grade_raw", None)),
        ("source_seniority", getattr(features, "seniority_raw", None)),
        ("employment_type", getattr(features, "employment_type", None)),
        ("title", getattr(features, "title", None)),
    ):
        text = _clean(value)
        if text:
            signals.append((field_name, text))
    return signals


def _match_direct(
    entries: list[GradeMappingEntry],
    signals: list[tuple[str, str]],
) -> tuple[GradeMappingEntry, str, str] | None:
    for field_name, signal in signals:
        for entry in entries:
            if _entry_matches_signal(entry, signal):
                return entry, field_name, signal
    return None


def _entry_matches_signal(entry: GradeMappingEntry, signal: str) -> bool:
    compact_signal = normalize_grade_key(signal)
    if not compact_signal:
        return False
    if compact_signal == entry.normalized_raw_grade_code:
        return True
    pattern = _entry_regex(entry.raw_grade_code, role_level=False)
    return bool(pattern and pattern.search(signal))


def _match_role_level(
    entries: list[GradeMappingEntry],
    features: Any,
) -> tuple[GradeMappingEntry, str, str] | None:
    for field_name, text in (
        ("source_seniority", getattr(features, "seniority_raw", None)),
        ("department", getattr(features, "department", None)),
        ("title", getattr(features, "title", None)),
    ):
        if not text:
            continue
        for entry in entries:
            if not _is_role_level_code(entry.raw_grade_code):
                continue
            pattern = _entry_regex(entry.raw_grade_code, role_level=True)
            if pattern and pattern.search(str(text)):
                return entry, field_name, str(text)
    return None


def _unv_signal(features: Any) -> str | None:
    if getattr(features, "source_id", None) != "unv_uvp":
        return None
    volunteer_type = _clean(getattr(features, "unv_volunteer_type", None))
    category = _clean(getattr(features, "unv_category_label", None))
    if volunteer_type and category:
        return f"{volunteer_type} UNV {category}"
    return f"UNV {category}" if category else None


def _standardization_from_entry(
    entry: GradeMappingEntry,
    *,
    evidence: dict[str, Any],
) -> GradeStandardization:
    return GradeStandardization(
        mapping_organization=entry.organization,
        mapping_raw_grade_code=entry.raw_grade_code,
        normalized_grade_family=entry.normalized_grade_family,
        normalized_seniority_tier=entry.normalized_seniority_tier,
        international_national_local=entry.international_national_local,
        staff_consultant_contractor_other=entry.staff_consultant_contractor_other,
        approximate_un_equivalent=entry.approximate_un_equivalent,
        approximate_experience_range=entry.approximate_experience_range,
        typical_role_scope=entry.typical_role_scope,
        supervisory_expectations=entry.supervisory_expectations,
        confidence_level=entry.confidence_level,
        evidence_type=entry.evidence_type,
        notes_caveats=entry.notes_caveats,
        evidence={
            **evidence,
            "mapping_version": entry.mapping_version,
            "mapping_organization": entry.organization,
            "mapping_raw_grade_code": entry.raw_grade_code,
        },
    )


def normalize_grade_key(value: object | None) -> str:
    if value is None:
        return ""
    return re.sub(r"[^A-Z0-9]+", "", str(value).upper())


def _entry_regex(raw_grade_code: str, *, role_level: bool) -> re.Pattern[str] | None:
    range_pattern = _range_regex(raw_grade_code)
    if range_pattern is not None:
        return range_pattern
    alternatives = _raw_code_alternatives(raw_grade_code)
    if not alternatives:
        return None
    if not role_level and _is_role_level_code(raw_grade_code):
        return None
    escaped = [_flexible_token_regex(item) for item in alternatives if item.strip()]
    if not escaped:
        return None
    return re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(escaped) + r")(?![A-Za-z0-9])", re.I)


def _range_regex(raw_grade_code: str) -> re.Pattern[str] | None:
    match = re.search(
        r"(?P<prefix>[A-Za-z]+)?[-\s]?(?P<start>\d{1,2})\.\."
        r"(?P<end_prefix>[A-Za-z]+)?[-\s]?(?P<end>\d{1,2})",
        raw_grade_code,
    )
    if not match:
        match = re.search(
            r"(?P<prefix>[A-Za-z]+)[-\s]?(?P<start>\d{1,2})"
            r"\s*[-–—]\s*(?P<end_prefix>[A-Za-z]+)?[-\s]?(?P<end>\d{1,2})",
            raw_grade_code,
        )
    if not match:
        return None
    prefix = match.group("prefix") or match.group("end_prefix") or ""
    start = int(match.group("start"))
    end = int(match.group("end"))
    if end < start or end - start > 40:
        return None
    values = [str(value) for value in range(start, end + 1)]
    width = max(len(match.group("start")), len(match.group("end")))
    if width > 1:
        values.extend(str(value).zfill(width) for value in range(start, end + 1))
    prefix_pattern = _flexible_token_regex(prefix) if prefix else ""
    return re.compile(
        r"(?<![A-Za-z0-9])" + prefix_pattern + r"[-\s]?(?:" + "|".join(sorted(set(values))) + r")(?![A-Za-z0-9])",
        re.I,
    )


def _raw_code_alternatives(raw_grade_code: str) -> list[str]:
    text = raw_grade_code.strip()
    if "/" in text:
        first, *rest = [part.strip() for part in text.split("/") if part.strip()]
        if len(first.split()) > 1:
            prefix = " ".join(first.split()[:-1])
            values = [first, *(f"{prefix} {part}" for part in rest)]
            return values
        return [first, *rest]
    return [text]


def _flexible_token_regex(value: str) -> str:
    parts = re.findall(r"[A-Za-z]+|\d+|[IVX]+", value.upper())
    if not parts:
        return re.escape(value)
    return r"[-\s]*".join(re.escape(part) for part in parts)


def _is_role_level_code(raw_grade_code: str) -> bool:
    return any(word in raw_grade_code.casefold() for word in ("assistant", "associate", "manager", "head", "director", "lead", "coordinator", "officer", "specialist", "delegate"))


def _clean(value: object | None) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).strip().split())
    return text or None
