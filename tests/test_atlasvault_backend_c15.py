from __future__ import annotations

import json
import logging
import sys
import threading
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "packages" / "vaultsync"))
sys.path.insert(0, str(ROOT / "services" / "atlasvault-api"))

from atlasvault_api.app import (
    ACCOUNT_SESSION_PROOF_DOMAIN,
    AtlasVaultBackend,
    create_app,
)
from atlasvault_api.controls import (
    AbuseControlPolicy,
    AccountDeviceRateLimiter,
    RequestRateExceeded,
    SecretFreeTelemetry,
    StoragePrincipal,
)
from test_atlasvault_backend_c13 import (
    ACCOUNT_A,
    ACCOUNT_B,
    REVISION_1,
    REVISION_2,
    _bootstrap,
    _canonical_bytes,
    _encode64,
    _identities,
    _session,
    _signed_transition,
)
from test_atlasvault_backend_c14 import (
    VAULT_ID,
    DeterministicEntropy,
    _metadata,
    _opaque_envelope,
    _write_headers,
)

DEVICE_REQUEST_LIMIT = 24
ACCOUNT_REQUEST_LIMIT = 40
MAX_REQUEST_BYTES = 192 * 1024 * 1024
MAX_RETAINED_ACCOUNTS = 1024
MAX_LIVE_CHALLENGES = 4096
MAX_CHALLENGES_PER_DEVICE = 8
MAX_LIVE_SESSIONS = 4096
MAX_SESSIONS_PER_DEVICE = 8
MAX_DEVICES_PER_ACCOUNT = 256
MAX_KEY_EPOCH = (1 << 63) - 1
OPENAPI_PATH = ROOT / "contracts" / "api" / "atlasvault_sync_openapi.json"


@pytest.fixture
def authorized_client() -> tuple[
    AtlasVaultBackend,
    TestClient,
    dict[str, str],
    str,
]:
    backend = AtlasVaultBackend(
        entropy=DeterministicEntropy(),
        monotonic=lambda: 3_000.0,
    )
    client = TestClient(create_app(backend))
    device, _ = _identities()
    _bootstrap(client, device, account_id=ACCOUNT_A)
    token, _ = _session(client, device, account_id=ACCOUNT_A)
    return backend, client, {"Authorization": f"Bearer {token}"}, token


def _storage_writes() -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (
            "put",
            f"/v1/vaults/{VAULT_ID}/metadata",
            _metadata(revision="authz-metadata-r1", payload=b"opaque"),
        ),
        (
            "put",
            f"/v1/vaults/{VAULT_ID}/objects/authz-object",
            _opaque_envelope(
                object_id="authz-object",
                revision="authz-object-r1",
                parent_revision=None,
                payload=b"opaque",
            ),
        ),
        (
            "post",
            f"/v1/vaults/{VAULT_ID}/patches",
            _opaque_envelope(
                object_id="authz-patch",
                revision="authz-patch-r1",
                parent_revision=None,
                payload=b"opaque",
            ),
        ),
        (
            "put",
            f"/v1/vaults/{VAULT_ID}/snapshots",
            _opaque_envelope(
                object_id="authz-snapshot",
                revision="authz-snapshot-r1",
                parent_revision=None,
                payload=b"opaque",
            ),
        ),
    ]


def _challenge_proof(
    client: TestClient,
    identity: Any,
    *,
    account_id: str,
) -> dict[str, str]:
    response = client.post(
        f"/v1/accounts/{account_id}/auth/challenges",
        json={"device_id": identity.device_id},
    )
    assert response.status_code == 201, response.text
    challenge = response.json()
    payload = {
        "format": "atlasvault-account-session-proof",
        "version": 1,
        "account_id": account_id,
        "device_id": identity.device_id,
        "challenge_id": challenge["challenge_id"],
        "challenge": challenge["challenge"],
    }
    return {
        "device_id": identity.device_id,
        "challenge_id": challenge["challenge_id"],
        "signature": _encode64(
            identity.sign(ACCOUNT_SESSION_PROOF_DOMAIN + _canonical_bytes(payload))
        ),
    }


def test_c15_every_ciphertext_route_requires_auth_and_unauthorized_writes_do_not_mutate(
    authorized_client: tuple[AtlasVaultBackend, TestClient, dict[str, str], str],
) -> None:
    _, client, authorization, _ = authorized_client
    for path in (
        f"/v1/vaults/{VAULT_ID}/metadata",
        f"/v1/vaults/{VAULT_ID}/objects/authz-object",
        f"/v1/vaults/{VAULT_ID}/patches",
        f"/v1/vaults/{VAULT_ID}/snapshots",
    ):
        response = client.get(path)
        assert response.status_code == 401
        assert response.json() == {"detail": "Authorization failed."}

    for index, (method, path, body) in enumerate(_storage_writes()):
        response = client.request(
            method,
            path,
            headers={
                "If-Match": "*",
                "Idempotency-Key": f"unauthorized-{index}",
            },
            json=body,
        )
        assert response.status_code == 401
        assert response.json() == {"detail": "Authorization failed."}

    missing = client.get(
        f"/v1/vaults/{VAULT_ID}/objects/authz-object",
        headers=authorization,
    )
    assert missing.status_code == 404


def test_c15_cross_account_tokens_resolve_only_their_own_opaque_namespace() -> None:
    backend = AtlasVaultBackend(
        entropy=DeterministicEntropy(),
        monotonic=lambda: 3_000.0,
    )
    client = TestClient(create_app(backend))
    device_a, device_b = _identities()
    _bootstrap(client, device_a, account_id=ACCOUNT_A)
    _bootstrap(
        client,
        device_b,
        account_id=ACCOUNT_B,
        revision="20000000-0000-4000-8000-000000000001",
    )
    token_a, _ = _session(client, device_a, account_id=ACCOUNT_A)
    token_b, _ = _session(client, device_b, account_id=ACCOUNT_B)
    auth_a = {"Authorization": f"Bearer {token_a}"}
    auth_b = {"Authorization": f"Bearer {token_b}"}
    path = f"/v1/vaults/{VAULT_ID}/objects/shared-opaque-id"
    object_a = _opaque_envelope(
        object_id="shared-opaque-id",
        revision="account-a-r1",
        parent_revision=None,
        payload=b"account-a-opaque-ciphertext",
    )
    object_b = _opaque_envelope(
        object_id="shared-opaque-id",
        revision="account-b-r1",
        parent_revision=None,
        payload=b"account-b-opaque-ciphertext",
    )

    assert (
        client.put(
            path,
            headers=_write_headers(
                auth_a,
                expected="*",
                idempotency_key="account-a-write",
            ),
            json=object_a,
        ).status_code
        == 200
    )
    assert client.get(path, headers=auth_b).status_code == 404
    assert (
        client.put(
            path,
            headers=_write_headers(
                auth_b,
                expected="*",
                idempotency_key="account-b-write",
            ),
            json=object_b,
        ).status_code
        == 200
    )
    assert client.get(path, headers=auth_a).json() == object_a
    assert client.get(path, headers=auth_b).json() == object_b


def test_c15_device_throttle_survives_session_rotation(
    authorized_client: tuple[AtlasVaultBackend, TestClient, dict[str, str], str],
) -> None:
    _, client, authorization, _ = authorized_client
    device, _ = _identities()
    path = f"/v1/vaults/{VAULT_ID}/objects/missing-rate-object"
    for _ in range(DEVICE_REQUEST_LIMIT):
        assert client.get(path, headers=authorization).status_code == 404

    replacement_token, _ = _session(client, device, account_id=ACCOUNT_A)
    limited = client.get(
        path,
        headers={"Authorization": f"Bearer {replacement_token}"},
    )
    assert limited.status_code == 429
    assert limited.json() == {"detail": "Request rate exceeded."}


def test_c15_account_throttle_cannot_be_evaded_with_another_device_or_session() -> None:
    backend = AtlasVaultBackend(
        entropy=DeterministicEntropy(),
        monotonic=lambda: 3_000.0,
    )
    client = TestClient(create_app(backend))
    device_a, device_b = _identities()
    _bootstrap(client, device_a, account_id=ACCOUNT_A, revision=REVISION_1)
    token_a, _ = _session(client, device_a, account_id=ACCOUNT_A)
    add_response = client.post(
        f"/v1/accounts/{ACCOUNT_A}/devices",
        headers={"Authorization": f"Bearer {token_a}"},
        json=_signed_transition(
            account_id=ACCOUNT_A,
            revision=REVISION_2,
            parent_revision=REVISION_1,
            device=device_b,
            signer=device_a,
        ),
    )
    assert add_response.status_code == 200, add_response.text
    token_b, _ = _session(client, device_b, account_id=ACCOUNT_A)
    auth_a = {"Authorization": f"Bearer {token_a}"}
    auth_b = {"Authorization": f"Bearer {token_b}"}
    path = f"/v1/vaults/{VAULT_ID}/objects/missing-account-rate-object"

    for _ in range(ACCOUNT_REQUEST_LIMIT // 2):
        assert client.get(path, headers=auth_a).status_code == 404
        assert client.get(path, headers=auth_b).status_code == 404

    replacement_token, _ = _session(client, device_b, account_id=ACCOUNT_A)
    limited = client.get(
        path,
        headers={"Authorization": f"Bearer {replacement_token}"},
    )
    assert limited.status_code == 429
    assert limited.json() == {"detail": "Request rate exceeded."}


def test_c15_replay_malformed_and_oversized_requests_fail_closed(
    authorized_client: tuple[AtlasVaultBackend, TestClient, dict[str, str], str],
) -> None:
    _, client, authorization, _ = authorized_client
    device, _ = _identities()
    _, proof = _session(client, device, account_id=ACCOUNT_A)
    replay = client.post(f"/v1/accounts/{ACCOUNT_A}/sessions", json=proof)
    assert replay.status_code == 401
    assert replay.json() == {"detail": "Authentication failed."}

    path = f"/v1/vaults/{VAULT_ID}/objects/adversarial-object"
    malformed = client.put(
        path,
        headers={
            **authorization,
            "If-Match": "*",
            "Idempotency-Key": "malformed-attempt",
            "Content-Type": "application/json",
        },
        content=b'{"ciphertext_b64":',
    )
    assert malformed.status_code == 422
    assert malformed.json() == {"detail": "Invalid request."}

    forbidden_marker = "DO_NOT_ECHO_RAW_KEY_MARKER"
    forbidden = _opaque_envelope(
        object_id="adversarial-object",
        revision="adversarial-r1",
        parent_revision=None,
        payload=b"opaque",
    )
    forbidden["raw_vault_key_b64"] = forbidden_marker
    invalid = client.put(
        path,
        headers=_write_headers(
            authorization,
            expected="*",
            idempotency_key="forbidden-attempt",
        ),
        json=forbidden,
    )
    assert invalid.status_code == 422
    assert invalid.json() == {"detail": "Invalid request."}
    assert forbidden_marker not in invalid.text

    oversized = _opaque_envelope(
        object_id="oversized-object",
        revision="oversized-r1",
        parent_revision=None,
        payload=b"opaque",
    )
    oversized_response = client.put(
        f"/v1/vaults/{VAULT_ID}/objects/oversized-object",
        headers={
            **_write_headers(
                authorization,
                expected="*",
                idempotency_key="oversized-attempt",
            ),
            "Content-Length": str(MAX_REQUEST_BYTES + 1),
        },
        content=json.dumps(oversized),
    )
    assert oversized_response.status_code == 413
    assert oversized_response.json() == {"detail": "Request body too large."}
    assert (
        client.get(
            f"/v1/vaults/{VAULT_ID}/objects/oversized-object",
            headers=authorization,
        ).status_code
        == 404
    )


def test_c15_logs_metrics_and_errors_are_secret_free(
    authorized_client: tuple[AtlasVaultBackend, TestClient, dict[str, str], str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    backend, client, authorization, token = authorized_client
    caplog.set_level(logging.INFO, logger="atlasvault_api.security")
    object_id = "pii-looking-object-user-at-example-com"
    ciphertext_marker = b"CIPHERTEXT_DERIVABLE_MARKER"
    envelope = _opaque_envelope(
        object_id=object_id,
        revision="secret-free-r1",
        parent_revision=None,
        payload=ciphertext_marker,
    )
    path = f"/v1/vaults/{VAULT_ID}/objects/{object_id}"
    idempotency_key = "SENSITIVE_IDEMPOTENCY_MARKER"
    success = client.put(
        path,
        headers=_write_headers(
            authorization,
            expected="*",
            idempotency_key=idempotency_key,
        ),
        json=envelope,
    )
    assert success.status_code == 200

    stale = dict(envelope)
    stale["revision"] = "secret-free-r2"
    conflict = client.put(
        path,
        headers=_write_headers(
            authorization,
            expected="wrong-parent",
            idempotency_key="SENSITIVE_CONFLICT_MARKER",
        ),
        json=stale,
    )
    assert conflict.status_code == 409
    assert conflict.json() == {"detail": "Opaque revision conflict."}

    unauthenticated = client.get(
        path,
        headers={"Authorization": "Bearer INVALID_TOKEN_SECRET_MARKER_000000000000"},
    )
    assert unauthenticated.status_code == 401
    assert unauthenticated.json() == {"detail": "Authorization failed."}

    telemetry = json.dumps(backend.telemetry.snapshot(), sort_keys=True)
    error_payloads = conflict.text + unauthenticated.text
    observed = telemetry + caplog.text + error_payloads
    forbidden_values = (
        token,
        ACCOUNT_A,
        _identities()[0].device_id,
        VAULT_ID,
        object_id,
        envelope["ciphertext_b64"],
        envelope["content_sha256"],
        idempotency_key,
        "SENSITIVE_CONFLICT_MARKER",
        "INVALID_TOKEN_SECRET_MARKER",
    )
    for forbidden in forbidden_values:
        assert forbidden not in observed

    snapshot = backend.telemetry.snapshot()
    assert snapshot["events"]
    assert {event["category"] for event in snapshot["events"]} <= {
        "account",
        "storage",
        "other",
    }
    assert all(
        set(metric) == {"category", "outcome", "count"}
        for metric in snapshot["metrics"]
    )


def test_c15_rate_limiter_is_windowed_and_fails_closed_on_clock_rollback() -> None:
    now = [10.0]
    limiter = AccountDeviceRateLimiter(
        AbuseControlPolicy(
            account_request_limit=2,
            device_request_limit=1,
            window_seconds=60.0,
            max_request_bytes=1024,
        ),
        monotonic=lambda: now[0],
    )
    first = StoragePrincipal(account_id="account-a", device_id="device-a")
    second = StoragePrincipal(account_id="account-a", device_id="device-b")
    limiter.consume(first)
    with pytest.raises(RequestRateExceeded):
        limiter.consume(first)
    limiter.consume(second)
    with pytest.raises(RequestRateExceeded):
        limiter.consume(StoragePrincipal("account-a", "device-c"))

    now[0] = 70.0
    limiter.consume(first)
    now[0] = 69.0
    with pytest.raises(RequestRateExceeded):
        limiter.consume(StoragePrincipal("account-b", "device-c"))


def test_c15_rate_limiter_samples_clock_after_request_scheduling() -> None:
    samples = iter((10.0, 11.0))
    sample_lock = threading.Lock()

    def monotonic() -> float:
        with sample_lock:
            return next(samples)

    class NewerRequestFirstLock:
        def __init__(self) -> None:
            self.older_waiting = threading.Event()
            self.release_older = threading.Event()
            self.mutex = threading.Lock()

        def __enter__(self) -> None:
            if threading.current_thread().name == "older-sample":
                self.older_waiting.set()
                assert self.release_older.wait(timeout=5)
            self.mutex.acquire()

        def __exit__(self, *_: object) -> None:
            self.mutex.release()
            if threading.current_thread().name == "newer-sample":
                self.release_older.set()

    limiter = AccountDeviceRateLimiter(
        AbuseControlPolicy(
            account_request_limit=4,
            device_request_limit=4,
            window_seconds=60.0,
            max_request_bytes=1024,
        ),
        monotonic=monotonic,
    )
    scheduling_lock = NewerRequestFirstLock()
    limiter._lock = scheduling_lock
    errors: list[RequestRateExceeded] = []

    def consume(device_id: str) -> None:
        try:
            limiter.consume(StoragePrincipal("account", device_id))
        except RequestRateExceeded as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    older = threading.Thread(
        target=consume,
        args=("older",),
        name="older-sample",
    )
    newer = threading.Thread(
        target=consume,
        args=("newer",),
        name="newer-sample",
    )
    older.start()
    assert scheduling_lock.older_waiting.wait(timeout=5)
    newer.start()
    older.join(timeout=5)
    newer.join(timeout=5)

    assert not older.is_alive()
    assert not newer.is_alive()
    assert errors == []


def test_c15_contract_declares_storage_controls() -> None:
    contract = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
    controls = contract["x-atlasvault-c15-controls"]
    assert controls == {
        "accountRequestLimit": ACCOUNT_REQUEST_LIMIT,
        "deviceRequestLimit": DEVICE_REQUEST_LIMIT,
        "maxRetainedAccounts": MAX_RETAINED_ACCOUNTS,
        "maxLiveChallenges": MAX_LIVE_CHALLENGES,
        "maxChallengesPerDevice": MAX_CHALLENGES_PER_DEVICE,
        "maxLiveSessions": MAX_LIVE_SESSIONS,
        "maxSessionsPerDevice": MAX_SESSIONS_PER_DEVICE,
        "maxDevicesPerAccount": MAX_DEVICES_PER_ACCOUNT,
        "maxRequestBytes": MAX_REQUEST_BYTES,
        "rateWindowSeconds": 60,
        "telemetryDimensions": ["category", "outcome", "count"],
    }
    generated = create_app().openapi()
    for path in (
        "/v1/vaults/{vault_id}/metadata",
        "/v1/vaults/{vault_id}/objects/{object_id}",
        "/v1/vaults/{vault_id}/patches",
        "/v1/vaults/{vault_id}/snapshots",
    ):
        for method, operation in contract["paths"][path].items():
            assert operation["security"] == [{"bearerAuth": []}]
            assert {"401", "413", "429"} <= set(operation["responses"])
            assert set(generated["paths"][path][method]["responses"]) == set(
                operation["responses"]
            )


def test_c15_account_bootstrap_is_bounded_before_retention() -> None:
    backend = AtlasVaultBackend(
        entropy=DeterministicEntropy(),
        monotonic=lambda: 3_000.0,
        abuse_policy=AbuseControlPolicy(max_accounts=1),
    )
    client = TestClient(create_app(backend))
    device_a, device_b = _identities()
    _bootstrap(client, device_a, account_id=ACCOUNT_A)

    rejected = _signed_transition(
        account_id=ACCOUNT_B,
        revision="20000000-0000-4000-8000-000000000001",
        parent_revision=None,
        device=device_b,
        signer=device_b,
    )
    rejected["transition"]["device"]["signature"] = "AA=="
    rejected["signature"] = "AA=="
    response = client.post(
        f"/v1/accounts/{ACCOUNT_B}/devices/bootstrap",
        json=rejected,
    )
    assert response.status_code == 429
    assert response.json() == {"detail": "Account capacity exceeded."}
    assert set(backend._accounts) == {ACCOUNT_A}


def test_c15_live_challenge_retention_is_bounded() -> None:
    backend = AtlasVaultBackend(
        entropy=DeterministicEntropy(),
        monotonic=lambda: 3_000.0,
        abuse_policy=AbuseControlPolicy(max_challenges=1),
    )
    client = TestClient(create_app(backend))
    device, _ = _identities()
    _bootstrap(client, device, account_id=ACCOUNT_A)
    path = f"/v1/accounts/{ACCOUNT_A}/auth/challenges"
    body = {"device_id": device.device_id}

    assert client.post(path, json=body).status_code == 201
    limited = client.post(path, json=body)
    assert limited.status_code == 429
    assert limited.json() == {"detail": "Challenge capacity exceeded."}
    assert len(backend._challenges) == 1


def test_c15_devices_per_account_are_bounded_before_mutation() -> None:
    backend = AtlasVaultBackend(
        entropy=DeterministicEntropy(),
        monotonic=lambda: 3_000.0,
        abuse_policy=AbuseControlPolicy(max_devices_per_account=1),
    )
    client = TestClient(create_app(backend))
    device_a, device_b = _identities()
    _bootstrap(client, device_a, account_id=ACCOUNT_A)
    token, _ = _session(client, device_a, account_id=ACCOUNT_A)

    response = client.post(
        f"/v1/accounts/{ACCOUNT_A}/devices",
        headers={"Authorization": f"Bearer {token}"},
        json=_signed_transition(
            account_id=ACCOUNT_A,
            revision=REVISION_2,
            parent_revision=REVISION_1,
            device=device_b,
            signer=device_a,
        ),
    )
    assert response.status_code == 429
    assert response.json() == {"detail": "Device capacity exceeded."}
    account = backend._accounts[ACCOUNT_A]
    assert set(account.devices) == {device_a.device_id}
    assert account.used_revisions == {REVISION_1}


def test_c15_challenge_capacity_is_isolated_per_account_device() -> None:
    backend = AtlasVaultBackend(
        entropy=DeterministicEntropy(),
        monotonic=lambda: 3_000.0,
        abuse_policy=AbuseControlPolicy(
            max_challenges=3,
            max_challenges_per_device=1,
        ),
    )
    client = TestClient(create_app(backend))
    device_a, device_b = _identities()
    _bootstrap(client, device_a, account_id=ACCOUNT_A)
    _bootstrap(
        client,
        device_b,
        account_id=ACCOUNT_B,
        revision="20000000-0000-4000-8000-000000000001",
    )

    path_a = f"/v1/accounts/{ACCOUNT_A}/auth/challenges"
    body_a = {"device_id": device_a.device_id}
    assert client.post(path_a, json=body_a).status_code == 201
    limited = client.post(path_a, json=body_a)
    assert limited.status_code == 429
    assert limited.json() == {"detail": "Challenge capacity exceeded."}

    path_b = f"/v1/accounts/{ACCOUNT_B}/auth/challenges"
    assert (
        client.post(path_b, json={"device_id": device_b.device_id}).status_code == 201
    )
    assert len(backend._challenges) == 2


def test_c15_live_session_retention_is_bounded_before_insertion() -> None:
    backend = AtlasVaultBackend(
        entropy=DeterministicEntropy(),
        monotonic=lambda: 3_000.0,
        abuse_policy=AbuseControlPolicy(
            max_sessions=1,
            max_sessions_per_device=1,
        ),
    )
    client = TestClient(create_app(backend))
    device, _ = _identities()
    _bootstrap(client, device, account_id=ACCOUNT_A)
    _session(client, device, account_id=ACCOUNT_A)

    proof = _challenge_proof(client, device, account_id=ACCOUNT_A)
    limited = client.post(f"/v1/accounts/{ACCOUNT_A}/sessions", json=proof)
    assert limited.status_code == 429
    assert limited.json() == {"detail": "Session capacity exceeded."}
    assert len(backend._sessions) == 1


def test_c15_account_openapi_matches_runtime_failures_and_body_limit() -> None:
    contract = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
    generated = create_app().openapi()
    for path in (
        "/v1/accounts/{account_id}/devices/bootstrap",
        "/v1/accounts/{account_id}/auth/challenges",
        "/v1/accounts/{account_id}/sessions",
        "/v1/accounts/{account_id}/devices",
    ):
        for method, operation in contract["paths"][path].items():
            assert "413" in operation["responses"]
            assert set(generated["paths"][path][method]["responses"]) == set(
                operation["responses"]
            )


def test_c15_storage_key_epochs_use_the_shared_signed_64_bit_bound(
    authorized_client: tuple[AtlasVaultBackend, TestClient, dict[str, str], str],
) -> None:
    backend, client, authorization, _ = authorized_client
    contract = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
    generated = create_app().openapi()
    for schemas, metadata_name, opaque_name in (
        (
            contract["components"]["schemas"],
            "EncryptedVaultMetadataEnvelope",
            "OpaqueCiphertextEnvelope",
        ),
        (
            generated["components"]["schemas"],
            "EncryptedVaultMetadataEnvelopeModel",
            "OpaqueCiphertextEnvelopeModel",
        ),
    ):
        assert schemas[metadata_name]["properties"]["key_epoch"]["maximum"] == (
            MAX_KEY_EPOCH
        )
        assert schemas[opaque_name]["properties"]["key_epoch"]["maximum"] == (
            MAX_KEY_EPOCH
        )

    envelope = _metadata(revision="epoch-too-large", payload=b"opaque")
    envelope["key_epoch"] = MAX_KEY_EPOCH + 1
    response = client.put(
        f"/v1/vaults/{VAULT_ID}/metadata",
        headers=_write_headers(
            authorization,
            expected="*",
            idempotency_key="epoch-too-large",
        ),
        json=envelope,
    )
    assert response.status_code == 422
    assert backend.storage._vaults == {}


@pytest.mark.parametrize(
    "field",
    (
        "account_request_limit",
        "device_request_limit",
        "max_request_bytes",
        "max_accounts",
        "max_challenges",
        "max_challenges_per_device",
        "max_sessions",
        "max_sessions_per_device",
        "max_devices_per_account",
    ),
)
@pytest.mark.parametrize("invalid", (float("nan"), 1.0, True))
def test_c15_abuse_control_integer_limits_require_positive_actual_integers(
    field: str,
    invalid: object,
) -> None:
    with pytest.raises(ValueError, match="invalid abuse-control policy"):
        AbuseControlPolicy(**{field: invalid})


def test_c15_telemetry_coarsens_unknown_categories() -> None:
    telemetry = SecretFreeTelemetry()
    telemetry.record("user-controlled-value", 418)
    assert telemetry.snapshot() == {
        "events": [
            {
                "category": "other",
                "outcome": "error",
                "status_code": 418,
            }
        ],
        "metrics": [{"category": "other", "outcome": "error", "count": 1}],
    }
