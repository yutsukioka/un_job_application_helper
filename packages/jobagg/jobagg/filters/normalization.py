"""Search value normalization helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass

from jobagg.classification.rules import load_rule_file


CITY_COUNTRY_HINTS = {
    "addis ababa": "ETH",
    "amman": "JOR",
    "bangkok": "THA",
    "beirut": "LBN",
    "copenhagen": "DNK",
    "dakar": "SEN",
    "geneva": "CHE",
    "kigali": "RWA",
    "nairobi": "KEN",
    "new york": "USA",
    "panama city": "PAN",
    "rome": "ITA",
    "vienna": "AUT",
}


@dataclass(frozen=True, slots=True)
class CountryInfo:
    iso2: str
    iso3: str
    name: str
    region: str | None = None
    subregion: str | None = None


def normalize_city(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip().casefold()
    text = re.sub(r"[-_/]+", " ", text)
    text = re.sub(r"[^a-z0-9\s'.]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def display_city(value: str | None) -> str | None:
    key = normalize_city(value)
    if not key:
        return None
    return " ".join(part.capitalize() for part in key.split())


def normalize_grade(value: str | None) -> str | None:
    if not value:
        return None
    compact = re.sub(r"[^A-Za-z0-9]", "", value).upper()
    return compact or None


def normalize_scope(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.casefold().strip()
    aliases = {
        "international post": "international",
        "international": "international",
        "intl": "international",
        "national post": "national",
        "national": "national",
        "local": "local",
        "unv national": "unv_national",
        "unv international": "unv_international",
    }
    return aliases.get(normalized, normalized)


def country_info(value: str | None) -> CountryInfo | None:
    if not value:
        return None
    normalized = value.strip().casefold()
    for iso2, data in _countries().items():
        aliases = {
            iso2.casefold(),
            str(data.get("iso3", "")).casefold(),
            str(data.get("name", "")).casefold(),
        }
        aliases.update(str(alias).casefold() for alias in data.get("aliases", []) or [])
        if normalized in aliases:
            return CountryInfo(
                iso2=iso2,
                iso3=str(data.get("iso3")),
                name=str(data.get("name")),
                region=data.get("region"),
                subregion=data.get("subregion"),
            )
    return None


def country_for_city(city: str | None) -> CountryInfo | None:
    key = normalize_city(city)
    if not key:
        return None
    iso3 = CITY_COUNTRY_HINTS.get(key)
    return country_info(iso3)


def known_city_keys() -> set[str]:
    return set(CITY_COUNTRY_HINTS)


def _countries() -> dict[str, dict[str, str]]:
    return load_rule_file("country_regions.yaml").get("countries", {})
