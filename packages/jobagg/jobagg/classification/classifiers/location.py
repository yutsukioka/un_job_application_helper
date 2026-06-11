"""Location classification."""

from __future__ import annotations

from jobagg.classification.models import FeatureBundle, LocationResult
from jobagg.classification.rules import load_rule_file


def classify_location(features: FeatureBundle) -> LocationResult:
    countries = _countries()
    raw_country = (features.country_code_raw or "").strip()
    if raw_country:
        result = _country_from_value(raw_country, countries)
        if result:
            result.city = _clean_city(features.city_raw)
            result.confidence = 0.95
            result.evidence = {"country_code_raw": raw_country, "city_raw": features.city_raw}
            return result
    text = features.location_text or ""
    result = _country_from_location_text(text, countries)
    if result:
        result.city = _clean_city(features.city_raw) or _city_from_location_text(text, result.country)
        result.confidence = 0.78 if result.city else 0.70
        result.evidence = {"location_text": text}
        return result
    if features.city_raw:
        return LocationResult(
            city=_clean_city(features.city_raw),
            confidence=0.45,
            evidence={"city_raw": features.city_raw},
        )
    return LocationResult(evidence={"location_text": text})


def _countries() -> dict[str, dict[str, str]]:
    return load_rule_file("country_regions.yaml").get("countries", {})


def _country_from_value(value: str, countries: dict[str, dict[str, str]]) -> LocationResult | None:
    normalized = value.strip().casefold()
    for iso2, data in countries.items():
        aliases = {iso2.casefold(), data.get("iso3", "").casefold(), data.get("name", "").casefold()}
        aliases.update(alias.casefold() for alias in data.get("aliases", []) or [])
        if normalized in aliases:
            return _location_from_country(iso2, data)
    return None


def _country_from_location_text(
    value: str,
    countries: dict[str, dict[str, str]],
) -> LocationResult | None:
    normalized = f" {value.casefold()} "
    for iso2, data in countries.items():
        names = [data.get("name", ""), *(data.get("aliases", []) or [])]
        for name in names:
            if name and f" {name.casefold()} " in normalized.replace(",", " "):
                return _location_from_country(iso2, data)
    return None


def _location_from_country(iso2: str, data: dict[str, str]) -> LocationResult:
    return LocationResult(
        country=data.get("name"),
        iso2=iso2,
        iso3=data.get("iso3"),
        region=data.get("region"),
        subregion=data.get("subregion"),
    )


def _city_from_location_text(value: str, country: str | None) -> str | None:
    if not value or not country:
        return None
    parts = [part.strip() for part in value.replace(";", ",").split(",") if part.strip()]
    if len(parts) < 2:
        return None
    first = parts[0]
    if first.casefold() in {"multiple locations", "various locations", "home-based", "remote"}:
        return None
    return first


def _clean_city(value: str | None) -> str | None:
    if not value:
        return None
    text = " ".join(str(value).split())
    if text.casefold() in {"multiple locations", "various locations", "home-based", "remote"}:
        return None
    return text or None
