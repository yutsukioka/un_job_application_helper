from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages" / "jobagg"))
sys.path.insert(0, str(ROOT / "services" / "job-api"))

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402
from job_api.app import create_app  # noqa: E402
from job_api.config import ApiSettings  # noqa: E402


def test_D1_health_scrubs_repo_local_database_path(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    db_path = repo_root / "private" / "jobagg" / "output" / "all_jobs.sqlite3"
    settings = ApiSettings(
        repo_root=repo_root,
        db_path=db_path,
        saved_searches_path=repo_root / "private" / "jobagg" / "saved_searches.json",
        tracker_path=repo_root / "private" / "jobagg" / "application_tracker.json",
    )
    client = TestClient(create_app(settings))

    response = client.get("/api/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["db_path"] == "<repo>/private/jobagg/output/all_jobs.sqlite3"
    assert str(repo_root) not in response.text


def test_D1_missing_database_error_uses_opaque_path_id(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    db_path = tmp_path / "outside" / "all_jobs.sqlite3"
    settings = ApiSettings(
        repo_root=repo_root,
        db_path=db_path,
        saved_searches_path=repo_root / "private" / "jobagg" / "saved_searches.json",
        tracker_path=repo_root / "private" / "jobagg" / "application_tracker.json",
    )
    client = TestClient(create_app(settings))

    response = client.post("/api/search", json={"text": "programme"})

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail.startswith("Job database does not exist: <path:")
    assert str(tmp_path) not in response.text
