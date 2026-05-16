"""SQL-backed vacancy filter queries."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date
from typing import Any

from jobagg.db import JobDatabase
from jobagg.filters.normalization import (
    country_info,
    normalize_city,
    normalize_grade,
    normalize_scope,
)
from jobagg.filters.schemas import VacancyFilters, VacancySearchRequest, VacancySearchResponse


def search_vacancies(db: JobDatabase, filters: VacancyFilters) -> list[dict[str, Any]]:
    query = """
        SELECT
            j.*,
            c.ccog_primary_code,
            c.ccog_primary_label,
            c.ccog_family_code,
            c.ccog_family_label,
            c.ccog_part,
            c.ccog_confidence,
            c.ccog_method,
            c.contract_category,
            c.contract_subtype,
            c.contract_confidence,
            c.national_international,
            c.national_international_confidence,
            c.grade_system,
            c.grade_family,
            c.grade_code,
            c.grade_level,
            c.staff_category,
            c.min_years_experience,
            c.grade_confidence,
            c.country,
            c.country_iso2,
            c.country_iso3,
            c.city,
            c.region,
            c.subregion,
            c.location_confidence,
            c.work_modality,
            c.work_modality_confidence,
            c.unv_category,
            c.unv_raw_category,
            c.unv_volunteer_type,
            c.unv_assignment_duration,
            c.unv_work_arrangement,
            c.unv_hours_per_week,
            c.unv_host_entity,
            c.unv_sdg,
            c.unv_expertise_areas,
            c.needs_review,
            c.classification_version,
            c.classified_at
        FROM jobs j
        LEFT JOIN vacancy_classifications c ON c.vacancy_id = j.job_key
    """
    clauses: list[str] = []
    params: list[Any] = []
    _add_filters(clauses, params, filters)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY (j.closes_at IS NULL), j.closes_at ASC, j.posted_at DESC"
    if filters.limit is not None:
        query += " LIMIT ?"
        params.append(filters.limit)
    with db.connect() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [_row_to_dict(row) for row in rows]


def search_collected_jobs(
    db: JobDatabase,
    request: VacancySearchRequest,
    *,
    include_facets: bool = True,
) -> VacancySearchResponse:
    clauses, params = _search_conditions(request)
    where_clause = " WHERE " + " AND ".join(clauses) if clauses else ""
    total_query = f"""
        SELECT COUNT(DISTINCT j.job_key) AS count
        FROM jobs j
        JOIN vacancy_classifications c ON c.vacancy_id = j.job_key
        {where_clause}
    """
    result_query = f"""
        SELECT
            j.job_key,
            j.source_id,
            j.org_id,
            j.ats_family,
            j.external_id,
            j.title,
            j.location,
            j.department,
            j.posted_at,
            j.closes_at,
            j.apply_url,
            j.source_url,
            j.status,
            c.grade_system,
            c.grade_family,
            c.grade_code,
            c.staff_category,
            c.grade_confidence,
            c.national_international,
            c.contract_category,
            c.contract_subtype,
            c.ccog_primary_code,
            c.ccog_primary_label,
            c.ccog_family_code,
            c.ccog_family_label,
            c.work_modality,
            c.country,
            c.city,
            c.region,
            c.classification_version,
            c.needs_review,
            c.evidence AS classification_evidence
        FROM jobs j
        JOIN vacancy_classifications c ON c.vacancy_id = j.job_key
        {where_clause}
        {_order_by(request)}
        LIMIT ? OFFSET ?
    """
    with db.connect() as conn:
        total = int(conn.execute(total_query, tuple(params)).fetchone()["count"])
        rows = conn.execute(
            result_query,
            tuple([*params, request.limit, request.offset]),
        ).fetchall()
        results = [_search_row_to_result(conn, row, request) for row in rows]
        unclassified_clauses, unclassified_params = _jobs_only_conditions(request)
        unclassified_where = (
            " WHERE " + " AND ".join(unclassified_clauses)
            if unclassified_clauses
            else ""
        )
        unclassified_query = f"""
            SELECT COUNT(*) AS count
            FROM jobs j
            LEFT JOIN vacancy_classifications c ON c.vacancy_id = j.job_key
            {unclassified_where}
            { "AND" if unclassified_where else "WHERE" } c.vacancy_id IS NULL
        """
        unclassified_count = int(
            conn.execute(unclassified_query, tuple(unclassified_params)).fetchone()["count"]
        )
    facets = search_facet_counts(db, request) if include_facets else {}
    return VacancySearchResponse(
        total=total,
        limit=request.limit,
        offset=request.offset,
        results=results,
        facets=facets,
        unclassified_count=unclassified_count,
    )


def search_facet_counts(
    db: JobDatabase,
    request: VacancySearchRequest,
) -> dict[str, dict[str, int]]:
    clauses, params = _search_conditions(request)
    where_clause = " WHERE " + " AND ".join(clauses) if clauses else ""
    facet_columns = {
        "grades": "c.grade_code",
        "organizations": "j.org_id",
        "ccog_families": "c.ccog_family_code",
        "contract_categories": "c.contract_category",
        "work_modalities": "c.work_modality",
        "regions": "c.region",
        "unv_categories": "c.unv_category",
    }
    result: dict[str, dict[str, int]] = {}
    with db.connect() as conn:
        for name, column in facet_columns.items():
            query = f"""
                SELECT {column} AS value, COUNT(DISTINCT j.job_key) AS count
                FROM jobs j
                JOIN vacancy_classifications c ON c.vacancy_id = j.job_key
                {where_clause}
                GROUP BY {column}
                ORDER BY count DESC, value
            """
            rows = conn.execute(query, tuple(params)).fetchall()
            result[name] = {
                str(row["value"]): int(row["count"])
                for row in rows
                if row["value"] not in (None, "")
            }
    return result


def build_where_clause(filters: VacancyFilters) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    _add_filters(clauses, params, filters)
    return (" WHERE " + " AND ".join(clauses), params) if clauses else ("", params)


def _search_conditions(request: VacancySearchRequest) -> tuple[list[str], list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if request.status:
        _add_in_clause(clauses, params, "j.status", [status.casefold() for status in request.status])
    _add_in_clause(clauses, params, "j.org_id", request.organizations)
    _add_in_clause(clauses, params, "j.source_id", request.source_ids)
    _add_in_clause(clauses, params, "j.ats_family", request.ats_families)
    _add_in_clause(clauses, params, "c.contract_category", request.contract_categories)
    _add_in_clause(clauses, params, "c.grade_system", request.grade_systems)
    grade_families = [str(value).upper() for value in request.grade_families]
    grade_codes = [value for value in (normalize_grade(code) for code in request.grade_codes) if value]
    _add_in_clause(clauses, params, "c.grade_family", grade_families)
    _add_in_clause(clauses, params, "c.grade_code", grade_codes)
    if (grade_codes or grade_families) and not request.include_low_confidence:
        clauses.append("c.grade_confidence >= ?")
        params.append(request.min_grade_confidence)
    scopes = [scope for scope in (normalize_scope(value) for value in request.national_international) if scope]
    if scopes:
        if "international" in scopes and ("P" in grade_families or any(code.startswith("P") for code in grade_codes)):
            placeholders = ", ".join("?" for _ in scopes)
            clauses.append(f"(c.national_international IN ({placeholders}) OR c.grade_family = 'P')")
            params.extend(scopes)
        else:
            _add_in_clause(clauses, params, "c.national_international", scopes)
    _add_in_clause(clauses, params, "c.ccog_primary_code", request.ccog_codes)
    _add_ccog_family_clause(clauses, params, request.ccog_families)
    _add_in_clause(clauses, params, "c.work_modality", request.work_modalities)
    _add_in_clause(clauses, params, "c.unv_category", request.unv_categories)
    _add_in_clause(clauses, params, "c.unv_volunteer_type", request.unv_volunteer_types)
    _add_date_clause(clauses, params, "j.closes_at", ">=", request.closing_date_from)
    _add_date_clause(clauses, params, "j.closes_at", "<=", request.closing_date_to)
    _add_date_clause(clauses, params, "j.posted_at", ">=", request.posted_date_from)
    _add_date_clause(clauses, params, "j.posted_at", "<=", request.posted_date_to)
    if request.text:
        clauses.append("(j.title LIKE ? OR j.description LIKE ? OR j.location LIKE ?)")
        needle = f"%{request.text}%"
        params.extend([needle, needle, needle])
    location_clause, location_params = _location_exists_clause(request)
    if location_clause:
        clauses.append(location_clause)
        params.extend(location_params)
    return clauses, params


def _jobs_only_conditions(request: VacancySearchRequest) -> tuple[list[str], list[Any]]:
    """Return clauses that depend only on the ``jobs`` table.

    Used to count rows that satisfy the request scope but have no
    classification row, so the search response can warn the user that
    classification is incomplete.
    """

    clauses: list[str] = []
    params: list[Any] = []
    if request.status:
        _add_in_clause(
            clauses, params, "j.status", [status.casefold() for status in request.status]
        )
    _add_in_clause(clauses, params, "j.org_id", request.organizations)
    _add_in_clause(clauses, params, "j.source_id", request.source_ids)
    _add_in_clause(clauses, params, "j.ats_family", request.ats_families)
    _add_date_clause(clauses, params, "j.closes_at", ">=", request.closing_date_from)
    _add_date_clause(clauses, params, "j.closes_at", "<=", request.closing_date_to)
    _add_date_clause(clauses, params, "j.posted_at", ">=", request.posted_date_from)
    _add_date_clause(clauses, params, "j.posted_at", "<=", request.posted_date_to)
    if request.text:
        clauses.append("(j.title LIKE ? OR j.description LIKE ? OR j.location LIKE ?)")
        needle = f"%{request.text}%"
        params.extend([needle, needle, needle])
    return clauses, params


def _location_exists_clause(request: VacancySearchRequest) -> tuple[str | None, list[Any]]:
    city_keys = [key for key in (normalize_city(city) for city in request.cities) if key]
    countries = [_normalize_country_iso3(country) for country in request.countries_iso3]
    countries = [country for country in countries if country]
    has_location_filter = bool(city_keys or countries or request.regions)
    if not has_location_filter:
        return None, []
    conditions = ["l.vacancy_id = j.job_key"]
    params: list[Any] = []
    if city_keys:
        _add_in_clause(conditions, params, "l.city_key", city_keys)
    if countries:
        _add_in_clause(conditions, params, "l.country_iso3", countries)
    if request.regions:
        _add_in_clause(conditions, params, "l.region", request.regions)
    if request.location_types:
        _add_in_clause(conditions, params, "l.location_type", request.location_types)
    if not request.include_low_confidence:
        conditions.append("l.confidence >= ?")
        params.append(request.min_location_confidence)
    clause = f"""
        EXISTS (
            SELECT 1
            FROM vacancy_locations l
            WHERE {" AND ".join(conditions)}
        )
    """
    return clause, params


def _add_in_clause(
    clauses: list[str],
    params: list[Any],
    column: str,
    values: list[str],
) -> None:
    cleaned = [value for value in values if value not in (None, "")]
    if not cleaned:
        return
    placeholders = ", ".join("?" for _ in cleaned)
    clauses.append(f"{column} IN ({placeholders})")
    params.extend(cleaned)


def _add_ccog_family_clause(
    clauses: list[str],
    params: list[Any],
    families: list[str],
) -> None:
    cleaned = [family for family in families if family]
    if not cleaned:
        return
    parts = []
    for family in cleaned:
        parts.append(
            "(c.ccog_family_code = ? OR c.ccog_primary_code = ? OR c.ccog_primary_code LIKE ?)"
        )
        params.extend([family, family, f"{family}.%"])
    clauses.append("(" + " OR ".join(parts) + ")")


def _add_date_clause(
    clauses: list[str],
    params: list[Any],
    column: str,
    operator: str,
    value: date | str | None,
) -> None:
    if value is None:
        return
    normalized = value.isoformat() if isinstance(value, date) else value
    if _is_date_only(normalized):
        clauses.append(f"date({column}) {operator} date(?)")
    else:
        clauses.append(f"{column} {operator} ?")
    params.append(normalized)


def _is_date_only(value: str) -> bool:
    return len(value) == 10 and value[4] == "-" and value[7] == "-"


def _normalize_country_iso3(value: str) -> str | None:
    country = country_info(value)
    return country.iso3 if country else value.upper().strip()


def _order_by(request: VacancySearchRequest) -> str:
    if request.sort == "posted_date_desc":
        return "ORDER BY j.posted_at IS NULL, j.posted_at DESC, j.closes_at ASC"
    if request.sort == "closing_date_desc":
        return "ORDER BY j.closes_at IS NULL, j.closes_at DESC, j.posted_at DESC"
    return "ORDER BY j.closes_at IS NULL, j.closes_at ASC, j.posted_at DESC"


def _add_filters(clauses: list[str], params: list[Any], filters: VacancyFilters) -> None:
    if filters.only_active:
        clauses.append("j.status = 'open'")
    mapping = {
        "organization": ("j.org_id", filters.organization),
        "source_id": ("j.source_id", filters.source_id),
        "ats_family": ("j.ats_family", filters.ats_family),
        "ccog_part": ("c.ccog_part", filters.ccog_part),
        "contract_category": ("c.contract_category", filters.contract_category),
        "contract_subtype": ("c.contract_subtype", filters.contract_subtype),
        "grade_system": ("c.grade_system", filters.grade_system),
        "grade_family": ("c.grade_family", filters.grade_family),
        "grade_code": ("c.grade_code", filters.grade_code),
        "staff_category": ("c.staff_category", filters.staff_category),
        "national_international": ("c.national_international", filters.national_international),
        "country": ("c.country", filters.country),
        "country_iso3": ("c.country_iso3", filters.country_iso3),
        "city": ("c.city", filters.city),
        "region": ("c.region", filters.region),
        "subregion": ("c.subregion", filters.subregion),
        "work_modality": ("c.work_modality", filters.work_modality),
        "unv_category": ("c.unv_category", filters.unv_category),
        "unv_volunteer_type": ("c.unv_volunteer_type", filters.unv_volunteer_type),
        "unv_assignment_duration": ("c.unv_assignment_duration", filters.unv_assignment_duration),
        "unv_work_arrangement": ("c.unv_work_arrangement", filters.unv_work_arrangement),
        "unv_hours_per_week": ("c.unv_hours_per_week", filters.unv_hours_per_week),
        "unv_host_entity": ("c.unv_host_entity", filters.unv_host_entity),
        "unv_sdg": ("c.unv_sdg", filters.unv_sdg),
    }
    for _, (column, value) in mapping.items():
        if value is not None:
            clauses.append(f"{column} = ?")
            params.append(value)
    if filters.ccog_code:
        clauses.append("c.ccog_primary_code = ?")
        params.append(filters.ccog_code)
    if filters.ccog_family:
        clauses.append(
            "(c.ccog_family_code = ? OR c.ccog_primary_code = ? OR c.ccog_primary_code LIKE ?)"
        )
        params.extend([filters.ccog_family, filters.ccog_family, f"{filters.ccog_family}.%"])
    if filters.max_min_years_experience is not None:
        clauses.append("(c.min_years_experience IS NULL OR c.min_years_experience <= ?)")
        params.append(filters.max_min_years_experience)
    if filters.unv_expertise_area:
        clauses.append("c.unv_expertise_areas LIKE ?")
        params.append(f"%{filters.unv_expertise_area}%")
    if filters.needs_review is not None:
        clauses.append("c.needs_review = ?")
        params.append(1 if filters.needs_review else 0)
    _add_date_clause(clauses, params, "j.posted_at", ">=", filters.posted_date_from)
    _add_date_clause(clauses, params, "j.posted_at", "<=", filters.posted_date_to)
    _add_date_clause(clauses, params, "j.closes_at", ">=", filters.closing_date_from)
    _add_date_clause(clauses, params, "j.closes_at", "<=", filters.closing_date_to)
    if filters.text:
        clauses.append(
            "(j.title LIKE ? OR j.description LIKE ? OR j.location LIKE ? OR j.department LIKE ?)"
        )
        needle = f"%{filters.text}%"
        params.extend([needle, needle, needle, needle])


def _row_to_dict(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["raw"] = json.loads(data.pop("raw_json") or "{}")
    if data.get("unv_expertise_areas"):
        data["unv_expertise_areas"] = json.loads(data["unv_expertise_areas"])
    data["needs_review"] = bool(data["needs_review"]) if data.get("needs_review") is not None else None
    return data


def _search_row_to_result(
    conn: Any,
    row: Any,
    request: VacancySearchRequest,
) -> dict[str, Any]:
    data = dict(row)
    evidence = json.loads(data.pop("classification_evidence") or "{}")
    location = _matched_location(conn, data["job_key"], request)
    return {
        "job_key": data["job_key"],
        "title": data["title"],
        "organization": data["org_id"],
        "source_id": data["source_id"],
        "ats_family": data["ats_family"],
        "duty_station": _location_label(location, data),
        "grade_code": data["grade_code"],
        "grade_family": data["grade_family"],
        "national_international": data["national_international"],
        "contract_category": data["contract_category"],
        "contract_subtype": data["contract_subtype"],
        "ccog_primary_code": data["ccog_primary_code"],
        "ccog_primary_label": data["ccog_primary_label"],
        "ccog_family_code": data["ccog_family_code"],
        "ccog_family_label": data["ccog_family_label"],
        "work_modality": data["work_modality"],
        "closing_date": data["closes_at"],
        "posted_date": data["posted_at"],
        "status": data["status"],
        "apply_url": data["apply_url"],
        "source_url": data["source_url"],
        "classification_version": data["classification_version"],
        "needs_review": bool(data["needs_review"]),
        "match_evidence": _match_evidence(data, location, evidence, request),
    }


def _matched_location(
    conn: Any,
    vacancy_id: str,
    request: VacancySearchRequest,
) -> dict[str, Any] | None:
    clauses = ["vacancy_id = ?"]
    params: list[Any] = [vacancy_id]
    city_keys = [key for key in (normalize_city(city) for city in request.cities) if key]
    countries = [_normalize_country_iso3(country) for country in request.countries_iso3]
    countries = [country for country in countries if country]
    if city_keys:
        _add_in_clause(clauses, params, "city_key", city_keys)
    if countries:
        _add_in_clause(clauses, params, "country_iso3", countries)
    if request.regions:
        _add_in_clause(clauses, params, "region", request.regions)
    if request.location_types:
        _add_in_clause(clauses, params, "location_type", request.location_types)
    if not request.include_low_confidence:
        clauses.append("confidence >= ?")
        params.append(request.min_location_confidence)
    query = f"""
        SELECT *
        FROM vacancy_locations
        WHERE {" AND ".join(clauses)}
        ORDER BY is_primary DESC, confidence DESC, id
        LIMIT 1
    """
    row = conn.execute(query, tuple(params)).fetchone()
    if row is None and (city_keys or countries or request.regions):
        return None
    if row is None:
        row = conn.execute(
            """
            SELECT *
            FROM vacancy_locations
            WHERE vacancy_id = ?
            ORDER BY is_primary DESC, confidence DESC, id
            LIMIT 1
            """,
            (vacancy_id,),
        ).fetchone()
    if row is None:
        return None
    data = dict(row)
    data["is_primary"] = bool(data["is_primary"])
    data["is_remote"] = bool(data["is_remote"])
    data["evidence"] = json.loads(data["evidence"] or "{}")
    return data


def _location_label(location: dict[str, Any] | None, row: dict[str, Any]) -> str | None:
    if location:
        parts = [location.get("city"), location.get("country")]
        value = ", ".join(part for part in parts if part)
        return value or location.get("country") or row.get("location")
    return row.get("location")


def _match_evidence(
    row: dict[str, Any],
    location: dict[str, Any] | None,
    evidence: dict[str, Any],
    request: VacancySearchRequest,
) -> dict[str, Any]:
    grade_evidence = evidence.get("grade") or {}
    scope_reason = "classification"
    scopes = [scope for scope in (normalize_scope(value) for value in request.national_international) if scope]
    if "international" in scopes and row.get("national_international") != "international":
        if row.get("grade_family") == "P":
            scope_reason = "grade_family=P"
    return {
        "location": {
            "matched_city": location.get("city") if location else None,
            "matched_country_iso3": location.get("country_iso3") if location else None,
            "source_field": location.get("source_field") if location else None,
            "location_type": location.get("location_type") if location else None,
            "confidence": location.get("confidence") if location else None,
            "evidence": location.get("evidence") if location else {},
        },
        "grade": {
            "matched_grade": row.get("grade_code"),
            "source_field": grade_evidence.get("field"),
            "confidence": row.get("grade_confidence"),
        },
        "scope": {
            "matched": row.get("national_international"),
            "reason": scope_reason,
        },
    }


def response_to_dict(response: VacancySearchResponse) -> dict[str, Any]:
    return asdict(response)
