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

import atlasvault_api.app as app_module
from atlasvault_api.app import (
    ACCOUNT_SESSION_PROOF_DOMAIN,
    CHALLENGE_LIFETIME_SECONDS,
    DEVICE_REGISTRY_TRANSITION_DOMAIN,
    SESSION_LIFETIME_SECONDS,
    AtlasVaultBackend,
    create_app,
)
from vaultsync.device_identity import (
    DeviceIdentity,
    device_identity_from_private_keys,
    generate_device_identity,
)
from vaultsync.service_contract_guard import (
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
UTC_SECONDS_PATTERN = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"
BASE64_32_PATTERN = r"^[A-Za-z0-9+/]{43}=$"
BASE64_64_PATTERN = r"^[A-Za-z0-9+/]{86}==$"


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
    assert contract["info"]["version"] == "1.2.0"
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
        assert (
            contract["components"]["schemas"][schema_name]["additionalProperties"]
            is False
        )

    for path in (
        "/v1/vaults/{vault_id}/metadata",
        "/v1/vaults/{vault_id}/objects/{object_id}",
        "/v1/vaults/{vault_id}/patches",
        "/v1/vaults/{vault_id}/snapshots",
    ):
        for operation in contract["paths"][path].values():
            assert operation["x-atlasvault-implementation-chunk"] == "C14"

    generated = create_app(AtlasVaultBackend()).openapi()
    implemented_operations = {
        ("/v1/accounts/{account_id}/devices/bootstrap", "post"),
        ("/v1/accounts/{account_id}/auth/challenges", "post"),
        ("/v1/accounts/{account_id}/sessions", "post"),
        ("/v1/accounts/{account_id}/devices", "get"),
        ("/v1/accounts/{account_id}/devices", "post"),
        ("/v1/vaults/{vault_id}/metadata", "put"),
        ("/v1/vaults/{vault_id}/metadata", "get"),
        ("/v1/vaults/{vault_id}/objects/{object_id}", "put"),
        ("/v1/vaults/{vault_id}/objects/{object_id}", "get"),
        ("/v1/vaults/{vault_id}/patches", "post"),
        ("/v1/vaults/{vault_id}/patches", "get"),
        ("/v1/vaults/{vault_id}/snapshots", "put"),
        ("/v1/vaults/{vault_id}/snapshots", "get"),
    }
    assert {
        (path, method)
        for path, path_item in generated["paths"].items()
        for method in path_item
    } == implemented_operations
    for path, method in implemented_operations:
        assert (
            generated["paths"][path][method]["operationId"]
            == contract["paths"][path][method]["operationId"]
        )

    assert find_raw_secret_wire_contract_violations(ROOT / "services") == []


def test_c13_contract_publishes_runtime_bounds_and_validation_responses() -> None:
    contract = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
    generated = create_app(AtlasVaultBackend()).openapi()
    maximum = (1 << 63) - 1
    assert (
        contract["components"]["schemas"]["DeviceDescriptor"]["properties"][
            "key_epoch"
        ]["maximum"]
        == maximum
    )
    assert (
        generated["components"]["schemas"]["DeviceDescriptorModel"]["properties"][
            "key_epoch"
        ]["maximum"]
        == maximum
    )

    for path, path_item in contract["paths"].items():
        for method, operation in path_item.items():
            assert "422" in operation["responses"], f"{method.upper()} {path}"


def test_c13_account_wire_models_reject_coerced_json_types() -> None:
    backend = AtlasVaultBackend(
        entropy=DeterministicEntropy(),
        monotonic=lambda: 1_000.0,
    )
    client = TestClient(create_app(backend))
    device, _ = _identities()
    transition = _signed_transition(
        account_id=ACCOUNT_A,
        revision=REVISION_1,
        parent_revision=None,
        device=device,
        signer=device,
    )
    descriptor = transition["transition"]["device"]["descriptor"]
    descriptor["key_epoch"] = str(descriptor["key_epoch"])

    response = client.post(
        f"/v1/accounts/{ACCOUNT_A}/devices/bootstrap",
        json=transition,
    )
    assert response.status_code == 422
    assert backend._accounts == {}


def test_c13_registry_revision_schema_matches_runtime_uuid_boundary() -> None:
    backend = AtlasVaultBackend(
        entropy=DeterministicEntropy(),
        monotonic=lambda: 1_000.0,
    )
    client = TestClient(create_app(backend))
    device, _ = _identities()
    transition = _signed_transition(
        account_id=ACCOUNT_A,
        revision="not-a-uuid",
        parent_revision=None,
        device=device,
        signer=device,
    )

    response = client.post(
        f"/v1/accounts/{ACCOUNT_A}/devices/bootstrap",
        json=transition,
    )
    assert response.status_code == 422

    contract = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
    canonical_schemas = contract["components"]["schemas"]
    for schema_name in ("DeviceRegistryTransition", "DeviceRegistryView"):
        revision = canonical_schemas[schema_name]["properties"]["revision"]
        assert revision["format"] == "uuid"
        assert revision["pattern"] == (
            "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
        )
    canonical_parent = canonical_schemas["DeviceRegistryTransition"]["properties"][
        "parent_revision"
    ]
    assert (
        canonical_parent["pattern"]
        == canonical_schemas["DeviceRegistryTransition"]["properties"]["revision"][
            "pattern"
        ]
    )

    served_schemas = client.get("/openapi.json").json()["components"]["schemas"]
    schema = served_schemas["DeviceRegistryTransitionModel"]["properties"]
    assert schema["revision"]["format"] == "uuid"
    assert (
        schema["revision"]["pattern"]
        == canonical_schemas["DeviceRegistryTransition"]["properties"]["revision"][
            "pattern"
        ]
    )
    parent = next(
        option
        for option in schema["parent_revision"]["anyOf"]
        if option.get("type") == "string"
    )
    assert parent["format"] == "uuid"
    assert parent["pattern"] == schema["revision"]["pattern"]
    registry_view_revision = served_schemas["DeviceRegistryView"]["properties"][
        "revision"
    ]
    assert registry_view_revision["format"] == "uuid"
    assert registry_view_revision["pattern"] == schema["revision"]["pattern"]


def test_c13_served_openapi_matches_account_encoding_and_validation_errors() -> None:
    generated = create_app(AtlasVaultBackend()).openapi()
    schemas = generated["components"]["schemas"]
    encoded_fields = {
        "DeviceDescriptorModel": (
            "signing_public_key",
            "agreement_public_key",
        ),
        "SignedDeviceDescriptorModel": ("signature",),
        "SignedDeviceRegistryTransitionModel": ("signature",),
        "AuthenticationChallenge": ("challenge",),
        "SessionProofRequest": ("signature",),
    }
    for schema_name, fields in encoded_fields.items():
        for field_name in fields:
            assert (
                schemas[schema_name]["properties"][field_name]["contentEncoding"]
                == "base64"
            )

    validation_schema = schemas["RequestValidationFailure"]
    assert validation_schema["properties"]["detail"] == {
        "const": "Invalid request.",
        "title": "Detail",
        "type": "string",
    }
    for path_item in generated["paths"].values():
        for operation in path_item.values():
            response = operation["responses"]["422"]
            assert response["content"]["application/json"]["schema"] == {
                "$ref": "#/components/schemas/RequestValidationFailure"
            }


def test_c13_descriptor_timestamp_schema_matches_runtime_boundary() -> None:
    backend = AtlasVaultBackend(
        entropy=DeterministicEntropy(),
        monotonic=lambda: 1_000.0,
    )
    client = TestClient(create_app(backend))
    device, _ = _identities()
    transition = _signed_transition(
        account_id=ACCOUNT_A,
        revision=REVISION_1,
        parent_revision=None,
        device=device,
        signer=device,
    )
    transition["transition"]["device"]["descriptor"]["created_at"] = (
        "2026-08-29T00:00:00+00:00"
    )
    response = client.post(
        f"/v1/accounts/{ACCOUNT_A}/devices/bootstrap",
        json=transition,
    )
    assert response.status_code == 422
    assert backend._accounts == {}

    contract = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
    canonical = contract["components"]["schemas"]["DeviceDescriptor"]["properties"][
        "created_at"
    ]
    served = client.get("/openapi.json").json()["components"]["schemas"][
        "DeviceDescriptorModel"
    ]["properties"]["created_at"]
    for timestamp in (canonical, served):
        assert timestamp["format"] == "date-time"
        assert timestamp["pattern"] == UTC_SECONDS_PATTERN


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


def test_c13_session_issuance_prunes_expired_digests() -> None:
    now = [1_000.0]
    backend = AtlasVaultBackend(
        entropy=DeterministicEntropy(),
        monotonic=lambda: now[0],
    )
    client = TestClient(create_app(backend))
    device_a, _ = _identities()
    _bootstrap(client, device_a)

    first_token, _ = _session(client, device_a)
    first_digest = hashlib.sha256(first_token.encode("ascii")).digest()
    assert first_digest in backend._sessions

    now[0] += SESSION_LIFETIME_SECONDS + 1
    second_token, _ = _session(client, device_a)
    second_digest = hashlib.sha256(second_token.encode("ascii")).digest()
    assert first_digest not in backend._sessions
    assert set(backend._sessions) == {second_digest}


def test_c13_challenge_issuance_prunes_expired_challenges() -> None:
    now = [1_000.0]
    backend = AtlasVaultBackend(
        entropy=DeterministicEntropy(),
        monotonic=lambda: now[0],
    )
    client = TestClient(create_app(backend))
    device_a, _ = _identities()
    _bootstrap(client, device_a)

    first = client.post(
        f"/v1/accounts/{ACCOUNT_A}/auth/challenges",
        json={"device_id": device_a.device_id},
    ).json()
    first_key = (ACCOUNT_A, first["challenge_id"])
    assert first_key in backend._challenges

    now[0] += CHALLENGE_LIFETIME_SECONDS + 1
    second = client.post(
        f"/v1/accounts/{ACCOUNT_A}/auth/challenges",
        json={"device_id": device_a.device_id},
    ).json()
    assert first_key not in backend._challenges
    assert set(backend._challenges) == {(ACCOUNT_A, second["challenge_id"])}


def test_c13_bearer_scheme_is_case_insensitive(
    backend_client: tuple[AtlasVaultBackend, TestClient],
) -> None:
    _, client = backend_client
    device_a, _ = _identities()
    _bootstrap(client, device_a)
    token, _ = _session(client, device_a)

    response = client.get(
        f"/v1/accounts/{ACCOUNT_A}/devices",
        headers={"Authorization": f"bEaReR {token}"},
    )
    assert response.status_code == 200


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


def test_c13_registry_rejects_a_previously_used_revision(
    backend_client: tuple[AtlasVaultBackend, TestClient],
) -> None:
    _, client = backend_client
    device_a, device_b = _identities()
    device_c = generate_device_identity(created_at="2026-08-29T00:00:00Z")
    _bootstrap(client, device_a)
    token, _ = _session(client, device_a)
    headers = {"Authorization": f"Bearer {token}"}

    first = client.post(
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
    assert first.status_code == 200, first.text

    cycled = client.post(
        f"/v1/accounts/{ACCOUNT_A}/devices",
        headers=headers,
        json=_signed_transition(
            account_id=ACCOUNT_A,
            revision=REVISION_1,
            parent_revision=REVISION_2,
            device=device_c,
            signer=device_a,
        ),
    )
    assert cycled.status_code == 409
    assert (
        client.get(
            f"/v1/accounts/{ACCOUNT_A}/devices",
            headers=headers,
        ).json()["revision"]
        == REVISION_2
    )


def test_c13_served_openapi_declares_bearer_security() -> None:
    generated = create_app().openapi()
    assert generated["components"]["securitySchemes"]["bearerAuth"] == {
        "type": "http",
        "scheme": "bearer",
    }

    protected_operations = {
        ("/v1/accounts/{account_id}/devices", "get"),
        ("/v1/accounts/{account_id}/devices", "post"),
        ("/v1/vaults/{vault_id}/metadata", "get"),
        ("/v1/vaults/{vault_id}/metadata", "put"),
        ("/v1/vaults/{vault_id}/objects/{object_id}", "get"),
        ("/v1/vaults/{vault_id}/objects/{object_id}", "put"),
        ("/v1/vaults/{vault_id}/patches", "get"),
        ("/v1/vaults/{vault_id}/patches", "post"),
        ("/v1/vaults/{vault_id}/snapshots", "get"),
        ("/v1/vaults/{vault_id}/snapshots", "put"),
    }
    for path, method in protected_operations:
        operation = generated["paths"][path][method]
        assert operation["security"] == [{"bearerAuth": []}]
        assert all(
            parameter.get("name") != "Authorization"
            for parameter in operation.get("parameters", [])
        )


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
    stale["transition"]["parent_revision"] = "10000000-0000-4000-8000-000000000099"
    stale["signature"] = _encode64(
        device_a.sign(
            DEVICE_REGISTRY_TRANSITION_DOMAIN + _canonical_bytes(stale["transition"])
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


def test_c13_models_reject_forbidden_extra_wire_fields(
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


def test_c13_device_signature_verification_runs_outside_backend_lock(
    backend_client: tuple[AtlasVaultBackend, TestClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, client = backend_client
    device_a, device_b = _identities()
    _bootstrap(client, device_a)
    token, _ = _session(client, device_a)
    lock_observations: list[bool] = []
    original_transition_verify = app_module._verify_transition_signature
    original_descriptor_verify = app_module.SignedDeviceDescriptorModel.verified

    def verify_transition(*args: object, **kwargs: object) -> None:
        lock_observations.append(backend._lock._is_owned())
        original_transition_verify(*args, **kwargs)

    def verify_descriptor(
        model: app_module.SignedDeviceDescriptorModel,
    ) -> object:
        lock_observations.append(backend._lock._is_owned())
        return original_descriptor_verify(model)

    monkeypatch.setattr(app_module, "_verify_transition_signature", verify_transition)
    monkeypatch.setattr(
        app_module.SignedDeviceDescriptorModel,
        "verified",
        verify_descriptor,
    )
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

    assert response.status_code == 200, response.text
    assert lock_observations == [False, False, False]


def test_c13_account_base64_schema_requires_exact_binary_lengths(
    backend_client: tuple[AtlasVaultBackend, TestClient],
) -> None:
    _, client = backend_client
    contract = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
    served = create_app(AtlasVaultBackend()).openapi()
    for schemas, names in (
        (
            contract["components"]["schemas"],
            {
                "descriptor": "DeviceDescriptor",
                "signed_descriptor": "SignedDeviceDescriptor",
                "signed_transition": "SignedDeviceRegistryTransition",
                "challenge": "AuthenticationChallenge",
                "proof": "SessionProofRequest",
            },
        ),
        (
            served["components"]["schemas"],
            {
                "descriptor": "DeviceDescriptorModel",
                "signed_descriptor": "SignedDeviceDescriptorModel",
                "signed_transition": "SignedDeviceRegistryTransitionModel",
                "challenge": "AuthenticationChallenge",
                "proof": "SessionProofRequest",
            },
        ),
    ):
        descriptor = schemas[names["descriptor"]]["properties"]
        for field in ("signing_public_key", "agreement_public_key"):
            value = descriptor[field]
            assert value["type"] == "string"
            assert value["minLength"] == 44
            assert value["maxLength"] == 44
            assert value["pattern"] == BASE64_32_PATTERN
            assert value["contentEncoding"] == "base64"
        for schema_name, field, pattern, length in (
            (names["signed_descriptor"], "signature", BASE64_64_PATTERN, 88),
            (names["signed_transition"], "signature", BASE64_64_PATTERN, 88),
            (names["challenge"], "challenge", BASE64_32_PATTERN, 44),
            (names["proof"], "signature", BASE64_64_PATTERN, 88),
        ):
            value = schemas[schema_name]["properties"][field]
            assert value["minLength"] == length
            assert value["maxLength"] == length
            assert value["pattern"] == pattern
            assert value["contentEncoding"] == "base64"

    device, _ = _identities()
    bootstrap = _signed_transition(
        account_id=ACCOUNT_A,
        revision=REVISION_1,
        parent_revision=None,
        device=device,
        signer=device,
    )
    bootstrap["transition"]["device"]["descriptor"]["signing_public_key"] = ""
    response = client.post(
        f"/v1/accounts/{ACCOUNT_A}/devices/bootstrap",
        json=bootstrap,
    )
    assert response.status_code == 422

    noncanonical = _signed_transition(
        account_id=ACCOUNT_A,
        revision=REVISION_1,
        parent_revision=None,
        device=device,
        signer=device,
    )
    noncanonical["transition"]["device"]["descriptor"]["signing_public_key"] = (
        f"{'A' * 42}B="
    )
    response = client.post(
        f"/v1/accounts/{ACCOUNT_A}/devices/bootstrap",
        json=noncanonical,
    )
    assert response.status_code == 422

    assert BASE64_32_PATTERN == r"^[A-Za-z0-9+/]{42}[AQgw]=$"
    assert BASE64_64_PATTERN == r"^[A-Za-z0-9+/]{85}[AEIMQUYcgkosw048]==$"


def test_c13_protected_unauthorized_routes_send_bearer_challenge() -> None:
    client = TestClient(create_app())

    devices = client.get(f"/v1/accounts/{ACCOUNT_A}/devices")
    storage = client.get("/v1/vaults/test-vault/metadata")
    challenge = client.post(
        f"/v1/accounts/{ACCOUNT_A}/auth/challenges",
        json={"device_id": f"avd1-{'0' * 64}"},
    )

    assert devices.status_code == 401
    assert devices.headers["www-authenticate"] == "Bearer"
    assert storage.status_code == 401
    assert storage.headers["www-authenticate"] == "Bearer"
    assert challenge.status_code == 401
    assert "www-authenticate" not in challenge.headers


def test_c13_canonical_reusable_errors_publish_json_body_schemas() -> None:
    contract = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
    responses = contract["components"]["responses"]
    for name in (
        "InvalidRequest",
        "Unauthorized",
        "Conflict",
        "NotFound",
        "RequestTooLarge",
        "RateLimited",
    ):
        assert responses[name]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/FixedErrorResponse"
        }
    assert responses["ValidationFailed"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/RequestValidationFailure"
    }
    schemas = contract["components"]["schemas"]
    assert schemas["FixedErrorResponse"]["required"] == ["detail"]
    assert schemas["RequestValidationFailure"]["properties"]["detail"] == {
        "type": "string",
        "const": "Invalid request.",
    }
