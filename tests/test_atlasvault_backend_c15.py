from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "packages" / "vaultsync"))
sys.path.insert(0, str(ROOT / "services" / "atlasvault-api"))

from atlasvault_api.app import AtlasVaultBackend, create_app
from test_atlasvault_backend_c13 import (
    ACCOUNT_A,
    ACCOUNT_B,
    REVISION_1,
    REVISION_2,
    _bootstrap,
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
