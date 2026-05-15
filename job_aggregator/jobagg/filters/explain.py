"""Explain search filter evaluation for a single vacancy."""

from __future__ import annotations

from typing import Any

from jobagg.db import JobDatabase
from jobagg.filters.normalization import (
    country_info,
    normalize_city,
    normalize_grade,
    normalize_scope,
)
from jobagg.filters.schemas import VacancySearchRequest


def explain_job_match(
    db: JobDatabase,
    job_key: str,
    request: VacancySearchRequest,
) -> dict[str, Any]:
    row = _job_with_classification(db, job_key)
    if row is None:
        return {
            "job_key": job_key,
            "matched": False,
            "reason": "job_key not found",
            "checks": [],
        }
    locations = list(db.iter_vacancy_locations(job_key))
    checks = _evaluate_checks(row, locations, request)
    matched = all(check["matched"] for check in checks)
    return {
        "job_key": job_key,
        "title": row.get("title"),
        "matched": matched,
        "reason": "matched all filters" if matched else _first_failed_reason(checks),
        "checks": checks,
    }


def explain_to_text(explanation: dict[str, Any]) -> str:
    lines = [
        f"Job: {explanation.get('job_key')}",
        f"Title: {explanation.get('title') or ''}",
        "Filter evaluation:",
    ]
    for check in explanation.get("checks", []):
        marker = "[PASS]" if check["matched"] else "[FAIL]"
        lines.append(f"{marker} {check['reason']}")
    lines.append(f"Final: {'matched' if explanation.get('matched') else 'not matched'}")
    return "\n".join(lines) + "\n"


def _evaluate_checks(
    row: dict[str, Any],
    locations: list[dict[str, Any]],
    request: VacancySearchRequest,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    _add_list_check(checks, "status", row.get("status"), [value.casefold() for value in request.status])
    _add_list_check(checks, "organization", row.get("org_id"), request.organizations)
    _add_list_check(checks, "source_id", row.get("source_id"), request.source_ids)
    _add_list_check(checks, "ats_family", row.get("ats_family"), request.ats_families)
    _add_list_check(checks, "contract_category", row.get("contract_category"), request.contract_categories)
    _add_list_check(checks, "grade_system", row.get("grade_system"), request.grade_systems)
    _add_list_check(
        checks,
        "grade_family",
        row.get("grade_family"),
        [value.upper() for value in request.grade_families],
    )
    _add_list_check(
        checks,
        "grade_code",
        row.get("grade_code"),
        [value for value in (normalize_grade(value) for value in request.grade_codes) if value],
    )
    if (request.grade_codes or request.grade_families) and not request.include_low_confidence:
        _add_confidence_check(
            checks,
            "grade_confidence",
            row.get("grade_confidence"),
            request.min_grade_confidence,
        )
    _add_scope_check(checks, row, request)
    _add_list_check(checks, "ccog_primary_code", row.get("ccog_primary_code"), request.ccog_codes)
    _add_ccog_family_check(checks, row, request.ccog_families)
    _add_list_check(checks, "work_modality", row.get("work_modality"), request.work_modalities)
    _add_list_check(checks, "unv_category", row.get("unv_category"), request.unv_categories)
    _add_list_check(
        checks,
        "unv_volunteer_type",
        row.get("unv_volunteer_type"),
        request.unv_volunteer_types,
    )
    _add_list_check(checks, "region", row.get("region"), request.regions)
    _add_text_check(checks, row, request.text)
    _add_date_check(checks, "closing_date_from", row.get("closes_at"), ">=", request.closing_date_from)
    _add_date_check(checks, "closing_date_to", row.get("closes_at"), "<=", request.closing_date_to)
    _add_date_check(checks, "posted_date_from", row.get("posted_at"), ">=", request.posted_date_from)
    _add_date_check(checks, "posted_date_to", row.get("posted_at"), "<=", request.posted_date_to)
    _add_location_check(checks, locations, request)
    return checks


def _add_list_check(
    checks: list[dict[str, Any]],
    label: str,
    actual: object,
    expected: list[str],
) -> None:
    expected = [value for value in expected if value not in (None, "")]
    if not expected:
        return
    actual_value = str(actual).casefold() if actual is not None else None
    expected_values = [str(value).casefold() for value in expected]
    matched = actual_value in expected_values
    checks.append(
        {
            "filter": label,
            "matched": matched,
            "actual": actual,
            "expected": expected,
            "reason": (
                f"{label} {actual} matched"
                if matched
                else f"{label} {actual} not in {expected}"
            ),
        }
    )


def _add_confidence_check(
    checks: list[dict[str, Any]],
    label: str,
    actual: object,
    minimum: float,
) -> None:
    confidence = float(actual or 0)
    matched = confidence >= minimum
    checks.append(
        {
            "filter": label,
            "matched": matched,
            "actual": confidence,
            "expected": minimum,
            "reason": (
                f"{label} {confidence:.2f} >= {minimum:.2f}"
                if matched
                else f"{label} {confidence:.2f} below {minimum:.2f}"
            ),
        }
    )


def _add_scope_check(
    checks: list[dict[str, Any]],
    row: dict[str, Any],
    request: VacancySearchRequest,
) -> None:
    scopes = [scope for scope in (normalize_scope(value) for value in request.national_international) if scope]
    if not scopes:
        return
    actual = row.get("national_international")
    grade_family = row.get("grade_family")
    matched = actual in scopes
    reason = f"scope {actual} matched"
    if not matched and "international" in scopes and grade_family == "P":
        matched = True
        reason = "scope international matched by grade_family=P"
    if not matched:
        reason = f"scope {actual} not in {scopes}"
    checks.append(
        {
            "filter": "scope",
            "matched": matched,
            "actual": actual,
            "expected": scopes,
            "reason": reason,
        }
    )


def _add_ccog_family_check(
    checks: list[dict[str, Any]],
    row: dict[str, Any],
    expected: list[str],
) -> None:
    expected = [value for value in expected if value]
    if not expected:
        return
    family = row.get("ccog_family_code")
    code = row.get("ccog_primary_code")
    matched = any(family == value or str(code or "").startswith(f"{value}.") for value in expected)
    checks.append(
        {
            "filter": "ccog_family",
            "matched": matched,
            "actual": family or code,
            "expected": expected,
            "reason": (
                f"ccog_family {family or code} matched"
                if matched
                else f"ccog_family {family or code} not in {expected}"
            ),
        }
    )


def _add_text_check(
    checks: list[dict[str, Any]],
    row: dict[str, Any],
    text: str | None,
) -> None:
    if not text:
        return
    haystack = " ".join(
        str(row.get(key) or "")
        for key in ("title", "description", "location")
    ).casefold()
    matched = text.casefold() in haystack
    checks.append(
        {
            "filter": "text",
            "matched": matched,
            "actual": text,
            "expected": "title/description/location contains text",
            "reason": f"text {text!r} {'matched' if matched else 'not found'}",
        }
    )


def _add_date_check(
    checks: list[dict[str, Any]],
    label: str,
    actual: object,
    operator: str,
    expected: object,
) -> None:
    if expected is None:
        return
    actual_text = str(actual or "")
    expected_text = expected.isoformat() if hasattr(expected, "isoformat") else str(expected)
    matched = bool(actual_text) and (
        actual_text >= expected_text if operator == ">=" else actual_text <= expected_text
    )
    checks.append(
        {
            "filter": label,
            "matched": matched,
            "actual": actual,
            "expected": expected_text,
            "reason": (
                f"{label} {actual_text} {operator} {expected_text}"
                if matched
                else f"{label} {actual_text or 'missing'} fails {operator} {expected_text}"
            ),
        }
    )


def _add_location_check(
    checks: list[dict[str, Any]],
    locations: list[dict[str, Any]],
    request: VacancySearchRequest,
) -> None:
    city_keys = [key for key in (normalize_city(city) for city in request.cities) if key]
    countries = [_country_iso3(country) for country in request.countries_iso3]
    countries = [country for country in countries if country]
    has_filter = bool(city_keys or countries or request.regions)
    if not has_filter:
        return

    matching_locations = [
        location
        for location in locations
        if _location_matches(location, city_keys, countries, request)
    ]
    matched = bool(matching_locations)
    best = matching_locations[0] if matching_locations else None
    checks.append(
        {
            "filter": "location",
            "matched": matched,
            "actual": _location_summary(best) if best else [_location_summary(item) for item in locations],
            "expected": {
                "cities": request.cities,
                "countries_iso3": countries,
                "regions": request.regions,
                "location_types": request.location_types,
                "min_confidence": request.min_location_confidence,
            },
            "reason": (
                f"location matched {_location_summary(best)}"
                if matched
                else "no vacancy_locations row matched requested location filters"
            ),
        }
    )


def _location_matches(
    location: dict[str, Any],
    city_keys: list[str],
    countries: list[str],
    request: VacancySearchRequest,
) -> bool:
    if city_keys and location.get("city_key") not in city_keys:
        return False
    if countries and location.get("country_iso3") not in countries:
        return False
    if request.regions and location.get("region") not in request.regions:
        return False
    if request.location_types and location.get("location_type") not in request.location_types:
        return False
    if not request.include_low_confidence and float(location.get("confidence") or 0) < request.min_location_confidence:
        return False
    return True


def _job_with_classification(db: JobDatabase, job_key: str) -> dict[str, Any] | None:
    with db.connect() as conn:
        row = conn.execute(
            """
            SELECT j.*, c.*
            FROM jobs j
            LEFT JOIN vacancy_classifications c ON c.vacancy_id = j.job_key
            WHERE j.job_key = ?
            """,
            (job_key,),
        ).fetchone()
    return dict(row) if row else None


def _country_iso3(value: str) -> str | None:
    country = country_info(value)
    return country.iso3 if country else value.upper().strip()


def _location_summary(location: dict[str, Any] | None) -> dict[str, Any] | None:
    if location is None:
        return None
    return {
        "city": location.get("city"),
        "country_iso3": location.get("country_iso3"),
        "region": location.get("region"),
        "location_type": location.get("location_type"),
        "confidence": location.get("confidence"),
        "source_field": location.get("source_field"),
    }


def _first_failed_reason(checks: list[dict[str, Any]]) -> str:
    for check in checks:
        if not check["matched"]:
            return str(check["reason"])
    return "matched all filters"
