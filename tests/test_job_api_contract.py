from __future__ import annotations

import sys
from copy import deepcopy
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
    assert "deadline_info" in detail.json()

    saved = client.post(
        "/api/saved-searches",
        json={"name": "programme", "summary": "Programme roles", "request": {"text": "Programme"}},
    )
    assert saved.status_code == 200
    assert client.post("/api/saved-searches/programme/run").json()["total"] == 1
    assert client.delete("/api/saved-searches/programme").json()["deleted"] is True

    tracker = client.post(f"/api/tracker/jobs/{job_key}")
    assert tracker.status_code == 200
    tracker_payload = tracker.json()
    assert tracker_payload["job_key"] == job_key
    assert tracker_payload["status"] == "saved"
    tracker_payload["status"] = "applied"
    applied = client.post("/api/tracker", json=tracker_payload)
    assert applied.status_code == 200
    assert applied.json()["status"] == "applied"
    saved_again = client.post(f"/api/tracker/jobs/{job_key}")
    assert saved_again.status_code == 200
    assert saved_again.json()["status"] == "applied"
    assert len(client.get("/api/tracker").json()) == 1


def _saved_search_snapshot(client: TestClient, *, name: str = "programme") -> dict[str, object]:
    response = client.post(
        "/api/saved-searches",
        json={
            "name": name,
            "summary": "PRIVATE_DESCRIPTION_SENTINEL",
            "request": {"text": "PRIVATE_QUERY_SENTINEL"},
        },
    )
    assert response.status_code == 200
    return response.json()


def _tracker_snapshot(client: TestClient, *, record_id: str = "record-1") -> dict[str, object]:
    response = client.post(
        "/api/tracker",
        json={
            "id": record_id,
            "job_key": "PRIVATE_JOB_KEY_SENTINEL",
            "status": "saved",
            "notes": "PRIVATE_NOTES_SENTINEL",
        },
    )
    assert response.status_code == 200
    return response.json()


def test_saved_search_conditional_delete_exact_absent_and_legacy(tmp_path: Path) -> None:
    client = _client(tmp_path)
    expected = _saved_search_snapshot(client)

    deleted = client.post(
        "/api/saved-searches/programme/conditional-delete",
        json={"expected": expected},
    )
    absent = client.post(
        "/api/saved-searches/programme/conditional-delete",
        json={"expected": expected},
    )

    assert deleted.status_code == 200
    assert deleted.json() == {"outcome": "deleted"}
    assert absent.status_code == 200
    assert absent.json() == {"outcome": "absent"}

    _saved_search_snapshot(client)
    assert client.delete("/api/saved-searches/programme").json() == {"deleted": True}


def test_saved_search_conditional_delete_rejects_identity_and_extra_fields(tmp_path: Path) -> None:
    client = _client(tmp_path)
    expected = _saved_search_snapshot(client)

    identity = client.post(
        "/api/saved-searches/different/conditional-delete",
        json={"expected": expected},
    )
    extra = client.post(
        "/api/saved-searches/programme/conditional-delete",
        json={"expected": expected, "unexpected": True},
    )

    assert identity.status_code == 400
    assert identity.json() == {"detail": "Conditional delete identity mismatch."}
    assert extra.status_code == 422


@pytest.mark.parametrize(
    "field,value",
    [
        ("description", "changed-description"),
        ("request", {"text": "changed-query"}),
        ("created_at", "2099-01-01T00:00:00+00:00"),
        ("updated_at", "2099-01-01T00:00:00+00:00"),
    ],
)
def test_saved_search_conditional_delete_mismatch_is_private_free(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    client = _client(tmp_path)
    current = _saved_search_snapshot(client)
    expected = deepcopy(current)
    expected[field] = value

    response = client.post(
        "/api/saved-searches/programme/conditional-delete",
        json={"expected": expected},
    )

    assert response.status_code == 412
    assert response.json() == {"detail": "Conditional delete precondition failed."}
    assert "PRIVATE_DESCRIPTION_SENTINEL" not in response.text
    assert "PRIVATE_QUERY_SENTINEL" not in response.text
    assert client.get("/api/saved-searches").json() == [current]


def test_tracker_conditional_delete_exact_absent_and_legacy(tmp_path: Path) -> None:
    client = _client(tmp_path)
    expected = _tracker_snapshot(client)

    deleted = client.post(
        "/api/tracker/record-1/conditional-delete",
        json={"expected": expected},
    )
    absent = client.post(
        "/api/tracker/record-1/conditional-delete",
        json={"expected": expected},
    )

    assert deleted.status_code == 200
    assert deleted.json() == {"outcome": "deleted"}
    assert absent.status_code == 200
    assert absent.json() == {"outcome": "absent"}

    _tracker_snapshot(client)
    assert client.delete("/api/tracker/record-1").json() == {"deleted": True}


def test_tracker_conditional_delete_rejects_identity_and_extra_fields(tmp_path: Path) -> None:
    client = _client(tmp_path)
    expected = _tracker_snapshot(client)

    identity = client.post(
        "/api/tracker/different/conditional-delete",
        json={"expected": expected},
    )
    extra = client.post(
        "/api/tracker/record-1/conditional-delete",
        json={"expected": expected, "unexpected": True},
    )

    assert identity.status_code == 400
    assert identity.json() == {"detail": "Conditional delete identity mismatch."}
    assert extra.status_code == 422


@pytest.mark.parametrize(
    "field,value",
    [
        ("job_key", "changed-job-key"),
        ("status", "applied"),
        ("notes", "changed-notes"),
        ("applied_at", "2099-01-01T00:00:00Z"),
        ("updated_at", "2099-01-01T00:00:00Z"),
    ],
)
def test_tracker_conditional_delete_mismatch_is_private_free(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    client = _client(tmp_path)
    current = _tracker_snapshot(client)
    expected = deepcopy(current)
    expected[field] = value

    response = client.post(
        "/api/tracker/record-1/conditional-delete",
        json={"expected": expected},
    )

    assert response.status_code == 412
    assert response.json() == {"detail": "Conditional delete precondition failed."}
    assert "PRIVATE_JOB_KEY_SENTINEL" not in response.text
    assert "PRIVATE_NOTES_SENTINEL" not in response.text
    assert client.get("/api/tracker").json() == [current]


def test_job_api_open_search_excludes_expired_open_rows(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db = JobDatabase(settings.db_path)
    db.initialize()
    source = _source()
    db.upsert_job(
        build_job(
            source,
            title="Expired Programme Management Officer, P-3",
            external_id="expired",
            location="Nairobi",
            closes_at="2020-01-01T00:00:00+00:00",
            apply_url="https://careers.un.org/jobSearchDescription/expired?language=en",
            raw={"jl": {"name": "P-3"}, "dutyStation": [{"description": "Nairobi"}]},
        )
    )
    db.upsert_job(
        build_job(
            source,
            title="Current Programme Management Officer, P-3",
            external_id="current",
            location="Nairobi",
            closes_at="2099-01-01T00:00:00+00:00",
            apply_url="https://careers.un.org/jobSearchDescription/current?language=en",
            raw={"jl": {"name": "P-3"}, "dutyStation": [{"description": "Nairobi"}]},
        )
    )
    classify_database(db, force=True)
    client = TestClient(create_app(settings))

    payload = client.post("/api/search", json={"status": ["open"], "limit": 10}).json()

    assert payload["total"] == 1
    assert payload["results"][0]["job_key"] == "un_inspira:current"
