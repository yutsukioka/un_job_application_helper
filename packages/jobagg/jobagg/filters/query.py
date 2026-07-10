"""SQL-backed vacancy filter queries."""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import UTC, date, datetime
from typing import Any

from jobagg.db import JobDatabase
from jobagg.filters.normalization import (
    country_info,
    normalize_city,
    normalize_grade,
    normalize_scope,
)
from jobagg.filters.schemas import VacancyFilters, VacancySearchRequest, VacancySearchResponse


# FTS5 reserves a small set of characters in query expressions. We escape
# user-supplied free-text by wrapping it as a quoted phrase and doubling
# any embedded double-quotes; the result is always a valid FTS5 phrase
# expression regardless of input.
def _fts_phrase(text: str) -> str:
    return '"' + text.replace('"', '""') + '"'


def _add_text_clause(
    clauses: list[str],
    params: list[Any],
    text: str,
    *,
    include_department: bool = False,
    include_classification: bool = False,
    use_fts: bool = True,
) -> None:
    """Add a free-text predicate, preferring FTS5 with a LIKE fallback.

    The FTS5 path uses ``jobs_fts MATCH ?`` against the rowid mirror; the
    LIKE branch is kept for environments where FTS5 is not available and
    so existing test fixtures (which don't drive text search) keep
    behaving identically.
    """

    needle = f"%{text}%"
    if use_fts:
        fts_columns = (
            "{title description department location}"
            if include_department
            else "{title description location}"
        )
        phrase = _fts_phrase(text)
        clauses.append(
            "(j.rowid IN (SELECT rowid FROM jobs_fts WHERE jobs_fts MATCH ?)"
            " OR j.title LIKE ? OR j.description LIKE ? OR j.location LIKE ?"
            + (" OR j.department LIKE ?" if include_department else "")
            + _classification_text_sql(include_classification)
            + ")"
        )
        params.append(f"{fts_columns} : {phrase}")
    else:
        clauses.append(
            "(j.title LIKE ? OR j.description LIKE ? OR j.location LIKE ?"
            + (" OR j.department LIKE ?" if include_department else "")
            + _classification_text_sql(include_classification)
            + ")"
        )
    params.extend([needle, needle, needle])
    if include_department:
        params.append(needle)
    if include_classification:
        params.extend([needle] * len(_CLASSIFICATION_TEXT_COLUMNS))


_CLASSIFICATION_TEXT_COLUMNS = (
    "c.capability_tags",
    "c.ccog_primary_label",
    "c.ccog_family_label",
    "c.occupational_family_label",
    "c.occupational_medium_label",
    "c.mandate_network_label",
    "c.mandate_family_label",
    "c.contract_category",
    "c.contract_group",
    "c.seniority_group",
    "c.grade_code",
    "c.standard_un_equivalent",
)


def _classification_text_sql(include_classification: bool) -> str:
    if not include_classification:
        return ""
    return "".join(f" OR {column} LIKE ?" for column in _CLASSIFICATION_TEXT_COLUMNS)


def search_vacancies(db: JobDatabase, filters: VacancyFilters) -> list[dict[str, Any]]:
    use_fts = db.fts_available()
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
            c.occupational_family_code,
            c.occupational_family_label,
            c.occupational_medium_code,
            c.occupational_medium_label,
            c.occupational_small_code,
            c.occupational_small_label,
            c.occupational_confidence,
            c.occupational_classifier_version,
            c.mandate_network_code,
            c.mandate_network_label,
            c.mandate_family_code,
            c.mandate_family_label,
            c.primary_mandate_network,
            c.primary_mandate_family,
            c.secondary_mandate_families,
            c.mandate_source,
            c.mandate_confidence,
            c.source_native_category,
            c.source_native_job_family,
            c.source_native_job_network,
            c.capability_tags,
            c.capability_classifier_version,
            c.contract_category,
            c.contract_subtype,
            c.contract_confidence,
            c.contract_group,
            c.contract_group_confidence,
            c.seniority_group,
            c.seniority_confidence,
            c.national_international,
            c.national_international_confidence,
            c.grade_system,
            c.grade_family,
            c.grade_code,
            c.grade_level,
            c.staff_category,
            c.min_years_experience,
            c.grade_confidence,
            c.grade_mapping_organization,
            c.grade_mapping_raw_grade_code,
            c.standard_grade_family,
            c.standard_seniority_tier,
            c.standard_scope,
            c.standard_employment_category,
            c.standard_un_equivalent,
            c.standard_experience_range,
            c.standard_role_scope,
            c.standard_supervisory_expectations,
            c.grade_mapping_confidence,
            c.grade_mapping_evidence_type,
            c.grade_mapping_notes,
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
            c.quality_flags,
            c.needs_review,
            c.classification_version,
            c.classified_at
        FROM jobs j
        LEFT JOIN vacancy_classifications c ON c.vacancy_id = j.job_key
    """
    clauses: list[str] = []
    params: list[Any] = []
    _add_filters(clauses, params, filters, use_fts=use_fts)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += (
        " ORDER BY (j.closes_at IS NULL), j.closes_at ASC,"
        " j.posted_at DESC, j.job_key ASC"
    )
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
    use_fts = db.fts_available()
    clauses, params = _search_conditions(request, use_fts=use_fts)
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
            c.grade_mapping_organization,
            c.grade_mapping_raw_grade_code,
            c.standard_grade_family,
            c.standard_seniority_tier,
            c.standard_scope,
            c.standard_employment_category,
            c.standard_un_equivalent,
            c.standard_experience_range,
            c.standard_role_scope,
            c.standard_supervisory_expectations,
            c.grade_mapping_confidence,
            c.grade_mapping_evidence_type,
            c.grade_mapping_notes,
            c.national_international,
            c.contract_category,
            c.contract_subtype,
            c.contract_group,
            c.seniority_group,
            c.ccog_primary_code,
            c.ccog_primary_label,
            c.ccog_family_code,
            c.ccog_family_label,
            c.occupational_family_code,
            c.occupational_family_label,
            c.occupational_medium_code,
            c.occupational_medium_label,
            c.mandate_network_code,
            c.mandate_network_label,
            c.mandate_family_code,
            c.mandate_family_label,
            c.capability_tags,
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
        unclassified_clauses, unclassified_params = _jobs_only_conditions(
            request,
            use_fts=use_fts,
        )
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
    clauses, params = _search_conditions(request, use_fts=db.fts_available())
    where_clause = " WHERE " + " AND ".join(clauses) if clauses else ""
    facet_columns = {
        "grades": "c.grade_code",
        "organizations": "j.org_id",
        "ccog_families": "c.ccog_family_code",
        "occupational_families": "c.occupational_family_code",
        "occupational_mediums": "c.occupational_medium_code",
        "mandate_networks": "c.mandate_network_code",
        "mandate_families": "c.mandate_family_code",
        "contract_groups": "c.contract_group",
        "seniority_groups": "c.seniority_group",
        "contract_categories": "c.contract_category",
        "work_modalities": "c.work_modality",
        "regions": "c.region",
        "unv_categories": "c.unv_category",
        "unv_volunteer_types": "c.unv_volunteer_type",
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
        result["volunteer_kinds"] = _volunteer_kind_facets(conn, where_clause, params)
        result["capability_tags"] = _json_array_facets(
            conn,
            where_clause,
            params,
            "c.capability_tags",
        )
    return result


def build_where_clause(filters: VacancyFilters, *, use_fts: bool = True) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    _add_filters(clauses, params, filters, use_fts=use_fts)
    return (" WHERE " + " AND ".join(clauses), params) if clauses else ("", params)


def _search_conditions(
    request: VacancySearchRequest,
    *,
    use_fts: bool = True,
) -> tuple[list[str], list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if request.status:
        _add_in_clause(clauses, params, "j.status", [status.casefold() for status in request.status])
        if request.exclude_expired_open and "open" in {status.casefold() for status in request.status}:
            clauses.append("(j.closes_at IS NULL OR j.closes_at >= ?)")
            params.append(datetime.now(tz=UTC).isoformat())
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
        scope_parts: list[str] = []
        non_unknown_scopes = [scope for scope in scopes if scope != "unknown"]
        if non_unknown_scopes:
            placeholders = ", ".join("?" for _ in non_unknown_scopes)
            part = f"c.national_international IN ({placeholders})"
            if "international" in non_unknown_scopes and (
                "P" in grade_families or any(code.startswith("P") for code in grade_codes)
            ):
                part = f"({part} OR c.grade_family = 'P')"
            scope_parts.append(part)
            params.extend(non_unknown_scopes)
        if "unknown" in scopes:
            scope_parts.append(
                "(c.national_international IS NULL OR c.national_international = '' "
                "OR c.national_international = 'unknown')"
            )
        clauses.append("(" + " OR ".join(scope_parts) + ")")
    _add_in_clause(clauses, params, "c.ccog_primary_code", request.ccog_codes)
    _add_ccog_family_clause(clauses, params, request.ccog_families)
    _add_in_clause(clauses, params, "c.occupational_family_code", request.occupational_family_codes)
    _add_in_clause(clauses, params, "c.occupational_medium_code", request.occupational_medium_codes)
    _add_in_clause(clauses, params, "c.mandate_network_code", request.mandate_network_codes)
    _add_in_clause(clauses, params, "c.mandate_family_code", request.mandate_family_codes)
    _add_capability_tags_clause(clauses, params, request.capability_tags)
    _add_in_clause(clauses, params, "c.contract_group", request.contract_groups)
    _add_in_clause(clauses, params, "c.seniority_group", request.seniority_groups)
    _add_in_clause(clauses, params, "c.work_modality", request.work_modalities)
    _add_volunteer_kind_clause(clauses, params, request.volunteer_kinds)
    _add_in_clause(clauses, params, "c.unv_category", request.unv_categories)
    _add_in_clause(clauses, params, "c.unv_volunteer_type", request.unv_volunteer_types)
    _add_date_clause(clauses, params, "j.closes_at", ">=", request.closing_date_from)
    _add_date_clause(clauses, params, "j.closes_at", "<=", request.closing_date_to)
    _add_date_clause(clauses, params, "j.posted_at", ">=", request.posted_date_from)
    _add_date_clause(clauses, params, "j.posted_at", "<=", request.posted_date_to)
    if request.text:
        _add_text_clause(
            clauses,
            params,
            request.text,
            include_classification=True,
            use_fts=use_fts,
        )
    location_clause, location_params = _location_exists_clause(request)
    if location_clause:
        clauses.append(location_clause)
        params.extend(location_params)
    return clauses, params


def _jobs_only_conditions(
    request: VacancySearchRequest,
    *,
    use_fts: bool = True,
) -> tuple[list[str], list[Any]]:
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
        _add_text_clause(clauses, params, request.text, use_fts=use_fts)
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
    if column not in _ALLOWED_IN_COLUMNS:
        # ``column`` is interpolated directly into SQL, so it must be a
        # vetted internal literal. The allowlist is module-level and easy
        # to grep/audit; reject anything else loudly.
        raise ValueError(f"Refusing to build IN clause for unknown column: {column!r}")
    cleaned = [value for value in values if value not in (None, "")]
    if not cleaned:
        return
    placeholders = ", ".join("?" for _ in cleaned)
    clauses.append(f"{column} IN ({placeholders})")
    params.extend(cleaned)


# Columns currently passed to ``_add_in_clause``. Keep alphabetised so the
# diff is easy to review when a new filter facet is added.
_ALLOWED_IN_COLUMNS: frozenset[str] = frozenset(
    {
        "c.contract_category",
        "c.contract_group",
        "c.grade_code",
        "c.grade_family",
        "c.grade_system",
        "c.mandate_family_code",
        "c.mandate_network_code",
        "c.national_international",
        "c.occupational_family_code",
        "c.occupational_medium_code",
        "c.ccog_primary_code",
        "c.seniority_group",
        "c.unv_category",
        "c.unv_volunteer_type",
        "c.work_modality",
        "city_key",
        "country_iso3",
        "j.ats_family",
        "j.org_id",
        "j.source_id",
        "j.status",
        "l.city_key",
        "l.country_iso3",
        "l.location_type",
        "l.region",
        "location_type",
        "region",
    }
)


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


def _add_json_array_contains_clause(
    clauses: list[str],
    params: list[Any],
    column: str,
    values: list[str],
) -> None:
    cleaned = [value for value in values if value]
    if not cleaned:
        return
    parts = []
    for value in cleaned:
        parts.append(f"{column} LIKE ?")
        params.append(f'%"{value}"%')
    clauses.append("(" + " OR ".join(parts) + ")")


def _add_capability_tags_clause(
    clauses: list[str],
    params: list[Any],
    values: list[str],
) -> None:
    cleaned = [value.strip() for value in values if value and value.strip()]
    if not cleaned:
        return
    parts = []
    for value in cleaned:
        for term in _capability_search_terms(value):
            parts.append("c.capability_tags LIKE ?")
            params.append(f"%{term}%")
    clauses.append("(" + " OR ".join(parts) + ")")


def _capability_search_terms(value: str) -> list[str]:
    normalized = re.sub(r"[\s/-]+", "_", value.casefold().strip()).strip("_")
    compact = value.casefold().strip()
    terms = [term for term in (compact, normalized) if term]
    return list(dict.fromkeys(terms))


def _add_volunteer_kind_clause(
    clauses: list[str],
    params: list[Any],
    values: list[str],
) -> None:
    cleaned = {value for value in values if value}
    if not cleaned:
        return
    parts: list[str] = []
    if "un_volunteer" in cleaned:
        parts.append(_UN_VOLUNTEER_SQL)
    if "volunteer" in cleaned:
        parts.append(_GENERIC_VOLUNTEER_SQL)
    if parts:
        clauses.append("(" + " OR ".join(parts) + ")")


_UN_VOLUNTEER_SQL = (
    "(j.source_id = 'unv_uvp' OR c.contract_category = 'volunteering_unv' "
    "OR (c.unv_category IS NOT NULL AND c.unv_category <> '' AND c.unv_category <> 'unknown'))"
)

_GENERIC_VOLUNTEER_SQL = (
    "((c.contract_group IN ('volunteer', 'roster_pipeline', 'other') "
    "OR c.contract_category LIKE '%volunteer%' "
    "OR c.standard_employment_category LIKE '%Volunteer%') "
    f"AND NOT {_UN_VOLUNTEER_SQL})"
)


def _volunteer_kind_facets(
    conn: Any,
    where_clause: str,
    params: list[Any],
) -> dict[str, int]:
    result: dict[str, int] = {}
    queries = {
        "un_volunteer": f"""
            SELECT COUNT(DISTINCT j.job_key) AS count
            FROM jobs j
            JOIN vacancy_classifications c ON c.vacancy_id = j.job_key
            {where_clause}
            {"AND" if where_clause else "WHERE"} {_UN_VOLUNTEER_SQL}
        """,
        "volunteer": f"""
            SELECT COUNT(DISTINCT j.job_key) AS count
            FROM jobs j
            JOIN vacancy_classifications c ON c.vacancy_id = j.job_key
            {where_clause}
            {"AND" if where_clause else "WHERE"} {_GENERIC_VOLUNTEER_SQL}
        """,
    }
    for key, query in queries.items():
        count = int(conn.execute(query, tuple(params)).fetchone()["count"])
        if count:
            result[key] = count
    return result


def _json_array_facets(
    conn: Any,
    where_clause: str,
    params: list[Any],
    column: str,
) -> dict[str, int]:
    if column not in {"c.capability_tags"}:
        raise ValueError(f"Refusing JSON facet for unknown column: {column!r}")
    query = f"""
        SELECT json_each.value AS value, COUNT(DISTINCT j.job_key) AS count
        FROM jobs j
        JOIN vacancy_classifications c ON c.vacancy_id = j.job_key
        JOIN json_each({column})
        {where_clause}
        GROUP BY json_each.value
        ORDER BY count DESC, value
    """
    try:
        rows = conn.execute(query, tuple(params)).fetchall()
    except Exception:
        return {}
    return {
        str(row["value"]): int(row["count"])
        for row in rows
        if row["value"] not in (None, "")
    }


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
    # We previously wrapped both sides in ``date(...)`` to coerce stored
    # ISO-8601 timestamps to plain dates. That defeats any index on
    # ``closes_at`` / ``posted_at``. Stored values sort correctly as strings,
    # so we use plain comparisons and only translate date-only upper bounds
    # to the start of the next day so the comparison remains inclusive.
    if _is_date_only(normalized):
        if operator in ("<=", "<"):
            normalized = _next_day_iso(normalized) + "T00:00:00+00:00"
            clauses.append(f"{column} < ?")
            params.append(normalized)
            return
        normalized = normalized + "T00:00:00+00:00"
    clauses.append(f"{column} {operator} ?")
    params.append(normalized)


def _is_date_only(value: str) -> bool:
    return len(value) == 10 and value[4] == "-" and value[7] == "-"


def _next_day_iso(value: str) -> str:
    from datetime import date as _date, timedelta

    parsed = _date.fromisoformat(value)
    return (parsed + timedelta(days=1)).isoformat()


def _normalize_country_iso3(value: str) -> str | None:
    country = country_info(value)
    return country.iso3 if country else value.upper().strip()


def _order_by(request: VacancySearchRequest) -> str:
    if request.sort == "posted_date_desc":
        return (
            "ORDER BY j.posted_at IS NULL, j.posted_at DESC,"
            " j.closes_at ASC, j.job_key ASC"
        )
    if request.sort == "closing_date_desc":
        return (
            "ORDER BY j.closes_at IS NULL, j.closes_at DESC,"
            " j.posted_at DESC, j.job_key ASC"
        )
    return (
        "ORDER BY j.closes_at IS NULL, j.closes_at ASC,"
        " j.posted_at DESC, j.job_key ASC"
    )


def _add_filters(
    clauses: list[str],
    params: list[Any],
    filters: VacancyFilters,
    *,
    use_fts: bool = True,
) -> None:
    if filters.only_active:
        clauses.append("j.status = 'open'")
    mapping = {
        "organization": ("j.org_id", filters.organization),
        "source_id": ("j.source_id", filters.source_id),
        "ats_family": ("j.ats_family", filters.ats_family),
        "ccog_part": ("c.ccog_part", filters.ccog_part),
        "occupational_family_code": ("c.occupational_family_code", filters.occupational_family_code),
        "occupational_medium_code": ("c.occupational_medium_code", filters.occupational_medium_code),
        "mandate_network_code": ("c.mandate_network_code", filters.mandate_network_code),
        "mandate_family_code": ("c.mandate_family_code", filters.mandate_family_code),
        "contract_group": ("c.contract_group", filters.contract_group),
        "seniority_group": ("c.seniority_group", filters.seniority_group),
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
    if filters.capability_tag:
        terms = _capability_search_terms(filters.capability_tag)
        if terms:
            clauses.append("(" + " OR ".join("c.capability_tags LIKE ?" for _ in terms) + ")")
            params.extend(f"%{term}%" for term in terms)
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
        _add_text_clause(
            clauses,
            params,
            filters.text,
            include_department=True,
            include_classification=True,
            use_fts=use_fts,
        )


def _row_to_dict(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["raw"] = json.loads(data.pop("raw_json") or "{}")
    for field_name in (
        "unv_expertise_areas",
        "secondary_mandate_families",
        "capability_tags",
        "quality_flags",
    ):
        if data.get(field_name):
            data[field_name] = json.loads(data[field_name])
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
        "grade_mapping_organization": data["grade_mapping_organization"],
        "grade_mapping_raw_grade_code": data["grade_mapping_raw_grade_code"],
        "standard_grade_family": data["standard_grade_family"],
        "standard_seniority_tier": data["standard_seniority_tier"],
        "standard_scope": data["standard_scope"],
        "standard_employment_category": data["standard_employment_category"],
        "standard_un_equivalent": data["standard_un_equivalent"],
        "standard_experience_range": data["standard_experience_range"],
        "grade_mapping_confidence": data["grade_mapping_confidence"],
        "national_international": data["national_international"],
        "contract_category": data["contract_category"],
        "contract_subtype": data["contract_subtype"],
        "ccog_primary_code": data["ccog_primary_code"],
        "ccog_primary_label": data["ccog_primary_label"],
        "ccog_family_code": data["ccog_family_code"],
        "ccog_family_label": data["ccog_family_label"],
        "occupational_family_code": data["occupational_family_code"],
        "occupational_family_label": data["occupational_family_label"],
        "occupational_medium_code": data["occupational_medium_code"],
        "occupational_medium_label": data["occupational_medium_label"],
        "mandate_network_code": data["mandate_network_code"],
        "mandate_network_label": data["mandate_network_label"],
        "mandate_family_code": data["mandate_family_code"],
        "mandate_family_label": data["mandate_family_label"],
        "capability_tags": json.loads(data["capability_tags"] or "[]"),
        "contract_group": data["contract_group"],
        "seniority_group": data["seniority_group"],
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
