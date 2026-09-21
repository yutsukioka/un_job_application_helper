from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from job_api import auth
from job_api.app import create_app
from job_api.config import ApiSettings


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_PRIVATE_API_MODE", "token")
    monkeypatch.setenv("ATLAS_PRIVATE_API_TOKEN", "a" * 48)
    settings = ApiSettings(
        repo_root=tmp_path,
        db_path=tmp_path / "missing.sqlite3",
        saved_searches_path=tmp_path / "saved.json",
        tracker_path=tmp_path / "tracker.json",
    )
    return TestClient(create_app(settings))


def test_A2_private_route_rejects_missing_token(client):
    assert client.get("/api/tracker").status_code == 403


def test_A2_private_route_accepts_valid_bearer_token(client):
    response = client.get(
        "/api/tracker", headers={"Authorization": "Bearer " + "a" * 48}
    )
    assert response.status_code == 200
    assert response.json() == []


def test_A2_token_uses_constant_time_compare(monkeypatch):
    calls = []

    def compare(left, right):
        calls.append((left, right))
        return True

    monkeypatch.setattr(auth.secrets, "compare_digest", compare)
    assert auth.PrivateApiToken.parse("a" * 48).matches_authorization(
        "Bearer " + "b" * 48
    )
    assert calls == [(b"b" * 48, b"a" * 48)]


def test_A2_repeated_bad_tokens_never_admit_private_requests(client):
    for _ in range(6):
        assert (
            client.get(
                "/api/tracker", headers={"Authorization": "Bearer " + "b" * 48}
            ).status_code
            == 403
        )
    assert client.get("/api/health").status_code == 200
