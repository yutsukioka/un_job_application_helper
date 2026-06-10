"""Facet count helpers."""

from __future__ import annotations

from typing import Any

from jobagg.db import JobDatabase
from jobagg.filters.query import build_where_clause
from jobagg.filters.schemas import VacancyFilters


FACET_COLUMNS = {
    "contract_categories": "c.contract_category",
    "grade_families": "c.grade_family",
    "regions": "c.region",
    "work_modalities": "c.work_modality",
    "national_international": "c.national_international",
    "unv_categories": "c.unv_category",
    "ccog_families": "c.ccog_family_code",
    "occupational_families": "c.occupational_family_code",
    "occupational_mediums": "c.occupational_medium_code",
    "mandate_networks": "c.mandate_network_code",
    "mandate_families": "c.mandate_family_code",
    "contract_groups": "c.contract_group",
    "seniority_groups": "c.seniority_group",
}


def facet_counts(db: JobDatabase, filters: VacancyFilters) -> dict[str, dict[str, int]]:
    where_clause, params = build_where_clause(filters)
    result: dict[str, dict[str, int]] = {}
    with db.connect() as conn:
        for facet_name, column in FACET_COLUMNS.items():
            query = f"""
                SELECT {column} AS value, COUNT(*) AS count
                FROM jobs j
                LEFT JOIN vacancy_classifications c ON c.vacancy_id = j.job_key
                {where_clause}
                GROUP BY {column}
                ORDER BY count DESC, value
            """
            rows = conn.execute(query, tuple(params)).fetchall()
            result[facet_name] = _counts(rows)
    return result


def _counts(rows: list[Any]) -> dict[str, int]:
    return {str(row["value"]): int(row["count"]) for row in rows if row["value"] not in (None, "")}
