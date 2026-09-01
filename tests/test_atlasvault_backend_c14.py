from __future__ import annotations

import base64
import hashlib
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "vaultsync"))
sys.path.insert(0, str(ROOT / "services" / "atlasvault-api"))

from atlasvault_api.app import (
    ACCOUNT_SESSION_PROOF_DOMAIN,
    DEVICE_REGISTRY_TRANSITION_DOMAIN,
    AtlasVaultBackend,
    create_app,
)
from atlasvault_api.controls import AbuseControlPolicy
from atlasvault_api.storage import (
    EncryptedVaultMetadataEnvelopeModel,
    InMemoryOpaqueStore,
    InvalidOpaqueStorageRequest,
    OpaqueStorageCapacityExceeded,
    OpaqueStorageConflict,
)
from vaultsync.device_identity import DeviceIdentity, device_identity_from_private_keys
from vaultsync.service_contract_guard import find_raw_secret_wire_contract_violations

VECTOR_PATH = (
    ROOT
    / "contracts"
    / "sync"
    / "test_vectors"
    / "atlasvault_device_identity_pairing_vectors_v1.json"
)
OPENAPI_PATH = ROOT / "contracts" / "api" / "atlasvault_sync_openapi.json"
ACCOUNT_ID = f"ava1-{hashlib.sha256(b'c14-account').hexdigest()}"
VAULT_ID = "vault-c14-opaque"
REGISTRY_REVISION = "14000000-0000-4000-8000-000000000001"
_CURSOR_LIFETIME_SECONDS = 300
_RECEIPT_LIFETIME_SECONDS = 600
HEADER_SAFE_ASCII_PATTERN = r"^[!-~]+$"
REVISION_PATTERN = r"^[A-Za-z0-9._~-]+$"
IF_MATCH_HEADER_PATTERN = r'^(?:\*|"[A-Za-z0-9._~-]+")$'


class DeterministicEntropy:
    def __init__(self) -> None:
        self._counter = 0

    def __call__(self, length: int) -> bytes:
        self._counter += 1
        output = b""
        block = 0
        while len(output) < length:
            output += hashlib.sha256(
                f"c14-entropy:{self._counter}:{block}".encode("ascii")
            ).digest()
            block += 1
        return output[:length]


def _encode64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _canonical_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _identity() -> DeviceIdentity:
    vector = json.loads(VECTOR_PATH.read_text(encoding="utf-8"))["device_a"]
    return device_identity_from_private_keys(
        signing_private_seed=base64.b64decode(vector["signing_private_seed"]),
        agreement_private_key=base64.b64decode(vector["agreement_private_key"]),
        created_at=vector["descriptor"]["created_at"],
        key_epoch=vector["descriptor"]["key_epoch"],
    )


def _transition(identity: DeviceIdentity) -> dict[str, Any]:
    transition = {
        "format": "atlasvault-device-registry-transition",
        "version": 1,
        "account_id": ACCOUNT_ID,
        "revision": REGISTRY_REVISION,
        "parent_revision": None,
        "operation": "add",
        "device": identity.sign_descriptor().to_dict(),
        "signer_device_id": identity.device_id,
    }
    return {
        "format": "atlasvault-signed-device-registry-transition",
        "version": 1,
        "transition": transition,
        "signature": _encode64(
            identity.sign(
                DEVICE_REGISTRY_TRANSITION_DOMAIN + _canonical_bytes(transition)
            )
        ),
    }


def _authorize(client: TestClient, identity: DeviceIdentity) -> dict[str, str]:
    bootstrap = client.post(
        f"/v1/accounts/{ACCOUNT_ID}/devices/bootstrap",
        json=_transition(identity),
    )
    assert bootstrap.status_code == 201, bootstrap.text
    challenge_response = client.post(
        f"/v1/accounts/{ACCOUNT_ID}/auth/challenges",
        json={"device_id": identity.device_id},
    )
    assert challenge_response.status_code == 201, challenge_response.text
    challenge = challenge_response.json()
    proof_payload = {
        "format": "atlasvault-account-session-proof",
        "version": 1,
        "account_id": ACCOUNT_ID,
        "device_id": identity.device_id,
        "challenge_id": challenge["challenge_id"],
        "challenge": challenge["challenge"],
    }
    session = client.post(
        f"/v1/accounts/{ACCOUNT_ID}/sessions",
        json={
            "device_id": identity.device_id,
            "challenge_id": challenge["challenge_id"],
            "signature": _encode64(
                identity.sign(
                    ACCOUNT_SESSION_PROOF_DOMAIN + _canonical_bytes(proof_payload)
                )
            ),
        },
    )
    assert session.status_code == 201, session.text
    return {"Authorization": f"Bearer {session.json()['access_token']}"}


@pytest.fixture
def storage_client() -> tuple[AtlasVaultBackend, TestClient, dict[str, str]]:
    backend = AtlasVaultBackend(
        entropy=DeterministicEntropy(),
        monotonic=lambda: 2_000.0,
    )
    client = TestClient(create_app(backend))
    return backend, client, _authorize(client, _identity())


def _write_headers(
    authorization: dict[str, str],
    *,
    expected: str,
    idempotency_key: str,
) -> dict[str, str]:
    return {
        **authorization,
        "If-Match": expected if expected == "*" else f'"{expected}"',
        "Idempotency-Key": idempotency_key,
    }


def _opaque_envelope(
    *,
    object_id: str,
    revision: str,
    parent_revision: str | None,
    payload: bytes,
) -> dict[str, Any]:
    return {
        "format": "atlasvault-opaque-ciphertext-envelope",
        "version": 1,
        "object_id": object_id,
        "revision": revision,
        "parent_revision": parent_revision,
        "key_epoch": 7,
        "nonce_b64": _encode64(b"nonce-is-opaque"),
        "ciphertext_b64": _encode64(payload),
        "aad_b64": _encode64(b"opaque-aad"),
        "signature_b64": _encode64(b"opaque-signature"),
        "tombstone": False,
        "content_sha256": hashlib.sha256(payload).hexdigest(),
    }


def _metadata(*, revision: str, payload: bytes) -> dict[str, Any]:
    return {
        "format": "atlasvault-encrypted-metadata-envelope",
        "version": 1,
        "vault_id": VAULT_ID,
        "revision": revision,
        "key_epoch": 7,
        "nonce_b64": _encode64(b"metadata-nonce"),
        "ciphertext_b64": _encode64(payload),
        "aad_b64": _encode64(b"metadata-aad"),
        "signature_b64": _encode64(b"metadata-signature"),
        "content_sha256": hashlib.sha256(payload).hexdigest(),
    }


def test_c14_opaque_metadata_object_and_snapshot_round_trip_without_inspection(
    storage_client: tuple[AtlasVaultBackend, TestClient, dict[str, str]],
) -> None:
    _, client, authorization = storage_client
    opaque_bytes = b'{"raw_vault_key_b64":"must-remain-opaque"}'
    metadata = _metadata(revision="metadata-r1", payload=opaque_bytes)
    metadata_path = f"/v1/vaults/{VAULT_ID}/metadata"
    response = client.put(
        metadata_path,
        headers=_write_headers(
            authorization,
            expected="*",
            idempotency_key="metadata-create",
        ),
        json=metadata,
    )
    assert response.status_code == 200, response.text
    assert response.json() == metadata
    assert client.get(metadata_path, headers=authorization).json() == metadata

    obj = _opaque_envelope(
        object_id="opaque-object",
        revision="object-r1",
        parent_revision=None,
        payload=opaque_bytes,
    )
    object_path = f"/v1/vaults/{VAULT_ID}/objects/opaque-object"
    response = client.put(
        object_path,
        headers=_write_headers(
            authorization,
            expected="*",
            idempotency_key="object-create",
        ),
        json=obj,
    )
    assert response.status_code == 200, response.text
    assert response.json() == obj
    assert client.get(object_path, headers=authorization).json() == obj

    snapshot = _opaque_envelope(
        object_id="opaque-snapshot",
        revision="snapshot-r1",
        parent_revision=None,
        payload=opaque_bytes,
    )
    snapshot_path = f"/v1/vaults/{VAULT_ID}/snapshots"
    response = client.put(
        snapshot_path,
        headers=_write_headers(
            authorization,
            expected="*",
            idempotency_key="snapshot-create",
        ),
        json=snapshot,
    )
    assert response.status_code == 200, response.text
    assert response.json() == snapshot
    assert client.get(snapshot_path, headers=authorization).json() == snapshot


def test_c14_conditional_object_write_rejects_stale_parent(
    storage_client: tuple[AtlasVaultBackend, TestClient, dict[str, str]],
) -> None:
    _, client, authorization = storage_client
    path = f"/v1/vaults/{VAULT_ID}/objects/cas-object"
    first = _opaque_envelope(
        object_id="cas-object",
        revision="cas-r1",
        parent_revision=None,
        payload=b"first-ciphertext",
    )
    second = _opaque_envelope(
        object_id="cas-object",
        revision="cas-r2",
        parent_revision="cas-r1",
        payload=b"second-ciphertext",
    )
    stale = _opaque_envelope(
        object_id="cas-object",
        revision="cas-r3",
        parent_revision="cas-r1",
        payload=b"stale-ciphertext",
    )

    assert (
        client.put(
            path,
            headers=_write_headers(
                authorization, expected="*", idempotency_key="cas-create"
            ),
            json=first,
        ).status_code
        == 200
    )
    assert (
        client.put(
            path,
            headers=_write_headers(
                authorization, expected="cas-r1", idempotency_key="cas-update"
            ),
            json=second,
        ).status_code
        == 200
    )
    conflict = client.put(
        path,
        headers=_write_headers(
            authorization, expected="cas-r1", idempotency_key="cas-stale"
        ),
        json=stale,
    )
    assert conflict.status_code == 409
    assert client.get(path, headers=authorization).json() == second


def test_c14_changed_revision_reuse_is_rejected_across_resource_history(
    storage_client: tuple[AtlasVaultBackend, TestClient, dict[str, str]],
) -> None:
    _, client, authorization = storage_client

    metadata_path = f"/v1/vaults/{VAULT_ID}/metadata"
    metadata_first = _metadata(revision="history-metadata-r1", payload=b"first")
    metadata_second = _metadata(revision="history-metadata-r2", payload=b"second")
    metadata_reused = _metadata(revision="history-metadata-r1", payload=b"changed")
    for expected, key, envelope in (
        ("*", "history-metadata-1", metadata_first),
        ("history-metadata-r1", "history-metadata-2", metadata_second),
    ):
        assert (
            client.put(
                metadata_path,
                headers=_write_headers(
                    authorization,
                    expected=expected,
                    idempotency_key=key,
                ),
                json=envelope,
            ).status_code
            == 200
        )
    assert (
        client.put(
            metadata_path,
            headers=_write_headers(
                authorization,
                expected="history-metadata-r2",
                idempotency_key="history-metadata-reuse",
            ),
            json=metadata_reused,
        ).status_code
        == 409
    )
    assert client.get(metadata_path, headers=authorization).json() == metadata_second
    assert (
        client.put(
            metadata_path,
            headers=_write_headers(
                authorization,
                expected="history-metadata-r2",
                idempotency_key="history-metadata-exact-reuse",
            ),
            json=metadata_first,
        ).status_code
        == 409
    )
    assert client.get(metadata_path, headers=authorization).json() == metadata_second

    object_path = f"/v1/vaults/{VAULT_ID}/objects/history-object"
    object_first = _opaque_envelope(
        object_id="history-object",
        revision="history-object-r1",
        parent_revision=None,
        payload=b"first",
    )
    object_second = _opaque_envelope(
        object_id="history-object",
        revision="history-object-r2",
        parent_revision="history-object-r1",
        payload=b"second",
    )
    object_reused = _opaque_envelope(
        object_id="history-object",
        revision="history-object-r1",
        parent_revision="history-object-r2",
        payload=b"changed",
    )
    for expected, key, envelope in (
        ("*", "history-object-1", object_first),
        ("history-object-r1", "history-object-2", object_second),
    ):
        assert (
            client.put(
                object_path,
                headers=_write_headers(
                    authorization,
                    expected=expected,
                    idempotency_key=key,
                ),
                json=envelope,
            ).status_code
            == 200
        )
    assert (
        client.put(
            object_path,
            headers=_write_headers(
                authorization,
                expected="history-object-r2",
                idempotency_key="history-object-reuse",
            ),
            json=object_reused,
        ).status_code
        == 409
    )
    assert client.get(object_path, headers=authorization).json() == object_second

    snapshot_path = f"/v1/vaults/{VAULT_ID}/snapshots"
    snapshot_first = _opaque_envelope(
        object_id="history-snapshot",
        revision="history-snapshot-r1",
        parent_revision=None,
        payload=b"first",
    )
    snapshot_second = _opaque_envelope(
        object_id="history-snapshot",
        revision="history-snapshot-r2",
        parent_revision="history-snapshot-r1",
        payload=b"second",
    )
    snapshot_reused = _opaque_envelope(
        object_id="history-snapshot",
        revision="history-snapshot-r1",
        parent_revision="history-snapshot-r2",
        payload=b"changed",
    )
    for expected, key, envelope in (
        ("*", "history-snapshot-1", snapshot_first),
        ("history-snapshot-r1", "history-snapshot-2", snapshot_second),
    ):
        assert (
            client.put(
                snapshot_path,
                headers=_write_headers(
                    authorization,
                    expected=expected,
                    idempotency_key=key,
                ),
                json=envelope,
            ).status_code
            == 200
        )
    assert (
        client.put(
            snapshot_path,
            headers=_write_headers(
                authorization,
                expected="history-snapshot-r2",
                idempotency_key="history-snapshot-reuse",
            ),
            json=snapshot_reused,
        ).status_code
        == 409
    )
    assert client.get(snapshot_path, headers=authorization).json() == snapshot_second


def test_c14_concurrent_compare_and_set_accepts_exactly_one_writer(
    storage_client: tuple[AtlasVaultBackend, TestClient, dict[str, str]],
) -> None:
    _, client, authorization = storage_client
    path = f"/v1/vaults/{VAULT_ID}/objects/concurrent-object"
    initial = _opaque_envelope(
        object_id="concurrent-object",
        revision="concurrent-r0",
        parent_revision=None,
        payload=b"initial",
    )
    assert (
        client.put(
            path,
            headers=_write_headers(
                authorization, expected="*", idempotency_key="concurrent-create"
            ),
            json=initial,
        ).status_code
        == 200
    )

    candidates = [
        _opaque_envelope(
            object_id="concurrent-object",
            revision=f"concurrent-r{index}",
            parent_revision="concurrent-r0",
            payload=f"candidate-{index}".encode("ascii"),
        )
        for index in (1, 2)
    ]

    def write(index: int) -> int:
        return client.put(
            path,
            headers=_write_headers(
                authorization,
                expected="concurrent-r0",
                idempotency_key=f"concurrent-writer-{index}",
            ),
            json=candidates[index],
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(pool.map(write, range(2)))

    assert outcomes == [200, 409]
    assert client.get(path, headers=authorization).json() in candidates


def test_c14_idempotent_retry_and_duplicate_patch_have_exactly_once_effect(
    storage_client: tuple[AtlasVaultBackend, TestClient, dict[str, str]],
) -> None:
    _, client, authorization = storage_client
    path = f"/v1/vaults/{VAULT_ID}/patches"
    patch = _opaque_envelope(
        object_id="patch-object",
        revision="patch-r1",
        parent_revision=None,
        payload=b"opaque-patch",
    )
    headers = _write_headers(
        authorization,
        expected="*",
        idempotency_key="patch-attempt-1",
    )
    first = client.post(path, headers=headers, json=patch)
    retry = client.post(path, headers=headers, json=patch)
    duplicate = client.post(
        path,
        headers=_write_headers(
            authorization,
            expected="*",
            idempotency_key="patch-attempt-2",
        ),
        json=patch,
    )
    assert first.status_code == retry.status_code == duplicate.status_code == 201
    assert first.json() == retry.json() == duplicate.json() == patch

    page = client.get(path, headers=authorization).json()
    assert page == {"objects": [patch], "next_cursor": None}

    changed_request = dict(patch)
    changed_request["ciphertext_b64"] = _encode64(b"different-ciphertext")
    changed_request["content_sha256"] = hashlib.sha256(
        b"different-ciphertext"
    ).hexdigest()
    conflict = client.post(path, headers=headers, json=changed_request)
    assert conflict.status_code == 409


def test_c14_cursor_pages_are_stable_across_retry_and_later_appends(
    storage_client: tuple[AtlasVaultBackend, TestClient, dict[str, str]],
) -> None:
    _, client, authorization = storage_client
    path = f"/v1/vaults/{VAULT_ID}/patches"
    patches: list[dict[str, Any]] = []
    parent: str | None = None
    for index in range(1, 6):
        patch = _opaque_envelope(
            object_id=f"patch-{index}",
            revision=f"page-r{index}",
            parent_revision=parent,
            payload=f"page-{index}".encode("ascii"),
        )
        expected = parent or "*"
        response = client.post(
            path,
            headers=_write_headers(
                authorization,
                expected=expected,
                idempotency_key=f"page-write-{index}",
            ),
            json=patch,
        )
        assert response.status_code == 201, response.text
        patches.append(patch)
        parent = patch["revision"]

    first = client.get(path, headers=authorization, params={"page_size": 2})
    assert first.status_code == 200, first.text
    first_page = first.json()
    assert first_page["objects"] == patches[:2]
    assert first_page["next_cursor"] is not None

    later = _opaque_envelope(
        object_id="patch-6",
        revision="page-r6",
        parent_revision="page-r5",
        payload=b"page-6",
    )
    assert (
        client.post(
            path,
            headers=_write_headers(
                authorization,
                expected="page-r5",
                idempotency_key="page-write-6",
            ),
            json=later,
        ).status_code
        == 201
    )

    second = client.get(
        path,
        headers=authorization,
        params={"cursor": first_page["next_cursor"]},
    )
    retry = client.get(
        path,
        headers=authorization,
        params={"cursor": first_page["next_cursor"]},
    )
    assert second.json() == retry.json()
    assert second.json()["objects"] == patches[2:4]
    third = client.get(
        path,
        headers=authorization,
        params={"cursor": second.json()["next_cursor"]},
    ).json()
    assert third == {"objects": patches[4:5], "next_cursor": None}

    fresh = client.get(path, headers=authorization).json()
    assert fresh["objects"] == [*patches, later]


def test_c14_expired_pagination_cursors_are_reclaimed() -> None:
    now = [2_000.0]
    backend = AtlasVaultBackend(
        entropy=DeterministicEntropy(),
        monotonic=lambda: now[0],
    )
    client = TestClient(create_app(backend))
    authorization = _authorize(client, _identity())
    path = f"/v1/vaults/{VAULT_ID}/patches"
    parent: str | None = None
    for index in range(3):
        revision = f"cursor-expiry-r{index + 1}"
        patch = _opaque_envelope(
            object_id=f"cursor-expiry-{index + 1}",
            revision=revision,
            parent_revision=parent,
            payload=revision.encode("ascii"),
        )
        assert (
            client.post(
                path,
                headers=_write_headers(
                    authorization,
                    expected=parent or "*",
                    idempotency_key=f"cursor-expiry-{index + 1}",
                ),
                json=patch,
            ).status_code
            == 201
        )
        parent = revision

    first = client.get(path, headers=authorization, params={"page_size": 1})
    cursor = first.json()["next_cursor"]
    assert cursor is not None
    assert cursor in backend.storage._cursors

    now[0] += _CURSOR_LIFETIME_SECONDS + 1
    expired = client.get(path, headers=authorization, params={"cursor": cursor})
    assert expired.status_code == 400
    assert backend.storage._cursors == {}


def test_c14_expired_idempotency_receipts_are_reclaimed() -> None:
    now = [2_000.0]
    backend = AtlasVaultBackend(
        entropy=DeterministicEntropy(),
        monotonic=lambda: now[0],
    )
    client = TestClient(create_app(backend))
    authorization = _authorize(client, _identity())
    path = f"/v1/vaults/{VAULT_ID}/metadata"
    first = _metadata(revision="receipt-r1", payload=b"first")
    second = _metadata(revision="receipt-r2", payload=b"second")

    assert (
        client.put(
            path,
            headers=_write_headers(
                authorization,
                expected="*",
                idempotency_key="receipt-first",
            ),
            json=first,
        ).status_code
        == 200
    )
    state = backend.storage._vaults[(ACCOUNT_ID, VAULT_ID)]
    assert ("metadata", "receipt-first") in state.receipts

    now[0] += _RECEIPT_LIFETIME_SECONDS + 1
    assert (
        client.put(
            path,
            headers=_write_headers(
                authorization,
                expected="receipt-r1",
                idempotency_key="receipt-second",
            ),
            json=second,
        ).status_code
        == 200
    )
    assert set(state.receipts) == {("metadata", "receipt-second")}


def test_c14_rejected_write_does_not_retain_empty_vault_state(
    storage_client: tuple[AtlasVaultBackend, TestClient, dict[str, str]],
) -> None:
    backend, client, authorization = storage_client
    rejected_vault = "vault-rejected-before-allocation"
    envelope = _metadata(revision="rejected-r1", payload=b"rejected")
    envelope["vault_id"] = rejected_vault

    response = client.put(
        f"/v1/vaults/{rejected_vault}/metadata",
        headers=_write_headers(
            authorization,
            expected="missing-revision",
            idempotency_key="rejected-before-allocation",
        ),
        json=envelope,
    )
    assert response.status_code == 409
    assert (ACCOUNT_ID, rejected_vault) not in backend.storage._vaults


def test_c14_rejected_object_write_does_not_retain_empty_history(
    storage_client: tuple[AtlasVaultBackend, TestClient, dict[str, str]],
) -> None:
    backend, client, authorization = storage_client
    metadata_path = f"/v1/vaults/{VAULT_ID}/metadata"
    assert (
        client.put(
            metadata_path,
            headers=_write_headers(
                authorization,
                expected="*",
                idempotency_key="retain-vault",
            ),
            json=_metadata(revision="retain-vault-r1", payload=b"metadata"),
        ).status_code
        == 200
    )

    object_id = "rejected-object-history"
    rejected = _opaque_envelope(
        object_id=object_id,
        revision="rejected-object-r1",
        parent_revision="missing-parent",
        payload=b"rejected-object",
    )
    response = client.put(
        f"/v1/vaults/{VAULT_ID}/objects/{object_id}",
        headers=_write_headers(
            authorization,
            expected="missing-parent",
            idempotency_key="rejected-object-history",
        ),
        json=rejected,
    )
    assert response.status_code == 409
    state = backend.storage._vaults[(ACCOUNT_ID, VAULT_ID)]
    assert object_id not in state.object_revision_fingerprints


@pytest.mark.parametrize(
    "extra_field",
    ["plaintext", "passphrase", "raw_vault_key_b64", "unwrapped_key"],
)
def test_c14_storage_models_reject_secret_fields_and_wire_guard_stays_green(
    storage_client: tuple[AtlasVaultBackend, TestClient, dict[str, str]],
    extra_field: str,
) -> None:
    _, client, authorization = storage_client
    envelope = _opaque_envelope(
        object_id="secret-field-object",
        revision="secret-field-r1",
        parent_revision=None,
        payload=b"opaque",
    )
    envelope[extra_field] = "forbidden"
    response = client.put(
        f"/v1/vaults/{VAULT_ID}/objects/secret-field-object",
        headers=_write_headers(
            authorization,
            expected="*",
            idempotency_key=f"secret-field-{extra_field}",
        ),
        json=envelope,
    )
    assert response.status_code == 422
    assert find_raw_secret_wire_contract_violations(ROOT / "services") == []


def test_c14_contract_requires_cas_idempotency_and_opaque_cursor_parameters() -> None:
    contract = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
    parameters = contract["components"]["parameters"]
    path_id_pattern = (
        r"^(?:[A-Za-z0-9_~-][A-Za-z0-9._~-]*|"
        r"\.[A-Za-z0-9_~-][A-Za-z0-9._~-]*|"
        r"\.\.[A-Za-z0-9._~-]+)$"
    )
    assert re.fullmatch(path_id_pattern, ".") is None
    assert re.fullmatch(path_id_pattern, "..") is None
    assert re.fullmatch(path_id_pattern, "vault.with.dots") is not None
    assert parameters["IfMatch"]["name"] == "If-Match"
    assert parameters["IdempotencyKey"]["name"] == "Idempotency-Key"
    assert parameters["IfMatch"]["schema"]["pattern"] == IF_MATCH_HEADER_PATTERN
    assert parameters["IfMatch"]["schema"]["maxLength"] == 130
    assert (
        parameters["IdempotencyKey"]["schema"]["pattern"] == HEADER_SAFE_ASCII_PATTERN
    )
    assert parameters["Cursor"]["name"] == "cursor"
    assert parameters["PageSize"]["name"] == "page_size"
    assert "default" not in parameters["PageSize"]["schema"]
    assert parameters["VaultId"]["schema"]["pattern"] == path_id_pattern
    assert parameters["ObjectId"]["schema"]["pattern"] == path_id_pattern

    generated = create_app(AtlasVaultBackend()).openapi()
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
        metadata_properties = schemas[metadata_name]["properties"]
        opaque_properties = schemas[opaque_name]["properties"]
        for identifier in (
            metadata_properties["vault_id"],
            opaque_properties["object_id"],
        ):
            assert identifier["minLength"] == 1
            assert identifier["maxLength"] == 128
            assert identifier["pattern"] == path_id_pattern
        for encoded in (
            metadata_properties["nonce_b64"],
            metadata_properties["ciphertext_b64"],
            metadata_properties["aad_b64"],
            metadata_properties["signature_b64"],
            opaque_properties["nonce_b64"],
            opaque_properties["ciphertext_b64"],
            opaque_properties["aad_b64"],
            opaque_properties["signature_b64"],
        ):
            assert encoded["minLength"] == 1
            assert encoded["contentEncoding"] == "base64"
        for revision in (
            metadata_properties["revision"],
            opaque_properties["revision"],
            opaque_properties["parent_revision"],
        ):
            revision_type = revision.get("type")
            if revision_type == "string" or (
                isinstance(revision_type, list) and "string" in revision_type
            ):
                string_revision = revision
            else:
                string_revision = next(
                    option
                    for option in revision["anyOf"]
                    if option.get("type") == "string"
                )
            assert string_revision["minLength"] == 1
            assert string_revision["pattern"] == REVISION_PATTERN
            max_length = revision.get("maxLength")
            if max_length is None:
                max_length = next(
                    option["maxLength"]
                    for option in revision["anyOf"]
                    if option.get("type") == "string"
                )
            assert max_length == 128
            assert revision["not"] == {"const": "*"}
        assert "parent_revision" in schemas[opaque_name]["required"]

    for path, method in (
        ("/v1/vaults/{vault_id}/metadata", "put"),
        ("/v1/vaults/{vault_id}/objects/{object_id}", "put"),
        ("/v1/vaults/{vault_id}/patches", "post"),
        ("/v1/vaults/{vault_id}/snapshots", "put"),
    ):
        refs = {item["$ref"] for item in contract["paths"][path][method]["parameters"]}
        assert "#/components/parameters/IfMatch" in refs
        assert "#/components/parameters/IdempotencyKey" in refs

    patch_refs = {
        item["$ref"]
        for item in contract["paths"]["/v1/vaults/{vault_id}/patches"]["get"][
            "parameters"
        ]
    }
    assert "#/components/parameters/Cursor" in patch_refs
    assert "#/components/parameters/PageSize" in patch_refs

    for path, parameter_name in (
        ("/v1/vaults/{vault_id}/metadata", "vault_id"),
        ("/v1/vaults/{vault_id}/objects/{object_id}", "object_id"),
    ):
        parameter = next(
            item
            for item in generated["paths"][path]["get"]["parameters"]
            if item["name"] == parameter_name
        )
        assert parameter["schema"]["pattern"] == path_id_pattern


@pytest.mark.parametrize("resource", ["metadata", "object"])
def test_c14_creation_wildcard_cannot_be_stored_as_a_revision(
    storage_client: tuple[AtlasVaultBackend, TestClient, dict[str, str]],
    resource: str,
) -> None:
    _, client, authorization = storage_client
    if resource == "metadata":
        path = f"/v1/vaults/{VAULT_ID}/metadata"
        envelope = _metadata(revision="*", payload=b"wildcard-metadata")
    else:
        path = f"/v1/vaults/{VAULT_ID}/objects/wildcard-object"
        envelope = _opaque_envelope(
            object_id="wildcard-object",
            revision="*",
            parent_revision=None,
            payload=b"wildcard-object",
        )

    response = client.put(
        path,
        headers=_write_headers(
            authorization,
            expected="*",
            idempotency_key=f"wildcard-{resource}",
        ),
        json=envelope,
    )
    assert response.status_code == 422


def test_c14_parent_revision_is_required_but_nullable(
    storage_client: tuple[AtlasVaultBackend, TestClient, dict[str, str]],
) -> None:
    _, client, authorization = storage_client
    path = f"/v1/vaults/{VAULT_ID}/objects/required-parent"
    envelope = _opaque_envelope(
        object_id="required-parent",
        revision="required-parent-r1",
        parent_revision=None,
        payload=b"required-parent",
    )
    missing = dict(envelope)
    del missing["parent_revision"]

    assert (
        client.put(
            path,
            headers=_write_headers(
                authorization,
                expected="*",
                idempotency_key="missing-parent",
            ),
            json=missing,
        ).status_code
        == 422
    )
    assert (
        client.put(
            path,
            headers=_write_headers(
                authorization,
                expected="*",
                idempotency_key="explicit-null-parent",
            ),
            json=envelope,
        ).status_code
        == 200
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [("key_epoch", "7"), ("tombstone", "false")],
)
def test_c14_opaque_wire_model_rejects_coerced_json_types(
    storage_client: tuple[AtlasVaultBackend, TestClient, dict[str, str]],
    field: str,
    value: str,
) -> None:
    _, client, authorization = storage_client
    object_id = f"strict-{field}"
    envelope = _opaque_envelope(
        object_id=object_id,
        revision=f"strict-{field}-r1",
        parent_revision=None,
        payload=field.encode("ascii"),
    )
    envelope[field] = value
    response = client.put(
        f"/v1/vaults/{VAULT_ID}/objects/{object_id}",
        headers=_write_headers(
            authorization,
            expected="*",
            idempotency_key=f"strict-{field}",
        ),
        json=envelope,
    )
    assert response.status_code == 422


@pytest.mark.parametrize("resource", ["metadata", "object"])
def test_c14_stored_revisions_use_header_safe_ascii(
    storage_client: tuple[AtlasVaultBackend, TestClient, dict[str, str]],
    resource: str,
) -> None:
    _, client, authorization = storage_client
    if resource == "metadata":
        path = f"/v1/vaults/{VAULT_ID}/metadata"
        envelope = _metadata(revision="revision-é", payload=b"metadata")
    else:
        path = f"/v1/vaults/{VAULT_ID}/objects/header-safe"
        envelope = _opaque_envelope(
            object_id="header-safe",
            revision="revision-é",
            parent_revision=None,
            payload=b"object",
        )
    response = client.put(
        path,
        headers=_write_headers(
            authorization,
            expected="*",
            idempotency_key=f"header-safe-{resource}",
        ),
        json=envelope,
    )
    assert response.status_code == 422


def test_c14_receipt_pruning_does_not_scan_every_retained_vault() -> None:
    class NoFullScanVaults(dict[tuple[str, str], object]):
        def values(self) -> object:
            raise AssertionError("full retained-vault scan is forbidden")

    backend = AtlasVaultBackend(
        entropy=DeterministicEntropy(),
        monotonic=lambda: 2_000.0,
    )
    client = TestClient(create_app(backend))
    authorization = _authorize(client, _identity())
    backend.storage._vaults = NoFullScanVaults(backend.storage._vaults)
    response = client.put(
        f"/v1/vaults/{VAULT_ID}/metadata",
        headers=_write_headers(
            authorization,
            expected="*",
            idempotency_key="no-full-vault-scan",
        ),
        json=_metadata(revision="no-scan-r1", payload=b"opaque"),
    )
    assert response.status_code == 200, response.text


@pytest.mark.parametrize(
    ("expected_revision", "idempotency_key"),
    [("\t", "safe-key"), ("*", "bad\tkey"), ("\x7f", "safe-key")],
)
def test_c14_write_tokens_reject_non_visible_ascii(
    expected_revision: str,
    idempotency_key: str,
) -> None:
    backend = AtlasVaultBackend(entropy=DeterministicEntropy())
    envelope = EncryptedVaultMetadataEnvelopeModel.model_validate(
        _metadata(revision="header-token-r1", payload=b"opaque")
    )
    with pytest.raises(InvalidOpaqueStorageRequest):
        backend.storage.put_metadata(
            ACCOUNT_ID,
            VAULT_ID,
            envelope,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
        )


def test_c14_cursor_pruning_does_not_scan_every_live_cursor() -> None:
    class NoFullScanCursors(dict[str, object]):
        def items(self) -> object:
            raise AssertionError("full cursor scan is forbidden")

    backend = AtlasVaultBackend(
        entropy=DeterministicEntropy(),
        monotonic=lambda: 2_000.0,
    )
    client = TestClient(create_app(backend))
    authorization = _authorize(client, _identity())
    parent: str | None = None
    for index in range(2):
        revision = f"cursor-no-scan-r{index + 1}"
        response = client.post(
            f"/v1/vaults/{VAULT_ID}/patches",
            headers=_write_headers(
                authorization,
                expected=parent or "*",
                idempotency_key=f"cursor-no-scan-{index + 1}",
            ),
            json=_opaque_envelope(
                object_id=f"cursor-no-scan-{index + 1}",
                revision=revision,
                parent_revision=parent,
                payload=revision.encode("ascii"),
            ),
        )
        assert response.status_code == 201, response.text
        parent = revision

    backend.storage._cursors = NoFullScanCursors()
    page = client.get(
        f"/v1/vaults/{VAULT_ID}/patches",
        headers=authorization,
        params={"page_size": 1},
    )
    assert page.status_code == 200, page.text
    assert page.json()["next_cursor"] is not None


def test_c14_expired_receipt_is_ignored_even_beyond_prune_budget() -> None:
    now = [2_000.0]
    backend = AtlasVaultBackend(
        entropy=DeterministicEntropy(),
        monotonic=lambda: now[0],
    )
    first = EncryptedVaultMetadataEnvelopeModel.model_validate(
        _metadata(revision="receipt-budget-r1", payload=b"first")
    )
    second = EncryptedVaultMetadataEnvelopeModel.model_validate(
        _metadata(revision="receipt-budget-r2", payload=b"second")
    )
    store = backend.storage
    store.put_metadata(
        ACCOUNT_ID,
        VAULT_ID,
        first,
        expected_revision="*",
        idempotency_key="receipt-budget-seed",
    )
    for index in range(65):
        store.put_metadata(
            ACCOUNT_ID,
            VAULT_ID,
            first,
            expected_revision="*",
            idempotency_key=f"receipt-budget-backlog-{index}",
        )
    store.put_metadata(
        ACCOUNT_ID,
        VAULT_ID,
        first,
        expected_revision="*",
        idempotency_key="receipt-budget-target",
    )
    store.put_metadata(
        ACCOUNT_ID,
        VAULT_ID,
        second,
        expected_revision="receipt-budget-r1",
        idempotency_key="receipt-budget-advance",
    )

    now[0] += _RECEIPT_LIFETIME_SECONDS + 1
    with pytest.raises(OpaqueStorageConflict):
        store.put_metadata(
            ACCOUNT_ID,
            VAULT_ID,
            first,
            expected_revision="*",
            idempotency_key="receipt-budget-target",
        )


def test_c14_retained_storage_quotas_fail_before_mutation() -> None:
    def client_for(
        **limits: int,
    ) -> tuple[AtlasVaultBackend, TestClient, dict[str, str]]:
        backend = AtlasVaultBackend(
            entropy=DeterministicEntropy(),
            monotonic=lambda: 2_000.0,
            abuse_policy=AbuseControlPolicy(**limits),
        )
        client = TestClient(create_app(backend))
        return backend, client, _authorize(client, _identity())

    backend, client, authorization = client_for(
        max_retained_vaults=1,
        max_retained_vaults_per_account=1,
    )
    first = _metadata(revision="vault-cap-r1", payload=b"first")
    assert (
        client.put(
            f"/v1/vaults/{VAULT_ID}/metadata",
            headers=_write_headers(
                authorization,
                expected="*",
                idempotency_key="vault-cap-first",
            ),
            json=first,
        ).status_code
        == 200
    )
    second = _metadata(revision="vault-cap-r2", payload=b"second")
    second["vault_id"] = "vault-c14-over-cap"
    limited = client.put(
        "/v1/vaults/vault-c14-over-cap/metadata",
        headers=_write_headers(
            authorization,
            expected="*",
            idempotency_key="vault-cap-second",
        ),
        json=second,
    )
    assert limited.status_code == 429
    assert set(backend.storage._vaults) == {(ACCOUNT_ID, VAULT_ID)}

    backend, client, authorization = client_for(max_retained_objects_per_account=1)
    for index in range(2):
        object_id = f"object-cap-{index + 1}"
        response = client.put(
            f"/v1/vaults/{VAULT_ID}/objects/{object_id}",
            headers=_write_headers(
                authorization,
                expected="*",
                idempotency_key=object_id,
            ),
            json=_opaque_envelope(
                object_id=object_id,
                revision=f"object-cap-r{index + 1}",
                parent_revision=None,
                payload=object_id.encode("ascii"),
            ),
        )
        assert response.status_code == (200 if index == 0 else 429), response.text
    state = backend.storage._vaults[(ACCOUNT_ID, VAULT_ID)]
    assert set(state.objects) == {"object-cap-1"}

    backend, client, authorization = client_for(max_retained_patches_per_account=1)
    first_patch = _opaque_envelope(
        object_id="patch-cap-1",
        revision="patch-cap-r1",
        parent_revision=None,
        payload=b"first",
    )
    assert (
        client.post(
            f"/v1/vaults/{VAULT_ID}/patches",
            headers=_write_headers(
                authorization,
                expected="*",
                idempotency_key="patch-cap-first",
            ),
            json=first_patch,
        ).status_code
        == 201
    )
    limited = client.post(
        f"/v1/vaults/{VAULT_ID}/patches",
        headers=_write_headers(
            authorization,
            expected="patch-cap-r1",
            idempotency_key="patch-cap-second",
        ),
        json=_opaque_envelope(
            object_id="patch-cap-2",
            revision="patch-cap-r2",
            parent_revision="patch-cap-r1",
            payload=b"second",
        ),
    )
    assert limited.status_code == 429
    assert [
        item.revision
        for item in backend.storage._vaults[(ACCOUNT_ID, VAULT_ID)].patches
    ] == ["patch-cap-r1"]

    backend, client, authorization = client_for(max_retained_revisions_per_account=1)
    first = _metadata(revision="revision-cap-r1", payload=b"first")
    assert (
        client.put(
            f"/v1/vaults/{VAULT_ID}/metadata",
            headers=_write_headers(
                authorization,
                expected="*",
                idempotency_key="revision-cap-first",
            ),
            json=first,
        ).status_code
        == 200
    )
    limited = client.put(
        f"/v1/vaults/{VAULT_ID}/metadata",
        headers=_write_headers(
            authorization,
            expected="revision-cap-r1",
            idempotency_key="revision-cap-second",
        ),
        json=_metadata(revision="revision-cap-r2", payload=b"second"),
    )
    assert limited.status_code == 429
    assert (
        backend.storage.get_metadata(ACCOUNT_ID, VAULT_ID).revision == "revision-cap-r1"
    )

    envelope = EncryptedVaultMetadataEnvelopeModel.model_validate(
        _metadata(revision="byte-cap-r1", payload=b"opaque")
    )
    retained_size = len(envelope.model_dump_json().encode("utf-8"))
    backend, client, authorization = client_for(
        max_retained_bytes=retained_size - 1,
        max_retained_bytes_per_account=retained_size - 1,
    )
    limited = client.put(
        f"/v1/vaults/{VAULT_ID}/metadata",
        headers=_write_headers(
            authorization,
            expected="*",
            idempotency_key="byte-cap-first",
        ),
        json=envelope.model_dump(mode="json"),
    )
    assert limited.status_code == 429
    assert backend.storage._vaults == {}


def test_c14_duplicate_receipts_retain_only_attempt_fingerprints(
    storage_client: tuple[AtlasVaultBackend, TestClient, dict[str, str]],
) -> None:
    backend, client, authorization = storage_client
    envelope = _metadata(revision="receipt-fingerprint-r1", payload=b"opaque")
    path = f"/v1/vaults/{VAULT_ID}/metadata"

    for key in ("receipt-fingerprint-first", "receipt-fingerprint-duplicate"):
        response = client.put(
            path,
            headers=_write_headers(
                authorization,
                expected="*",
                idempotency_key=key,
            ),
            json=envelope,
        )
        assert response.status_code == 200, response.text

    state = backend.storage._vaults[(ACCOUNT_ID, VAULT_ID)]
    assert len(state.receipts) == 2
    for receipt in state.receipts.values():
        attempt = vars(receipt.attempt)
        assert set(attempt) == {"expected_revision", "envelope_fingerprint"}
        assert isinstance(attempt["envelope_fingerprint"], bytes)
        assert len(attempt["envelope_fingerprint"]) == hashlib.sha256().digest_size
        assert receipt.response is state.metadata


def test_c14_cursor_cleanup_is_bounded_and_target_expiry_fails_closed() -> None:
    now = [2_000.0]
    backend = AtlasVaultBackend(
        entropy=DeterministicEntropy(),
        monotonic=lambda: now[0],
    )
    store = backend.storage
    tokens = [
        store._new_cursor(
            ACCOUNT_ID,
            VAULT_ID,
            start=0,
            end=0,
            page_size=1,
        )
        for _ in range(65)
    ]

    now[0] += _CURSOR_LIFETIME_SECONDS + 1
    with pytest.raises(InvalidOpaqueStorageRequest):
        store.list_patches(
            ACCOUNT_ID,
            VAULT_ID,
            cursor=tokens[-1],
            page_size=None,
        )

    assert len(store._cursor_expiries) == 1
    assert tokens[-1] not in store._cursors


def test_c14_envelope_facts_are_computed_once_outside_the_store_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = AtlasVaultBackend(
        entropy=DeterministicEntropy(),
        monotonic=lambda: 2_000.0,
    )
    envelope = EncryptedVaultMetadataEnvelopeModel.model_validate(
        _metadata(revision="one-pass-r1", payload=b"opaque")
    )
    original = EncryptedVaultMetadataEnvelopeModel.model_dump_json
    lock_observations: list[bool] = []

    def observed_dump(
        self: EncryptedVaultMetadataEnvelopeModel, *args: object, **kwargs: object
    ) -> str:
        lock_observations.append(backend.storage._lock._is_owned())
        return original(self, *args, **kwargs)

    monkeypatch.setattr(
        EncryptedVaultMetadataEnvelopeModel,
        "model_dump_json",
        observed_dump,
    )
    backend.storage.put_metadata(
        ACCOUNT_ID,
        VAULT_ID,
        envelope,
        expected_revision="*",
        idempotency_key="one-pass",
    )

    assert lock_observations == [False]


def test_c14_if_match_uses_strong_entity_tags_at_the_http_boundary(
    storage_client: tuple[AtlasVaultBackend, TestClient, dict[str, str]],
) -> None:
    _, client, authorization = storage_client
    path = f"/v1/vaults/{VAULT_ID}/metadata"
    first = client.put(
        path,
        headers={
            **authorization,
            "If-Match": "*",
            "Idempotency-Key": "etag-first",
        },
        json=_metadata(revision="etag-r1", payload=b"first"),
    )
    assert first.status_code == 200, first.text

    second = client.put(
        path,
        headers={
            **authorization,
            "If-Match": '"etag-r1"',
            "Idempotency-Key": "etag-second",
        },
        json=_metadata(revision="etag-r2", payload=b"second"),
    )
    assert second.status_code == 200, second.text

    unquoted = client.put(
        path,
        headers={
            **authorization,
            "If-Match": "etag-r2",
            "Idempotency-Key": "etag-unquoted",
        },
        json=_metadata(revision="etag-r3", payload=b"third"),
    )
    assert unquoted.status_code == 422


def test_c14_global_capacity_keeps_an_unallocatable_reserve() -> None:
    first = EncryptedVaultMetadataEnvelopeModel.model_validate(
        _metadata(revision="reserve-r1", payload=b"opaque")
    )
    second = EncryptedVaultMetadataEnvelopeModel.model_validate(
        {
            **_metadata(revision="reserve-r2", payload=b"opaque"),
            "vault_id": "vault-c14-reserve-second",
        }
    )
    envelope_bytes = len(first.model_dump_json().encode("utf-8"))
    limits = AbuseControlPolicy(
        max_retained_bytes=envelope_bytes * 2,
        max_retained_bytes_per_account=envelope_bytes,
        reserved_retained_bytes=envelope_bytes,
    )
    store = InMemoryOpaqueStore(
        entropy=DeterministicEntropy(),
        monotonic=lambda: 2_000.0,
        limits=limits,
    )

    store.put_metadata(
        "reserve-account-a",
        first.vault_id,
        first,
        expected_revision="*",
        idempotency_key="reserve-first",
    )
    with pytest.raises(OpaqueStorageCapacityExceeded):
        store.put_metadata(
            "reserve-account-b",
            second.vault_id,
            second,
            expected_revision="*",
            idempotency_key="reserve-second",
        )

    assert store._retained_bytes == envelope_bytes
    assert limits.max_retained_bytes - store._retained_bytes == (
        limits.reserved_retained_bytes
    )


def test_c14_storage_clock_fails_closed_before_state_or_cursor_mutation() -> None:
    now = [2_000.0]
    backend = AtlasVaultBackend(
        entropy=DeterministicEntropy(),
        monotonic=lambda: now[0],
    )
    first = EncryptedVaultMetadataEnvelopeModel.model_validate(
        _metadata(revision="clock-r1", payload=b"opaque")
    )
    backend.storage.put_metadata(
        ACCOUNT_ID,
        first.vault_id,
        first,
        expected_revision="*",
        idempotency_key="clock-first",
    )

    before = backend.storage._retained_bytes
    now[0] = 1_999.0
    with pytest.raises(InvalidOpaqueStorageRequest):
        backend.storage.put_metadata(
            ACCOUNT_ID,
            first.vault_id,
            EncryptedVaultMetadataEnvelopeModel.model_validate(
                _metadata(revision="clock-r2", payload=b"changed")
            ),
            expected_revision="clock-r1",
            idempotency_key="clock-regressed",
        )
    assert backend.storage._retained_bytes == before
    assert backend.storage.get_metadata(ACCOUNT_ID, first.vault_id).revision == (
        "clock-r1"
    )

    now[0] = float("nan")
    with pytest.raises(InvalidOpaqueStorageRequest):
        backend.storage.list_patches(
            ACCOUNT_ID,
            first.vault_id,
            cursor=None,
            page_size=1,
        )
    assert backend.storage._cursors == {}
