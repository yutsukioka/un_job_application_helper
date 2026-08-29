from __future__ import annotations

import base64
import copy
import inspect
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from vaultsync.device_identity import device_identity_from_private_keys
from vaultsync.key_delivery import (
    PairingBootstrap,
    PairingKeyDeliveryError,
    SignedPairingAcknowledgement,
    SignedPairingKeyRequest,
    SignedVaultKeyDelivery,
    create_pairing_acknowledgement,
    create_pairing_key_request,
    create_vault_key_delivery,
    create_vault_key_delivery_for_testing,
    derive_pairing_sas,
    open_vault_key_delivery,
    verify_pairing_acknowledgement,
    verify_pairing_key_request,
)
from vaultsync.pairing import SignedPairingAcceptance, SignedPairingOffer


REPO_ROOT = Path(__file__).resolve().parents[3]
VECTOR_PATH = (
    REPO_ROOT
    / "contracts"
    / "sync"
    / "test_vectors"
    / "atlasvault_trusted_pairing_delivery_vectors_v1.json"
)


def _root() -> dict[str, object]:
    return json.loads(VECTOR_PATH.read_text(encoding="utf-8"))


def _bytes(value: object) -> bytes:
    assert isinstance(value, str)
    return base64.b64decode(value.encode("ascii"), validate=True)


def _identity(root: dict[str, object], name: str):
    value = root[name]
    return device_identity_from_private_keys(
        signing_private_seed=_bytes(value["signing_private_seed_b64"]),
        agreement_private_key=_bytes(value["agreement_private_key_b64"]),
        created_at=value["created_at"],
        key_epoch=value["key_epoch"],
    )


def test_sas_request_bootstrap_delivery_and_acknowledgement_match_vector() -> None:
    root = _root()
    inviter = _identity(root, "inviter")
    invitee = _identity(root, "invitee")
    offer = SignedPairingOffer.from_dict(root["signed_offer"])
    acceptance = SignedPairingAcceptance.from_dict(root["signed_acceptance"])
    transcript = bytes.fromhex(root["transcript_sha256"])

    assert derive_pairing_sas(_bytes(root["pairing_session_key_b64"]), transcript) == root["sas"]

    request = create_pairing_key_request(
        invitee=invitee,
        request_id=root["request_id"],
        transcript_sha256=transcript,
        inviter_device_id=inviter.device_id,
        invitee_ephemeral_public_key=_bytes(root["invitee_ephemeral_public_key_b64"]),
        nonce=_bytes(root["request_nonce_b64"]),
        issued_at=root["request_issued_at"],
        expires_at=root["request_expires_at"],
    )
    assert request.to_dict() == root["signed_key_request"]
    assert request.canonical_bytes() == _bytes(root["signed_key_request_canonical_b64"])
    verify_pairing_key_request(
        request,
        transcript_sha256=transcript,
        inviter_device_id=inviter.device_id,
        invitee_device_id=invitee.device_id,
        current_time=root["verification_time"],
    )

    bootstrap = PairingBootstrap.from_dict(root["bootstrap"])
    assert bootstrap.canonical_bytes() == _bytes(root["bootstrap_canonical_b64"])
    assert bootstrap.sha256_hex() == root["bootstrap_sha256"]
    assert len(bootstrap.records) == 2
    assert bootstrap.records[0].deleted is False
    assert bootstrap.records[1].deleted is True

    delivery = create_vault_key_delivery_for_testing(
        inviter=inviter,
        key_request=request,
        transcript_sha256=transcript,
        bootstrap=bootstrap,
        vault_key=_bytes(root["test_only_vault_key_b64"]),
        inviter_ephemeral_private_key=_bytes(root["inviter_ephemeral_private_key_b64"]),
        nonce=_bytes(root["delivery_nonce_b64"]),
        delivery_id=root["delivery_id"],
        key_epoch=root["vault_key_epoch"],
        expires_at=root["delivery_expires_at"],
    )
    assert delivery.to_dict() == root["signed_delivery"]
    assert delivery.canonical_bytes() == _bytes(root["signed_delivery_canonical_b64"])
    assert open_vault_key_delivery(
        delivery,
        key_request=request,
        invitee_ephemeral_private_key=_bytes(root["invitee_ephemeral_private_key_b64"]),
        bootstrap=bootstrap,
        transcript_sha256=transcript,
        current_time=root["verification_time"],
    ) == _bytes(root["test_only_vault_key_b64"])

    acknowledgement = create_pairing_acknowledgement(
        invitee=invitee,
        acknowledgement_id=root["acknowledgement_id"],
        delivery=delivery,
        installed_at=root["installed_at"],
    )
    assert acknowledgement.to_dict() == root["signed_acknowledgement"]
    assert acknowledgement.canonical_bytes() == _bytes(root["signed_acknowledgement_canonical_b64"])
    verify_pairing_acknowledgement(
        acknowledgement,
        delivery=delivery,
        inviter_device_id=inviter.device_id,
        invitee_device_id=invitee.device_id,
    )

    assert offer.offer.inviter.descriptor.device_id == inviter.device_id
    assert acceptance.acceptance.invitee.descriptor.device_id == invitee.device_id


@pytest.mark.parametrize("value", ["record-e\u0301", "record-\U0001f512", "record-\nline"])
def test_pairing_bootstrap_rejects_non_ascii_authenticated_metadata(
    value: str,
) -> None:
    root = _root()
    bootstrap = copy.deepcopy(root["bootstrap"])
    bootstrap["records"][0]["key_id"] = value

    with pytest.raises(PairingKeyDeliveryError):
        PairingBootstrap.from_dict(bootstrap)


@pytest.mark.parametrize(
    "field",
    [
        "transcript_sha256",
        "inviter_device_id",
        "invitee_device_id",
        "request_sha256",
        "bootstrap_sha256",
        "key_epoch",
    ],
)
def test_delivery_binding_tamper_fails(field: str) -> None:
    root = _root()
    value = dict(root["signed_delivery"])
    inner = dict(value["delivery"])
    inner[field] = 2 if field == "key_epoch" else root["tamper_values"][field]
    value["delivery"] = inner
    with pytest.raises(PairingKeyDeliveryError):
        SignedVaultKeyDelivery.from_dict(value)


def test_signature_ciphertext_expiry_and_acknowledgement_tamper_fail() -> None:
    root = _root()
    transcript = bytes.fromhex(root["transcript_sha256"])
    request = SignedPairingKeyRequest.from_dict(root["signed_key_request"])
    delivery = SignedVaultKeyDelivery.from_dict(root["signed_delivery"])
    bootstrap = PairingBootstrap.from_dict(root["bootstrap"])
    acknowledgement = SignedPairingAcknowledgement.from_dict(root["signed_acknowledgement"])

    with pytest.raises(PairingKeyDeliveryError):
        open_vault_key_delivery(
            delivery,
            key_request=request,
            invitee_ephemeral_private_key=_bytes(root["invitee_ephemeral_private_key_b64"]),
            bootstrap=bootstrap,
            transcript_sha256=transcript,
            current_time=root["expired_verification_time"],
        )
    with pytest.raises(PairingKeyDeliveryError):
        verify_pairing_acknowledgement(
            acknowledgement,
            delivery=SignedVaultKeyDelivery.from_dict(root["other_delivery"]),
            inviter_device_id=root["inviter"]["device_id"],
            invitee_device_id=root["invitee"]["device_id"],
        )

    signature_tamper = dict(root["signed_delivery"])
    signature = bytearray(_bytes(signature_tamper["signature"]))
    signature[0] ^= 1
    signature_tamper["signature"] = base64.b64encode(signature).decode("ascii")
    with pytest.raises(PairingKeyDeliveryError):
        SignedVaultKeyDelivery.from_dict(signature_tamper)

    ciphertext_tamper = dict(root["signed_delivery"])
    delivery_value = dict(ciphertext_tamper["delivery"])
    ciphertext = bytearray(_bytes(delivery_value["ciphertext"]))
    ciphertext[-1] ^= 1
    delivery_value["ciphertext"] = base64.b64encode(ciphertext).decode("ascii")
    ciphertext_tamper["delivery"] = delivery_value
    with pytest.raises(PairingKeyDeliveryError):
        SignedVaultKeyDelivery.from_dict(ciphertext_tamper)


def test_all_zero_ephemeral_secret_and_errors_fail_closed_without_secrets() -> None:
    root = _root()
    secret = root["test_only_vault_key_b64"]
    with pytest.raises(PairingKeyDeliveryError) as raised:
        open_vault_key_delivery(
            SignedVaultKeyDelivery.from_dict(root["signed_delivery"]),
            key_request=SignedPairingKeyRequest.from_dict(root["signed_key_request"]),
            invitee_ephemeral_private_key=b"\0" * 32,
            bootstrap=PairingBootstrap.from_dict(root["bootstrap"]),
            transcript_sha256=bytes.fromhex(root["transcript_sha256"]),
            current_time=root["verification_time"],
        )
    assert secret not in str(raised.value)


def _production_delivery(delivery_id: str) -> SignedVaultKeyDelivery:
    root = _root()
    return create_vault_key_delivery(
        inviter=_identity(root, "inviter"),
        key_request=SignedPairingKeyRequest.from_dict(root["signed_key_request"]),
        transcript_sha256=bytes.fromhex(root["transcript_sha256"]),
        bootstrap=PairingBootstrap.from_dict(root["bootstrap"]),
        vault_key=_bytes(root["test_only_vault_key_b64"]),
        delivery_id=delivery_id,
        key_epoch=root["vault_key_epoch"],
        expires_at=root["delivery_expires_at"],
    )


def _delivery_entropy(delivery: SignedVaultKeyDelivery) -> tuple[bytes, bytes]:
    value = delivery.delivery
    return value.inviter_ephemeral_public_key, value.nonce


def test_production_delivery_api_owns_ephemeral_key_and_nonce() -> None:
    parameters = inspect.signature(create_vault_key_delivery).parameters
    assert "inviter_ephemeral_private_key" not in parameters
    assert "nonce" not in parameters


def test_production_delivery_revision_stress_and_crash_retry_use_unique_entropy() -> None:
    delivery_id = str(uuid.UUID(int=1))
    pairs = {
        _delivery_entropy(_production_delivery(delivery_id))
        for _ in range(96)
    }
    assert len(pairs) == 96

    abandoned = _production_delivery(delivery_id)
    recovered = _production_delivery(delivery_id)
    assert _delivery_entropy(abandoned) != _delivery_entropy(recovered)


def test_production_delivery_concurrency_uses_unique_entropy() -> None:
    delivery_id = str(uuid.UUID(int=2))
    with ThreadPoolExecutor(max_workers=8) as pool:
        deliveries = list(pool.map(_production_delivery, [delivery_id] * 48))
    assert len({_delivery_entropy(value) for value in deliveries}) == 48
