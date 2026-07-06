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
from job_api import auth as job_api_auth  # noqa: E402
from job_api.app import create_app  # noqa: E402
from job_api.config import ApiSettings  # noqa: E402


def _settings(tmp_path: Path, *, token: str | None = "secret-token") -> ApiSettings:
    return ApiSettings(
        repo_root=ROOT,
        db_path=tmp_path / "missing.sqlite3",
        saved_searches_path=tmp_path / "saved_searches.json",
        tracker_path=tmp_path / "tracker.json",
        allow_lan=True,
        api_token=token,
    )


def test_A2_lan_mode_rejects_missing_token_header(tmp_path: Path) -> None:
    client = TestClient(create_app(_settings(tmp_path)))

    response = client.get("/api/health")

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing or invalid X-Job-Api-Token"


def test_A2_lan_mode_accepts_valid_token_header(tmp_path: Path) -> None:
    client = TestClient(create_app(_settings(tmp_path)))

    response = client.get("/api/health", headers={"X-Job-Api-Token": "secret-token"})

    assert response.status_code == 200
    assert response.json()["status"] == "missing_db"


def test_A2_lan_mode_uses_constant_time_token_compare(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[bytes, bytes]] = []

    def compare_digest(left: bytes, right: bytes) -> bool:
        calls.append((left, right))
        return True

    monkeypatch.setattr(job_api_auth.hmac, "compare_digest", compare_digest)
    client = TestClient(create_app(_settings(tmp_path)))

    response = client.get("/api/health", headers={"X-Job-Api-Token": "secret-token"})

    assert response.status_code == 200
    assert calls == [(b"secret-token", b"secret-token")]


def test_A2_lan_mode_throttles_repeated_failed_auth(tmp_path: Path) -> None:
    client = TestClient(create_app(_settings(tmp_path)))

    for _ in range(5):
        response = client.get("/api/health", headers={"X-Job-Api-Token": "wrong"})
        assert response.status_code == 401

    throttled = client.get("/api/health", headers={"X-Job-Api-Token": "wrong"})

    assert throttled.status_code == 429
    assert throttled.json()["detail"] == "Too many failed authentication attempts"
