"""C26 adversarial epoch activation; all cryptographic material is synthetic."""

import base64
import copy
import json
from pathlib import Path

import pytest

from vaultsync.epoch_rotation import RotationError, verify_epoch_rotation

V = json.loads(
    (
        Path(__file__).resolve().parents[3]
        / "contracts/sync/test_vectors/atlasvault_epoch_rotation_v1.json"
    ).read_text()
)


def checked(proof):
    return verify_epoch_rotation(
        proof,
        registry=V["proof"]["registry"],
        account_id=V["proof"]["plan"]["account_id"],
        vault_id="vault-c26",
        previous_epoch=3,
        state_root="ab" * 32,
    )


def test_shared_authenticated_epoch_rotation_and_exact_recipient_set():
    result = checked(V["proof"])
    assert result["new_epoch"] == 4
    assert result["recipients"] == sorted(V["device_ids"][:2])
    assert V["device_ids"][2] not in result["recipients"]
    assert result["binding_root"] == V["binding_root"]
    assert all(d["key_epoch"] == 4 for d in V["proof"]["deliveries"])


@pytest.mark.parametrize("field", list(V["proof"]["plan"]))
def test_each_epoch_context_field_is_authenticated(field):
    proof = copy.deepcopy(V["proof"])
    value = proof["plan"][field]
    proof["plan"][field] = (
        value + 1 if type(value) is int else [] if type(value) is list else "substituted"
    )
    with pytest.raises(RotationError):
        checked(proof)


@pytest.mark.parametrize(
    "attack",
    [
        "revoked_recipient",
        "missing_recipient",
        "duplicate_recipient",
        "unauthorized_signer",
        "ciphertext",
        "encapsulation",
        "signature",
        "unknown_field",
    ],
)
def test_rotation_material_and_signer_substitution_fail_closed(attack):
    proof = copy.deepcopy(V["proof"])
    if attack == "revoked_recipient":
        proof["deliveries"][0]["device_id"] = V["device_ids"][2]
    elif attack == "missing_recipient":
        proof["deliveries"].pop()
    elif attack == "duplicate_recipient":
        proof["deliveries"].append(proof["deliveries"][0])
    elif attack == "unauthorized_signer":
        proof["rotation_signer_device_id"] = V["device_ids"][2]
    elif attack == "ciphertext":
        proof["deliveries"][0]["ciphertext_b64"] = base64.b64encode(bytes(48)).decode()
    elif attack == "encapsulation":
        proof["deliveries"][0]["encapsulated_key_b64"] = base64.b64encode(bytes(32)).decode()
    elif attack == "signature":
        proof["signature_b64"] = base64.b64encode(bytes(64)).decode()
    else:
        proof["wrapped_vault_key"] = "forbidden-sentinel"
    with pytest.raises(RotationError):
        checked(proof)
