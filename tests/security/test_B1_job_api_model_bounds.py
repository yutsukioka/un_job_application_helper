from __future__ import annotations

import sys
from pathlib import Path

import pytest
from hypothesis import given, strategies as st
from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages" / "jobagg"))
sys.path.insert(0, str(ROOT / "services" / "job-api"))

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402
from job_api.app import create_app  # noqa: E402
from job_api.config import ApiSettings  # noqa: E402
from job_api.models import AssistantRunRequest, SearchRequest  # noqa: E402


def _client(tmp_path: Path, loopback_client) -> TestClient:
    settings = ApiSettings(
        repo_root=ROOT,
        db_path=tmp_path / "missing.sqlite3",
        saved_searches_path=tmp_path / "saved_searches.json",
        tracker_path=tmp_path / "tracker.json",
    )
    return loopback_client(create_app(settings))


@pytest.mark.parametrize(
    "payload",
    [
        {"limit": -1},
        {"limit": 201},
        {"offset": -1},
        {"offset": 100_001},
        {"text": "x" * 2001},
        {"organizations": [f"org-{index}" for index in range(101)]},
    ],
)
def test_B1_search_request_rejects_unbounded_payloads(tmp_path: Path, payload: dict[str, object], loopback_client) -> None:
    response = _client(tmp_path, loopback_client).post("/api/search", json=payload)

    assert response.status_code == 422


def test_B1_tracker_record_rejects_oversized_notes(tmp_path: Path, loopback_client) -> None:
    response = _client(tmp_path, loopback_client).post(
        "/api/tracker",
        json={
            "id": "record-1",
            "job_key": "source:job",
            "status": "saved",
            "notes": "x" * 4001,
        },
    )

    assert response.status_code == 422


def test_B1_assistant_request_rejects_oversized_document_list() -> None:
    with pytest.raises(ValidationError):
        AssistantRunRequest(job_key="source:job", requested_documents=["cv"] * 51)


@given(st.one_of(st.integers(max_value=-1), st.integers(min_value=201, max_value=1000)))
def test_B1_search_limit_property_rejects_values_outside_contract(limit: int) -> None:
    with pytest.raises(ValidationError):
        SearchRequest(limit=limit)
