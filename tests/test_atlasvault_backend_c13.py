from __future__ import annotations

import base64
import hashlib
import json
import sys
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "vaultsync"))
sys.path.insert(0, str(ROOT / "services" / "atlasvault-api"))

from atlasvault_api.app import (  # noqa: E402
    ACCOUNT_SESSION_PROOF_DOMAIN,
    DEVICE_REGISTRY_TRANSITION_DOMAIN,
    AtlasVaultBackend,
    create_app,
)
from vaultsync.device_identity import (  # noqa: E402
    DeviceIdentity,
    device_identity_from_private_keys,
)
from vaultsync.service_contract_guard import (  # noqa: E402
    BANNED_WIRE_FIELD_NAMES,
    find_raw_secret_wire_contract_violations,
)


VECTOR_PATH = (
    ROOT
    / "contracts"
    / "sync"
    / "test_vectors"
    / "atlasvault_device_identity_pairing_vectors_v1.json"
)
OPENAPI_PATH = ROOT / "contracts" / "api" / "atlasvault_sync_openapi.json"
ACCOUNT_A = f"ava1-{hashlib.sha256(b'account-a').hexdigest()}"
ACCOUNT_B = f"ava1-{hashlib.sha256(b'account-b').hexdigest()}"
REVISION_1 = "10000000-0000-4000-8000-000000000001"
REVISION_2 = "10000000-0000-4000-8000-000000000002"


class DeterministicEntropy:
    def __init__(self) -> None:
        self._counter = 0

    def __call__(self, length: int) -> bytes:
        self._counter += 1
        result = b""
        block = 0
        while len(result) < length:
            result += hashlib.sha256(
                f"c13-entropy:{self._counter}:{block}".encode("ascii")
            ).digest()
            block += 1
        return result[:length]


def _canonical_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _decode64(value: str) -> bytes:
    return base64.b64decode(value.encode("ascii"), validate=True)


def _encode64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _identities() -> tuple[DeviceIdentity, DeviceIdentity]:
    vectors = json.loads(VECTOR_PATH.read_text(encoding="utf-8"))
    result = []
    for name in ("device_a", "device_b"):
        vector = vectors[name]
        result.append(
            device_identity_from_private_keys(
                signing_private_seed=_decode64(vector["signing_private_seed"]),
                agreement_private_key=_decode64(vector["agreement_private_key"]),
                created_at=vector["descriptor"]["created_at"],
                key_epoch=vector["descriptor"]["key_epoch"],
            )
        )
    return result[0], result[1]


def _signed_transition(
    *,
    account_id: str,
    revision: str,
    parent_revision: str | None,
    device: DeviceIdentity,
    signer: DeviceIdentity,
) -> dict[str, Any]:
    transition = {
        "format": "atlasvault-device-registry-transition",
        "version": 1,
        "account_id": account_id,
        "revision": revision,
        "parent_revision": parent_revision,
        "operation": "add",
        "device": device.sign_descriptor().to_dict(),
        "signer_device_id": signer.device_id,
    }
    return {
        "format": "atlasvault-signed-device-registry-transition",
        "version": 1,
        "transition": transition,
        "signature": _encode64(
            signer.sign(
                DEVICE_REGISTRY_TRANSITION_DOMAIN + _canonical_bytes(transition)
            )
        ),
    }


def _bootstrap(
    client: TestClient,
    identity: DeviceIdentity,
    *,
    account_id: str = ACCOUNT_A,
    revision: str = REVISION_1,
) -> dict[str, Any]:
    response = client.post(
        f"/v1/accounts/{account_id}/devices/bootstrap",
        json=_signed_transition(
            account_id=account_id,
            revision=revision,
            parent_revision=None,
            device=identity,
            signer=identity,
        ),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _session(
    client: TestClient,
    identity: DeviceIdentity,
    *,
    account_id: str = ACCOUNT_A,
) -> tuple[str, dict[str, Any]]:
    challenge_response = client.post(
        f"/v1/accounts/{account_id}/auth/challenges",
        json={"device_id": identity.device_id},
    )
    assert challenge_response.status_code == 201, challenge_response.text
    challenge = challenge_response.json()
    proof_payload = {
        "format": "atlasvault-account-session-proof",
        "version": 1,
        "account_id": account_id,
        "device_id": identity.device_id,
        "challenge_id": challenge["challenge_id"],
        "challenge": challenge["challenge"],
    }
    proof = {
        "device_id": identity.device_id,
        "challenge_id": challenge["challenge_id"],
        "signature": _encode64(
            identity.sign(
                ACCOUNT_SESSION_PROOF_DOMAIN + _canonical_bytes(proof_payload)
            )
        ),
    }
    session_response = client.post(
        f"/v1/accounts/{account_id}/sessions",
        json=proof,
    )
    assert session_response.status_code == 201, session_response.text
    return session_response.json()["access_token"], proof


@pytest.fixture
def backend_client() -> tuple[AtlasVaultBackend, TestClient]:
    backend = AtlasVaultBackend(
        entropy=DeterministicEntropy(),
        monotonic=lambda: 1_000.0,
    )
    return backend, TestClient(create_app(backend))


def test_c13_openapi_is_zero_knowledge_and_matches_wire_guard() -> None:
    contract = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))

    assert contract["openapi"] == "3.1.0"
    assert contract["info"]["version"] == "1.0.0"
    assert set(contract["paths"]) == {
        "/v1/accounts/{account_id}/devices/bootstrap",
        "/v1/accounts/{account_id}/auth/challenges",
        "/v1/accounts/{account_id}/sessions",
        "/v1/accounts/{account_id}/devices",
        "/v1/vaults/{vault_id}/metadata",
        "/v1/vaults/{vault_id}/objects/{object_id}",
        "/v1/vaults/{vault_id}/patches",
        "/v1/vaults/{vault_id}/snapshots",
    }
    properties = {
        property_name.casefold()
        for schema in contract["components"]["schemas"].values()
        for property_name in schema.get("properties", {})
    }
    assert properties.isdisjoint(BANNED_WIRE_FIELD_NAMES)

    for schema_name in (
        "AuthenticationChallengeRequest",
        "SessionProofRequest",
        "DeviceDescriptor",
        "SignedDeviceDescriptor",
        "DeviceRegistryTransition",
        "SignedDeviceRegistryTransition",
        "OpaqueCiphertextEnvelope",
    ):
        assert contract["components"]["schemas"][schema_name][
            "additionalProperties"
        ] is False

    for path in (
        "/v1/vaults/{vault_id}/metadata",
        "/v1/vaults/{vault_id}/objects/{object_id}",
        "/v1/vaults/{vault_id}/patches",
        "/v1/vaults/{vault_id}/snapshots",
    ):
        for operation in contract["paths"][path].values():
            assert operation["x-atlasvault-implementation-chunk"] == "C14"

    assert find_raw_secret_wire_contract_violations(ROOT / "services") == []


def test_c13_account_session_authenticates_device_and_stores_only_token_digest(
    backend_client: tuple[AtlasVaultBackend, TestClient],
) -> None:
    backend, client = backend_client
    device_a, _ = _identities()
    registry = _bootstrap(client, device_a)

    assert registry["account_id"] == ACCOUNT_A
    assert registry["revision"] == REVISION_1
    assert [item["descriptor"]["device_id"] for item in registry["devices"]] == [
        device_a.device_id
    ]

    token, _ = _session(client, device_a)
    response = client.get(
        f"/v1/accounts/{ACCOUNT_A}/devices",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert token not in repr(backend._sessions)
    assert hashlib.sha256(token.encode("ascii")).digest() in backend._sessions


def test_c13_session_proof_is_single_use_and_account_scoped(
    backend_client: tuple[AtlasVaultBackend, TestClient],
) -> None:
    _, client = backend_client
    device_a, device_b = _identities()
    _bootstrap(client, device_a)
    token, proof = _session(client, device_a)

    replay = client.post(f"/v1/accounts/{ACCOUNT_A}/sessions", json=proof)
    assert replay.status_code == 401
    wrong_account = client.get(
        f"/v1/accounts/{ACCOUNT_B}/devices",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert wrong_account.status_code == 401

    challenge = client.post(
        f"/v1/accounts/{ACCOUNT_A}/auth/challenges",
        json={"device_id": device_a.device_id},
    ).json()
    forged = {
        "device_id": device_a.device_id,
        "challenge_id": challenge["challenge_id"],
        "signature": _encode64(device_b.sign(b"not-the-session-proof")),
    }
    assert (
        client.post(f"/v1/accounts/{ACCOUNT_A}/sessions", json=forged).status_code
        == 401
    )


def test_c13_signed_device_transition_adds_public_descriptor_only(
    backend_client: tuple[AtlasVaultBackend, TestClient],
) -> None:
    _, client = backend_client
    device_a, device_b = _identities()
    _bootstrap(client, device_a)
    token, _ = _session(client, device_a)
    headers = {"Authorization": f"Bearer {token}"}

    response = client.post(
        f"/v1/accounts/{ACCOUNT_A}/devices",
        headers=headers,
        json=_signed_transition(
            account_id=ACCOUNT_A,
            revision=REVISION_2,
            parent_revision=REVISION_1,
            device=device_b,
            signer=device_a,
        ),
    )
    assert response.status_code == 200, response.text
    registry = response.json()
    assert registry["revision"] == REVISION_2
    assert {item["descriptor"]["device_id"] for item in registry["devices"]} == {
        device_a.device_id,
        device_b.device_id,
    }
    serialized = json.dumps(registry, sort_keys=True)
    assert "private" not in serialized.casefold()
    assert "passphrase" not in serialized.casefold()


def test_c13_registry_tamper_and_stale_parent_fail_closed(
    backend_client: tuple[AtlasVaultBackend, TestClient],
) -> None:
    _, client = backend_client
    device_a, device_b = _identities()
    _bootstrap(client, device_a)
    token, _ = _session(client, device_a)
    headers = {"Authorization": f"Bearer {token}"}
    transition = _signed_transition(
        account_id=ACCOUNT_A,
        revision=REVISION_2,
        parent_revision=REVISION_1,
        device=device_b,
        signer=device_a,
    )

    tampered = deepcopy(transition)
    tampered["transition"]["revision"] = str(uuid.uuid4())
    assert (
        client.post(
            f"/v1/accounts/{ACCOUNT_A}/devices",
            headers=headers,
            json=tampered,
        ).status_code
        == 400
    )

    stale = deepcopy(transition)
    stale["transition"]["parent_revision"] = None
    stale["signature"] = _encode64(
        device_a.sign(
            DEVICE_REGISTRY_TRANSITION_DOMAIN
            + _canonical_bytes(stale["transition"])
        )
    )
    assert (
        client.post(
            f"/v1/accounts/{ACCOUNT_A}/devices",
            headers=headers,
            json=stale,
        ).status_code
        == 409
    )


def test_c13_models_reject_forbidden_extra_wire_fields_and_storage_is_deferred(
    backend_client: tuple[AtlasVaultBackend, TestClient],
) -> None:
    _, client = backend_client
    device_a, _ = _identities()
    bootstrap = _signed_transition(
        account_id=ACCOUNT_A,
        revision=REVISION_1,
        parent_revision=None,
        device=device_a,
        signer=device_a,
    )
    bootstrap["raw_vault_key_b64"] = "ZmFrZQ=="
    assert (
        client.post(
            f"/v1/accounts/{ACCOUNT_A}/devices/bootstrap",
            json=bootstrap,
        ).status_code
        == 422
    )

    assert (
        client.put(
            "/v1/vaults/opaque-vault/objects/opaque-object",
            json={"ciphertext": "ZmFrZQ=="},
        ).status_code
        == 404
    )
