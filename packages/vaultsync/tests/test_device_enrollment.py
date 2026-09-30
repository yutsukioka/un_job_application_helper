"""D099: public-only synthetic vectors; confirmation values are never diagnostics."""

import copy
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from vaultsync.device_enrollment import (
    EnrollmentError,
    create_enrollment,
    verify_enrollment,
    verify_enrollment_attestation,
)

V = json.loads(
    (
        Path(__file__).resolve().parents[3]
        / "contracts/sync/test_vectors/atlasvault_device_enrollment_v1.json"
    ).read_text()
)


def checked(proof=None, registry=None, context=None, transcript=None, status="ACTIVE"):
    return verify_enrollment(
        proof or V["proof"],
        registry=registry or V["registry"],
        context=context or V["context"],
        confirmed_transcript=transcript
        if transcript is not None
        else V["proof"]["transcript_sha256"],
        status=status,
    )


def test_shared_enrollment_and_active_signature():
    after = checked()
    assert len(after) == len(V["registry"]) + 1
    assert V["target"] in after
    unsigned = {k: v for k, v in V["proof"].items() if k not in ("root", "signature_b64")}
    created = create_enrollment(
        unsigned,
        registry=V["registry"],
        context=V["context"],
        confirmed_transcript=V["proof"]["transcript_sha256"],
        status="ACTIVE",
        signing_key=Ed25519PrivateKey.from_private_bytes(bytes([10]) * 32),
    )
    assert created == V["proof"]


def test_server_attestation_does_not_substitute_for_client_confirmation():
    assert (
        verify_enrollment_attestation(
            V["proof"], registry=V["registry"], context=V["context"], status="ACTIVE"
        )
        == checked()
    )
    with pytest.raises(EnrollmentError):
        checked(transcript="aa" * 32)


@pytest.mark.parametrize("field", list(V["proof"]))
def test_every_authenticated_field_rejects_substitution(field):
    bad = copy.deepcopy(V["proof"])
    bad[field] = "substitution"
    with pytest.raises(EnrollmentError, match="^ATLAS_ENROLLMENT_REJECTED$"):
        checked(bad)
    with pytest.raises(EnrollmentError, match="^ATLAS_ENROLLMENT_REJECTED$"):
        verify_enrollment_attestation(
            bad, registry=V["registry"], context=V["context"], status="ACTIVE"
        )


@pytest.mark.parametrize("field", list(V["context"]))
def test_valid_signature_cannot_override_current_context(field):
    context = dict(V["context"])
    context[field] = context[field] + 1 if isinstance(context[field], int) else "wrong-context"
    with pytest.raises(EnrollmentError):
        checked(context=context)


@pytest.mark.parametrize(
    "status",
    ["RECOVERY_PENDING", "ACTIVATION_PENDING", "CATCH_UP_PENDING", "CLEANUP_PENDING", "REVOKED"],
)
def test_pending_and_revoked_fences(status):
    with pytest.raises(EnrollmentError):
        checked(status=status)


@pytest.mark.parametrize("transcript", ["", "00" * 32, "aa" * 32])
def test_absent_or_mismatched_confirmation(transcript):
    with pytest.raises(EnrollmentError):
        checked(transcript=transcript)


def test_replay_and_revoked_identity_are_not_new_enrollment():
    with pytest.raises(EnrollmentError):
        checked(registry=checked())
    registry = copy.deepcopy(V["registry"])
    registry[0]["state"] = "REVOKED"
    with pytest.raises(EnrollmentError):
        checked(registry=registry)


@pytest.mark.parametrize(
    "field", ["version", "key_epoch", "registry_generation", "next_registry_generation"]
)
def test_float_counters_rejected(field):
    bad = dict(V["proof"])
    bad[field] = float(bad[field])
    with pytest.raises(EnrollmentError):
        checked(bad)


@pytest.mark.parametrize("field", ["account_id", "vault_id"])
def test_signed_identifier_whitespace_is_not_canonical(field):
    unsigned = {k: v for k, v in V["proof"].items() if k not in ("root", "signature_b64")}
    context = dict(V["context"])
    context[field] += "\n"
    unsigned[field] = context[field]
    with pytest.raises(EnrollmentError):
        create_enrollment(
            unsigned,
            registry=V["registry"],
            context=context,
            confirmed_transcript=V["proof"]["transcript_sha256"],
            status="ACTIVE",
            signing_key=Ed25519PrivateKey.from_private_bytes(bytes([10]) * 32),
        )
