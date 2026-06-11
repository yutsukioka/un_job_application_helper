"""FastAPI entry point for the local job search API."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from jobagg.db import JobDatabase
from jobagg.filters.query import search_collected_jobs
from jobagg.filters.saved_searches import (
    get_saved_search,
    list_saved_searches,
    remove_saved_search,
    save_search,
)
from jobagg.filters.schemas import VacancySearchRequest
from jobagg.scoring import load_strategy_signals, score_jobs

from job_api.config import ApiSettings, load_settings
from job_api.models import (
    ApplicationRecord,
    AssistantRunRequest,
    AssistantRunResult,
    SavedSearchModel,
    SearchRequest,
    SearchResponse,
)
from job_api.tracker import create_record, delete_record, list_records, upsert_record


def create_app(settings: ApiSettings | None = None) -> FastAPI:
    settings = settings or load_settings()
    app = FastAPI(title="UN Job Application Helper API", version="0.1.0")

    def db() -> JobDatabase:
        return JobDatabase(settings.db_path)

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        if not settings.db_path.exists():
            return {
                "status": "missing_db",
                "db_path": str(settings.db_path),
                "schema_version": "unknown",
                "open_jobs": 0,
                "enabled_sources": 0,
                "last_sync_at": None,
            }
        with sqlite3.connect(settings.db_path) as conn:
            open_jobs = _scalar(conn, "SELECT COUNT(*) FROM jobs WHERE status = 'open'")
            enabled_sources = _scalar(conn, "SELECT COUNT(DISTINCT source_id) FROM jobs")
            last_sync_at = _scalar(conn, "SELECT MAX(observed_at) FROM source_runs")
        return {
            "status": "ok",
            "db_path": str(settings.db_path),
            "schema_version": "jobagg-sqlite",
            "open_jobs": open_jobs,
            "enabled_sources": enabled_sources,
            "last_sync_at": last_sync_at,
        }

    @app.post("/api/search", response_model=SearchResponse)
    def search(request: SearchRequest) -> SearchResponse:
        _require_db(settings.db_path)
        job_request = _to_jobagg_request(request)
        response = search_collected_jobs(db(), job_request, include_facets=request.include_facets)
        payload = asdict(response)
        if request.score_against:
            signals = load_strategy_signals(request.score_against)
            results = score_jobs(payload["results"], signals)
            if request.min_score is not None:
                results = [row for row in results if row.get("score", 0) >= request.min_score]
                payload["total"] = len(results)
            payload["results"] = results
        return SearchResponse(**payload)

    @app.get("/api/jobs/{job_key}")
    def job_detail(job_key: str) -> dict[str, Any]:
        _require_db(settings.db_path)
        job = db().get_job(job_key)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Unknown job_key: {job_key}")
        job["locations"] = list(db().iter_vacancy_locations(job_key))
        return job

    @app.get("/api/facets")
    def facets() -> dict[str, dict[str, int]]:
        _require_db(settings.db_path)
        response = search_collected_jobs(db(), VacancySearchRequest(limit=0), include_facets=True)
        return response.facets

    @app.post("/api/facets")
    def filtered_facets(request: SearchRequest) -> dict[str, dict[str, int]]:
        _require_db(settings.db_path)
        response = search_collected_jobs(db(), _to_jobagg_request(request), include_facets=True)
        return response.facets

    @app.get("/api/taxonomies")
    def taxonomies() -> dict[str, Any]:
        _require_db(settings.db_path)
        return _taxonomy_metadata(settings.db_path)

    @app.get("/api/saved-searches")
    def saved_searches() -> list[dict[str, Any]]:
        return [search.to_dict() for search in list_saved_searches(settings.saved_searches_path)]

    @app.post("/api/saved-searches")
    def upsert_saved_search(search: SavedSearchModel) -> dict[str, Any]:
        saved = save_search(
            settings.saved_searches_path,
            name=search.name,
            request=_to_jobagg_request(search.request),
            description=search.summary or None,
            overwrite=True,
        )
        return saved.to_dict()

    @app.post("/api/saved-searches/{name}/run", response_model=SearchResponse)
    def run_saved_search(name: str) -> SearchResponse:
        _require_db(settings.db_path)
        try:
            saved = get_saved_search(settings.saved_searches_path, name)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        response = search_collected_jobs(db(), saved.request, include_facets=True)
        return SearchResponse(**asdict(response))

    @app.delete("/api/saved-searches/{name}")
    def delete_saved_search(name: str) -> dict[str, bool]:
        return {"deleted": remove_saved_search(settings.saved_searches_path, name)}

    @app.get("/api/updates")
    def updates() -> dict[str, Any]:
        _require_db(settings.db_path)
        with sqlite3.connect(settings.db_path) as conn:
            conn.row_factory = sqlite3.Row
            recent_runs = [dict(row) for row in conn.execute(
                """
                SELECT source_id, fetched, inserted, updated, missing, closed, observed_at
                FROM source_runs
                ORDER BY observed_at DESC
                LIMIT 25
                """
            )]
        return {"recent_source_runs": recent_runs}

    @app.post("/api/sync/run")
    def run_sync() -> dict[str, str]:
        raise HTTPException(status_code=501, detail="Sync orchestration is not exposed yet.")

    @app.get("/api/sync/runs")
    def sync_runs() -> list[dict[str, Any]]:
        _require_db(settings.db_path)
        return list(db().iter_source_runs())

    @app.get("/api/tracker", response_model=list[ApplicationRecord])
    def tracker_records() -> list[ApplicationRecord]:
        return list_records(settings.tracker_path)

    @app.post("/api/tracker", response_model=ApplicationRecord)
    def add_tracker_record(record: ApplicationRecord) -> ApplicationRecord:
        return upsert_record(settings.tracker_path, record)

    @app.post("/api/tracker/jobs/{job_key}", response_model=ApplicationRecord)
    def save_job(job_key: str) -> ApplicationRecord:
        return create_record(settings.tracker_path, job_key)

    @app.delete("/api/tracker/{record_id}")
    def remove_tracker_record(record_id: str) -> dict[str, bool]:
        return {"deleted": delete_record(settings.tracker_path, record_id)}

    @app.post("/api/assistant/runs", response_model=AssistantRunResult)
    def create_assistant_run(request: AssistantRunRequest) -> AssistantRunResult:
        return AssistantRunResult(
            id="not-implemented",
            status="not_implemented",
            agent_mode=request.agent_mode,
            message="Assistant document generation is contracted but not wired to runtime execution yet.",
        )

    return app


def _to_jobagg_request(request: SearchRequest) -> VacancySearchRequest:
    data = request.model_dump(exclude={"include_facets", "include_explain", "score_against", "min_score"})
    return VacancySearchRequest(**data)


def _require_db(path: Path) -> None:
    if not path.exists():
        raise HTTPException(status_code=503, detail=f"Job database does not exist: {path}")


def _scalar(conn: sqlite3.Connection, query: str) -> Any:
    try:
        return conn.execute(query).fetchone()[0]
    except sqlite3.Error:
        return None


def _taxonomy_metadata(db_path: Path) -> dict[str, Any]:
    queries = {
        "organizations": "SELECT DISTINCT org_id AS value FROM jobs ORDER BY org_id",
        "source_ids": "SELECT DISTINCT source_id AS value FROM jobs ORDER BY source_id",
        "ats_families": "SELECT DISTINCT ats_family AS value FROM jobs ORDER BY ats_family",
        "countries": "SELECT DISTINCT country_iso3 AS value FROM vacancy_classifications ORDER BY country_iso3",
        "cities": "SELECT DISTINCT city AS value FROM vacancy_classifications ORDER BY city",
        "grade_codes": "SELECT DISTINCT grade_code AS value FROM vacancy_classifications ORDER BY grade_code",
        "contract_groups": "SELECT DISTINCT contract_group AS value FROM vacancy_classifications ORDER BY contract_group",
        "seniority_groups": "SELECT DISTINCT seniority_group AS value FROM vacancy_classifications ORDER BY seniority_group",
        "work_modalities": "SELECT DISTINCT work_modality AS value FROM vacancy_classifications ORDER BY work_modality",
        "ccog_families": "SELECT DISTINCT ccog_family_code AS value FROM vacancy_classifications ORDER BY ccog_family_code",
        "capability_tags": "SELECT DISTINCT capability_tags AS value FROM vacancy_classifications ORDER BY capability_tags",
    }
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return {name: _values(conn, query) for name, query in queries.items()}


def _values(conn: sqlite3.Connection, query: str) -> list[str]:
    try:
        return [row["value"] for row in conn.execute(query) if row["value"] not in (None, "")]
    except sqlite3.Error:
        return []


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run("job_api.app:app", host="127.0.0.1", port=8765, reload=False)


if __name__ == "__main__":
    main()
