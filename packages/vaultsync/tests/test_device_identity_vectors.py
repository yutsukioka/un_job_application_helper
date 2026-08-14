from __future__ import annotations

import base64
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

import vaultsync
import vaultsync.device_identity as device_identity
from vaultsync.device_identity import (
    DeviceDescriptor,
    DeviceIdentity,
    DeviceIdentityError,
    DeviceIdentitySecret,
    SignedDeviceDescriptor,
    device_identity_from_private_keys,
    derive_device_id,
    verify_signed_device_descriptor,
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
    document = json.loads(VECTOR_PATH.read_text(encoding="utf-8"))
    assert set(document) == {
        "_warning",
        "format",
        "version",
        "device_a",
        "device_b",
        "pairing",
        "invalid_cases",
    }
    assert document["_warning"] == "FAKE TEST DATA ONLY"
    assert document["format"] == "atlasvault-device-identity-pairing-v1"
    assert document["version"] == 1
    return document


def decode64(value: str) -> bytes:
    return base64.b64decode(value.encode("ascii"), validate=True)


@pytest.mark.parametrize("device_name", ["device_a", "device_b"])
def test_private_vectors_rederive_public_identity_and_signatures(
    device_name: str,
) -> None:
    vector = load_vector()[device_name]
    identity = device_identity_from_private_keys(
        signing_private_seed=decode64(vector["signing_private_seed"]),
        agreement_private_key=decode64(vector["agreement_private_key"]),
        created_at=vector["descriptor"]["created_at"],
        key_epoch=vector["descriptor"]["key_epoch"],
    )

    assert identity.signing_public_key == decode64(vector["signing_public_key"])
    assert identity.agreement_public_key == decode64(
        vector["agreement_public_key"]
    )
    assert identity.device_id == vector["device_id"]
    assert identity.descriptor.to_dict() == vector["descriptor"]
    assert identity.descriptor.canonical_bytes() == decode64(
        vector["descriptor_canonical_json_b64"]
    )

    signed = identity.sign_descriptor()
    assert signed.signature == decode64(vector["descriptor_signature"])
    assert signed.to_dict() == vector["signed_descriptor"]
    assert signed.canonical_bytes() == decode64(
        vector["signed_descriptor_canonical_json_b64"]
    )
    assert verify_signed_device_descriptor(signed) == identity.descriptor


@pytest.mark.parametrize("device_name", ["device_a", "device_b"])
def test_secret_bundle_is_strict_and_rederives_identity(device_name: str) -> None:
    vector = load_vector()[device_name]
    secret = DeviceIdentitySecret.from_dict(vector["secret_bundle"])

    assert secret.canonical_bytes() == decode64(
        vector["secret_bundle_canonical_json_b64"]
    )
    assert secret.to_dict() == vector["secret_bundle"]
    assert secret.load_identity().descriptor.to_dict() == vector["descriptor"]

    extra = deepcopy(vector["secret_bundle"])
    extra["platform"] = "test"
    with pytest.raises(DeviceIdentityError, match="invalid device identity"):
        DeviceIdentitySecret.from_dict(extra)

    mismatched = deepcopy(vector["secret_bundle"])
    mismatched["device_id"] = load_vector()[
        "device_b" if device_name == "device_a" else "device_a"
    ]["device_id"]
    with pytest.raises(DeviceIdentityError, match="invalid device identity"):
        DeviceIdentitySecret.from_dict(mismatched)


def test_device_id_derivation_is_domain_separated_and_ordered() -> None:
    vector = load_vector()["device_a"]
    signing = decode64(vector["signing_public_key"])
    agreement = decode64(vector["agreement_public_key"])

    assert derive_device_id(signing, agreement) == vector["device_id"]
    assert derive_device_id(agreement, signing) != vector["device_id"]


def test_direct_identity_construction_rejects_mismatched_descriptor() -> None:
    root = load_vector()
    device_a = root["device_a"]
    device_b = root["device_b"]

    with pytest.raises(DeviceIdentityError, match="invalid device identity"):
        DeviceIdentity(
            _signing_private_seed=decode64(device_a["signing_private_seed"]),
            _agreement_private_key=decode64(device_a["agreement_private_key"]),
            descriptor=DeviceDescriptor.from_dict(device_b["descriptor"]),
        )


def test_descriptor_parser_rejects_tamper_unknown_fields_and_noncanonical_data() -> None:
    vector = load_vector()["device_a"]

    invalid_objects = []
    mismatch = deepcopy(vector["descriptor"])
    mismatch["device_id"] = load_vector()["device_b"]["device_id"]
    invalid_objects.append(mismatch)
    uppercase = deepcopy(vector["descriptor"])
    uppercase["device_id"] = uppercase["device_id"].upper()
    invalid_objects.append(uppercase)
    extra = deepcopy(vector["descriptor"])
    extra["device_label"] = "private label"
    invalid_objects.append(extra)
    boolean_epoch = deepcopy(vector["descriptor"])
    boolean_epoch["key_epoch"] = True
    invalid_objects.append(boolean_epoch)
    fractional_time = deepcopy(vector["descriptor"])
    fractional_time["created_at"] = "2026-01-15T12:00:00.000Z"
    invalid_objects.append(fractional_time)
    noncanonical_key = deepcopy(vector["descriptor"])
    noncanonical_key["signing_public_key"] = noncanonical_key[
        "signing_public_key"
    ].rstrip("=")
    invalid_objects.append(noncanonical_key)

    for invalid in invalid_objects:
        with pytest.raises(DeviceIdentityError, match="invalid device identity"):
            DeviceDescriptor.from_dict(invalid)


def test_device_key_epoch_rejects_values_above_signed_64_bit() -> None:
    root = load_vector()
    vector = root["device_a"]
    oversized = (1 << 63)

    descriptor = deepcopy(vector["descriptor"])
    descriptor["key_epoch"] = oversized
    with pytest.raises(DeviceIdentityError, match="invalid device identity"):
        DeviceDescriptor.from_dict(descriptor)

    secret = deepcopy(vector["secret_bundle"])
    secret["key_epoch"] = oversized
    with pytest.raises(DeviceIdentityError, match="invalid device identity"):
        DeviceIdentitySecret.from_dict(secret)

    with pytest.raises(DeviceIdentityError, match="invalid device identity"):
        device_identity_from_private_keys(
            signing_private_seed=decode64(vector["signing_private_seed"]),
            agreement_private_key=decode64(vector["agreement_private_key"]),
            created_at=vector["descriptor"]["created_at"],
            key_epoch=oversized,
        )


def test_signed_descriptor_rejects_signature_and_key_substitution() -> None:
    root = load_vector()
    vector = root["device_a"]

    tampered_signature = deepcopy(vector["signed_descriptor"])
    signature = bytearray(decode64(tampered_signature["signature"]))
    signature[0] ^= 1
    tampered_signature["signature"] = base64.b64encode(signature).decode("ascii")

    signing_substitution = deepcopy(vector["signed_descriptor"])
    signing_substitution["descriptor"]["signing_public_key"] = root["device_b"][
        "signing_public_key"
    ]
    signing_substitution["descriptor"]["device_id"] = derive_device_id(
        decode64(root["device_b"]["signing_public_key"]),
        decode64(vector["agreement_public_key"]),
    )

    agreement_substitution = deepcopy(vector["signed_descriptor"])
    agreement_substitution["descriptor"]["agreement_public_key"] = root[
        "device_b"
    ]["agreement_public_key"]
    agreement_substitution["descriptor"]["device_id"] = derive_device_id(
        decode64(vector["signing_public_key"]),
        decode64(root["device_b"]["agreement_public_key"]),
    )

    for invalid in (
        tampered_signature,
        signing_substitution,
        agreement_substitution,
    ):
        with pytest.raises(DeviceIdentityError, match="invalid device identity"):
            verify_signed_device_descriptor(SignedDeviceDescriptor.from_dict(invalid))


def test_secret_descriptions_and_errors_do_not_expose_private_values() -> None:
    vector = load_vector()["device_a"]
    secret = DeviceIdentitySecret.from_dict(vector["secret_bundle"])
    identity = secret.load_identity()
    private_values = (
        vector["signing_private_seed"],
        vector["agreement_private_key"],
    )

    for rendered in (repr(secret), str(secret), repr(identity), str(identity)):
        for private_value in private_values:
            assert private_value not in rendered

    invalid = deepcopy(vector["secret_bundle"])
    invalid["format"] = vector["signing_private_seed"]
    with pytest.raises(DeviceIdentityError) as failure:
        DeviceIdentitySecret.from_dict(invalid)
    for private_value in private_values:
        assert private_value not in str(failure.value)


def test_package_root_exports_identity_api_without_side_effects(monkeypatch) -> None:
    approved = {
        "DeviceDescriptor": device_identity.DeviceDescriptor,
        "DeviceIdentity": device_identity.DeviceIdentity,
        "DeviceIdentityError": device_identity.DeviceIdentityError,
        "DeviceIdentitySecret": device_identity.DeviceIdentitySecret,
        "SignedDeviceDescriptor": device_identity.SignedDeviceDescriptor,
        "device_identity_from_private_keys": (
            device_identity.device_identity_from_private_keys
        ),
        "derive_device_id": device_identity.derive_device_id,
        "generate_device_identity": device_identity.generate_device_identity,
        "verify_signed_device_descriptor": (
            device_identity.verify_signed_device_descriptor
        ),
    }
    historical = {
        "AtlasVaultExport",
        "VaultMetadata",
        "WrappedKey",
        "generate_vault_key",
        "wrap_vault_key",
        "unwrap_vault_key",
    }

    for name, defining_object in approved.items():
        assert getattr(vaultsync, name) is defining_object
        assert vaultsync.__all__.count(name) == 1
    assert historical <= set(vaultsync.__all__)
    assert len(vaultsync.__all__) == len(set(vaultsync.__all__))

    def unexpected_random(_: int) -> bytes:
        raise AssertionError("package import generated identity material")

    monkeypatch.setattr("secrets.token_bytes", unexpected_random)
    __import__("importlib").reload(vaultsync)
