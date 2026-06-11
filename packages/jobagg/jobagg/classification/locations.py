"""Build normalized vacancy location rows."""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Iterable

from jobagg.classification.models import (
    FeatureBundle,
    LocationResult,
    ModalityResult,
    VacancyLocation,
    WorkModality,
)
from jobagg.filters.normalization import (
    CountryInfo,
    country_for_city,
    country_info,
    display_city,
    known_city_keys,
    normalize_city,
)


SEARCHABLE_LOCATION_TYPES = {"primary", "duty_station", "outposted", "remote_anchor"}
MULTIPLE_LOCATION_RE = re.compile(r"\b\d+\s+locations?\b|multiple locations|various locations", re.I)
REMOTE_RE = re.compile(r"\b(remote|virtual|online|home[- ]?based)\b", re.I)


def build_vacancy_locations(
    features: FeatureBundle,
    location: LocationResult,
    modality: ModalityResult,
) -> list[VacancyLocation]:
    locations: list[VacancyLocation] = []
    text = " ".join([features.title or "", features.location_text or ""])
    if MULTIPLE_LOCATION_RE.search(text):
        locations.append(
            VacancyLocation(
                vacancy_id=features.vacancy_id,
                location_type="multiple_unknown",
                confidence=0.40,
                source_field="jobs.location/title",
                evidence={"text": text},
            )
        )

    if features.source_id == "unv_uvp":
        locations.extend(_unv_locations(features))
    elif features.ats_family == "inspira":
        locations.extend(_structured_city_locations(features, location, "duty_station"))
    elif features.ats_family == "oracle_hcm":
        locations.extend(_oracle_locations(features, location))
    else:
        locations.extend(_structured_city_locations(features, location, "primary"))

    if features.source_id != "unv_uvp":
        locations.extend(_title_locations(features))

    if not locations and (location.city or location.iso3 or location.country):
        locations.append(_from_location_result(features.vacancy_id, location, "primary", "classifier"))

    locations = _dedupe_locations(locations)
    if not locations:
        return []
    if modality.value in {WorkModality.ONLINE_REMOTE, WorkModality.HOME_BASED}:
        locations = [_remote_adjusted(item) for item in locations]
    best = best_vacancy_location(locations)
    if best:
        locations = [
            replace(item, is_primary=item is best or item == best)
            for item in locations
        ]
    return locations


def best_vacancy_location(locations: Iterable[VacancyLocation]) -> VacancyLocation | None:
    priority = {
        "duty_station": 0,
        "primary": 1,
        "outposted": 2,
        "remote_anchor": 3,
        "secondary": 4,
        "home_based": 5,
        "organization_region": 6,
        "multiple_unknown": 7,
    }
    candidates = list(locations)
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda item: (
            item.location_type not in SEARCHABLE_LOCATION_TYPES,
            item.city_key is None,
            priority.get(item.location_type, 99),
            -item.confidence,
        ),
    )[0]


def location_result_from_vacancy_location(location: VacancyLocation | None) -> LocationResult:
    if location is None:
        return LocationResult()
    return LocationResult(
        country=location.country,
        iso2=location.country_iso2,
        iso3=location.country_iso3,
        city=location.city,
        region=location.region,
        subregion=location.subregion,
        confidence=location.confidence,
        evidence={
            "source_field": location.source_field,
            "location_type": location.location_type,
            "vacancy_locations": True,
            **location.evidence,
        },
    )


def _unv_locations(features: FeatureBundle) -> list[VacancyLocation]:
    # Deliberately ignores unvRegion and host entity regional office fields.
    if features.city_raw:
        return [
            _build_location(
                vacancy_id=features.vacancy_id,
                city=features.city_raw,
                country_value=features.country_code_raw or features.location_text,
                location_type="duty_station",
                confidence=0.98,
                source_field="raw.dutyStations[].label",
                evidence={"city_raw": features.city_raw},
            )
        ]
    if features.country_code_raw or features.location_text:
        return [
            _build_location(
                vacancy_id=features.vacancy_id,
                city=None,
                country_value=features.country_code_raw or features.location_text,
                location_type="primary",
                confidence=0.80,
                source_field="raw.country",
                evidence={"country_code_raw": features.country_code_raw},
            )
        ]
    return []


def _oracle_locations(features: FeatureBundle, location: LocationResult) -> list[VacancyLocation]:
    locations = _structured_city_locations(features, location, "primary")
    secondary_text = features.work_modality_raw or ""
    for parsed in _parse_location_text(
        features.vacancy_id,
        secondary_text,
        location_type="secondary",
        source_field="raw.secondaryLocations",
        confidence=0.74,
    ):
        locations.append(parsed)
    return locations


def _structured_city_locations(
    features: FeatureBundle,
    location: LocationResult,
    location_type: str,
) -> list[VacancyLocation]:
    if features.city_raw:
        return [
            _build_location(
                vacancy_id=features.vacancy_id,
                city=features.city_raw,
                country_value=features.country_code_raw or location.iso3 or features.location_text,
                location_type=location_type,
                confidence=0.98 if location_type == "duty_station" else max(location.confidence, 0.86),
                source_field=_source_city_field(features, location_type),
                evidence={"city_raw": features.city_raw, "location_text": features.location_text},
            )
        ]
    return _parse_location_text(
        features.vacancy_id,
        features.location_text or "",
        location_type=location_type,
        source_field="jobs.location",
        confidence=max(location.confidence, 0.70),
    )


def _title_locations(features: FeatureBundle) -> list[VacancyLocation]:
    title = features.title or ""
    if not title:
        return []
    location_type = "outposted" if re.search(r"\boutposted to\b", title, re.I) else "duty_station"
    confidence = 0.85 if location_type == "outposted" else 0.80
    return _parse_location_text(
        features.vacancy_id,
        title,
        location_type=location_type,
        source_field="title",
        confidence=confidence,
    )


def _parse_location_text(
    vacancy_id: str,
    text: str,
    *,
    location_type: str,
    source_field: str,
    confidence: float,
) -> list[VacancyLocation]:
    if not text or MULTIPLE_LOCATION_RE.fullmatch(text.strip()):
        return []
    matches: list[VacancyLocation] = []
    for city_key in known_city_keys():
        pattern = re.compile(rf"\b{re.escape(city_key)}\b", re.I)
        if not pattern.search(text):
            continue
        city = display_city(city_key)
        country = _country_from_nearby_text(text, city_key) or country_for_city(city_key)
        matches.append(
            _build_location(
                vacancy_id=vacancy_id,
                city=city,
                country_value=country.iso3 if country else None,
                location_type="remote_anchor" if REMOTE_RE.search(text) else location_type,
                confidence=confidence,
                source_field=source_field,
                is_remote=bool(REMOTE_RE.search(text)),
                evidence={"matched_text": text, "matched_city": city_key},
            )
        )
    if matches:
        return matches
    country_only = _country_only_location(vacancy_id, text, location_type, source_field, confidence)
    return [country_only] if country_only else []


def _country_only_location(
    vacancy_id: str,
    text: str,
    location_type: str,
    source_field: str,
    confidence: float,
) -> VacancyLocation | None:
    country = _country_from_nearby_text(text, "")
    if not country:
        return None
    return _build_location(
        vacancy_id=vacancy_id,
        city=None,
        country_value=country.iso3,
        location_type=location_type,
        confidence=max(0.65, confidence - 0.10),
        source_field=source_field,
        evidence={"matched_text": text},
    )


def _country_from_nearby_text(text: str, city_key: str) -> CountryInfo | None:
    lowered = text.casefold()
    for value in re.split(r"[,;()/|-]", lowered):
        country = country_info(value.strip())
        if country:
            return country
    if city_key:
        return country_for_city(city_key)
    for token in lowered.split():
        country = country_info(token)
        if country:
            return country
    return None


def _from_location_result(
    vacancy_id: str,
    location: LocationResult,
    location_type: str,
    source_field: str,
) -> VacancyLocation:
    return _build_location(
        vacancy_id=vacancy_id,
        city=location.city,
        country_value=location.iso3 or location.country,
        location_type=location_type,
        confidence=location.confidence,
        source_field=source_field,
        evidence=location.evidence,
    )


def _build_location(
    *,
    vacancy_id: str,
    city: str | None,
    country_value: str | None,
    location_type: str,
    confidence: float,
    source_field: str,
    is_remote: bool = False,
    evidence: dict | None = None,
) -> VacancyLocation:
    country = country_info(country_value) or country_for_city(city)
    city_display = display_city(city)
    return VacancyLocation(
        vacancy_id=vacancy_id,
        city=city_display,
        city_key=normalize_city(city_display),
        country=country.name if country else None,
        country_iso2=country.iso2 if country else None,
        country_iso3=country.iso3 if country else None,
        region=country.region if country else None,
        subregion=country.subregion if country else None,
        location_type=location_type,
        is_remote=is_remote,
        confidence=confidence,
        source_field=source_field,
        evidence=evidence or {},
    )


def _remote_adjusted(location: VacancyLocation) -> VacancyLocation:
    if location.city_key and location.location_type in {"primary", "duty_station", "outposted"}:
        return replace(location, location_type="remote_anchor", is_remote=True)
    if not location.city_key:
        return replace(location, location_type="home_based", is_remote=True)
    return location


def _dedupe_locations(locations: list[VacancyLocation]) -> list[VacancyLocation]:
    best_by_key: dict[tuple, VacancyLocation] = {}
    for location in locations:
        key = (
            location.city_key,
            location.country_iso3,
            location.location_type,
            location.source_field,
        )
        current = best_by_key.get(key)
        if current is None or location.confidence > current.confidence:
            best_by_key[key] = location
    return list(best_by_key.values())


def _source_city_field(features: FeatureBundle, location_type: str) -> str:
    if features.source_id == "unv_uvp":
        return "raw.dutyStations[].label"
    if features.ats_family == "inspira":
        return "raw.dutyStation.description"
    if features.ats_family == "oracle_hcm":
        return "raw.workLocation.TownOrCity"
    return "jobs.location" if location_type == "primary" else "source.city"
