"""Source-specific feature extractors."""

from __future__ import annotations

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
        vacancy_type = get_flex(raw, "Vacancy Type")
        recruiting_type = get_flex(raw, "Recruiting Type")
        features.contract_raw = (
            vacancy_type
            or recruiting_type
            or raw.get("RecruitingType")
            or raw.get("JobType")
            or features.contract_raw
        )
        if vacancy_type:
            features.contract_source_field = "requisitionFlexFields.Vacancy Type"
        elif recruiting_type:
            features.contract_source_field = "requisitionFlexFields.Recruiting Type"
        features.country_code_raw = raw.get("PrimaryLocationCountry") or work_location.get("Country")
        features.city_raw = work_location.get("TownOrCity")
        features.work_modality_raw = text_join(secondary_names)
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


class TaleoExtractor(SourceFeatureExtractor):
    def extract(self, vacancy: dict[str, Any]) -> FeatureBundle:
        raw = raw_dict(vacancy)
        flat = raw.get("_taleo_flat") if isinstance(raw.get("_taleo_flat"), dict) else {}
        features = base_features(vacancy, evidence={"taleo_flat": flat, "detail_url": raw.get("_taleo_detail_url")})
        features.grade_raw = flat.get("JOB_LEVEL") or flat.get("job_level")
        features.grade_source_field = "_taleo_flat.JOB_LEVEL" if features.grade_raw else None
        features.contract_raw = (
            flat.get("JOB_TYPE")
            or flat.get("EMPLOYEE_STATUS")
            or flat.get("JOB_SCHEDULE")
            or features.contract_raw
        )
        features.department = vacancy.get("department") or flat.get("JOB_FIELD")
        features.location_text = vacancy.get("location") or flat.get("LOCATION")
        return features


_SOURCE_EXTRACTORS: dict[str, type[SourceFeatureExtractor]] = {
    "iom_oracle_hcm": IOMOracleHCMExtractor,
    "un_inspira": InspiraExtractor,
    "isa_inspira_split": InspiraExtractor,
    "itc_inspira_split": InspiraExtractor,
    "unicef_pageup": PageUpExtractor,
    "unv_uvp": UNVExtractor,
    "unops_avature": UNOPSAvatureExtractor,
    "worldbank_csod": CSODExtractor,
    "adb_taleo": TaleoExtractor,
}

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
