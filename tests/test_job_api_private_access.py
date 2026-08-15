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
        base_url="http://127.0.0.1",
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


def test_loopback_peer_rejects_dns_rebinding_host(tmp_path: Path) -> None:
    response = _client(
        tmp_path,
        headers={"Host": "attacker-controlled.example"},
    ).get("/api/saved-searches")

    assert response.status_code == 403
    assert response.json() == {"detail": "Private API access denied."}


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "LOCALHOST:8765",
        "localhost.:8765",
        "127.0.0.7:8765",
        "[::1]:8765",
        "[::ffff:127.0.0.1]:8765",
    ],
)
def test_loopback_peer_accepts_only_loopback_host_allowlist(
    tmp_path: Path,
    host: str,
) -> None:
    response = _client(tmp_path, headers={"Host": host}).get("/api/saved-searches")

    assert response.status_code == 200


def test_loopback_peer_rejects_ambiguous_or_duplicate_host(tmp_path: Path) -> None:
    client = _client(tmp_path)

    ambiguous = client.get(
        "/api/saved-searches",
        headers={"Host": "127.0.0.1.attacker.example"},
    )
    duplicate = client.get(
        "/api/saved-searches",
        headers=[("Host", "127.0.0.1"), ("Host", "localhost")],
    )

    assert ambiguous.status_code == 403
    assert duplicate.status_code == 403


@pytest.mark.parametrize(
    "host",
    [
        "[::1",
        "[::1]suffix",
        "::1",
        "localhost:0",
        "localhost:65536",
        "localhost@attacker.example",
        "localhost,attacker.example",
    ],
)
def test_loopback_peer_rejects_malformed_host(tmp_path: Path, host: str) -> None:
    response = _client(tmp_path, headers={"Host": host}).get("/api/tracker")

    assert response.status_code == 403


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


@pytest.mark.parametrize(
    "authorization",
    [
        f"bearer {VALID_TOKEN}",
        f"BEARER {VALID_TOKEN}",
        f"Bearer  {VALID_TOKEN}",
    ],
)
def test_bearer_scheme_uses_http_case_and_space_grammar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    authorization: str,
) -> None:
    monkeypatch.setenv("ATLAS_PRIVATE_API_MODE", "token")
    monkeypatch.setenv("ATLAS_PRIVATE_API_TOKEN", VALID_TOKEN)

    response = _client(
        tmp_path,
        peer="192.0.2.44",
        headers={"Authorization": authorization},
    ).get("/api/saved-searches")

    assert response.status_code == 200


@pytest.mark.parametrize(
    "authorization",
    [
        f"Bearer\t{VALID_TOKEN}",
        f"Bearer {VALID_TOKEN} ",
        f" Bearer {VALID_TOKEN}",
        f"Bearer {VALID_TOKEN} extra",
    ],
)
def test_bearer_grammar_rejects_ambiguous_whitespace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    authorization: str,
) -> None:
    monkeypatch.setenv("ATLAS_PRIVATE_API_MODE", "token")
    monkeypatch.setenv("ATLAS_PRIVATE_API_TOKEN", VALID_TOKEN)

    response = _client(
        tmp_path,
        peer="192.0.2.44",
        headers={"Authorization": authorization},
    ).get("/api/saved-searches")

    assert response.status_code == 403


def test_valid_token_is_authority_for_deliberate_non_loopback_host(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ATLAS_PRIVATE_API_MODE", "token")
    monkeypatch.setenv("ATLAS_PRIVATE_API_TOKEN", VALID_TOKEN)

    response = _client(
        tmp_path,
        peer="192.0.2.44",
        headers={
            "Host": "atlas-lan.example:8765",
            "Authorization": f"Bearer {VALID_TOKEN}",
        },
    ).get("/api/saved-searches")

    assert response.status_code == 200


def test_token_in_query_string_is_not_an_authentication_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ATLAS_PRIVATE_API_MODE", "token")
    monkeypatch.setenv("ATLAS_PRIVATE_API_TOKEN", VALID_TOKEN)

    response = _client(tmp_path).get(
        "/api/saved-searches",
        params={"access_token": VALID_TOKEN},
    )

    assert response.status_code == 403


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


def test_token_never_appears_in_policy_representation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ATLAS_PRIVATE_API_MODE", "token")
    monkeypatch.setenv("ATLAS_PRIVATE_API_TOKEN", VALID_TOKEN)
    private_access = importlib.import_module("job_api.private_access")

    policy = private_access.load_private_access_policy()

    assert VALID_TOKEN not in repr(policy)


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


def test_token_file_error_redacts_path_and_underlying_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel_path = tmp_path / "PRIVATE_TOKEN_PATH_SENTINEL"
    monkeypatch.setenv("ATLAS_PRIVATE_API_MODE", "token")
    monkeypatch.setenv("ATLAS_PRIVATE_API_TOKEN_FILE", str(sentinel_path))

    with pytest.raises(ValueError) as caught:
        create_app(_settings(tmp_path))

    assert str(sentinel_path) not in str(caught.value)
    assert caught.value.__cause__ is None


def test_token_mode_rejects_symlink_token_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_file = tmp_path / "private-api-token"
    token_file.write_text(VALID_TOKEN, encoding="ascii")
    token_file.chmod(0o600)
    link = tmp_path / "token-link"
    try:
        link.symlink_to(token_file)
    except OSError:
        pytest.skip("Symlink creation is unavailable on this platform")
    monkeypatch.setenv("ATLAS_PRIVATE_API_MODE", "token")
    monkeypatch.setenv("ATLAS_PRIVATE_API_TOKEN_FILE", str(link))

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


def test_token_mode_rejects_duplicate_authorization_headers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ATLAS_PRIVATE_API_MODE", "token")
    monkeypatch.setenv("ATLAS_PRIVATE_API_TOKEN", VALID_TOKEN)
    client = _client(tmp_path)

    response = client.get(
        "/api/saved-searches",
        headers=[
            ("Authorization", f"Bearer {VALID_TOKEN}"),
            ("Authorization", f"Bearer {VALID_TOKEN}"),
        ],
    )

    assert response.status_code == 403


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
    ("POST", "/api/sync/run", None),
    ("POST", "/api/assistant/runs", {}),
)

PRIVATE_ROUTE_CONTRACT = {
    ("GET", "/api/saved-searches"),
    ("POST", "/api/saved-searches"),
    ("POST", "/api/saved-searches/{name}/run"),
    ("DELETE", "/api/saved-searches/{name}"),
    ("POST", "/api/saved-searches/{name}/conditional-delete"),
    ("GET", "/api/tracker"),
    ("POST", "/api/tracker"),
    ("POST", "/api/tracker/jobs/{job_key}"),
    ("DELETE", "/api/tracker/{record_id}"),
    ("POST", "/api/tracker/{record_id}/conditional-delete"),
    ("POST", "/api/sync/run"),
    ("POST", "/api/assistant/runs"),
}

PUBLIC_ROUTE_CONTRACT = {
    ("GET", "/api/health"),
    ("POST", "/api/search"),
    ("GET", "/api/job-detail"),
    ("GET", "/api/jobs/by-key"),
    ("GET", "/api/jobs/{job_key}"),
    ("GET", "/api/jobs/path/{job_key:path}"),
    ("GET", "/api/facets"),
    ("POST", "/api/facets"),
    ("GET", "/api/taxonomies"),
    ("GET", "/api/updates"),
    ("GET", "/api/sources"),
    ("GET", "/api/sync/runs"),
}


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


def test_public_health_does_not_disclose_local_storage_path(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = TestClient(create_app(settings), client=("192.0.2.44", 50123))

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["db_path"] is None
    assert str(settings.db_path) not in response.text


def test_public_missing_database_error_does_not_disclose_storage_path(
    tmp_path: Path,
) -> None:
    settings = ApiSettings(
        repo_root=ROOT,
        db_path=tmp_path / "PRIVATE_DATABASE_PATH_SENTINEL.sqlite3",
        saved_searches_path=tmp_path / "saved-searches.json",
        tracker_path=tmp_path / "tracker.json",
    )
    client = TestClient(create_app(settings), client=("192.0.2.44", 50123))

    response = client.post("/api/search", json={"limit": 1})

    assert response.status_code == 503
    assert response.json() == {"detail": "Job database is unavailable."}
    assert str(settings.db_path) not in response.text


def test_non_loopback_search_with_private_strategy_requires_admission(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    sentinel_path = tmp_path / "PRIVATE_STRATEGY_PATH_SENTINEL.json"
    sentinel_path.write_text('{"terms": []}', encoding="utf-8")
    client = TestClient(
        create_app(settings),
        client=("192.0.2.44", 50123),
        raise_server_exceptions=False,
    )

    response = client.post(
        "/api/search",
        json={"limit": 1, "score_against": str(sentinel_path)},
    )

    assert response.status_code == 403
    assert str(sentinel_path) not in response.text


def test_token_admission_allows_private_strategy_scoring(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    strategy = tmp_path / "strategy.json"
    strategy.write_text('{"terms": []}', encoding="utf-8")
    monkeypatch.setenv("ATLAS_PRIVATE_API_MODE", "token")
    monkeypatch.setenv("ATLAS_PRIVATE_API_TOKEN", VALID_TOKEN)
    client = TestClient(
        create_app(settings),
        client=("192.0.2.44", 50123),
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
    )

    response = client.post(
        "/api/search",
        json={"limit": 1, "score_against": str(strategy)},
    )

    assert response.status_code == 200


def test_private_route_classifier_covers_every_audited_route() -> None:
    private_access = importlib.import_module("job_api.private_access")

    assert all(
        private_access.is_private_endpoint(path) for _, path, _ in PRIVATE_REQUESTS
    )
    assert not private_access.is_private_endpoint("/api/health")
    assert not private_access.is_private_endpoint("/api/search")


def test_every_api_route_has_an_explicit_public_or_private_classification(
    tmp_path: Path,
) -> None:
    private_access = importlib.import_module("job_api.private_access")
    application = create_app(_settings(tmp_path))
    actual = {
        (method, route.path)
        for route in application.routes
        if route.path.startswith("/api/")
        for method in route.methods
    }

    assert actual == PRIVATE_ROUTE_CONTRACT | PUBLIC_ROUTE_CONTRACT
    assert all(
        private_access.is_private_endpoint(path) for _, path in PRIVATE_ROUTE_CONTRACT
    )
    assert not any(
        private_access.is_private_endpoint(path) for _, path in PUBLIC_ROUTE_CONTRACT
    )


def test_raw_uvicorn_application_still_enforces_direct_peer_policy(
    tmp_path: Path,
) -> None:
    response = _client(tmp_path, peer="198.51.100.19").get("/api/tracker")

    assert response.status_code == 403


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/saved-searches"),
        ("GET", "/api/tracker"),
        ("POST", "/api/sync/run"),
        ("POST", "/api/assistant/runs"),
    ],
)
def test_root_path_cannot_hide_private_route_from_admission(
    tmp_path: Path,
    method: str,
    path: str,
) -> None:
    client = TestClient(
        create_app(_settings(tmp_path)),
        base_url="http://127.0.0.1/prefix",
        root_path="/prefix",
        client=("192.0.2.44", 50123),
    )

    response = client.request(method, path, json={})

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


def test_cors_preflight_does_not_bypass_private_token_on_actual_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    origin = "https://atlas.example"
    monkeypatch.setenv("ATLAS_CORS_ORIGINS", origin)
    monkeypatch.setenv("ATLAS_PRIVATE_API_MODE", "token")
    monkeypatch.setenv("ATLAS_PRIVATE_API_TOKEN", VALID_TOKEN)
    client = _client(tmp_path, peer="192.0.2.44")

    preflight = client.options(
        "/api/saved-searches",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization",
        },
    )
    denied = client.get("/api/saved-searches", headers={"Origin": origin})
    admitted = client.get(
        "/api/saved-searches",
        headers={"Origin": origin, "Authorization": f"Bearer {VALID_TOKEN}"},
    )

    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == origin
    assert denied.status_code == 403
    assert admitted.status_code == 200


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


@pytest.mark.parametrize("host", ["localhost", "LOCALHOST", "localhost."])
def test_launcher_normalizes_reserved_localhost_names(host: str) -> None:
    launcher = importlib.import_module("job_api.launcher")

    config = launcher.load_launch_config({"ATLAS_API_HOST": host})

    assert config.host == "127.0.0.1"
    assert config.private_access.mode.value == "loopback"


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


def test_launcher_starts_uvicorn_without_proxy_header_trust(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = importlib.import_module("job_api.launcher")
    uvicorn = importlib.import_module("uvicorn")
    captured: dict[str, Any] = {}

    def fake_run(application: str, **options: Any) -> None:
        captured["application"] = application
        captured.update(options)

    monkeypatch.setattr(uvicorn, "run", fake_run)

    launcher.main()

    assert captured == {
        "application": "job_api.app:app",
        "host": "127.0.0.1",
        "port": 8765,
        "reload": False,
        "proxy_headers": False,
        "access_log": False,
    }


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
