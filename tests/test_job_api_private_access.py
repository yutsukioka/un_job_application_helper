from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "jobagg"))
sys.path.insert(0, str(ROOT / "services" / "job-api"))

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402
from job_api.app import create_app  # noqa: E402
from job_api.config import ApiSettings  # noqa: E402
from jobagg.db import JobDatabase  # noqa: E402


VALID_TOKEN = "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0U1v2W3x4"
PRIVATE_ENVIRONMENT = (
    "ATLAS_PRIVATE_API_MODE",
    "ATLAS_PRIVATE_API_TOKEN",
    "ATLAS_PRIVATE_API_TOKEN_FILE",
    "ATLAS_CORS_ORIGINS",
    "ATLAS_API_HOST",
    "ATLAS_API_PORT",
    "ATLAS_ALLOW_LAN",
    "ATLAS_TRUST_PROXY_HEADERS",
)


@pytest.fixture(autouse=True)
def _clean_private_access_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in PRIVATE_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)


def _settings(tmp_path: Path) -> ApiSettings:
    settings = ApiSettings(
        repo_root=ROOT,
        db_path=tmp_path / "all_jobs.sqlite3",
        saved_searches_path=tmp_path / "saved_searches.json",
        tracker_path=tmp_path / "tracker.json",
    )
    JobDatabase(settings.db_path).initialize()
    return settings


def _client(
    tmp_path: Path,
    *,
    peer: str = "127.0.0.1",
    headers: dict[str, str] | None = None,
) -> TestClient:
    return TestClient(
        create_app(_settings(tmp_path)),
        client=(peer, 50123),
        headers=headers,
    )


def _request(
    client: TestClient,
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> Any:
    return client.request(method, path, json=body)


def test_default_policy_allows_direct_loopback_private_read(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/api/saved-searches")

    assert response.status_code == 200


def test_default_policy_allows_direct_loopback_private_mutation(tmp_path: Path) -> None:
    response = _client(tmp_path).post(
        "/api/saved-searches",
        json={"name": "fake", "summary": "fake", "request": {"text": "fake"}},
    )

    assert response.status_code == 200


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-Forwarded-For": "127.0.0.1"},
        {"Forwarded": "for=127.0.0.1"},
    ],
)
def test_default_policy_rejects_direct_non_loopback_peer(
    tmp_path: Path,
    headers: dict[str, str],
) -> None:
    response = _client(tmp_path, peer="192.0.2.44", headers=headers).get(
        "/api/saved-searches"
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Private API access denied."}


def test_ipv4_mapped_loopback_peer_is_treated_as_loopback(tmp_path: Path) -> None:
    response = _client(tmp_path, peer="::ffff:127.0.0.1").get("/api/saved-searches")

    assert response.status_code == 200


@pytest.mark.parametrize(
    ("authorization", "expected_status"),
    [
        (None, 403),
        ("Basic abc", 403),
        ("Bearer", 403),
        ("Bearer wrong-token-with-sufficiently-long-padding", 403),
        (f"Bearer {VALID_TOKEN}", 200),
    ],
)
def test_token_mode_requires_exact_bearer_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    authorization: str | None,
    expected_status: int,
) -> None:
    monkeypatch.setenv("ATLAS_PRIVATE_API_MODE", "token")
    monkeypatch.setenv("ATLAS_PRIVATE_API_TOKEN", VALID_TOKEN)
    headers = {} if authorization is None else {"Authorization": authorization}

    response = _client(tmp_path, headers=headers).get("/api/saved-searches")

    assert response.status_code == expected_status


def test_token_never_appears_in_denial_response(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = "PRIVATE_TOKEN_SENTINEL_012345678901234567890123456789"
    monkeypatch.setenv("ATLAS_PRIVATE_API_MODE", "token")
    monkeypatch.setenv("ATLAS_PRIVATE_API_TOKEN", VALID_TOKEN)

    response = _client(
        tmp_path,
        headers={"Authorization": f"Bearer {sentinel}"},
    ).get("/api/tracker")

    assert response.status_code == 403
    assert sentinel not in response.text


def test_token_mode_loads_one_regular_bounded_token_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_file = tmp_path / "private-api-token"
    token_file.write_text(VALID_TOKEN, encoding="ascii")
    token_file.chmod(0o600)
    monkeypatch.setenv("ATLAS_PRIVATE_API_MODE", "token")
    monkeypatch.setenv("ATLAS_PRIVATE_API_TOKEN_FILE", str(token_file))

    client = _client(tmp_path, headers={"Authorization": f"Bearer {VALID_TOKEN}"})

    assert client.get("/api/tracker").status_code == 200
    assert _client(tmp_path).get("/api/tracker").status_code == 403


@pytest.mark.parametrize(
    "content",
    ["short", f"{VALID_TOKEN}\nextra", f" {VALID_TOKEN}", f"{VALID_TOKEN}\x00"],
)
def test_token_mode_rejects_malformed_token_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    content: str,
) -> None:
    token_file = tmp_path / "private-api-token"
    token_file.write_bytes(content.encode("ascii"))
    monkeypatch.setenv("ATLAS_PRIVATE_API_MODE", "token")
    monkeypatch.setenv("ATLAS_PRIVATE_API_TOKEN_FILE", str(token_file))

    with pytest.raises(ValueError, match="private API configuration"):
        create_app(_settings(tmp_path))


def test_token_mode_rejects_non_regular_or_oversized_token_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ATLAS_PRIVATE_API_MODE", "token")
    monkeypatch.setenv("ATLAS_PRIVATE_API_TOKEN_FILE", str(tmp_path))
    with pytest.raises(ValueError, match="private API configuration"):
        create_app(_settings(tmp_path))

    token_file = tmp_path / "oversized-token"
    token_file.write_bytes(b"a" * 4097)
    monkeypatch.setenv("ATLAS_PRIVATE_API_TOKEN_FILE", str(token_file))
    with pytest.raises(ValueError, match="private API configuration"):
        create_app(_settings(tmp_path))


def test_token_mode_rejects_both_secret_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_file = tmp_path / "private-api-token"
    token_file.write_text(VALID_TOKEN, encoding="ascii")
    monkeypatch.setenv("ATLAS_PRIVATE_API_MODE", "token")
    monkeypatch.setenv("ATLAS_PRIVATE_API_TOKEN", VALID_TOKEN)
    monkeypatch.setenv("ATLAS_PRIVATE_API_TOKEN_FILE", str(token_file))

    with pytest.raises(ValueError, match="private API configuration"):
        create_app(_settings(tmp_path))


def test_token_mode_rejects_missing_or_invalid_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ATLAS_PRIVATE_API_MODE", "token")
    with pytest.raises(ValueError, match="private API configuration"):
        create_app(_settings(tmp_path))

    monkeypatch.setenv("ATLAS_PRIVATE_API_TOKEN", "too-short")
    with pytest.raises(ValueError, match="private API configuration"):
        create_app(_settings(tmp_path))


PRIVATE_REQUESTS = (
    ("GET", "/api/saved-searches", None),
    ("POST", "/api/saved-searches", {}),
    ("POST", "/api/saved-searches/name/run", None),
    ("DELETE", "/api/saved-searches/name", None),
    ("POST", "/api/saved-searches/name/conditional-delete", {}),
    ("GET", "/api/tracker", None),
    ("POST", "/api/tracker", {}),
    ("POST", "/api/tracker/jobs/job-key", None),
    ("DELETE", "/api/tracker/record-id", None),
    ("POST", "/api/tracker/record-id/conditional-delete", {}),
    ("POST", "/api/assistant/runs", {}),
)


@pytest.mark.parametrize(("method", "path", "body"), PRIVATE_REQUESTS)
def test_disabled_mode_rejects_every_audited_private_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    monkeypatch.setenv("ATLAS_PRIVATE_API_MODE", "disabled")

    response = _request(_client(tmp_path), method, path, body)

    assert response.status_code == 503
    assert response.json() == {"detail": "Private API access unavailable."}


def test_disabled_mode_keeps_public_health_and_search_available(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ATLAS_PRIVATE_API_MODE", "disabled")
    client = _client(tmp_path, peer="192.0.2.44")

    assert client.get("/api/health").status_code == 200
    assert client.post("/api/search", json={"limit": 1}).status_code == 200


def test_private_route_classifier_covers_every_audited_route() -> None:
    private_access = importlib.import_module("job_api.private_access")

    assert all(
        private_access.is_private_endpoint(path) for _, path, _ in PRIVATE_REQUESTS
    )
    assert not private_access.is_private_endpoint("/api/health")
    assert not private_access.is_private_endpoint("/api/search")


def test_raw_uvicorn_application_still_enforces_direct_peer_policy(
    tmp_path: Path,
) -> None:
    response = _client(tmp_path, peer="198.51.100.19").get("/api/tracker")

    assert response.status_code == 403


def test_token_comparison_uses_constant_time_boundary() -> None:
    source = (ROOT / "services/job-api/job_api/auth.py").read_text(encoding="utf-8")

    assert "compare_digest" in source


def test_cors_defaults_to_no_cross_origin_access(tmp_path: Path) -> None:
    response = _client(tmp_path).get(
        "/api/health", headers={"Origin": "https://unconfigured.example"}
    )

    assert "access-control-allow-origin" not in response.headers


def test_cors_accepts_only_explicit_origins(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "ATLAS_CORS_ORIGINS",
        "https://atlas.example,https://preview.example",
    )
    client = _client(tmp_path)

    accepted = client.get("/api/health", headers={"Origin": "https://atlas.example"})
    rejected = client.get("/api/health", headers={"Origin": "https://other.example"})

    assert accepted.headers["access-control-allow-origin"] == "https://atlas.example"
    assert "access-control-allow-origin" not in rejected.headers


def test_cors_rejects_wildcard_when_private_endpoints_exist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ATLAS_CORS_ORIGINS", "*")

    with pytest.raises(ValueError, match="CORS configuration"):
        create_app(_settings(tmp_path))


def test_launcher_defaults_to_loopback() -> None:
    launcher = importlib.import_module("job_api.launcher")

    config = launcher.load_launch_config({})

    assert config.host == "127.0.0.1"
    assert config.port == 8765


@pytest.mark.parametrize(
    "environment",
    [
        {"ATLAS_API_HOST": "0.0.0.0"},
        {"ATLAS_API_HOST": "0.0.0.0", "ATLAS_ALLOW_LAN": "1"},
        {
            "ATLAS_API_HOST": "0.0.0.0",
            "ATLAS_ALLOW_LAN": "1",
            "ATLAS_PRIVATE_API_MODE": "disabled",
        },
    ],
)
def test_launcher_rejects_unsafe_non_loopback_configuration(
    environment: dict[str, str],
) -> None:
    launcher = importlib.import_module("job_api.launcher")

    with pytest.raises(ValueError, match="launch configuration"):
        launcher.load_launch_config(environment)


def test_launcher_accepts_explicit_token_protected_lan_configuration() -> None:
    launcher = importlib.import_module("job_api.launcher")
    environment = {
        "ATLAS_API_HOST": "0.0.0.0",
        "ATLAS_ALLOW_LAN": "1",
        "ATLAS_PRIVATE_API_MODE": "token",
        "ATLAS_PRIVATE_API_TOKEN": VALID_TOKEN,
    }

    config = launcher.load_launch_config(environment)

    assert config.host == "0.0.0.0"
    assert config.private_access.mode.value == "token"


def test_launcher_rejects_implicit_proxy_trust() -> None:
    launcher = importlib.import_module("job_api.launcher")

    with pytest.raises(ValueError, match="launch configuration"):
        launcher.load_launch_config({"ATLAS_TRUST_PROXY_HEADERS": "1"})


def test_documentation_has_no_unconditional_unsafe_bind_example() -> None:
    apple_readme = (ROOT / "apps/apple/README.md").read_text(encoding="utf-8")
    service_readme = (ROOT / "services/job-api/README.md").read_text(encoding="utf-8")

    assert "--host 0.0.0.0" not in apple_readme
    assert "python -m job_api.launcher" in apple_readme
    assert "python -m job_api.launcher" in service_readme
    assert VALID_TOKEN not in apple_readme
    assert VALID_TOKEN not in service_readme


def test_private_access_environment_does_not_use_process_environment_snapshot() -> None:
    launcher = importlib.import_module("job_api.launcher")
    environment = {
        "ATLAS_PRIVATE_API_MODE": "token",
        "ATLAS_PRIVATE_API_TOKEN": VALID_TOKEN,
    }

    config = launcher.load_launch_config(environment)

    assert config.private_access.mode.value == "token"
    assert os.environ.get("ATLAS_PRIVATE_API_TOKEN") != VALID_TOKEN
