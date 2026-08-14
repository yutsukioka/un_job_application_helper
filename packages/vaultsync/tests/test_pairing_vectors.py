from __future__ import annotations

import base64
import json
import os
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest

from vaultsync.device_identity import (
    SignedDeviceDescriptor,
    device_identity_from_private_keys,
    verify_signed_device_descriptor,
)
from vaultsync.pairing import (
    InMemoryPairingReplayGuard,
    PairingError,
    PairingProofs,
    SignedPairingAcceptance,
    SignedPairingOffer,
    create_pairing_acceptance,
    create_pairing_offer,
    derive_pairing_proofs,
    derive_pairing_session_key,
    derive_pairing_session_key_from_shared_secret,
    pairing_transcript_sha256,
    verify_pairing_offer,
    verify_pairing_transcript,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
VECTOR_PATH = (
    REPO_ROOT
    / "contracts"
    / "sync"
    / "test_vectors"
    / "atlasvault_device_identity_pairing_vectors_v1.json"
)


def load_vector() -> dict[str, Any]:
    return json.loads(VECTOR_PATH.read_text(encoding="utf-8"))


def decode64(value: str) -> bytes:
    return base64.b64decode(value.encode("ascii"), validate=True)


def identity(root: dict[str, Any], name: str):
    vector = root[name]
    return device_identity_from_private_keys(
        signing_private_seed=decode64(vector["signing_private_seed"]),
        agreement_private_key=decode64(vector["agreement_private_key"]),
        created_at=vector["descriptor"]["created_at"],
        key_epoch=vector["descriptor"]["key_epoch"],
    )


def test_offer_and_acceptance_match_exact_vector_bytes_and_signatures() -> None:
    root = load_vector()
    pairing = root["pairing"]
    inviter = identity(root, "device_a")
    invitee = identity(root, "device_b")

    offer = create_pairing_offer(
        inviter,
        offer_id=pairing["offer_id"],
        nonce=decode64(pairing["offer_nonce"]),
        issued_at=pairing["issued_at"],
        expires_at=pairing["expires_at"],
    )
    acceptance = create_pairing_acceptance(
        invitee,
        offer,
        nonce=decode64(pairing["acceptance_nonce"]),
        accepted_at=pairing["accepted_at"],
        current_time=pairing["verification_time"],
    )

    assert offer.to_dict() == pairing["signed_offer"]
    assert offer.signature == decode64(pairing["offer_signature"])
    assert offer.canonical_bytes() == decode64(
        pairing["signed_offer_canonical_json_b64"]
    )
    assert offer.sha256_hex() == pairing["offer_sha256"]
    assert acceptance.to_dict() == pairing["signed_acceptance"]
    assert acceptance.signature == decode64(pairing["acceptance_signature"])
    assert acceptance.canonical_bytes() == decode64(
        pairing["signed_acceptance_canonical_json_b64"]
    )


def test_shared_secret_transcript_session_and_proofs_match_vector() -> None:
    root = load_vector()
    pairing = root["pairing"]
    inviter = identity(root, "device_a")
    invitee = identity(root, "device_b")
    offer = SignedPairingOffer.from_dict(pairing["signed_offer"])
    acceptance = SignedPairingAcceptance.from_dict(pairing["signed_acceptance"])

    transcript = pairing_transcript_sha256(offer, acceptance)
    inviter_session = derive_pairing_session_key(inviter, offer, acceptance)
    invitee_session = derive_pairing_session_key(invitee, offer, acceptance)
    proofs = derive_pairing_proofs(inviter_session, transcript)

    assert transcript.hex() == pairing["transcript_sha256"]
    assert inviter.shared_secret_for(invitee.agreement_public_key) == decode64(
        pairing["x25519_shared_secret"]
    )
    assert invitee.shared_secret_for(inviter.agreement_public_key) == decode64(
        pairing["x25519_shared_secret"]
    )
    assert inviter_session == invitee_session == decode64(
        pairing["hkdf_session_key"]
    )
    assert proofs.inviter == decode64(pairing["inviter_proof"])
    assert proofs.invitee == decode64(pairing["invitee_proof"])


def test_complete_verification_consumes_replay_guard_only_after_proofs() -> None:
    root = load_vector()
    pairing = root["pairing"]
    inviter = identity(root, "device_a")
    offer = SignedPairingOffer.from_dict(pairing["signed_offer"])
    acceptance = SignedPairingAcceptance.from_dict(pairing["signed_acceptance"])
    proofs = PairingProofs(
        inviter=decode64(pairing["inviter_proof"]),
        invitee=decode64(pairing["invitee_proof"]),
    )
    guard = InMemoryPairingReplayGuard()

    verified = verify_pairing_transcript(
        local_identity=inviter,
        signed_offer=offer,
        signed_acceptance=acceptance,
        proofs=proofs,
        current_time=pairing["verification_time"],
        replay_guard=guard,
    )
    assert verified.transcript_sha256.hex() == pairing["transcript_sha256"]
    assert verified.session_key == decode64(pairing["hkdf_session_key"])

    with pytest.raises(PairingError, match="pairing verification failed"):
        verify_pairing_transcript(
            local_identity=inviter,
            signed_offer=offer,
            signed_acceptance=acceptance,
            proofs=proofs,
            current_time=pairing["verification_time"],
            replay_guard=guard,
        )

    fresh_guard = InMemoryPairingReplayGuard()
    swapped = PairingProofs(inviter=proofs.invitee, invitee=proofs.inviter)
    with pytest.raises(PairingError, match="pairing verification failed"):
        verify_pairing_transcript(
            local_identity=inviter,
            signed_offer=offer,
            signed_acceptance=acceptance,
            proofs=swapped,
            current_time=pairing["verification_time"],
            replay_guard=fresh_guard,
        )
    assert fresh_guard.consumed_count == 0


def test_replay_guard_consumption_is_atomic_across_threads() -> None:
    guard = InMemoryPairingReplayGuard()
    callers_ready = Barrier(3)
    unsynchronized_contains = Barrier(2)

    class CoordinatedSet(set[tuple[str, bytes, str]]):
        def __contains__(self, item: object) -> bool:
            present = super().__contains__(item)
            guard_lock = getattr(guard, "_lock", None)
            if guard_lock is None or not guard_lock.locked():
                unsynchronized_contains.wait()
            return present

    guard._consumed = CoordinatedSet()

    def consume() -> str:
        callers_ready.wait()
        return guard.consume(
            "00000000-0000-4000-8000-000000000001",
            bytes.fromhex("11" * 32),
            "2026-01-15T12:15:00Z",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = [executor.submit(consume), executor.submit(consume)]
        callers_ready.wait()
        results = [outcome.result() for outcome in outcomes]

    assert sorted(results) == ["accepted", "already_consumed"]
    assert guard.consumed_count == 1


def test_offer_lifetime_over_600_seconds_fails_at_creation() -> None:
    root = load_vector()
    pairing = root["pairing"]
    inviter = identity(root, "device_a")

    with pytest.raises(PairingError, match="pairing verification failed"):
        create_pairing_offer(
            inviter,
            offer_id=pairing["offer_id"],
            nonce=decode64(pairing["offer_nonce"]),
            issued_at="2026-01-15T12:05:00Z",
            expires_at="2026-01-15T12:15:01Z",
        )


@pytest.mark.parametrize(
    ("issued_at", "expires_at", "current_time"),
    [
        ("2026-01-15T12:05:00Z", "2026-01-15T12:15:00Z", "2026-01-15T12:15:00Z"),
        ("2026-01-15T12:09:01Z", "2026-01-15T12:15:00Z", "2026-01-15T12:07:00Z"),
    ],
)
def test_expired_and_future_issued_offers_fail_verification(
    issued_at: str,
    expires_at: str,
    current_time: str,
) -> None:
    root = load_vector()
    pairing = root["pairing"]
    inviter = identity(root, "device_a")
    invitee = identity(root, "device_b")
    offer = create_pairing_offer(
        inviter,
        offer_id=pairing["offer_id"],
        nonce=decode64(pairing["offer_nonce"]),
        issued_at=issued_at,
        expires_at=expires_at,
    )

    with pytest.raises(PairingError, match="pairing verification failed"):
        create_pairing_acceptance(
            invitee,
            offer,
            nonce=decode64(pairing["acceptance_nonce"]),
            accepted_at=pairing["accepted_at"],
            current_time=current_time,
        )


def test_offer_and_acceptance_tampering_fail_without_consumption() -> None:
    root = load_vector()
    pairing = root["pairing"]
    inviter = identity(root, "device_a")
    proofs = PairingProofs(
        inviter=decode64(pairing["inviter_proof"]),
        invitee=decode64(pairing["invitee_proof"]),
    )
    invalid_pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []

    bad_offer_signature = deepcopy(pairing["signed_offer"])
    signature = bytearray(decode64(bad_offer_signature["signature"]))
    signature[0] ^= 1
    bad_offer_signature["signature"] = base64.b64encode(signature).decode()
    invalid_pairs.append((bad_offer_signature, pairing["signed_acceptance"]))

    bad_offer_nonce = deepcopy(pairing["signed_offer"])
    nonce = bytearray(decode64(bad_offer_nonce["offer"]["nonce"]))
    nonce[0] ^= 1
    bad_offer_nonce["offer"]["nonce"] = base64.b64encode(nonce).decode()
    invalid_pairs.append((bad_offer_nonce, pairing["signed_acceptance"]))

    bad_acceptance_hash = deepcopy(pairing["signed_acceptance"])
    bad_acceptance_hash["acceptance"]["offer_sha256"] = "0" * 64
    invalid_pairs.append((pairing["signed_offer"], bad_acceptance_hash))

    bad_acceptance_signature = deepcopy(pairing["signed_acceptance"])
    signature = bytearray(decode64(bad_acceptance_signature["signature"]))
    signature[-1] ^= 1
    bad_acceptance_signature["signature"] = base64.b64encode(signature).decode()
    invalid_pairs.append((pairing["signed_offer"], bad_acceptance_signature))

    for offer_json, acceptance_json in invalid_pairs:
        guard = InMemoryPairingReplayGuard()
        with pytest.raises(PairingError, match="pairing verification failed"):
            verify_pairing_transcript(
                local_identity=inviter,
                signed_offer=SignedPairingOffer.from_dict(offer_json),
                signed_acceptance=SignedPairingAcceptance.from_dict(
                    acceptance_json
                ),
                proofs=proofs,
                current_time=pairing["verification_time"],
                replay_guard=guard,
            )
        assert guard.consumed_count == 0


def test_same_identity_and_all_zero_shared_secret_fail_closed() -> None:
    root = load_vector()
    pairing = root["pairing"]
    inviter = identity(root, "device_a")
    same_identity_acceptance = deepcopy(pairing["signed_acceptance"])
    same_identity_acceptance["acceptance"]["invitee"] = root["device_a"][
        "signed_descriptor"
    ]

    with pytest.raises(PairingError, match="pairing verification failed"):
        derive_pairing_session_key(
            inviter,
            SignedPairingOffer.from_dict(pairing["signed_offer"]),
            SignedPairingAcceptance.from_dict(same_identity_acceptance),
        )

    with pytest.raises(PairingError, match="pairing verification failed"):
        derive_pairing_session_key_from_shared_secret(
            shared_secret=b"\0" * 32,
            transcript_sha256=bytes.fromhex(pairing["transcript_sha256"]),
        )


def test_invalid_case_manifest_is_complete_and_errors_are_redacted() -> None:
    root = load_vector()
    expected = {
        "descriptor_signature_tamper",
        "descriptor_device_id_mismatch",
        "signing_key_substitution",
        "agreement_key_substitution",
        "offer_signature_tamper",
        "offer_nonce_tamper",
        "offer_id_tamper",
        "expired_offer",
        "excessive_lifetime",
        "future_issue_time",
        "acceptance_offer_hash_mismatch",
        "acceptance_signature_tamper",
        "same_inviter_invitee_identity",
        "all_zero_shared_secret",
        "transcript_tamper",
        "swapped_proof",
        "replay_consumption_duplicate",
    }
    assert {case["case_id"] for case in root["invalid_cases"]} == expected

    secret_values = {
        root["device_a"]["signing_private_seed"],
        root["device_a"]["agreement_private_key"],
        root["pairing"]["hkdf_session_key"],
        root["pairing"]["x25519_shared_secret"],
    }
    failure = PairingError("pairing verification failed")
    for secret in secret_values:
        assert secret not in str(failure)


def test_python_verifies_public_swift_runtime_signature_artifact() -> None:
    directory = os.environ.get("ATLAS_DEVICE_IDENTITY_RUNTIME_VECTOR_DIR")
    if directory is None:
        pytest.skip("Swift runtime signature artifact was not requested")
    artifact_path = Path(directory) / "swift-generated-signed-transcript.json"
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert set(artifact) == {
        "_warning",
        "format",
        "version",
        "device_a_id",
        "device_b_id",
        "signed_descriptor_a",
        "signed_descriptor_a_canonical_json_b64",
        "signed_descriptor_b",
        "signed_descriptor_b_canonical_json_b64",
        "signed_offer",
        "signed_offer_canonical_json_b64",
        "signed_acceptance",
        "signed_acceptance_canonical_json_b64",
        "verification_time",
        "transcript_sha256",
        "inviter_proof",
        "invitee_proof",
    }
    assert artifact["_warning"] == (
        "FAKE TEST DATA ONLY - PUBLIC SIGNED ARTIFACT"
    )
    assert artifact["format"] == (
        "atlasvault-swift-runtime-signed-transcript-v1"
    )
    assert artifact["version"] == 1

    descriptor_a = SignedDeviceDescriptor.from_dict(
        artifact["signed_descriptor_a"]
    )
    descriptor_b = SignedDeviceDescriptor.from_dict(
        artifact["signed_descriptor_b"]
    )
    assert descriptor_a.canonical_bytes() == decode64(
        artifact["signed_descriptor_a_canonical_json_b64"]
    )
    assert descriptor_b.canonical_bytes() == decode64(
        artifact["signed_descriptor_b_canonical_json_b64"]
    )
    assert verify_signed_device_descriptor(descriptor_a).device_id == artifact[
        "device_a_id"
    ]
    assert verify_signed_device_descriptor(descriptor_b).device_id == artifact[
        "device_b_id"
    ]

    offer = SignedPairingOffer.from_dict(artifact["signed_offer"])
    acceptance = SignedPairingAcceptance.from_dict(
        artifact["signed_acceptance"]
    )
    assert offer.canonical_bytes() == decode64(
        artifact["signed_offer_canonical_json_b64"]
    )
    assert acceptance.canonical_bytes() == decode64(
        artifact["signed_acceptance_canonical_json_b64"]
    )
    assert offer.offer.inviter == descriptor_a
    assert acceptance.acceptance.invitee == descriptor_b
    assert acceptance.acceptance.offer_sha256 == offer.sha256_hex()
    verify_pairing_offer(offer, current_time=artifact["verification_time"])

    vector = load_vector()
    inviter = identity(vector, "device_a")
    transcript = pairing_transcript_sha256(offer, acceptance)
    session_key = derive_pairing_session_key(inviter, offer, acceptance)
    proofs = derive_pairing_proofs(session_key, transcript)
    assert transcript.hex() == artifact["transcript_sha256"]
    assert proofs.inviter == decode64(artifact["inviter_proof"])
    assert proofs.invitee == decode64(artifact["invitee_proof"])

    verified = verify_pairing_transcript(
        local_identity=inviter,
        signed_offer=offer,
        signed_acceptance=acceptance,
        proofs=PairingProofs(
            inviter=decode64(artifact["inviter_proof"]),
            invitee=decode64(artifact["invitee_proof"]),
        ),
        current_time=artifact["verification_time"],
        replay_guard=InMemoryPairingReplayGuard(),
    )
    assert verified.transcript_sha256 == transcript
