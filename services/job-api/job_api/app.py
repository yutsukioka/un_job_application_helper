"""FastAPI entry point for the local job search API."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import unquote

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from jobagg.atomic_json_store import AtomicJsonStoreError
from jobagg.db import JobDatabase
from jobagg.filters.query import search_collected_jobs
from jobagg.filters.saved_searches import (
    SavedSearch,
    compare_and_remove_saved_search,
    get_saved_search,
    list_saved_searches,
    remove_saved_search,
    save_search,
)
from jobagg.filters.schemas import VacancySearchRequest
from jobagg.scoring import load_strategy_signals, score_jobs
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from job_api.config import ApiSettings, load_settings
from job_api.models import (
    ApplicationRecord,
    AssistantRunRequest,
    AssistantRunResult,
    ConditionalDeleteResponse,
    SavedSearchConditionalDeleteRequest,
    SavedSearchModel,
    SearchRequest,
    SearchResponse,
    TrackerConditionalDeleteRequest,
)
from job_api.tracker import (
    compare_and_delete_record,
    create_record,
    delete_record,
    list_records,
    upsert_record,
)

_CONDITIONAL_DELETE_PATH = re.compile(
    r"^/api/(?:saved-searches/.*|tracker/[^/]+)/conditional-delete$"
)


def create_app(settings: ApiSettings | None = None) -> FastAPI:
    settings = settings or load_settings()
    app = FastAPI(title="UN Job Application Helper API", version="0.1.0")

    @app.exception_handler(RequestValidationError)
    async def conditional_delete_validation_error(
        request: Request,
        exc: RequestValidationError,
    ) -> Any:
        if request.method == "POST" and _CONDITIONAL_DELETE_PATH.fullmatch(
            request.url.path
        ):
            return JSONResponse(
                status_code=422,
                content={"detail": "Conditional delete request is invalid."},
            )
        return await request_validation_exception_handler(request, exc)

    @app.exception_handler(StarletteHTTPException)
    async def conditional_delete_body_decode_error(
        request: Request,
        exc: StarletteHTTPException,
    ) -> Any:
        if (
            request.method == "POST"
            and _CONDITIONAL_DELETE_PATH.fullmatch(request.url.path)
            and exc.status_code == 400
            and exc.detail == "There was an error parsing the body"
        ):
            return JSONResponse(
                status_code=422,
                content={"detail": "Conditional delete request is invalid."},
            )
        return await http_exception_handler(request, exc)

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
        payload["facet_labels"] = _facet_labels(settings.db_path, payload.get("facets") or {})
        if request.score_against:
            signals = load_strategy_signals(request.score_against)
            results = score_jobs(payload["results"], signals)
            if request.min_score is not None:
                results = [row for row in results if row.get("score", 0) >= request.min_score]
                payload["total"] = len(results)
            payload["results"] = results
        return SearchResponse(**payload)

    @app.get("/api/job-detail")
    def job_detail_query(job_key: str) -> dict[str, Any]:
        return _job_detail_payload(settings.db_path, db(), job_key)

    @app.get("/api/jobs/by-key")
    def job_detail_by_key(job_key: str) -> dict[str, Any]:
        return _job_detail_payload(settings.db_path, db(), job_key)

    @app.get("/api/jobs/{job_key}")
    def job_detail(job_key: str) -> dict[str, Any]:
        return _job_detail_payload(settings.db_path, db(), job_key)

    @app.get("/api/jobs/path/{job_key:path}")
    def job_detail_path(job_key: str) -> dict[str, Any]:
        return _job_detail_payload(settings.db_path, db(), job_key)

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
        payload = asdict(response)
        payload["facet_labels"] = _facet_labels(settings.db_path, payload.get("facets") or {})
        return SearchResponse(**payload)

    @app.delete("/api/saved-searches/{name}")
    def delete_saved_search(name: str) -> dict[str, bool]:
        return {"deleted": remove_saved_search(settings.saved_searches_path, name)}

    @app.post(
        "/api/saved-searches/{name:path}/conditional-delete",
        response_model=ConditionalDeleteResponse,
    )
    def conditional_delete_saved_search(
        name: str,
        body: Annotated[Any, Body()],
    ) -> ConditionalDeleteResponse:
        try:
            request = SavedSearchConditionalDeleteRequest.model_validate(body)
        except ValidationError:
            raise HTTPException(
                status_code=422, detail="Conditional delete request is invalid."
            ) from None
        expected = request.expected
        if name != expected.name:
            raise HTTPException(
                status_code=400, detail="Conditional delete identity mismatch."
            )
        expected_search = SavedSearch(
            name=expected.name,
            description=expected.description,
            request=_to_jobagg_request(expected.request),
            created_at=expected.created_at,
            updated_at=expected.updated_at,
        )
        try:
            outcome = compare_and_remove_saved_search(
                settings.saved_searches_path,
                name=name,
                expected=expected_search,
            )
        except AtomicJsonStoreError:
            raise HTTPException(
                status_code=500, detail="Private-state store operation failed."
            ) from None
        if outcome == "mismatch":
            raise HTTPException(
                status_code=412, detail="Conditional delete precondition failed."
            )
        return ConditionalDeleteResponse(outcome=outcome)

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

    @app.get("/api/sources")
    def sources() -> dict[str, Any]:
        _require_db(settings.db_path)
        return {"sources": _source_summaries(settings.db_path)}

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
        return create_record(settings.tracker_path, unquote(job_key))

    @app.delete("/api/tracker/{record_id}")
    def remove_tracker_record(record_id: str) -> dict[str, bool]:
        return {"deleted": delete_record(settings.tracker_path, record_id)}

    @app.post(
        "/api/tracker/{record_id}/conditional-delete",
        response_model=ConditionalDeleteResponse,
    )
    def conditional_delete_tracker_record(
        record_id: str,
        body: Annotated[Any, Body()],
    ) -> ConditionalDeleteResponse:
        try:
            request = TrackerConditionalDeleteRequest.model_validate(body)
            expected_payload = request.expected.model_dump()
            expected = ApplicationRecord.model_validate(expected_payload)
        except ValidationError:
            raise HTTPException(
                status_code=422, detail="Conditional delete request is invalid."
            ) from None
        if expected.model_dump(mode="json") != expected_payload:
            raise HTTPException(
                status_code=422, detail="Conditional delete request is invalid."
            )
        if not record_id or record_id != expected.id:
            raise HTTPException(
                status_code=400, detail="Conditional delete identity mismatch."
            )
        try:
            outcome = compare_and_delete_record(
                settings.tracker_path,
                record_id=record_id,
                expected=expected,
            )
        except AtomicJsonStoreError:
            raise HTTPException(
                status_code=500, detail="Private-state store operation failed."
            ) from None
        if outcome == "mismatch":
            raise HTTPException(
                status_code=412, detail="Conditional delete precondition failed."
            )
        return ConditionalDeleteResponse(outcome=outcome)

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


def _job_detail_payload(db_path: Path, database: JobDatabase, job_key: str) -> dict[str, Any]:
    _require_db(db_path)
    job_key = unquote(job_key)
    job = database.get_job(job_key)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Unknown job_key: {job_key}")
    job["locations"] = list(database.iter_vacancy_locations(job_key))
    job["classification"] = _job_classification(db_path, job_key)
    job["source_features"] = _job_source_features(db_path, job_key)
    job["deadline_info"] = _deadline_info(job)
    job["display_sections"] = _job_display_sections(job)
    return job


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
        "unv_categories": "SELECT DISTINCT unv_category AS value FROM vacancy_classifications ORDER BY unv_category",
        "unv_volunteer_types": "SELECT DISTINCT unv_volunteer_type AS value FROM vacancy_classifications ORDER BY unv_volunteer_type",
    }
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return {name: _values(conn, query) for name, query in queries.items()}


def _values(conn: sqlite3.Connection, query: str) -> list[str]:
    try:
        return [row["value"] for row in conn.execute(query) if row["value"] not in (None, "")]
    except sqlite3.Error:
        return []


def _facet_labels(db_path: Path, facets: dict[str, dict[str, int]]) -> dict[str, dict[str, str]]:
    labels: dict[str, dict[str, str]] = {}
    if not facets:
        return labels
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        labels["organizations"] = _label_map(
            conn,
            """
            SELECT org_id AS value, COALESCE(MAX(raw_json_extract), org_id) AS label
            FROM (
                SELECT
                    org_id,
                    json_extract(raw_json, '$.organization') AS raw_json_extract
                FROM jobs
            )
            GROUP BY org_id
            """,
        )
        labels["ccog_families"] = _label_map(
            conn,
            """
            SELECT ccog_family_code AS value, ccog_family_label AS label
            FROM vacancy_classifications
            WHERE ccog_family_code IS NOT NULL
              AND ccog_family_code <> ''
              AND ccog_family_label IS NOT NULL
              AND ccog_family_label <> ''
            GROUP BY ccog_family_code
            """,
        )
        for key in (
            "contract_groups",
            "volunteer_kinds",
            "seniority_groups",
            "work_modalities",
            "regions",
            "grades",
            "contract_categories",
            "occupational_families",
            "occupational_mediums",
            "mandate_networks",
            "mandate_families",
            "unv_categories",
            "unv_volunteer_types",
        ):
            labels.setdefault(key, {})
        labels["volunteer_kinds"].update(
            {
                "un_volunteer": "UN Volunteer",
                "volunteer": "Volunteer",
            }
        )
        labels["unv_categories"].update(_unv_category_labels())
        labels["unv_volunteer_types"].update(_unv_volunteer_type_labels())
    for key, values in facets.items():
        target = labels.setdefault(key, {})
        for value in values:
            target.setdefault(value, _display_label(value))
    return labels


def _label_map(conn: sqlite3.Connection, query: str) -> dict[str, str]:
    try:
        return {
            str(row["value"]): str(row["label"])
            for row in conn.execute(query)
            if row["value"] not in (None, "") and row["label"] not in (None, "")
        }
    except sqlite3.Error:
        return {}


def _job_classification(db_path: Path, job_key: str) -> dict[str, Any]:
    return _one_row(
        db_path,
        "SELECT * FROM vacancy_classifications WHERE vacancy_id = ?",
        (job_key,),
        json_fields={
            "secondary_mandate_families",
            "capability_tags",
            "capability_tag_scores",
            "capability_tag_evidence",
            "quality_flags",
            "evidence",
            "occupational_evidence",
            "mandate_evidence",
            "contract_group_evidence",
            "seniority_evidence",
            "unv_expertise_areas",
        },
        bool_fields={"needs_review"},
    )


def _job_source_features(db_path: Path, job_key: str) -> dict[str, Any]:
    return _one_row(
        db_path,
        "SELECT * FROM vacancy_source_features WHERE vacancy_id = ?",
        (job_key,),
        json_fields={"source_unv_expertise_areas", "evidence"},
    )


def _one_row(
    db_path: Path,
    query: str,
    params: tuple[Any, ...],
    *,
    json_fields: set[str] | None = None,
    bool_fields: set[str] | None = None,
) -> dict[str, Any]:
    json_fields = json_fields or set()
    bool_fields = bool_fields or set()
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(query, params).fetchone()
    if row is None:
        return {}
    data = dict(row)
    for field in json_fields:
        if field in data:
            data[field] = _json_value(data[field])
    for field in bool_fields:
        if field in data and data[field] is not None:
            data[field] = bool(data[field])
    return data


def _job_display_sections(job: dict[str, Any]) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    raw = job.get("raw") or {}
    raw_sections = _raw_field_sections(raw if isinstance(raw, dict) else {})
    sections.extend(raw_sections)
    existing_bodies = {
        str(section.get("body") or "").strip()
        for section in raw_sections
        if section.get("body")
    }
    description = str(job.get("description") or "").strip()
    if description and _clean_display_text(description) not in existing_bodies:
        structured = _structured_description_sections(description, str(job.get("ats_family") or ""))
        sections.extend(structured or [{"title": "Full Description", "body": _clean_display_text(description)}])
    sections.append({"title": "Job Record", "rows": _display_rows(job, exclude={"raw", "locations", "classification", "source_features", "deadline_info", "display_sections"})})
    if job.get("classification"):
        sections.append({"title": "Classification", "rows": _display_rows(job["classification"])})
    if job.get("locations"):
        sections.append({"title": "Locations", "body": _pretty_json(job["locations"])})
    if job.get("source_features"):
        sections.append({"title": "Source Features", "rows": _display_rows(job["source_features"])})
    if raw:
        sections.append({"title": "Raw Source Data", "body": _pretty_json(raw)})
    return sections


_RAW_SECTION_FIELDS = (
    ("ShortDescriptionStr", "Summary"),
    ("ExternalDescriptionStr", "Description"),
    ("ExternalResponsibilitiesStr", "Responsibilities"),
    ("ExternalQualificationsStr", "Qualifications"),
    ("externalDescription", "Description"),
    ("responsibilities", "Responsibilities"),
    ("qualifications", "Qualifications"),
    ("requirements", "Requirements"),
)


def _raw_field_sections(raw: dict[str, Any]) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key, title in _RAW_SECTION_FIELDS:
        value = raw.get(key)
        if not isinstance(value, str):
            continue
        body = _clean_display_text(value)
        if not body or _not_available(body) or body in seen:
            continue
        seen.add(body)
        sections.append({"title": title, "body": _format_detail_body(body)})
    return sections


_COMMON_DESCRIPTION_HEADINGS = (
    "Org. Setting and Reporting",
    "Organizational Setting and Reporting Relationship",
    "Organizational Setting",
    "Duties and Responsibilities",
    "Key Functions",
    "Key Responsibilities",
    "Responsibilities",
    "Competencies",
    "Education",
    "Job - Specific Qualification",
    "Job Specific Qualification",
    "Qualifications",
    "Required Qualifications",
    "Required Skills and Experience",
    "Selection Criteria",
    "Work Experience",
    "Languages",
    "Assessment",
    "Special Notice",
    "United Nations Considerations",
    "No Fee",
    "Your Role",
    "You will",
    "Benefits",
    "Additional Information",
    "Primary Location",
    "Department",
    "Division",
    "Staff Category",
    "Position Level",
    "Grade Level",
    "Type of Requisition",
    "Vacancy Type",
    "Contract type",
    "Grade",
    "Remuneration",
    "Rémunération",
    "Closing Date",
)


def _structured_description_sections(description: str, ats_family: str) -> list[dict[str, Any]]:
    del ats_family
    text = _clean_display_text(description)
    if not text:
        return []
    headings = sorted(_COMMON_DESCRIPTION_HEADINGS, key=len, reverse=True)
    heading_pattern = "|".join(re.escape(heading) for heading in headings)
    pattern = re.compile(
        rf"(?<!\w)({heading_pattern})(?:\s*:|\s*\n|\s{{2,}}|\s+(?=[A-Z0-9]))",
        flags=re.IGNORECASE,
    )
    matches = list(pattern.finditer(text))
    if len(matches) < 2:
        return []
    sections: list[dict[str, Any]] = []
    intro = text[: matches[0].start()].strip()
    if intro and not _not_available(intro):
        _append_detail_section(sections, "Summary", intro)
    for index, match in enumerate(matches):
        title = _canonical_heading(match.group(1))
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[start:end].strip(" :-\n\t")
        if body and not _not_available(body):
            _append_detail_section(sections, title, _format_detail_body(body))
    return sections


def _append_detail_section(sections: list[dict[str, Any]], title: str, body: str) -> None:
    clean_body = body.strip()
    if not clean_body:
        return
    if sections and sections[-1].get("title") == title:
        existing = str(sections[-1].get("body") or "").strip()
        if clean_body not in existing:
            sections[-1]["body"] = f"{existing}\n\n{clean_body}".strip()
        return
    sections.append({"title": title, "body": clean_body})


def _canonical_heading(value: str) -> str:
    normalized = " ".join(value.replace(" - ", " ").split()).casefold()
    for heading in _COMMON_DESCRIPTION_HEADINGS:
        if " ".join(heading.replace(" - ", " ").split()).casefold() == normalized:
            return heading
    return _display_label(value)


def _clean_display_text(value: str) -> str:
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _format_detail_body(value: str) -> str:
    text = _clean_display_text(value)
    text = re.sub(r"\s+(?=[•*-]\s)", "\n", text)
    text = re.sub(r"\s+(?=\d+[.)]\s)", "\n", text)
    return text.strip()


def _not_available(value: str) -> bool:
    normalized = " ".join(value.casefold().split()).strip(" .:-")
    return normalized in {"not available", "n/a", "na", "none", "not applicable"}


def _deadline_info(job: dict[str, Any]) -> dict[str, Any]:
    raw = job.get("raw") if isinstance(job.get("raw"), dict) else {}
    source_text = _source_deadline_text(raw)
    return {
        "stored_utc": job.get("closes_at"),
        "source_local": job.get("closes_at_local"),
        "source_timezone": job.get("closes_tz"),
        "source_text": source_text,
    }


def _source_deadline_text(raw: dict[str, Any]) -> str | None:
    candidates: list[str] = []

    def visit(value: Any, path: str = "") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                next_path = f"{path}.{key}" if path else str(key)
                if _deadline_key(str(key)) and child not in (None, "", [], {}):
                    candidates.append(f"{_display_label(str(key))}: {_display_value(child)}")
                visit(child, next_path)
        elif isinstance(value, list):
            for item in value:
                visit(item, path)

    visit(raw)
    if not candidates:
        return None
    return candidates[0]


def _deadline_key(key: str) -> bool:
    normalized = key.casefold().replace("_", " ")
    return any(
        token in normalized
        for token in (
            "closing",
            "deadline",
            "posted end",
            "posting end",
            "externalpostedenddate",
            "end date",
        )
    )


def _display_rows(data: dict[str, Any], *, exclude: set[str] | None = None) -> list[dict[str, str]]:
    exclude = exclude or set()
    rows: list[dict[str, str]] = []
    for key in sorted(data):
        if key in exclude:
            continue
        value = data[key]
        if value in (None, "", [], {}):
            continue
        rows.append({"label": _display_label(key), "value": _display_value(value)})
    return rows


def _display_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return _pretty_json(value)
    return str(value)


def _pretty_json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False)


def _json_value(value: Any) -> Any:
    if value in (None, ""):
        return value
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value


def _source_summaries(db_path: Path) -> list[dict[str, Any]]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT
                j.source_id,
                COALESCE(NULLIF(j.org_id, ''), j.source_id) AS organization,
                COUNT(*) AS total_jobs,
                SUM(CASE WHEN j.status = 'open' THEN 1 ELSE 0 END) AS open_jobs,
                MAX(j.last_seen_at) AS last_seen_at
            FROM jobs j
            GROUP BY j.source_id, j.org_id
            ORDER BY open_jobs DESC, total_jobs DESC, j.source_id
            """
        ).fetchall()
        diagnostics = _latest_diagnostics(conn)
    result = []
    for row in rows:
        source_id = row["source_id"]
        diagnostic = diagnostics.get(source_id, {})
        result.append(
            {
                "source_id": source_id,
                "organization": row["organization"],
                "total_jobs": int(row["total_jobs"] or 0),
                "open_jobs": int(row["open_jobs"] or 0),
                "last_seen_at": row["last_seen_at"],
                "health_status": diagnostic.get("health_status"),
                "observed_at": diagnostic.get("observed_at"),
                "detail_attempted": diagnostic.get("detail_attempted"),
                "detail_failed": diagnostic.get("detail_failed"),
                "missing_transition_allowed": bool(diagnostic.get("missing_transition_allowed"))
                if diagnostic.get("missing_transition_allowed") is not None
                else None,
            }
        )
    return result


def _latest_diagnostics(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    try:
        rows = conn.execute(
            """
            SELECT d.*
            FROM source_run_diagnostics d
            JOIN (
                SELECT source_id, MAX(observed_at) AS observed_at
                FROM source_run_diagnostics
                GROUP BY source_id
            ) latest
              ON latest.source_id = d.source_id
             AND latest.observed_at = d.observed_at
            """
        ).fetchall()
    except sqlite3.Error:
        return {}
    return {row["source_id"]: dict(row) for row in rows}


def _display_label(value: str) -> str:
    text = str(value).strip().replace("_", " ")
    if not text:
        return "Unknown"
    return " ".join(word.upper() if len(word) <= 4 and word.isalpha() else word.capitalize() for word in text.split())


def _unv_category_labels() -> dict[str, str]:
    return {
        "un_community_volunteer": "UN Community Volunteer",
        "un_university_volunteer": "UN University Volunteer",
        "un_youth_volunteer": "UN Youth Volunteer",
        "un_volunteer_specialist": "UN Volunteer Specialist",
        "un_volunteer_expert": "UN Volunteer Expert",
        "other_unv": "Other UNV",
        "unknown": "Unknown UNV Category",
    }


def _unv_volunteer_type_labels() -> dict[str, str]:
    return {
        "unv_international": "International UN Volunteer",
        "unv_national": "National UN Volunteer",
        "international": "International",
        "national": "National",
        "unknown": "Unknown UNV Type",
    }


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run("job_api.app:app", host="127.0.0.1", port=8765, reload=False)


if __name__ == "__main__":
    main()
