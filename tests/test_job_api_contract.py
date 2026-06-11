from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "jobagg"))
sys.path.insert(0, str(ROOT / "services" / "job-api"))

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402
from job_api.app import create_app  # noqa: E402
from job_api.config import ApiSettings  # noqa: E402
from jobagg.classification import classify_database  # noqa: E402
from jobagg.db import JobDatabase  # noqa: E402
from jobagg.models import OrganizationSource  # noqa: E402
from jobagg.normalize import build_job  # noqa: E402


def _source() -> OrganizationSource:
    return OrganizationSource(
        id="un_inspira",
        name="UN Inspira",
        ats_family="inspira",
        base_url="https://careers.un.org",
    )


def _settings(tmp_path: Path) -> ApiSettings:
    return ApiSettings(
        repo_root=ROOT,
        db_path=tmp_path / "all_jobs.sqlite3",
        saved_searches_path=tmp_path / "saved_searches.json",
        tracker_path=tmp_path / "tracker.json",
    )


def _client(tmp_path: Path) -> TestClient:
    settings = _settings(tmp_path)
    db = JobDatabase(settings.db_path)
    db.initialize()
    db.upsert_job(
        build_job(
            _source(),
            title="Programme Management Officer, P-3",
            external_id="123",
            location="Nairobi",
            apply_url="https://careers.un.org/jobSearchDescription/123?language=en",
            raw={"jl": {"name": "P-3"}, "dutyStation": [{"description": "Nairobi"}]},
        )
    )
    classify_database(db, force=True)
    return TestClient(create_app(settings))


def test_job_api_health_search_detail_saved_search_and_tracker(tmp_path: Path) -> None:
    client = _client(tmp_path)

    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    search = client.post("/api/search", json={"text": "Programme", "limit": 10})
    assert search.status_code == 200
    payload = search.json()
    assert payload["total"] == 1
    job_key = payload["results"][0]["job_key"]

    detail = client.get(f"/api/jobs/{job_key}")
    assert detail.status_code == 200
    assert detail.json()["title"] == "Programme Management Officer, P-3"

    saved = client.post(
        "/api/saved-searches",
        json={"name": "programme", "summary": "Programme roles", "request": {"text": "Programme"}},
    )
    assert saved.status_code == 200
    assert client.post("/api/saved-searches/programme/run").json()["total"] == 1

    tracker = client.post(f"/api/tracker/jobs/{job_key}")
    assert tracker.status_code == 200
    assert tracker.json()["job_key"] == job_key
    assert len(client.get("/api/tracker").json()) == 1
