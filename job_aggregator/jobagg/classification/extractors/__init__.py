"""Source-specific feature extractors."""

from __future__ import annotations

import re
from typing import Any

from jobagg.classification.extractors.base import (
    SourceFeatureExtractor,
    base_features,
    code,
    first_dict,
    get_flex,
    label,
    raw_dict,
    text_join,
)
from jobagg.classification.models import FeatureBundle


class GenericExtractor(SourceFeatureExtractor):
    def extract(self, vacancy: dict[str, Any]) -> FeatureBundle:
        return base_features(vacancy)


class CERNCustomHTMLExtractor(SourceFeatureExtractor):
    def extract(self, vacancy: dict[str, Any]) -> FeatureBundle:
        raw = raw_dict(vacancy)
        features = base_features(
            vacancy,
            evidence={
                "raw_grade": raw.get("grade"),
                "detail_url": raw.get("detail_url") or raw.get("href"),
                "parser": raw.get("parser"),
            },
        )
        grade, source_field = _cern_grade_signal(raw, features.description)
        if grade:
            features.grade_raw = grade
            features.grade_source_field = source_field
        return features


class IOMOracleHCMExtractor(SourceFeatureExtractor):
    def extract(self, vacancy: dict[str, Any]) -> FeatureBundle:
        raw = raw_dict(vacancy)
        work_location = first_dict(raw.get("workLocation"))
        features = base_features(
            vacancy,
            evidence={
                "requisitionFlexFields": raw.get("requisitionFlexFields", []),
                "workLocation": raw.get("workLocation", []),
            },
        )
        features.description = vacancy.get("description") or raw.get("ShortDescriptionStr")
        features.location_text = vacancy.get("location") or raw.get("PrimaryLocation")
        features.grade_raw = get_flex(raw, "Grade")
        features.grade_source_field = "requisitionFlexFields.Grade" if features.grade_raw else None
        features.contract_raw = get_flex(raw, "Recruiting Type") or features.contract_raw
        features.contract_source_field = (
            "requisitionFlexFields.Recruiting Type"
            if get_flex(raw, "Recruiting Type")
            else features.contract_source_field
        )
        features.country_code_raw = raw.get("PrimaryLocationCountry") or work_location.get("Country")
        features.city_raw = work_location.get("TownOrCity")
        return features


class InspiraExtractor(SourceFeatureExtractor):
    def extract(self, vacancy: dict[str, Any]) -> FeatureBundle:
        raw = raw_dict(vacancy)
        duty_station = first_dict(raw.get("dutyStation"))
        features = base_features(
            vacancy,
            evidence={
                "jc": raw.get("jc"),
                "jf": raw.get("jf"),
                "jl": raw.get("jl"),
                "jn": raw.get("jn"),
                "categoryCode": raw.get("categoryCode"),
                "jobLevel": raw.get("jobLevel"),
            },
        )
        features.title = vacancy.get("title") or raw.get("postingTitle")
        features.location_text = vacancy.get("location") or duty_station.get("description")
        features.department = vacancy.get("department") or first_dict(raw.get("dept")).get("name")
        features.grade_raw = label(raw.get("jl")) or raw.get("jobLevel")
        features.grade_source_field = "raw.jl.name/raw.jobLevel" if features.grade_raw else None
        features.contract_raw = raw.get("recruitmentType") or features.contract_raw
        features.contract_source_field = (
            "raw.recruitmentType" if raw.get("recruitmentType") else features.contract_source_field
        )
        features.job_family_code = code(raw.get("jf")) or raw.get("jobFamilyCode")
        features.job_family_label = label(raw.get("jf"))
        features.job_network_code = code(raw.get("jn"))
        features.job_network_label = label(raw.get("jn"))
        features.staff_category_raw = label(raw.get("jc"))
        features.recruitment_type = raw.get("recruitmentType")
        features.city_raw = duty_station.get("description")
        unrwa_grade = _unrwa_grade_signal(features.title, features.description)
        if unrwa_grade:
            features.grade_raw = unrwa_grade
            features.grade_source_field = "description/title.UNRWA Grade"
        if _unrwa_national_local_signal(features.title, features.description):
            features.contract_raw = "UNRWA national local appointment / national consultant"
            features.contract_source_field = "description/title.UNRWA NLA"
        return features


class OracleHCMExtractor(SourceFeatureExtractor):
    def extract(self, vacancy: dict[str, Any]) -> FeatureBundle:
        raw = raw_dict(vacancy)
        secondary_names = [
            item.get("Name")
            for item in raw.get("secondaryLocations", []) or []
            if isinstance(item, dict) and item.get("Name")
        ]
        work_location = first_dict(raw.get("workLocation"))
        features = base_features(
            vacancy,
            evidence={
                "PrimaryLocation": raw.get("PrimaryLocation"),
                "PrimaryLocationCountry": raw.get("PrimaryLocationCountry"),
                "secondaryLocations": raw.get("secondaryLocations", []),
                "workLocation": raw.get("workLocation", []),
                "oracle_site_number": raw.get("oracle_site_number"),
                "oracle_site_name": raw.get("oracle_site_name"),
                "Agency": get_flex(raw, "Agency"),
                "Vacancy Type": get_flex(raw, "Vacancy Type"),
                "Practice Area": get_flex(raw, "Practice Area"),
                "Bureau": get_flex(raw, "Bureau"),
                "Contract Duration": get_flex(raw, "Contract Duration"),
                "Vacancy Timeline": get_flex(raw, "Vacancy Timeline"),
                "source_priority": raw.get("source_priority") or raw.get("_source_priority"),
            },
        )
        features.title = vacancy.get("title") or raw.get("Title")
        features.description = vacancy.get("description") or raw.get("ShortDescriptionStr")
        features.location_text = text_join(
            [vacancy.get("location"), raw.get("PrimaryLocation"), *secondary_names]
        )
        features.grade_raw = get_flex(raw, "Grade")
        features.grade_source_field = "requisitionFlexFields.Grade" if features.grade_raw else None
        if not features.grade_raw:
            features.grade_raw = _oracle_nonstaff_grade_signal(features.title, features.description)
            features.grade_source_field = "title/description.IPSA_NPSA" if features.grade_raw else None
        vacancy_type = get_flex(raw, "Vacancy Type")
        recruiting_type = get_flex(raw, "Recruiting Type")
        features.contract_raw = (
            vacancy_type
            or recruiting_type
            or raw.get("RecruitingType")
            or raw.get("JobType")
            or features.contract_raw
        )
        if not features.contract_raw:
            text = " ".join(value or "" for value in (features.title, features.description))
            if re.search(r"\b(?:special\s+services?\s+agreement|SSA)\b", text, re.I):
                features.contract_raw = "Special Service Agreement (SSA)"
                features.contract_source_field = "title/description.Oracle SSA signal"
            elif re.search(r"\b(?:individual\s+consult(?:ant|ancy)|\bIC\b)", text, re.I):
                features.contract_raw = "Individual Consultancy"
                features.contract_source_field = "title/description.Oracle individual consultancy signal"
        if vacancy_type:
            features.contract_source_field = "requisitionFlexFields.Vacancy Type"
        elif recruiting_type:
            features.contract_source_field = "requisitionFlexFields.Recruiting Type"
        features.country_code_raw = raw.get("PrimaryLocationCountry") or work_location.get("Country")
        features.city_raw = work_location.get("TownOrCity")
        features.work_modality_raw = text_join(secondary_names)
        return features


class ICAOOracleHCMExtractor(OracleHCMExtractor):
    def extract(self, vacancy: dict[str, Any]) -> FeatureBundle:
        features = super().extract(vacancy)
        text = " ".join(value or "" for value in (features.title, features.description))
        if features.contract_raw and re.search(r"\b(?:special\s+services?\s+agreement|SSA)\b", features.contract_raw, re.I):
            return features
        if re.search(r"\b(?:FRA2601|FAF/26/0(?:19|20)|auditeur|consult(?:ant|ing|ancy))\b", text, re.I):
            features.contract_raw = "Consulting"
            features.contract_source_field = "title/description.ICAO consulting signal"
        elif re.search(r"\b(?:ICAO\s+Roster|iPACK|Government Safety Inspector)\b", text, re.I):
            features.seniority_raw = "ICAO Roster"
            features.contract_raw = features.contract_raw or "Consulting"
            features.contract_source_field = features.contract_source_field or "title/description.ICAO roster signal"
        return features


class PageUpExtractor(SourceFeatureExtractor):
    def extract(self, vacancy: dict[str, Any]) -> FeatureBundle:
        raw = raw_dict(vacancy)
        features = base_features(
            vacancy,
            evidence={
                "listing_html_present": bool(raw.get("listing_html")),
                "detail_html_present": bool(raw.get("detail_html")),
                "detail_url": raw.get("_pageup_detail_url"),
            },
        )
        return features


class UNICEFPageUpExtractor(PageUpExtractor):
    def extract(self, vacancy: dict[str, Any]) -> FeatureBundle:
        features = super().extract(vacancy)
        consultant_signal = _unicef_consultant_signal(features.title, features.description)
        if consultant_signal:
            features.contract_raw = consultant_signal
            features.contract_source_field = "title/description.UNICEF consultant signal"
        return features


class WorkdayExtractor(SourceFeatureExtractor):
    def extract(self, vacancy: dict[str, Any]) -> FeatureBundle:
        raw = raw_dict(vacancy)
        features = base_features(
            vacancy,
            evidence={
                "externalPath": raw.get("externalPath"),
                "bulletFields": raw.get("bulletFields"),
                "postedOn": raw.get("postedOn"),
                "needs_detail_fetch": vacancy.get("description") is None,
            },
        )
        features.title = raw.get("title") or vacancy.get("title")
        features.location_text = raw.get("locationsText") or vacancy.get("location")
        features.description = vacancy.get("description") or raw.get("jobDescription")
        return features


class IMFWorkdayExtractor(WorkdayExtractor):
    def extract(self, vacancy: dict[str, Any]) -> FeatureBundle:
        features = super().extract(vacancy)
        grade = _imf_hiring_for_signal(features.description)
        if grade:
            features.grade_raw = grade
            features.grade_source_field = "description.Hiring For"
        return features


class GlobalFundWorkdayExtractor(WorkdayExtractor):
    def extract(self, vacancy: dict[str, Any]) -> FeatureBundle:
        features = super().extract(vacancy)
        grade = _globalfund_grade_signal(features.title, features.description)
        if grade:
            features.grade_raw = grade
            features.grade_source_field = "title/description.GlobalFundGrade"
        return features


class ICRCSuccessFactorsExtractor(SourceFeatureExtractor):
    def extract(self, vacancy: dict[str, Any]) -> FeatureBundle:
        features = base_features(vacancy)
        seniority = _icrc_functional_proxy(features.title, features.description)
        if seniority:
            features.seniority_raw = seniority
            features.grade_source_field = "title/description.ICRC functional proxy"
            features.contract_raw = features.contract_raw or "Staff contract"
            features.contract_source_field = (
                features.contract_source_field or "title/description.ICRC functional proxy"
            )
        return features


class ICCSuccessFactorsLegacyExtractor(SourceFeatureExtractor):
    def extract(self, vacancy: dict[str, Any]) -> FeatureBundle:
        features = base_features(vacancy)
        if _contains_any(features.title, features.description, needles=("internship",)):
            features.seniority_raw = "Internship"
            features.contract_raw = "Internship"
            features.contract_source_field = "title.ICC internship signal"
        elif _contains_any(features.title, features.description, needles=("visiting professional",)):
            features.seniority_raw = "Visiting Professional"
            features.contract_raw = "Visiting Professional"
            features.contract_source_field = "title.Visiting Professional"
        elif _contains_any(
            features.title,
            features.description,
            needles=("freelance translator", "freelance", "expression of interest"),
        ):
            features.seniority_raw = "Freelance Translator"
            features.contract_raw = "Consultancy Expression of Interest / Freelance"
            features.contract_source_field = "title/description.ICC freelance EOI signal"
        return features


class UNESCOSuccessFactorsExtractor(SourceFeatureExtractor):
    def extract(self, vacancy: dict[str, Any]) -> FeatureBundle:
        features = base_features(vacancy)
        grade = _unesco_grade_signal(features.description)
        if grade:
            features.grade_raw = grade
            features.grade_source_field = "description.UNESCO Grade"
        contract = _unesco_labeled_value(features.description, "Type of contract")
        if contract:
            features.contract_raw = contract
            features.contract_source_field = "description.UNESCO Type of contract"
        level = _unesco_labeled_value(features.description, "Level")
        if level and "consult" in (features.contract_raw or "").casefold():
            features.grade_raw = level
            features.grade_source_field = "description.UNESCO Consultant Level"
            features.seniority_raw = level
        return features


class UNOPSAvatureExtractor(SourceFeatureExtractor):
    def extract(self, vacancy: dict[str, Any]) -> FeatureBundle:
        raw = raw_dict(vacancy)
        features = base_features(
            vacancy,
            evidence={
                "detail_url": raw.get("_detail_url"),
                "listing_html_present": bool(raw.get("listing_html")),
                "detail_html_present": bool(raw.get("detail_html")),
            },
        )
        features.seniority_raw = vacancy.get("department")
        return features


class UNVExtractor(SourceFeatureExtractor):
    def extract(self, vacancy: dict[str, Any]) -> FeatureBundle:
        raw = raw_dict(vacancy)
        country = raw.get("country") or {}
        host = raw.get("hostEntity") or {}
        institution = host.get("institution") or {} if isinstance(host, dict) else {}
        duty_stations = raw.get("dutyStations") or []
        expertise = raw.get("expertiseAreas") or []
        features = base_features(
            vacancy,
            evidence={
                "isOnsite": raw.get("isOnsite"),
                "categoryName": raw.get("categoryName"),
                "volunteerType": raw.get("volunteerType"),
                "workLocation": raw.get("workLocation"),
                "workArrangement": raw.get("workArrangement"),
                "assignmentDuration": raw.get("assignmentDuration"),
                "hoursWeek": raw.get("hoursWeek"),
                "expertiseAreas": expertise,
                "hostEntity": host,
                "sdgType": raw.get("sdgType"),
            },
        )
        features.title = raw.get("name") or vacancy.get("title")
        features.location_text = label(country) or vacancy.get("location")
        features.country_code_raw = (country.get("props") or {}).get("codeISO2") if isinstance(country, dict) else None
        features.city_raw = label(duty_stations[0]) if duty_stations else None
        features.region_raw = label(raw.get("unvRegion"))
        features.contract_raw = "UNV"
        features.contract_source_field = "source_id"
        features.unv_category_code = code(raw.get("categoryName"))
        features.unv_category_label = label(raw.get("categoryName"))
        features.unv_volunteer_type = label(raw.get("volunteerType"))
        features.unv_work_location = label(raw.get("workLocation"))
        features.unv_work_arrangement = label(raw.get("workArrangement"))
        features.unv_assignment_duration = label(raw.get("assignmentDuration"))
        features.unv_hours_week = label(raw.get("hoursWeek"))
        features.unv_host_entity = label(institution) or (host.get("name") if isinstance(host, dict) else None)
        features.unv_sdg = label(raw.get("sdgType"))
        features.unv_expertise_areas = [area for area in (label(item) for item in expertise) if area]
        return features


class CSODExtractor(SourceFeatureExtractor):
    def extract(self, vacancy: dict[str, Any]) -> FeatureBundle:
        raw = raw_dict(vacancy)
        loc = first_dict(raw.get("locations"))
        features = base_features(
            vacancy,
            evidence={"locations": raw.get("locations", []), "requisitionId": raw.get("requisitionId")},
        )
        features.title = raw.get("displayJobTitle") or vacancy.get("title")
        features.description = raw.get("externalDescription") or vacancy.get("description")
        features.country_code_raw = loc.get("country")
        features.city_raw = loc.get("city")
        return features


class IMOAPIExtractor(SourceFeatureExtractor):
    def extract(self, vacancy: dict[str, Any]) -> FeatureBundle:
        raw = raw_dict(vacancy)
        features = base_features(
            vacancy,
            evidence={
                "classification": raw.get("classification"),
                "contractType": raw.get("contractType"),
                "contractHours": raw.get("contractHours"),
                "role": raw.get("role"),
            },
        )
        classification = raw.get("classification")
        if classification not in (None, ""):
            features.grade_raw = str(classification)
            features.grade_source_field = "raw.classification"
        return features


class TaleoExtractor(SourceFeatureExtractor):
    def extract(self, vacancy: dict[str, Any]) -> FeatureBundle:
        raw = raw_dict(vacancy)
        flat = raw.get("_taleo_flat") if isinstance(raw.get("_taleo_flat"), dict) else {}
        features = base_features(vacancy, evidence={"taleo_flat": flat, "detail_url": raw.get("_taleo_detail_url")})
        requisition_type = (
            flat.get("TYPE_OF_REQUISITION")
            or flat.get("Type of Requisition")
            or flat.get("JOB_TYPE")
            or features.employment_type
        )
        features.grade_raw = (
            flat.get("JOB_LEVEL")
            or flat.get("job_level")
            or flat.get("Grade Level")
            or flat.get("grade_level")
            or flat.get("Position Level")
            or flat.get("position_level")
        )
        if features.source_id == "fao_taleo" and not features.grade_raw:
            features.grade_raw = _fao_requisition_grade_signal(requisition_type)
        if features.source_id == "fao_taleo" and not features.grade_raw:
            features.grade_raw = _fao_requisition_grade_signal(features.title)
        features.grade_source_field = "_taleo_flat.JOB_LEVEL" if features.grade_raw else None
        if features.source_id == "fao_taleo" and features.grade_raw in _FAO_REQUISITION_SIGNALS:
            features.grade_source_field = (
                "_taleo_flat.Type of Requisition"
                if _fao_requisition_grade_signal(requisition_type)
                else "title.FAO requisition signal"
            )
            features.contract_raw = _fao_requisition_contract_signal(requisition_type) or features.grade_raw
            features.contract_source_field = features.grade_source_field
        features.contract_raw = (
            features.contract_raw
            or flat.get("TYPE_OF_REQUISITION")
            or flat.get("Type of Requisition")
            or flat.get("JOB_TYPE")
            or flat.get("EMPLOYEE_STATUS")
            or flat.get("JOB_SCHEDULE")
            or flat.get("POSITION_LEVEL_LABEL")
            or features.contract_raw
        )
        if features.source_id == "iaea_taleo" and _iaea_consultant_signal(features):
            features.contract_raw = "Consultant"
            features.contract_source_field = "title/description.IAEA consultant signal"
        features.department = vacancy.get("department") or flat.get("JOB_FIELD")
        features.location_text = vacancy.get("location") or flat.get("LOCATION")
        return features


_FAO_REQUISITION_SIGNALS = {
    "NPP",
    "PSA",
    "Fellows Programme",
    "Volunteer Programme",
    "FAO Regular Volunteer Programme",
}


def _fao_requisition_grade_signal(value: object | None) -> str | None:
    text = str(value or "").strip().casefold()
    if text.startswith("npp") or "national project personnel" in text:
        return "NPP"
    if text.startswith("psa") or "personal services agreement" in text:
        return "PSA"
    if "fellow" in text:
        return "Fellows Programme"
    if "regular volunteer" in text:
        return "FAO Regular Volunteer Programme"
    if "volunteer" in text:
        return "Volunteer Programme"
    return None


def _fao_requisition_contract_signal(value: object | None) -> str | None:
    signal = _fao_requisition_grade_signal(value)
    if signal == "NPP":
        return "National Consultant / NPP (National Project Personnel)"
    if signal == "PSA":
        return "International Consultant / PSA (Personal Services Agreement)"
    if signal in {"Fellows Programme", "Volunteer Programme", "FAO Regular Volunteer Programme"}:
        return signal
    return str(value) if value not in (None, "") else None


_SOURCE_EXTRACTORS: dict[str, type[SourceFeatureExtractor]] = {
    "cern_custom_html": CERNCustomHTMLExtractor,
    "globalfund_workday": GlobalFundWorkdayExtractor,
    "imf_workday": IMFWorkdayExtractor,
    "icrc_successfactors": ICRCSuccessFactorsExtractor,
    "icc_successfactors_legacy": ICCSuccessFactorsLegacyExtractor,
    "imo_api": IMOAPIExtractor,
    "icao_oracle_hcm": ICAOOracleHCMExtractor,
    "iom_oracle_hcm": IOMOracleHCMExtractor,
    "un_inspira": InspiraExtractor,
    "isa_inspira_split": InspiraExtractor,
    "itc_inspira_split": InspiraExtractor,
    "unesco_successfactors": UNESCOSuccessFactorsExtractor,
    "unicef_pageup": UNICEFPageUpExtractor,
    "unv_uvp": UNVExtractor,
    "unops_avature": UNOPSAvatureExtractor,
    "worldbank_csod": CSODExtractor,
    "adb_taleo": TaleoExtractor,
}


def _contains_any(*values: str | None, needles: tuple[str, ...]) -> bool:
    text = " ".join(value or "" for value in values).casefold()
    return any(needle in text for needle in needles)


def _unicef_consultant_signal(title: str | None, description: str | None) -> str | None:
    text = " ".join(value or "" for value in (title, description))
    folded = text.casefold()
    if re.search(r"\b(?:consultores?|consultoras?|consultor[ií]a|consultancy|consultants?)\b", folded, re.I):
        if re.search(r"\b(?:consultores?|consultoras?)\s+internacionales\b|\binternational\s+consultant\b", folded, re.I):
            return "International Consultant"
        if re.search(r"\b(?:consultores?|consultoras?)\s+nacionales\b|\bnational\s+consultant\b", folded, re.I):
            return "National Consultant"
        if _spanish_local_consultancy_signal(folded):
            return "National Consultant"
        return "Consultant"
    if re.search(r"\basistencia\s+t[eé]cnica\b", folded, re.I):
        return "National Consultant" if _spanish_local_consultancy_signal(folded) else "Consultant"
    return None


def _spanish_local_consultancy_signal(text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:panam[aá]|quito|ecuador|buenos\s+aires|argentina|guatemala|"
            r"espa[nñ]ol|habilidades\s+requeridas)\b",
            text,
            re.I,
        )
    )


def _imf_hiring_for_signal(description: str | None) -> str | None:
    if not description:
        return None
    match = re.search(
        r"\bHiring\s+For\s*:\s*(?P<grade>[A-Z]\d{2}(?:\s*,\s*[A-Z]\d{2})*|B)\b",
        description,
        flags=re.I,
    )
    if not match:
        return None
    return " ".join(match.group("grade").upper().replace(",", ", ").split())


def _globalfund_grade_signal(title: str | None, description: str | None) -> str | None:
    text = " ".join(value or "" for value in (title, description))
    match = re.search(r"\bGL[-\s]?([A-G])\b|\bGrade\s+([A-G])\b", text, re.I)
    if match:
        return f"GL {match.group(1) or match.group(2)}".upper()
    if re.search(r"\b10\s+years\b.{0,180}\bprogressive\s+experience\b", text, re.I | re.S):
        return "GL E"
    return None


def _icrc_functional_proxy(title: str | None, description: str | None) -> str | None:
    text = " ".join(value or "" for value in (title, description)).casefold()
    if re.search(r"\b(?:director|head)\b", text):
        return "Head / Director"
    if re.search(r"\b(?:coordinator|manager|supervisor|lead)\b", text):
        return "Coordinator / Manager / Supervisor / Lead"
    if re.search(
        r"\b(?:officer|oficial|analyst|delegate|specialist|adviser|advisor|engineer|nurse|doctor|nutritionist)\b",
        text,
    ):
        return "Officer / Accountant / Analyst / Engineer / Nurse / Doctor / Delegate / Specialist / Adviser / Nutritionist"
    if re.search(r"\b(?:assistant|associate|driver)\b", text):
        return "Assistant / Associate / Driver"
    if re.search(r"\b3\s*[-–]\s*4\s+(?:years|años)\b", text, re.I):
        return "Officer / Accountant / Analyst / Engineer / Nurse / Doctor / Delegate / Specialist / Adviser / Nutritionist"
    return None


def _unrwa_grade_signal(title: str | None, description: str | None) -> str | None:
    text = " ".join(value or "" for value in (title, description))
    match = re.search(r"\bGrade(?:\s+and\s+Salary)?\s*:?\s*Grade\s*(?P<grade>\d{1,2})\b", text, re.I)
    if not match:
        match = re.search(r"\bGrade\s*(?P<grade>1[0-9]|20)\b", text, re.I)
    if not match:
        match = re.search(r",\s*(?P<grade>1[0-9]|20)\s*(?:$|[,-])", title or "", re.I)
    if not match:
        return None
    grade = int(match.group("grade"))
    if 1 <= grade <= 20:
        return f"UNRWA Grade {grade}"
    return None


def _unrwa_national_local_signal(title: str | None, description: str | None) -> bool:
    text = " ".join(value or "" for value in (title, description))
    return bool(re.search(r"\b(?:NLA|UNRWA|Grade\s+and\s+Salary)\b", text, re.I))


def _oracle_nonstaff_grade_signal(title: str | None, description: str | None) -> str | None:
    text = " ".join(value or "" for value in (title, description))
    match = re.search(r"\b(?P<family>IPSA|NPSA|I[-\s]?PSA|N[-\s]?PSA)[-\s]?(?P<level>\d{1,2})\b", text, re.I)
    if not match:
        return None
    family = re.sub(r"[^A-Z]", "", match.group("family").upper())
    if family == "IPSA":
        family = "IPSA"
    elif family == "NPSA":
        family = "NPSA"
    return f"{family}-{int(match.group('level'))}"


def _unesco_labeled_value(description: str | None, field_label: str) -> str | None:
    if not description:
        return None
    boundary_labels = (
        "Type of contract",
        "Grade",
        "Level",
        "Duration of contract",
        "Hiring Unit",
        "Duty Station",
        "Work Location",
        "Job Family",
        "Parent Sector",
        "Application Deadline",
    )
    other_labels = [label for label in boundary_labels if label.casefold() != field_label.casefold()]
    pattern = re.compile(
        rf"\b{re.escape(field_label)}\s*:\s*(?P<value>.*?)"
        rf"(?=\s+(?:{'|'.join(re.escape(label) for label in other_labels)})\s*:|$)",
        re.I | re.S,
    )
    match = pattern.search(description)
    if not match:
        return None
    value = " ".join(match.group("value").split())
    return value or None


def _unesco_grade_signal(description: str | None) -> str | None:
    grade = _unesco_labeled_value(description, "Grade")
    if not grade:
        return None
    match = re.search(r"\b(?:P[-\s]?[1-5]|D[-\s]?[12]|NO[-\s]?[A-D]|G[-\s]?[1-7])\b", grade, re.I)
    return match.group(0).upper().replace(" ", "-") if match else None


def _iaea_consultant_signal(features: FeatureBundle) -> bool:
    text = " ".join(
        value or ""
        for value in (
            features.vacancy_id,
            features.title,
            features.description,
            features.contract_raw,
        )
    )
    return bool(re.search(r"\b(?:consultant|consultancy)\b|\bPIP[-_]TC", text, re.I))


def _cern_grade_signal(raw: dict[str, Any], description: str | None) -> tuple[str | None, str | None]:
    raw_grade = _cern_grade_from_value(raw.get("grade"))
    if raw_grade:
        return raw_grade, "raw.grade"
    text = description or ""
    explicit = _cern_grade_from_text(text)
    if explicit:
        return explicit, "description.grade_range"
    inferred = _cern_grade_from_experience_text(text)
    if inferred:
        return inferred, "description.experience_eligibility"
    return None, None


def _cern_grade_from_value(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    match = re.search(r"\b(?:Grade\s*)?(?P<level>[2-8])\b", text, flags=re.I)
    if match:
        return f"Grade {match.group('level')}"
    return text if text else None


def _cern_grade_from_text(text: str) -> str | None:
    match = re.search(r"\bGrade\s+range\s*:?\s*(?P<level>[2-8])\b", text, flags=re.I)
    if match:
        return f"Grade {match.group('level')}"
    return None


def _cern_grade_from_experience_text(text: str) -> str | None:
    normalized = " ".join(text.split())
    if re.search(
        r"master.?s degree with 2 to 6 years of professional experience since graduation"
        r".{0,160}phd with a maximum of 3 years",
        normalized,
        flags=re.I,
    ):
        return "Grade 4"
    if re.search(
        r"maximum of 2 years of professional experience since graduation"
        r".{0,220}highest educational qualification is either a bachelor.?s or master.?s degree"
        r".{0,120}(?:can.?t|cannot) hold a phd",
        normalized,
        flags=re.I,
    ):
        return "Grade 2"
    if re.search(
        r"maximum of 2 years of professional experience since graduation"
        r".{0,220}highest educational qualification is a general secondary education diploma"
        r".{0,120}shorter non-university degree"
        r".{0,160}(?:can.?t|cannot) hold a bachelor.?s degree, master.?s degree or phd",
        normalized,
        flags=re.I,
    ):
        return "Grade 2"
    return None

_ATS_EXTRACTORS: dict[str, type[SourceFeatureExtractor]] = {
    "oracle_hcm": OracleHCMExtractor,
    "inspira": InspiraExtractor,
    "pageup": PageUpExtractor,
    "workday": WorkdayExtractor,
    "avature": UNOPSAvatureExtractor,
    "unv": UNVExtractor,
    "csod": CSODExtractor,
    "taleo": TaleoExtractor,
}


def get_extractor(source_id: str, ats_family: str) -> SourceFeatureExtractor:
    extractor_cls = _SOURCE_EXTRACTORS.get(source_id) or _ATS_EXTRACTORS.get(ats_family)
    return (extractor_cls or GenericExtractor)()
